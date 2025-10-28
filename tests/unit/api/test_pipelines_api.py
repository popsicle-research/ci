from __future__ import annotations

from datetime import datetime
from typing import Generator

import pytest
from flask import Flask

from popsicle.webhook.app import create_app
from popsicle.storage.sqlite import SQLiteStore


@pytest.fixture()
def app_with_store(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[tuple[Flask, SQLiteStore], None, None]:
    db_root = tmp_path_factory.mktemp("db")
    db_path = db_root / "popsicle.db"
    store = SQLiteStore(db_path)
    app = create_app(store=store)
    app.config.update({"TESTING": True})
    yield app, store


def _seed_pipeline(store: SQLiteStore) -> dict[str, int]:
    pipeline_id = store.create_pipeline(
        repo="example/repo",
        commit_sha="abc123",
        branch="main",
        start_time=datetime.utcnow().isoformat() + "Z",
    )
    job_id = store.create_job(pipeline_id, "build")
    store.update_job_status(job_id, "success", end_time="2024-01-01T00:00:00Z")
    store.set_job_log(job_id, "Line 1\nLine 2\n")
    store.update_pipeline_status(
        pipeline_id, "success", end_time="2024-01-01T00:10:00Z"
    )
    return {"pipeline_id": pipeline_id, "job_id": job_id}


def test_list_pipelines_returns_recent_runs(app_with_store: tuple) -> None:
    app, store = app_with_store
    ids = _seed_pipeline(store)
    client = app.test_client()

    response = client.get("/pipelines")

    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert any(item["id"] == ids["pipeline_id"] for item in data)


def test_get_pipeline_details_includes_jobs(app_with_store: tuple) -> None:
    app, store = app_with_store
    ids = _seed_pipeline(store)
    client = app.test_client()

    response = client.get(f"/pipelines/{ids['pipeline_id']}")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["id"] == ids["pipeline_id"]
    assert payload["jobs"][0]["id"] == ids["job_id"]
    assert "log" not in payload["jobs"][0]


def test_get_pipeline_details_missing_returns_404(app_with_store: tuple) -> None:
    app, _ = app_with_store
    client = app.test_client()

    response = client.get("/pipelines/9999")

    assert response.status_code == 404
    assert response.get_json()["error"] == "pipeline not found"


def test_get_job_log_returns_plain_text(app_with_store: tuple) -> None:
    app, store = app_with_store
    ids = _seed_pipeline(store)
    client = app.test_client()

    response = client.get(f"/pipelines/{ids['pipeline_id']}/jobs/{ids['job_id']}/log")

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.get_data(as_text=True) == "Line 1\nLine 2\n"


def test_get_job_log_invalid_pair_returns_404(app_with_store: tuple) -> None:
    app, store = app_with_store
    ids = _seed_pipeline(store)
    other_pipeline = store.create_pipeline(
        repo="example/repo",
        commit_sha="def456",
        branch="feature",
    )
    client = app.test_client()

    response = client.get(f"/pipelines/{other_pipeline}/jobs/{ids['job_id']}/log")

    assert response.status_code == 404
    assert response.get_data(as_text=True) == "job not found"


def test_list_runners_returns_configured_hosts(app_with_store: tuple) -> None:
    app, store = app_with_store
    store.add_runner("localhost")
    client = app.test_client()

    response = client.get("/runners")

    assert response.status_code == 200
    runners = response.get_json()
    assert isinstance(runners, list)
    assert runners[0]["host"] == "localhost"


def test_create_runner_adds_entry(app_with_store: tuple) -> None:
    app, _ = app_with_store
    client = app.test_client()

    response = client.post("/runners", json={"host": "runner.example"})

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["host"] == "runner.example"
    assert payload["active"] is True


def test_create_runner_requires_host(app_with_store: tuple) -> None:
    app, _ = app_with_store
    client = app.test_client()

    response = client.post("/runners", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "host is required"
