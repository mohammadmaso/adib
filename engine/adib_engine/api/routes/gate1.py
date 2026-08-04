"""Gate 1 — Structure review: the extracted tree, editable before analysis.

The editor works on the whole tree rather than per-node patches: a v1 tree
edit (re-level headings, merge/split, mark front/back matter, delete junk) is
infrequent and the tree is small enough in memory that "load, edit client-side,
PUT the whole thing back" is simpler and harder to get subtly wrong than a
patch-op protocol. Re-syncing segments after a save is what makes this cheap:
content-addressed ids mean untouched nodes keep their translations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from adib_engine.api.deps import project_store
from adib_engine.models.document import DocTree
from adib_engine.models.project import ProjectStage
from adib_engine.store.project_store import SOURCE, ProjectStore

router = APIRouter(prefix="/projects", tags=["gate1"])


@router.get("/{project_id}/tree", response_model=DocTree)
def get_tree(kind: str = SOURCE, store: ProjectStore = Depends(project_store)) -> DocTree:
    tree = store.load_tree(kind)
    if tree is None:
        raise HTTPException(status_code=404, detail=f"no '{kind}' tree yet — ingest first")
    return tree


class TreeSyncResult(DocTree):
    """Echoes the saved tree plus the segment reconciliation counts."""

    segments_added: int = 0
    segments_kept: int = 0
    segments_removed: int = 0


@router.put("/{project_id}/tree", response_model=TreeSyncResult)
def put_tree(tree: DocTree, store: ProjectStore = Depends(project_store)) -> TreeSyncResult:
    """Save an edited source tree and reconcile segments against it.

    Content-addressed segment ids mean an unedited paragraph keeps its
    translation even if headings around it were re-leveled or deleted.
    """
    store.save_tree(SOURCE, tree)
    added, kept, removed = store.sync_segments(tree)
    return TreeSyncResult(
        **tree.model_dump(), segments_added=added, segments_kept=kept, segments_removed=removed
    )


@router.post("/{project_id}/tree/approve", status_code=204)
def approve_structure(store: ProjectStore = Depends(project_store)) -> None:
    """Advance past Gate 1. The tree is whatever was last PUT (or ingested)."""
    meta = store.meta()
    if meta.stage not in (ProjectStage.STRUCTURE_REVIEW,):
        raise HTTPException(
            status_code=409, detail=f"cannot approve structure from stage '{meta.stage.value}'"
        )
    store.set_stage(ProjectStage.ANALYZING)
