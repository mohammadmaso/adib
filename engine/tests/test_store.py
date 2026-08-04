from __future__ import annotations

from pathlib import Path

import pytest

from adib_engine.models.glossary import GlossaryTerm, GlossaryTermUpdate, TermPolicy
from adib_engine.models.project import ProjectStage, RunStatus, direction_for
from adib_engine.models.segment import SegmentStatus, SegmentUpdate
from adib_engine.segmentation import apply_translations, build_segments
from adib_engine.store import SOURCE, ProjectRegistry, create_project, open_project
from tests.fixtures import simple_book


@pytest.fixture
def store(tmp_path: Path):
    with create_project(
        tmp_path / "book.adib",
        name="Test Book",
        source_path="/tmp/book.pdf",
        target_lang="fa",
    ) as s:
        yield s


def test_create_and_reopen_roundtrips_metadata(tmp_path: Path):
    path = tmp_path / "b.adib"
    with create_project(path, name="B", source_path="/x.pdf", target_lang="ar") as s:
        s.set_stage(ProjectStage.STRUCTURE_REVIEW)

    with open_project(path) as reopened:
        meta = reopened.meta()
        assert meta.name == "B"
        assert meta.target_lang == "ar"
        assert meta.stage is ProjectStage.STRUCTURE_REVIEW


def test_create_refuses_to_clobber_existing(tmp_path: Path):
    path = tmp_path / "b.adib"
    create_project(path, name="B", source_path="/x.pdf").close()
    with pytest.raises(FileExistsError):
        create_project(path, name="B again", source_path="/y.pdf")


def test_tree_survives_a_save_load_cycle(store):
    tree = simple_book()
    store.save_tree(SOURCE, tree)

    loaded = store.load_tree(SOURCE)
    assert loaded is not None
    assert loaded.model_dump() == tree.model_dump()


def test_sync_segments_creates_rows_from_tree(store):
    added, kept, removed = store.sync_segments(simple_book())

    assert added == len(build_segments(simple_book()))
    assert (kept, removed) == (0, 0)
    assert store.segment_counts()["total"] == added


def test_resync_after_translation_preserves_work(store):
    """The core resumability guarantee: re-ingesting must not lose translations."""
    tree = simple_book()
    store.sync_segments(tree)

    seg = store.segments()[0]
    store.record_translation(seg.id, target_text="ترجمه", model_name="test")

    added, kept, removed = store.sync_segments(tree)

    assert (added, removed) == (0, 0)
    assert kept > 0
    assert store.get_segment(seg.id).target_text == "ترجمه"


def test_editing_source_text_drops_the_stale_translation(store):
    tree = simple_book()
    store.sync_segments(tree)

    seg = store.segments()[0]
    store.record_translation(seg.id, target_text="ترجمه", model_name="test")

    # Simulate a Gate 1 edit to a heading.
    tree.nodes[0].text = "A Completely Different Heading"
    added, _kept, removed = store.sync_segments(tree)

    assert added == 1 and removed == 1
    assert store.get_segment(seg.id) is None, "stale translation must not survive"


def test_pending_segments_drives_resumption(store):
    store.sync_segments(simple_book())
    total = len(store.segments())

    for seg in store.segments()[:3]:
        store.record_translation(seg.id, target_text="x", model_name="test")

    assert len(store.pending_segments()) == total - 3


def test_locked_segments_are_never_retranslated_or_overwritten(store):
    store.sync_segments(simple_book())
    seg = store.segments()[0]

    store.update_segment(seg.id, SegmentUpdate(target_text="دست‌نویس", locked=True))

    assert all(s.id != seg.id for s in store.pending_segments())

    # A run already in flight must not clobber the human's text.
    store.record_translation(seg.id, target_text="MACHINE", model_name="test")
    assert store.get_segment(seg.id).target_text == "دست‌نویس"


def test_failure_is_recorded_and_becomes_retryable(store):
    store.sync_segments(simple_book())
    seg = store.segments()[0]

    store.record_failure(seg.id, "rate limited")

    refreshed = store.get_segment(seg.id)
    assert refreshed.status is SegmentStatus.FAILED
    assert refreshed.error == "rate limited"
    assert any(s.id == seg.id for s in store.pending_segments())


def test_translations_feed_back_into_a_rendered_tree(store):
    tree = simple_book()
    store.sync_segments(tree)
    for seg in store.segments():
        store.record_translation(seg.id, target_text=f"FA:{seg.source_text}", model_name="test")

    translated = apply_translations(tree, store.translations())
    assert translated.nodes[0].text == "FA:Introduction"


