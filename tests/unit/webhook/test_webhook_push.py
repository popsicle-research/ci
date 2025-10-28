"""Tests for the GitHub webhook push handler."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

import pytest
from flask import Flask

from popsicle.orchestrator import PipelineOrchestrator
from popsicle.runner import RunnerResult
from popsicle.storage.sqlite import SQLiteStore
from popsicle.webhook.app import create_app


class RecordingOrchestrator(PipelineOrchestrator):
    """Test orchestrator that records invocations instead of running jobs."""

    def __init__(self, store: SQLiteStore, status_reporter) -> None:
        super().__init__(store, status_reporter=status_reporter)
        self._runner = _SuccessfulRunner()
        self.invocations: list[tuple[int, Path]] = []

    def run_pipeline(self, pipeline_id: int, config, workspace_path: Path) -> None:  # type: ignore[override]
        super().run_pipeline(pipeline_id, config, workspace_path)
        self.invocations.append((pipeline_id, workspace_path))


class RecordingStatusReporter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int]] = []

    def report_pending(
        self, repo: str, commit_sha: str, pipeline_id: int, *, description: str = "", target_url: str | None = None
    ) -> bool:
        self.calls.append(("pending", repo, commit_sha, pipeline_id))
        return True

    def report_failure(
        self, repo: str, commit_sha: str, pipeline_id: int, *, description: str = "", target_url: str | None = None
    ) -> bool:
        self.calls.append(("failure", repo, commit_sha, pipeline_id))
        return True

    def report_success(
        self, repo: str, commit_sha: str, pipeline_id: int, *, description: str = "", target_url: str | None = None
    ) -> bool:
        self.calls.append(("success", repo, commit_sha, pipeline_id))
        return True


class _SuccessfulRunner:
    def run(self, job, workspace_path):
        return RunnerResult(success=True, output="ok", return_code=0)


def _build_payload(repo_name: str = "example/repo") -> dict[str, object]:
    return {
        "after": "1234567890abcdef",
        "ref": "refs/heads/main",
        "repository": {
            "full_name": repo_name,
            "clone_url": "https://github.com/example/repo.git",
        },
    }


def _write_config(repo_path: Path) -> None:
    config_dir = repo_path / ".popsicle"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "ci.yml").write_text(
        """
        version: 2.1
        jobs:
          build:
            docker:
              - image: python:3.11
            steps:
              - checkout
              - run: echo "hello"
        """,
        encoding="utf-8",
    )


@pytest.fixture()
def temp_store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "test.db")


@pytest.fixture()
def status_reporter() -> RecordingStatusReporter:
    return RecordingStatusReporter()


@pytest.fixture()
def orchestrator(
    temp_store: SQLiteStore, status_reporter: RecordingStatusReporter
) -> RecordingOrchestrator:
    return RecordingOrchestrator(temp_store, status_reporter)


@pytest.fixture()
def webhook_app(
    tmp_path: Path,
    temp_store: SQLiteStore,
    orchestrator: RecordingOrchestrator,
    status_reporter: RecordingStatusReporter,
) -> Flask:
    template_repo = tmp_path / "template"
    template_repo.mkdir()
    _write_config(template_repo)

    def fake_clone(_: str, destination: Path, __: str, ___: str) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template_repo, destination, dirs_exist_ok=True)

    def run_in_place(callback: Callable[[], None]) -> None:
        callback()

    return create_app(
        store=temp_store,
        orchestrator=orchestrator,
        workspace_root=tmp_path / "workspaces",
        git_clone=fake_clone,
        background_runner=run_in_place,
        status_reporter=status_reporter,
    )


def test_push_event_creates_pipeline_and_jobs(
    webhook_app: Flask,
    temp_store: SQLiteStore,
    orchestrator: RecordingOrchestrator,
    status_reporter: RecordingStatusReporter,
) -> None:
    client = webhook_app.test_client()
    payload = _build_payload()

    response = client.post(
        "/webhook",
        data=json.dumps(payload),
        content_type="application/json",
        headers={"X-GitHub-Event": "push"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body == {"status": "queued", "pipeline_id": 1}

    pipeline = temp_store.get_pipeline(1)
    assert pipeline is not None
    assert pipeline.repo == "example/repo"
    assert pipeline.commit_sha == "1234567890abcdef"
    assert pipeline.branch == "main"

    jobs = temp_store.get_jobs_for_pipeline(1)
    assert [job.job_name for job in jobs] == ["build"]

    assert orchestrator.invocations
    invoked_pipeline_id, workspace_path = orchestrator.invocations[0]
    assert invoked_pipeline_id == 1
    assert workspace_path.name == "pipeline-1"
    assert workspace_path.parent == Path(webhook_app.config["WORKSPACE_ROOT"])

    assert ("pending", "example/repo", "1234567890abcdef", 1) in status_reporter.calls
    assert ("success", "example/repo", "1234567890abcdef", 1) in status_reporter.calls


def test_missing_config_marks_pipeline_failed(
    tmp_path: Path,
    temp_store: SQLiteStore,
    orchestrator: RecordingOrchestrator,
    status_reporter: RecordingStatusReporter,
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    def fake_clone(_: str, destination: Path, __: str, ___: str) -> None:
        destination.mkdir(parents=True, exist_ok=True)

    app = create_app(
        store=temp_store,
        orchestrator=orchestrator,
        workspace_root=workspace_root,
        git_clone=fake_clone,
        background_runner=lambda fn: fn(),
        status_reporter=status_reporter,
    )

    client = app.test_client()

    response = client.post(
        "/webhook",
        data=json.dumps(_build_payload()),
        content_type="application/json",
        headers={"X-GitHub-Event": "push"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "failed"
    assert body["pipeline_id"] == 1

    pipeline = temp_store.get_pipeline(1)
    assert pipeline is not None
    assert pipeline.status == "failure"

    assert orchestrator.invocations == []
    assert ("failure", "example/repo", "1234567890abcdef", 1) in status_reporter.calls
