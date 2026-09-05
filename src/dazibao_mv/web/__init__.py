"""Web UI (FastAPI) for dazibao-mv."""

from __future__ import annotations

__all__ = ["create_app"]


def create_app():
    from .app import create_app as _create

    return _create()
