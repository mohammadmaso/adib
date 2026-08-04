"""Importers: external files -> DocTree.

Every parser emits the same DocTree, so nothing downstream knows or cares which
parser ran. `router.route()` picks the parser by extension plus a probe and
returns the tree together with a ProbeReport the UI can show.
"""

from adib_engine.ingest.router import ProbeReport, route

__all__ = ["ProbeReport", "route"]