def test_glossary_upsert_respects_locked_terms(store):
    store.upsert_terms(
        [GlossaryTerm(id="", source="backpropagation", target="پس‌انتشار", frequency=5)]
    )

    term = store.terms()[0]
    store.update_term(term.id, GlossaryTermUpdate(target="پس‌انتشار خطا"))

    # A second agent run proposing a different translation must not win.
    added, updated, skipped = store.upsert_terms(
        [GlossaryTerm(id="", source="backpropagation", target="بازگشت", frequency=9)]
    )

    assert (added, updated, skipped) == (0, 0, 1)
    assert store.terms()[0].target == "پس‌انتشار خطا"


def test_protected_terms_are_the_ones_needing_placeholders(store):
    store.upsert_terms(
        [
            GlossaryTerm(id="", source="TCP", policy=TermPolicy.KEEP),
            GlossaryTerm(id="", source="router", policy=TermPolicy.TRANSLATE),
            GlossaryTerm(id="", source="socket", policy=TermPolicy.TRANSLATE_PAREN),
        ]
    )

    assert {t.source for t in store.protected_terms()} == {"TCP", "socket"}


def test_disabled_terms_are_excluded_when_asked(store):
    store.upsert_terms(
        [
            GlossaryTerm(id="", source="keep-me", enabled=True),
            GlossaryTerm(id="", source="drop-me", enabled=False),
        ]
    )

    assert {t.source for t in store.terms(enabled_only=True)} == {"keep-me"}
    assert len(store.terms()) == 2


def test_runs_and_usage_accumulate_cost(store):
    run = store.start_run(ProjectStage.TRANSLATING)
    store.record_usage(
        purpose="translation",
        model="m",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.002,
        run_id=run.id,
    )
    store.record_usage(
        purpose="translation",
        model="m",
        prompt_tokens=200,
        completion_tokens=80,
        cost_usd=0.004,
        run_id=run.id,
    )
    store.finish_run(run.id, RunStatus.SUCCEEDED, stats={"segments": 2})

    assert store.total_cost() == pytest.approx(0.006)
    assert store.total_tokens() == (300, 130)
    assert store.runs()[0].status is RunStatus.SUCCEEDED


def test_summary_reports_progress(store):
    store.sync_segments(simple_book())
    for seg in store.segments()[:2]:
        store.record_translation(seg.id, target_text="x", model_name="m", cost_usd=0.001)

    summary = store.summary()
    assert summary.name == "Test Book"
    assert summary.segments_done == 2
    assert summary.cost_usd == pytest.approx(0.002)


def test_registry_lists_projects_newest_first(tmp_path: Path):
    projects = tmp_path / "projects"
    projects.mkdir()
    registry = ProjectRegistry(projects)

    for name in ("One", "Two"):
        create_project(registry.path_for(name), name=name, source_path="/x.pdf").close()

    listed = registry.list()
    assert {s.name for s in listed} == {"One", "Two"}
    assert listed == sorted(listed, key=lambda s: s.updated_at, reverse=True)


def test_registry_survives_a_corrupt_project_file(tmp_path: Path):
    projects = tmp_path / "projects"
    projects.mkdir()
    create_project(projects / "good.adib", name="Good", source_path="/x.pdf").close()
    (projects / "broken.adib").write_bytes(b"this is not a database")

    listed = ProjectRegistry(projects).list()

    # One bad file must not make the whole Library unopenable.
    assert len(listed) == 2
    broken = next(s for s in listed if s.name == "broken")
    assert broken.error is not None


def test_datetimes_read_back_timezone_aware(store):
    # SQLite drops tzinfo. If these came back naive, sorting them against an
    # aware timestamp (as the Library does for unreadable files) raises
    # TypeError and the whole listing fails.
    meta = store.meta()
    assert meta.created_at.tzinfo is not None
    assert meta.updated_at.tzinfo is not None

    run = store.start_run(ProjectStage.TRANSLATING)
    finished = store.finish_run(run.id, RunStatus.SUCCEEDED)
    assert finished.started_at.tzinfo is not None
    assert finished.finished_at.tzinfo is not None


def test_registry_keeps_non_latin_titles_readable(tmp_path: Path):
    projects = tmp_path / "projects"
    projects.mkdir()

    path = ProjectRegistry(projects).path_for("کتاب شبکه‌ها")
    assert path.stem == "کتاب-شبکه‌ها"


def test_registry_avoids_filename_collisions(tmp_path: Path):
    projects = tmp_path / "projects"
    projects.mkdir()
    registry = ProjectRegistry(projects)

    first = registry.path_for("Book")
    create_project(first, name="Book", source_path="/x.pdf").close()
    second = registry.path_for("Book")

    assert first != second
    assert second.stem == "Book-2"


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("fa", "rtl"),
        ("ar", "rtl"),
        ("fa-IR", "rtl"),
        ("en", "ltr"),
        ("de-DE", "ltr"),
        (None, "ltr"),
    ],
)
def test_direction_detection(lang, expected):
    assert direction_for(lang).value == expected
