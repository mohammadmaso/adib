"""Discovery of project files for the Library screen.

There is deliberately no central index database: projects are self-describing
files, so the registry just scans the directory. That keeps a project portable
(copy the `.adib` and its `.assets` folder and it works elsewhere) and means a
central index can never drift out of sync with what is on disk.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from adib_engine.models.project import ProjectStage, ProjectSummary
from adib_engine.store.project_store import PROJECT_SUFFIX, open_project

log = logging.getLogger(__name__)


class ProjectRegistry:
    def __init__(self, projects_dir: Path) -> None:
        self.projects_dir = Path(projects_dir)

    def paths(self) -> list[Path]:
        if not self.projects_dir.exists():
            return []
        return sorted(self.projects_dir.glob(f"*{PROJECT_SUFFIX}"))

    def list(self) -> list[ProjectSummary]:
        """Summaries for every project, newest first.

        A corrupt or half-written file yields a summary carrying `error` rather
        than raising: one bad project must not make the Library unopenable.
        """
        summaries: list[ProjectSummary] = []

        for path in self.paths():
            try:
                with open_project(path) as store:
                    summaries.append(store.summary())
            except Exception as exc:
                log.warning("could not read project %s: %s", path, exc)
                summaries.append(
                    ProjectSummary(
                        path=str(path),
                        name=path.stem,
                        source_lang=None,
                        target_lang="",
                        stage=ProjectStage.FAILED,
                        updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                        error=str(exc),
                    )
                )

        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    def path_for(self, name: str) -> Path:
        """A collision-free path for a new project named `name`."""
        stem = _slugify(name) or "book"
        candidate = self.projects_dir / f"{stem}{PROJECT_SUFFIX}"
        n = 2
        while candidate.exists():
            candidate = self.projects_dir / f"{stem}-{n}{PROJECT_SUFFIX}"
            n += 1
        return candidate


def _slugify(name: str) -> str:
    """Filesystem-safe stem that still keeps non-Latin titles readable.

    Only characters that are actually unsafe in a path are replaced, so a
    Persian or Arabic book title survives instead of collapsing to "book".
    """
    unsafe = set('/\\:*?"<>|\0')
    cleaned = "".join("-" if ch in unsafe or ch.isspace() else ch for ch in name.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-.")[:80]
