from __future__ import annotations

from sqlalchemy.orm import Session


def get_alert_session() -> Session:
    """Return an alert DB session through the engine's patchable entry point."""
    # Keep tests and older callers that monkeypatch alerts.engine.get_session working.
    from alerts import engine as engine_module

    return engine_module.get_session()
