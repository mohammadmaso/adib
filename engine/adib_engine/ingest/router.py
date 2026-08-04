"""Dispatch a source file to the right importer and report what happened.

Every importer returns the same DocTree, so nothing downstream needs to know
which one ran; `route()` just picks and records the choice in `tree.parser`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from adib_engine.models.document import DocTree

SUPPORTED_EXTENSIONS = {".pdf", ".epub", ".docx", ".html", ".htm", ".md", ".markdown", ".txt"}


class ProbeReport(BaseModel):
    """What the router decided, for the New Project screen to show."""

    format: str
    pages: int | None = None
    has_text_layer: bool | None = None
    escalated: bool = False
    escalation_reason: str = ""
    parser: str = ""


class UnsupportedFormatError(ValueError):
    pass


class ScannedPdfError(ValueError):
    """Raised when a PDF has no usable text layer. OCR is out of scope for v1."""


def route(
    path: Path,
    *,
    assets_dir: Path | None = None,
    force_docling: bool = False,
) -> tuple[DocTree, ProbeReport]:
    """Ingest `path`, choosing a parser by extension and, for PDFs, by probe."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(f"unsupported format: {suffix or '(no extension)'}")

    if suffix == ".epub":
        from adib_engine.ingest.epub import ingest_epub

        tree = ingest_epub(path, assets_dir=assets_dir)
        return tree, ProbeReport(format="epub", parser=tree.parser or "epub")

    if suffix == ".pdf":
        return _route_pdf(path, assets_dir=assets_dir, force_docling=force_docling)

    if suffix in (".md", ".markdown"):
        from adib_engine.ingest.markdown import ingest_markdown

        tree = ingest_markdown(path, assets_dir=assets_dir)
        return tree, ProbeReport(format="markdown", parser=tree.parser or "markdown")

    if suffix in (".html", ".htm"):
        from adib_engine.ingest.html import ingest_html

        tree = ingest_html(path, assets_dir=assets_dir)
        return tree, ProbeReport(format="html", parser=tree.parser or "html")

    if suffix == ".txt":
        from adib_engine.ingest.txt import ingest_txt

        tree = ingest_txt(path)
        return tree, ProbeReport(format="txt", parser=tree.parser or "txt")

    if suffix == ".docx":
        return _route_docling(path, assets_dir=assets_dir, fmt="docx")

    raise UnsupportedFormatError(suffix)


def _route_pdf(
    path: Path, *, assets_dir: Path | None, force_docling: bool
) -> tuple[DocTree, ProbeReport]:
    from adib_engine.ingest.pdf import ingest_pdf, probe_pdf

    probe = probe_pdf(path)
    if probe.likely_scanned:
        raise ScannedPdfError(
            "This PDF has no reliable text layer (looks scanned). "
            "OCR is not supported in this version."
        )

    if force_docling or probe.should_escalate:
        tree, report = _route_docling(path, assets_dir=assets_dir, fmt="pdf")
        report.pages = probe.pages
        report.has_text_layer = probe.has_text_layer
        report.escalated = True
        report.escalation_reason = probe.reason or "forced"
        return tree, report

    tree = ingest_pdf(path, assets_dir=assets_dir)
    report = ProbeReport(
        format="pdf",
        pages=probe.pages,
        has_text_layer=probe.has_text_layer,
        escalated=False,
        parser=tree.parser or "pymupdf",
    )
    return tree, report


def _route_docling(path: Path, *, assets_dir: Path | None, fmt: str) -> tuple[DocTree, ProbeReport]:
    try:
        import docling  # noqa: F401

        from adib_engine.ingest.docling_ingest import ingest_docling
    except ImportError as e:
        raise UnsupportedFormatError(
            f"{fmt} requires the optional 'docling' dependency, which is not installed"
        ) from e

    tree = ingest_docling(path, assets_dir=assets_dir)
    return tree, ProbeReport(format=fmt, parser=tree.parser or "docling")


__all__ = [
    "ProbeReport",
    "ScannedPdfError",
    "SUPPORTED_EXTENSIONS",
    "UnsupportedFormatError",
    "route",
]
