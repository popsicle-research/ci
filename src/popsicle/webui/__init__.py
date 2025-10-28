"""Web UI blueprint registration helpers."""

from __future__ import annotations

from flask import Flask

from popsicle.storage.sqlite import SQLiteStore

from .routes import ui_bp


def register_ui(app: Flask, store: SQLiteStore) -> None:
    """Register the Web UI blueprint with the given application."""

    app.config.setdefault("POPSICLE_UI_STORE", store)
    app.register_blueprint(ui_bp)


__all__ = ["register_ui", "ui_bp"]
