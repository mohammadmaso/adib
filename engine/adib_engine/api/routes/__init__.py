"""Route modules, one per screen/concern. `app.py` mounts every `router` here."""

from adib_engine.api.routes import events, gate1, gate2, gate3, ingest, presets, projects, provider

__all__ = ["events", "gate1", "gate2", "gate3", "ingest", "presets", "projects", "provider"]
