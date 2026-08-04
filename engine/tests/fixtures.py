"""Hand-built document trees used across the model, store, and render tests."""

from __future__ import annotations

from adib_engine.models.document import (
    Asset,
    AssetRef,
    BookMeta,
    DocNode,
    DocTree,
    NodeKind,
    SourceSpan,
    TableCell,
    TableData,
    make_node_id,
)


def node(kind: NodeKind, text: str | None = None, ordinal: int = 0, **kw) -> DocNode:
    return DocNode(id=make_node_id(kind, text, ordinal), kind=kind, text=text, **kw)


def simple_book() -> DocTree:
    """A small but structurally complete book: headings, prose, table, figure, code."""
    table = TableData(
        rows=[
            [TableCell(text="Layer", is_header=True), TableCell(text="Purpose", is_header=True)],
            [TableCell(text="Transport"), TableCell(text="End-to-end delivery")],
            [TableCell(text="Network"), TableCell(text="Routing between hosts")],
        ],
        caption="Protocol layers",
    )

    return DocTree(
        parser="fixture",
        meta=BookMeta(
            title="A Short Book About Networks",
            authors=["Ada Lovelace"],
            source_lang="en",
        ),
        assets={
            "img1": Asset(id="img1", path="img1.png", mime="image/png", width=800, height=600)
        },
        nodes=[
            node(NodeKind.HEADING, "Introduction", 0, level=1),
            node(
                NodeKind.PARAGRAPH,
                "Networks move bytes between machines that will never meet.",
                1,
                source=SourceSpan(page_start=1, page_end=1),
            ),
            node(NodeKind.HEADING, "Layers", 1, level=2),
            node(
                NodeKind.PARAGRAPH,
                "The stack is layered so each part can change independently.",
                2,
            ),
            DocNode(
                id=make_node_id(NodeKind.TABLE, "layers", 3),
                kind=NodeKind.TABLE,
                table=table,
            ),
            DocNode(
                id=make_node_id(NodeKind.FIGURE, "fig1", 4),
                kind=NodeKind.FIGURE,
                assets=[AssetRef(asset_id="img1", caption="A packet in flight", alt="Diagram")],
            ),
            node(
                NodeKind.CODE,
                "print('hello')",
                5,
                attrs={"language": "python"},
            ),
            node(NodeKind.HEADING, "Conclusion", 2, level=1),
            node(NodeKind.PARAGRAPH, "That is all for now.", 6),
        ],
    )
