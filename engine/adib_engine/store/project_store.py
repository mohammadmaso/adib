"""Repository over one `.adib` project file.

Every pipeline stage reads and writes through this class. Segment writes commit
immediately rather than at the end of a run — that is the whole basis of
resumability, so it is worth the extra transactions.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from adib_engine.models.analysis import BookAnalysis
from adib_engine.models.document import DocTree
from adib_engine.models.glossary import GlossaryTerm, GlossaryTermUpdate, TermPolicy
from adib_engine.models.preset import Preset
from adib_engine.models.project import (
    DocumentRecord,
    ProjectMeta,
    ProjectStage,
    ProjectSummary,
    Run,
    RunStatus,
    UsageEvent,
)
from adib_engine.models.segment import Segment, SegmentStatus, SegmentUpdate
from adib_engine.segmentation import SegmentSpec, build_segments
from adib_engine.store.database import create_project_engine

#: Suffix for project files. The asset directory sits beside it as `<stem>.assets`.
PROJECT_SUFFIX = ".adib"

SOURCE = "source"
TARGET = "target"


def _now() -> datetime:
    return datetime.now(UTC)


class ProjectStore:
    def __init__(self, path: Path, engine: Engine) -> None:
        self.path = path
        self.engine = engine

    # -- lifecycle ------------------------------------------------------------

    @property
    def assets_dir(self) -> Path:
        """Images live beside the database, not inside it.

        Keeping blobs out of SQLite keeps the file small enough to copy and lets
        the renderers hand Typst a real path instead of a temp extraction.
        """
        return self.path.with_suffix(".assets")

    def session(self) -> Session:
        return Session(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> ProjectStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- project metadata -----------------------------------------------------

    def meta(self) -> ProjectMeta:
        with self.session() as s:
            meta = s.get(ProjectMeta, 1)
            if meta is None:
                raise LookupError(f"{self.path} has no project row")
            return meta

    def update_meta(self, **fields) -> ProjectMeta:
        with self.session() as s:
            meta = s.get(ProjectMeta, 1)
            if meta is None:
                raise LookupError(f"{self.path} has no project row")
            for key, value in fields.items():
                setattr(meta, key, value)
            meta.updated_at = _now()
            s.add(meta)
            s.commit()
            s.refresh(meta)
            return meta

    def set_stage(self, stage: ProjectStage) -> ProjectMeta:
        return self.update_meta(stage=stage)

    def preset(self) -> Preset | None:
        raw = self.meta().preset
        return Preset.model_validate(raw) if raw else None

    def set_preset(self, preset: Preset) -> None:
        self.update_meta(preset=preset.model_dump(mode="json"))

    def analysis(self) -> BookAnalysis | None:
        raw = self.meta().analysis
        return BookAnalysis.model_validate(raw) if raw else None

    def set_analysis(self, analysis: BookAnalysis) -> None:
        self.update_meta(analysis=analysis.model_dump(mode="json"))

    # -- document trees -------------------------------------------------------

    def save_tree(self, kind: str, tree: DocTree) -> None:
        with self.session() as s:
            record = s.get(DocumentRecord, kind)
            payload = tree.model_dump(mode="json")
            if record is None:
                record = DocumentRecord(id=kind, tree=payload)
            else:
                record.tree = payload
                record.updated_at = _now()
            s.add(record)
            s.commit()

    def load_tree(self, kind: str = SOURCE) -> DocTree | None:
        with self.session() as s:
            record = s.get(DocumentRecord, kind)
            return DocTree.model_validate(record.tree) if record else None

    # -- segments -------------------------------------------------------------

    def sync_segments(self, tree: DocTree) -> tuple[int, int, int]:
        """Reconcile stored segments with the current tree.

        Returns (added, kept, removed). Because ids are content-addressed,
        "kept" segments retain their translations — this is what makes Gate 1
        edits and re-ingests cheap instead of catastrophic.
        """
        specs: list[SegmentSpec] = build_segments(tree)
        desired = {spec.id: spec for spec in specs}

        with self.session() as s:
            existing = {seg.id: seg for seg in s.exec(select(Segment)).all()}

            added = kept = 0
            for spec in specs:
                current = existing.get(spec.id)
                if current is None:
                    s.add(
                        Segment(
                            id=spec.id,
                            node_id=spec.node_id,
                            ordinal=spec.ordinal,
                            kind=spec.kind,
                            source_text=spec.source_text,
                            heading_path=spec.heading_path,
                        )
                    )
                    added += 1
                else:
                    # Position and context can shift without invalidating a
                    # translation; the text itself cannot (a changed text yields
                    # a different id and lands in the `added` branch).
                    current.ordinal = spec.ordinal
                    current.heading_path = spec.heading_path
                    s.add(current)
                    kept += 1

            removed = 0
            for seg_id, seg in existing.items():
                if seg_id not in desired:
                    s.delete(seg)
                    removed += 1

            s.commit()

        return added, kept, removed

    def segments(
        self,
        *,
        status: SegmentStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Segment]:
        with self.session() as s:
            stmt = select(Segment).order_by(Segment.ordinal)
            if status is not None:
                stmt = stmt.where(Segment.status == status)
            if offset:
                stmt = stmt.offset(offset)
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(s.exec(stmt).all())

    def pending_segments(self) -> list[Segment]:
        """Segments a translation run still has to do.

        Locked segments are excluded even when unfinished: a human edit outranks
        anything the pipeline wants to write.
        """
        with self.session() as s:
            stmt = (
                select(Segment)
                .where(Segment.locked == False)  # noqa: E712 - SQL, not Python truthiness
                .where(Segment.status.in_([SegmentStatus.PENDING, SegmentStatus.FAILED]))
                .order_by(Segment.ordinal)
            )
            return list(s.exec(stmt).all())

    def get_segment(self, segment_id: str) -> Segment | None:
        with self.session() as s:
            return s.get(Segment, segment_id)

    def update_segment(self, segment_id: str, update: SegmentUpdate) -> Segment:
        with self.session() as s:
            seg = s.get(Segment, segment_id)
            if seg is None:
                raise LookupError(f"no segment {segment_id}")
            for key, value in update.model_dump(exclude_unset=True).items():
                setattr(seg, key, value)
            seg.updated_at = _now()
            s.add(seg)
            s.commit()
            s.refresh(seg)
            return seg

    def record_translation(
        self,
        segment_id: str,
        *,
        target_text: str,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        status: SegmentStatus = SegmentStatus.TRANSLATED,
        qa_flags: list[dict] | None = None,
        run_id: str | None = None,
    ) -> Segment:
        """Commit one finished translation. Called per segment, never batched.

        Also writes the matching usage event, so the cost meter and the spend cap
        stay in agreement with what the segments actually cost — the two must
        never be able to drift apart.

        `qa_flags`, when non-empty, forces the status to `FLAGGED` regardless of
        `status` — the deterministic QA pass overrides the caller's default so a
        segment that tripped a rule can never silently land as plain `TRANSLATED`.
        """
        with self.session() as s:
            seg = s.get(Segment, segment_id)
            if seg is None:
                raise LookupError(f"no segment {segment_id}")
            if seg.locked:
                # A human edited this while the run was in flight. Their text wins.
                return seg
            seg.target_text = target_text
            seg.status = SegmentStatus.FLAGGED if qa_flags else status
            seg.qa_flags = qa_flags or []
            seg.model_name = model_name
            seg.prompt_tokens = prompt_tokens
            seg.completion_tokens = completion_tokens
            seg.cost_usd = cost_usd
            seg.attempts += 1
            seg.error = None
            seg.updated_at = _now()
            s.add(seg)

            if prompt_tokens or completion_tokens or cost_usd:
                s.add(
                    UsageEvent(
                        id=f"use-{uuid.uuid4().hex[:12]}",
                        run_id=run_id,
                        segment_id=segment_id,
                        purpose="translation",
                        model=model_name,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cost_usd=cost_usd,
                    )
                )

            # One transaction for both: a crash between them would desync spend.
            s.commit()
            s.refresh(seg)
            return seg

    def record_failure(self, segment_id: str, error: str) -> Segment:
        with self.session() as s:
            seg = s.get(Segment, segment_id)
            if seg is None:
                raise LookupError(f"no segment {segment_id}")
            seg.status = SegmentStatus.FAILED
            seg.error = error
            seg.attempts += 1
            seg.updated_at = _now()
            s.add(seg)
            s.commit()
            s.refresh(seg)
            return seg

    def translations(self) -> dict[str, str]:
        """Segment id -> translated text, for feeding `apply_translations`."""
        with self.session() as s:
            rows = s.exec(select(Segment.id, Segment.target_text)).all()
            return {sid: text for sid, text in rows if text}

    def segment_counts(self) -> dict[str, int]:
        with self.session() as s:
            rows = s.exec(
                select(Segment.status, func.count()).group_by(Segment.status)  # type: ignore[arg-type]
            ).all()
            counts = {str(status): int(n) for status, n in rows}
            counts["total"] = sum(counts.values())
            return counts

    # -- glossary -------------------------------------------------------------

    def upsert_terms(self, terms: Iterable[GlossaryTerm]) -> tuple[int, int, int]:
        """Insert or update glossary terms, keyed by source form.

        Returns (added, updated, skipped_locked). Locked terms are never
        overwritten, so re-running the glossary agent cannot undo a user's
        careful choice of rendering for a key term.
        """
        added = updated = skipped = 0
        with self.session() as s:
            for term in terms:
                existing = s.exec(
                    select(GlossaryTerm).where(GlossaryTerm.source == term.source)
                ).first()

                if existing is None:
                    if not term.id:
                        term.id = f"term-{uuid.uuid4().hex[:12]}"
                    s.add(term)
                    added += 1
                    continue

                if existing.locked:
                    skipped += 1
                    continue

                for field in ("target", "policy", "explanation", "part_of_speech", "origin"):
                    value = getattr(term, field)
                    if value is not None:
                        setattr(existing, field, value)
                # Frequency and first sighting come from the miner, which sees
                # the whole book; always take the fresh numbers.
                existing.frequency = term.frequency
                existing.first_seen_node = term.first_seen_node
                existing.updated_at = _now()
                s.add(existing)
                updated += 1

            s.commit()
        return added, updated, skipped

    def terms(self, *, enabled_only: bool = False) -> list[GlossaryTerm]:
        with self.session() as s:
            stmt = select(GlossaryTerm).order_by(GlossaryTerm.frequency.desc())  # type: ignore[attr-defined]
            if enabled_only:
                stmt = stmt.where(GlossaryTerm.enabled == True)  # noqa: E712
            return list(s.exec(stmt).all())

    def protected_terms(self) -> list[GlossaryTerm]:
        """Terms whose source form must survive translation untouched."""
        return [
            t
            for t in self.terms(enabled_only=True)
            if t.policy in (TermPolicy.KEEP, TermPolicy.TRANSLATE_PAREN)
        ]

    def update_term(self, term_id: str, update: GlossaryTermUpdate) -> GlossaryTerm:
        with self.session() as s:
            term = s.get(GlossaryTerm, term_id)
            if term is None:
                raise LookupError(f"no glossary term {term_id}")
            for key, value in update.model_dump(exclude_unset=True).items():
                setattr(term, key, value)
            # Any manual edit pins the term against future agent runs.
            term.locked = True
            term.updated_at = _now()
            s.add(term)
            s.commit()
            s.refresh(term)
            return term

    # -- runs and usage -------------------------------------------------------

    def start_run(self, stage: ProjectStage) -> Run:
        run = Run(id=f"run-{uuid.uuid4().hex[:12]}", stage=stage)
        with self.session() as s:
            s.add(run)
            s.commit()
            s.refresh(run)
            return run

    def finish_run(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error: str | None = None,
        stats: dict | None = None,
    ) -> Run:
        with self.session() as s:
            run = s.get(Run, run_id)
            if run is None:
                raise LookupError(f"no run {run_id}")
            run.status = status
            run.error = error
            run.finished_at = _now()
            if stats is not None:
                run.stats = stats
            s.add(run)
            s.commit()
            s.refresh(run)
            return run

    def runs(self) -> list[Run]:
        with self.session() as s:
            return list(s.exec(select(Run).order_by(Run.started_at.desc())).all())  # type: ignore[attr-defined]

    def record_usage(
        self,
        *,
        purpose: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        run_id: str | None = None,
        segment_id: str | None = None,
    ) -> UsageEvent:
        event = UsageEvent(
            id=f"use-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            segment_id=segment_id,
            purpose=purpose,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )
        with self.session() as s:
            s.add(event)
            s.commit()
            s.refresh(event)
            return event

    def total_cost(self) -> float:
        with self.session() as s:
            return float(s.exec(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0))).one())

    def total_tokens(self) -> tuple[int, int]:
        with self.session() as s:
            row = s.exec(
                select(
                    func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
                    func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
                )
            ).one()
            return int(row[0]), int(row[1])

    # -- summary --------------------------------------------------------------

    def summary(self) -> ProjectSummary:
        meta = self.meta()
        counts = self.segment_counts()
        done = counts.get(SegmentStatus.TRANSLATED, 0) + counts.get(SegmentStatus.APPROVED, 0)
        return ProjectSummary(
            path=str(self.path),
            name=meta.name,
            source_lang=meta.source_lang,
            target_lang=meta.target_lang,
            stage=meta.stage,
            failed_stage=meta.failed_stage,
            segments_total=counts.get("total", 0),
            segments_done=done,
            cost_usd=self.total_cost(),
            updated_at=meta.updated_at,
        )


def open_project(path: Path) -> ProjectStore:
    """Open an existing project file. Raises if it is not there."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    return ProjectStore(path, create_project_engine(path))


def create_project(
    path: Path,
    *,
    name: str,
    source_path: str,
    target_lang: str = "fa",
) -> ProjectStore:
    """Create a new project file. Refuses to clobber an existing one."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)

    store = ProjectStore(path, create_project_engine(path))
    store.assets_dir.mkdir(parents=True, exist_ok=True)
    with store.session() as s:
        s.add(
            ProjectMeta(
                id=1,
                name=name,
                source_path=source_path,
                target_lang=target_lang,
            )
        )
        s.commit()
    return store
