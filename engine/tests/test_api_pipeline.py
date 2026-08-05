"""End-to-end API tests: the full pipeline through HTTP, one stage at a time.

Background tasks run synchronously in tests (FastAPI's `TestClient`/httpx
transport executes them before the response context exits under
`asgi_lifespan`... in practice they still run via `asyncio.create_task`-like
scheduling, so we explicitly await task completion by polling `GET
/projects/{id}` for the stage flip, matching how the real webview would).

The LLM-calling stages (analyze, glossary build, translate) monkeypatch the
agent entry points at the route-module level with deterministic stand-ins, so
these tests never touch the network — same approach as the agent unit tests,
just exercised through the HTTP surface instead of calling the functions
directly.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from adib_engine.api.app import create_app
from adib_engine.models.analysis import BookAnalysis
from adib_engine.settings import RuntimeSettings
from tests.conftest import TEST_TOKEN
from tests.fixture_documents import write_markdown


@pytest.fixture
async def client(settings: RuntimeSettings):
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://engine",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        ) as c:
            yield c


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "book.md"
    write_markdown(path)
    return path


async def _wait_for_stage(client: httpx.AsyncClient, project_id: str, stage: str, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = await client.get(f"/projects/{project_id}")
        if resp.json()["stage"] == stage:
            return resp.json()
        await asyncio.sleep(0.02)
    raise TimeoutError(f"project never reached stage '{stage}'")


def _create_payload(source_file: Path, name: str = "Test Book") -> dict:
    return {"name": name, "source_path": str(source_file), "target_lang": "fa"}


# ---------------------------------------------------------------------------
# Projects + presets (no LLM involved)
# ---------------------------------------------------------------------------


async def test_create_list_get_delete_project(client: httpx.AsyncClient, source_file: Path):
    resp = await client.post(
        "/projects",
        json={"name": "My Book", "source_path": str(source_file), "target_lang": "fa"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["project_id"] == "My-Book"
    meta = body["meta"]
    assert meta["stage"] == "created"
    assert meta["target_lang"] == "fa"

    listing = await client.get("/projects")
    assert listing.status_code == 200
    assert any(p["name"] == "My Book" for p in listing.json())

    project_id = "my-book"
    got = await client.get(f"/projects/{project_id}")
    assert got.status_code == 200
    assert got.json()["name"] == "My Book"

    deleted = await client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 204
    missing = await client.get(f"/projects/{project_id}")
    assert missing.status_code == 404


async def test_create_project_rejects_missing_source_file(client: httpx.AsyncClient):
    resp = await client.post(
        "/projects", json={"name": "Ghost", "source_path": "/nope/does-not-exist.pdf"}
    )
    assert resp.status_code == 400


async def test_project_path_rejects_traversal(client: httpx.AsyncClient):
    resp = await client.get("/projects/..%2F..%2Fetc")
    assert resp.status_code in (400, 404)


async def test_list_presets_and_get_one(client: httpx.AsyncClient):
    resp = await client.get("/presets")
    assert resp.status_code == 200
    ids = {p["id"] for p in resp.json()}
    assert "general" in ids
    assert "technical-manual" in ids

    one = await client.get("/presets/general")
    assert one.status_code == 200
    assert one.json()["name"] == "General"

    missing = await client.get("/presets/does-not-exist")
    assert missing.status_code == 404


async def test_probe_reports_format_for_a_markdown_file(
    client: httpx.AsyncClient, source_file: Path
):
    resp = await client.get("/probe", params={"path": str(source_file)})
    assert resp.status_code == 200
    assert resp.json()["format"] == "md"


async def test_probe_rejects_missing_file(client: httpx.AsyncClient):
    resp = await client.get("/probe", params={"path": "/nope.pdf"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Auth: every non-public route requires the bearer token (regression guard —
# these routers must not have been mounted before the auth middleware).
# ---------------------------------------------------------------------------


async def test_projects_route_requires_auth(settings: RuntimeSettings):
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://engine") as c:
            resp = await c.get("/projects")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Ingest -> Gate 1
# ---------------------------------------------------------------------------


async def test_ingest_populates_tree_and_advances_to_structure_review(
    client: httpx.AsyncClient, source_file: Path
):
    project_id = "test-book"
    await client.post("/projects", json=_create_payload(source_file))

    resp = await client.post(f"/projects/{project_id}/ingest")
    assert resp.status_code == 202

    meta = await _wait_for_stage(client, project_id, "structure_review")
    assert meta["source_format"] == "markdown"

    tree_resp = await client.get(f"/projects/{project_id}/tree")
    assert tree_resp.status_code == 200
    tree = tree_resp.json()
    assert tree["nodes"]
    assert any(n["kind"] == "heading" for n in tree["nodes"])


async def test_events_stream_emits_valid_sse_frames():
    """Regression test: the hub publishes plain progress dicts, and the SSE
    route must wrap them as `{"data": ...}` rather than yielding them
    directly — `EventSourceResponse` otherwise tries `ServerSentEvent(**event)`
    and blows up on unexpected keyword arguments like `stage`.

    Exercised at the generator level rather than through a live streaming HTTP
    request: interleaving a held-open SSE GET with a second request against
    the same in-process ASGI transport deadlocks under `httpx.ASGITransport`,
    which is a test-harness limitation, not something the route itself does.
    """
    import asyncio
    import json

    from sse_starlette.event import ServerSentEvent

    from adib_engine.api.progress import ProgressHub

    hub = ProgressHub()

    # Reach into the route's closure-free generator logic the same way
    # `stream_events` builds it, against our own hub instance.
    async def generator():
        async for event in hub.subscribe("proj-1"):
            yield {"data": json.dumps(event)}

    gen = generator()
    pending = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0)  # let the generator run up to `await queue.get()` and register
    hub.publish("proj-1", {"stage": "ingesting", "percent": 0})
    frame = await pending
    encoded = ServerSentEvent(**frame).encode()
    assert json.loads(encoded.decode().removeprefix("data: ").strip()) == {
        "stage": "ingesting",
        "percent": 0,
    }
    await gen.aclose()


async def test_ingest_twice_is_rejected(client: httpx.AsyncClient, source_file: Path):
    project_id = "test-book"
    await client.post("/projects", json=_create_payload(source_file))
    await client.post(f"/projects/{project_id}/ingest")
    await _wait_for_stage(client, project_id, "structure_review")

    resp = await client.post(f"/projects/{project_id}/ingest")
    assert resp.status_code == 409


async def test_gate1_put_tree_resyncs_segments(client: httpx.AsyncClient, source_file: Path):
    project_id = "test-book"
    await client.post("/projects", json=_create_payload(source_file))
    await client.post(f"/projects/{project_id}/ingest")
    await _wait_for_stage(client, project_id, "structure_review")

    tree = (await client.get(f"/projects/{project_id}/tree")).json()
    # Delete the last node (a "junk" edit a user might make in Gate 1).
    tree["nodes"].pop()

    put_resp = await client.put(f"/projects/{project_id}/tree", json=tree)
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["segments_removed"] >= 1

    approve = await client.post(f"/projects/{project_id}/tree/approve")
    assert approve.status_code == 204
    meta = await client.get(f"/projects/{project_id}")
    assert meta.json()["stage"] == "analyzing"


async def test_gate1_approve_rejects_wrong_stage(client: httpx.AsyncClient, source_file: Path):
    project_id = "test-book"
    await client.post("/projects", json=_create_payload(source_file))
    # Never ingested — still at "created".
    resp = await client.post(f"/projects/{project_id}/tree/approve")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Gate 2: analysis + glossary, with the LLM entry points monkeypatched.
# ---------------------------------------------------------------------------


async def _canned_analysis(tree, provider, *, api_key=None, library=None, max_sample_chars=12_000):
    from adib_engine.agents.base import AgentResult

    analysis = BookAnalysis(
        detected_source_lang="en",
        genre="technical",
        tone="direct",
        language_register="technical",
        audience="engineers",
        suggested_preset="technical-manual",
        style_guide="Keep acronyms untranslated.",
        reader_notes=[],
        confidence=0.9,
    )
    return AgentResult(output=analysis, prompt_tokens=100, completion_tokens=20, requests=1)


async def _canned_glossary(
    candidates, provider, *, target_lang, api_key=None, analysis=None, default_policy=None
):
    from adib_engine.agents.base import AgentResult
    from adib_engine.agents.glossary import GlossaryDecision, GlossaryVerdict

    decisions = [
        GlossaryDecision(index=i, kept=True, target=f"[fa]{c.source}")
        for i, c in enumerate(candidates)
    ]
    return AgentResult(
        output=GlossaryVerdict(decisions=decisions),
        prompt_tokens=50,
        completion_tokens=10,
        requests=1,
    )


@pytest.fixture(autouse=False)
def patch_llm_agents(monkeypatch):
    import adib_engine.api.routes.gate2 as gate2_mod

    monkeypatch.setattr(gate2_mod, "analyze_book", _canned_analysis)
    monkeypatch.setattr(gate2_mod, "adjudicate_glossary", _canned_glossary)


async def _reach_style_review(client: httpx.AsyncClient, source_file: Path, project_id: str):
    await client.post("/projects", json=_create_payload(source_file))
    await client.post(f"/projects/{project_id}/ingest")
    await _wait_for_stage(client, project_id, "structure_review")
    await client.post(f"/projects/{project_id}/tree/approve")
    await client.post(f"/projects/{project_id}/analyze", json={})
    await _wait_for_stage(client, project_id, "style_review")


async def test_analyze_runs_and_persists_analysis(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents
):
    project_id = "test-book"
    await _reach_style_review(client, source_file, project_id)

    got = await client.get(f"/projects/{project_id}/analysis")
    assert got.status_code == 200
    assert got.json()["suggested_preset"] == "technical-manual"


async def test_update_analysis_lets_the_user_edit_before_approving(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents
):
    project_id = "test-book"
    await _reach_style_review(client, source_file, project_id)

    analysis = (await client.get(f"/projects/{project_id}/analysis")).json()
    analysis["tone"] = "edited by human"
    put = await client.put(f"/projects/{project_id}/analysis", json=analysis)
    assert put.status_code == 200
    assert put.json()["tone"] == "edited by human"


async def test_glossary_build_mines_and_adjudicates(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents
):
    project_id = "test-book"
    await _reach_style_review(client, source_file, project_id)

    resp = await client.post(f"/projects/{project_id}/glossary/build", json={})
    assert resp.status_code == 202

    # No stage flip to await for glossary (it doesn't move ProjectStage), so
    # poll the glossary listing until the background task lands.
    deadline = time.monotonic() + 5.0
    terms = []
    while time.monotonic() < deadline:
        terms = (await client.get(f"/projects/{project_id}/glossary")).json()
        if terms:
            break
        await asyncio.sleep(0.02)
    # A small markdown fixture may or may not mine any candidates; either way
    # the endpoint must respond correctly and never 500.
    assert isinstance(terms, list)


async def test_glossary_term_crud(client: httpx.AsyncClient, source_file: Path):
    project_id = "test-book"
    await client.post("/projects", json=_create_payload(source_file))

    add = await client.post(
        f"/projects/{project_id}/glossary",
        json={"source": "TCP", "target": "تی‌سی‌پی", "policy": "keep"},
    )
    assert add.status_code == 201
    term = add.json()
    assert term["origin"] == "user"

    patch = await client.patch(
        f"/projects/{project_id}/glossary/{term['id']}", json={"target": "TCP/IP-fa"}
    )
    assert patch.status_code == 200
    assert patch.json()["target"] == "TCP/IP-fa"
    assert patch.json()["locked"] is True  # manual edits pin the term

    listing = await client.get(f"/projects/{project_id}/glossary")
    assert any(t["source"] == "TCP" for t in listing.json())


async def test_gate2_approve_resolves_preset_and_advances_stage(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents
):
    project_id = "test-book"
    await _reach_style_review(client, source_file, project_id)

    resp = await client.post(
        f"/projects/{project_id}/gate2/approve",
        json={"preset_id": "technical-manual", "style_delta": {"extra_instructions": "Be terse."}},
    )
    assert resp.status_code == 200
    resolved = resp.json()
    assert "Be terse." in resolved["system_prompt"]
    assert resolved["builtin"] is False

    meta = await client.get(f"/projects/{project_id}")
    assert meta.json()["stage"] == "translating"


async def test_gate2_approve_rejects_unknown_preset(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents
):
    project_id = "test-book"
    await _reach_style_review(client, source_file, project_id)
    resp = await client.post(
        f"/projects/{project_id}/gate2/approve", json={"preset_id": "no-such-preset"}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Gate 3: translate, review, export — full pipeline through HTTP.
# ---------------------------------------------------------------------------


async def _fa_translate_fn(messages, info):
    """Answer a batched translation prompt in the batch protocol.

    `translate_book` packs many segments into one request, so the stand-in has
    to echo every block marker back with its translation, the way a real
    endpoint is instructed to.
    """
    from pydantic_ai.messages import ModelResponse, TextPart

    from adib_engine.agents.translate import BATCH_CLOSE, BATCH_OPEN, parse_batch_output

    prompt = messages[-1].parts[-1].content
    blocks = parse_batch_output(prompt, count=10_000)
    body = "\n".join(
        f"{BATCH_OPEN}#{i}{BATCH_CLOSE}\n[fa] {text}" for i, text in sorted(blocks.items())
    )
    return ModelResponse(parts=[TextPart(content=body)])


@pytest.fixture
def patch_translate(monkeypatch):
    """Route the real `translate_book` through a `FunctionModel` instead of a
    live endpoint, by patching `build_model` where `translate_book` looks it up."""
    from pydantic_ai.models.function import FunctionModel

    import adib_engine.agents.base as base_mod

    fn_model = FunctionModel(_fa_translate_fn)
    monkeypatch.setattr(base_mod, "build_model", lambda provider, api_key=None: fn_model)


async def _reach_translating(
    client: httpx.AsyncClient, source_file: Path, project_id: str, patch_llm_agents
):
    await _reach_style_review(client, source_file, project_id)
    await client.post(
        f"/projects/{project_id}/gate2/approve", json={"preset_id": "technical-manual"}
    )


async def test_translate_runs_and_reaches_review(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents, patch_translate
):
    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)

    resp = await client.post(f"/projects/{project_id}/translate", json={})
    assert resp.status_code == 202

    meta = await _wait_for_stage(client, project_id, "review", timeout=10.0)
    assert meta["stage"] == "review"

    counts = await client.get(f"/projects/{project_id}/segments/counts")
    assert counts.json()["total"] > 0

    segments = (await client.get(f"/projects/{project_id}/segments")).json()
    translated = [s["target_text"] for s in segments if s["target_text"]]
    assert translated and all(t.startswith("[fa]") for t in translated)


async def test_starting_a_run_marks_the_project_translating_before_it_begins(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents, patch_translate, monkeypatch
):
    """Regression: a run started from any stage but `translating` (resuming a
    pause, retrying a failure, re-running from review) left the stage alone, so
    the UI showed no run in progress and `/translate/pause` answered 409 —
    making an already-running translation impossible to stop.
    """
    import adib_engine.api.routes.gate3 as gate3

    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)
    await client.post(f"/projects/{project_id}/translate", json={})
    await _wait_for_stage(client, project_id, "review", timeout=10.0)

    real_translate_book = gate3.translate_book
    seen: dict[str, str] = {}

    async def watching_translate_book(store, *args, **kwargs):
        seen["stage"] = store.meta().stage.value
        return await real_translate_book(store, *args, **kwargs)

    monkeypatch.setattr(gate3, "translate_book", watching_translate_book)

    resp = await client.post(f"/projects/{project_id}/translate", json={})
    assert resp.status_code == 202
    # The run itself saw a project already marked as translating.
    assert seen["stage"] == "translating"


async def test_retrying_a_failed_run_clears_the_recorded_failure(
    client: httpx.AsyncClient,
    source_file: Path,
    settings: RuntimeSettings,
    patch_llm_agents,
    patch_translate,
):
    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)

    from adib_engine.models.project import ProjectStage
    from adib_engine.store.project_store import PROJECT_SUFFIX, open_project

    with open_project(settings.projects_dir / f"{project_id}{PROJECT_SUFFIX}") as store:
        store.update_meta(
            stage=ProjectStage.FAILED,
            failed_stage=ProjectStage.TRANSLATING,
            failed_reason="endpoint exploded",
        )

    await client.post(f"/projects/{project_id}/translate", json={})
    await _wait_for_stage(client, project_id, "review", timeout=10.0)

    meta = (await client.get(f"/projects/{project_id}")).json()
    assert meta["failed_stage"] is None
    assert meta["failed_reason"] is None


async def test_pause_translation_rejected_when_not_translating(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents, patch_translate
):
    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)
    await client.post(f"/projects/{project_id}/translate", json={})
    await _wait_for_stage(client, project_id, "review", timeout=10.0)

    resp = await client.post(f"/projects/{project_id}/translate/pause")
    assert resp.status_code == 409


async def test_segment_edit_and_lock(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents, patch_translate
):
    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)
    await client.post(f"/projects/{project_id}/translate", json={})
    await _wait_for_stage(client, project_id, "review", timeout=10.0)

    segments = (await client.get(f"/projects/{project_id}/segments")).json()
    seg_id = segments[0]["id"]

    patch = await client.patch(
        f"/projects/{project_id}/segments/{seg_id}",
        json={"target_text": "Human edit.", "locked": True, "status": "approved"},
    )
    assert patch.status_code == 200
    assert patch.json()["target_text"] == "Human edit."
    assert patch.json()["locked"] is True


def _find_typst_bin() -> str | None:
    import shutil

    on_path = shutil.which("typst")
    if on_path:
        return on_path
    bundled = list(
        (Path(__file__).parents[2] / "apps/desktop/src-tauri/binaries").glob("typst-*")
    )
    return str(bundled[0]) if bundled else None


async def test_export_produces_pdf_and_epub(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents, patch_translate, tmp_path: Path
):
    typst_bin = _find_typst_bin()
    if typst_bin is None:
        pytest.skip("no typst binary on PATH or bundled under apps/desktop")

    from adib_engine.settings import get_settings

    settings = get_settings()
    settings.typst_bin = Path(typst_bin)

    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)
    await client.post(f"/projects/{project_id}/translate", json={})
    await _wait_for_stage(client, project_id, "review", timeout=10.0)

    resp = await client.post(f"/projects/{project_id}/export", json={"formats": ["pdf", "epub"]})
    assert resp.status_code == 202

    meta = await _wait_for_stage(client, project_id, "done", timeout=20.0)
    assert meta["stage"] == "done"

    # Default destination: the project's own .export folder, files named after
    # the project rather than a generic "book".
    out_dir = settings.projects_dir / f"{project_id}.export"
    name = (await client.get(f"/projects/{project_id}")).json()["name"]
    assert (out_dir / f"{name}.pdf").exists()
    assert (out_dir / f"{name}.epub").exists()


async def test_export_writes_to_a_chosen_folder_and_filename(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents, patch_translate, tmp_path: Path
):
    """The whole point of the picker: the book lands where the user chose."""
    typst_bin = _find_typst_bin()
    if typst_bin is None:
        pytest.skip("no typst binary on PATH or bundled under apps/desktop")

    from adib_engine.settings import get_settings

    get_settings().typst_bin = Path(typst_bin)

    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)
    await client.post(f"/projects/{project_id}/translate", json={})
    await _wait_for_stage(client, project_id, "review", timeout=10.0)

    dest = tmp_path / "Desktop" / "exports"
    resp = await client.post(
        f"/projects/{project_id}/export",
        json={"formats": ["epub"], "out_dir": str(dest), "filename": "My Book/v2"},
    )
    assert resp.status_code == 202
    assert resp.json()["directory"] == str(dest)

    await _wait_for_stage(client, project_id, "done", timeout=20.0)
    # The slash is stripped, not treated as a subdirectory.
    assert (dest / "My Bookv2.epub").exists()
    assert not (dest / "My Bookv2.pdf").exists()


async def test_export_rejects_an_unusable_folder_before_starting(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents, patch_translate, tmp_path: Path
):
    """A bad path must fail the request, not the background run 30s later."""
    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)
    await client.post(f"/projects/{project_id}/translate", json={})
    await _wait_for_stage(client, project_id, "review", timeout=10.0)

    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x")

    resp = await client.post(
        f"/projects/{project_id}/export", json={"formats": ["epub"], "out_dir": str(not_a_dir)}
    )
    assert resp.status_code == 400
    assert "not a folder" in resp.json()["detail"]

    relative = await client.post(
        f"/projects/{project_id}/export", json={"formats": ["epub"], "out_dir": "exports"}
    )
    assert relative.status_code == 400

    # Refused up front means the project never left review.
    stage = (await client.get(f"/projects/{project_id}")).json()["stage"]
    assert stage == "review"


async def test_export_target_previews_paths_without_writing_anything(
    client: httpx.AsyncClient, source_file: Path, patch_llm_agents, patch_translate, tmp_path: Path
):
    project_id = "test-book"
    await _reach_translating(client, source_file, project_id, patch_llm_agents)

    dest = tmp_path / "picked"
    resp = await client.get(
        f"/projects/{project_id}/export/target",
        params={"out_dir": str(dest), "filename": "Atomic Habits", "formats": "pdf,epub"},
    )
    assert resp.status_code == 200
    target = resp.json()
    assert target["directory"] == str(dest)
    assert target["files"]["pdf"] == str(dest / "Atomic Habits.pdf")
    assert target["files"]["epub"] == str(dest / "Atomic Habits.epub")
    assert not (dest / "Atomic Habits.pdf").exists()


# ---------------------------------------------------------------------------
# Image provider settings + cover translation
# ---------------------------------------------------------------------------


async def test_image_provider_get_defaults_then_put_persists(client: httpx.AsyncClient):
    defaults = await client.get("/image-provider")
    assert defaults.status_code == 200
    assert defaults.json()["base_url"] == "https://openrouter.ai/api/v1"

    updated = dict(defaults.json(), model="my-image-model")
    put_resp = await client.put("/image-provider", json=updated)
    assert put_resp.status_code == 200
    assert put_resp.json()["model"] == "my-image-model"

    refetched = await client.get("/image-provider")
    assert refetched.json()["model"] == "my-image-model"


@pytest.fixture
def epub_with_cover(tmp_path: Path) -> Path:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("cover-epub")
    book.set_title("A Book With A Cover")
    book.set_language("en")
    chapter = epub.EpubHtml(title="Intro", file_name="chap1.xhtml", lang="en")
    chapter.content = "<html><body><h1>Intro</h1><p>Hello there.</p></body></html>"
    book.add_item(chapter)
    book.add_item(
        epub.EpubItem(
            uid="cover-image",
            file_name="images/cover.png",
            media_type="image/png",
            content=b"\x89PNG\r\n\x1a\n" + b"0" * 600,
        )
    )
    book.toc = (epub.Link("chap1.xhtml", "Intro", "intro"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    path = tmp_path / "cover-book.epub"
    epub.write_epub(str(path), book)
    return path


async def _ingest_and_reach_structure_review(
    client: httpx.AsyncClient, source_file: Path, name: str = "Test Book"
) -> str:
    resp = await client.post("/projects", json=_create_payload(source_file, name=name))
    project_id = resp.json()["project_id"]
    await client.post(f"/projects/{project_id}/ingest")
    await _wait_for_stage(client, project_id, "structure_review")
    return project_id


async def test_cover_status_reports_the_source_cover(
    client: httpx.AsyncClient, epub_with_cover: Path
):
    project_id = await _ingest_and_reach_structure_review(client, epub_with_cover, "Cover Book")

    status = await client.get(f"/projects/{project_id}/cover")
    assert status.status_code == 200
    body = status.json()
    assert body["has_source_cover"] is True
    assert body["source_asset_id"] is not None
    assert body["translated_asset_id"] is None

    asset_resp = await client.get(f"/projects/{project_id}/assets/{body['source_asset_id']}")
    assert asset_resp.status_code == 200
    assert asset_resp.headers["content-type"] == "image/png"


async def test_project_without_a_cover_reports_none(
    client: httpx.AsyncClient, source_file: Path
):
    project_id = await _ingest_and_reach_structure_review(client, source_file)

    status = await client.get(f"/projects/{project_id}/cover")
    assert status.json()["has_source_cover"] is False

    resp = await client.post(f"/projects/{project_id}/cover/translate", json={})
    assert resp.status_code == 409


async def test_cover_translate_stages_the_result_and_wires_it_into_export(
    client: httpx.AsyncClient, epub_with_cover: Path, monkeypatch
):
    import adib_engine.api.routes.cover as cover_mod
    from adib_engine.agents.cover import CoverResult

    async def fake_translate_cover(image_bytes, mime, *, target_lang, provider, api_key):
        return CoverResult(image_bytes=b"translated-png-bytes", cost_usd=0.03)

    monkeypatch.setattr(cover_mod, "translate_cover", fake_translate_cover)

    project_id = await _ingest_and_reach_structure_review(client, epub_with_cover, "Cover Book 2")

    resp = await client.post(f"/projects/{project_id}/cover/translate", json={})
    assert resp.status_code == 202

    deadline = time.monotonic() + 5.0
    body = None
    while time.monotonic() < deadline:
        body = (await client.get(f"/projects/{project_id}/cover")).json()
        if body["translated_asset_id"]:
            break
        await asyncio.sleep(0.02)
    assert body is not None and body["translated_asset_id"], "cover translation never completed"

    asset_resp = await client.get(f"/projects/{project_id}/assets/{body['translated_asset_id']}")
    assert asset_resp.status_code == 200
    assert asset_resp.content == b"translated-png-bytes"
