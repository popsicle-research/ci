"""Unit tests for the webhook health endpoint."""

from __future__ import annotations

from flask import Flask

from popsicle.webhook.app import create_app


def test_health_endpoint_returns_ok() -> None:
    """The health endpoint should respond with a 200 and status payload."""
    app: Flask = create_app()
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
