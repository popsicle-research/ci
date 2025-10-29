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
        self.calls: list[dict[str, object]] = []

    def report_pending(
        self,
        repo: str,
        commit_sha: str,
        pipeline_id: int,
        *,
        description: str = "",
        target_url: str | None = None,
        context: str | None = None,
    ) -> bool:
        self.calls.append(
            {
                "state": "pending",
                "repo": repo,
                "commit_sha": commit_sha,
                "pipeline_id": pipeline_id,
                "context": context,
            }
        )
        return True

    def report_failure(
        self,
        repo: str,
        commit_sha: str,
        pipeline_id: int,
        *,
        description: str = "",
        target_url: str | None = None,
        context: str | None = None,
    ) -> bool:
        self.calls.append(
            {
                "state": "failure",
                "repo": repo,
                "commit_sha": commit_sha,
                "pipeline_id": pipeline_id,
                "context": context,
            }
        )
        return True

    def report_success(
        self,
        repo: str,
        commit_sha: str,
        pipeline_id: int,
        *,
        description: str = "",
        target_url: str | None = None,
        context: str | None = None,
    ) -> bool:
        self.calls.append(
            {
                "state": "success",
                "repo": repo,
                "commit_sha": commit_sha,
                "pipeline_id": pipeline_id,
                "context": context,
            }
        )
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
    assert body["status"] == "queued"
    assert isinstance(body.get("pipelines"), list)
    assert len(body["pipelines"]) == 1
    pipeline_payload = body["pipelines"][0]
    assert pipeline_payload["pipeline_id"] == 1
    assert pipeline_payload["workflow"] == "ci"
    assert pipeline_payload["config_path"] == ".popsicle/ci.yml"
    assert pipeline_payload["status"] == "queued"

    pipeline = temp_store.get_pipeline(1)
    assert pipeline is not None
    assert pipeline.repo == "example/repo"
    assert pipeline.commit_sha == "1234567890abcdef"
    assert pipeline.branch == "main"
    assert pipeline.workflow_name == "ci"
    assert pipeline.config_path == ".popsicle/ci.yml"

    jobs = temp_store.get_jobs_for_pipeline(1)
    assert [job.job_name for job in jobs] == ["build"]

    assert orchestrator.invocations
    invoked_pipeline_id, workspace_path = orchestrator.invocations[0]
    assert invoked_pipeline_id == 1
    assert workspace_path.name == "pipeline-1"
    assert workspace_path.parent == Path(webhook_app.config["WORKSPACE_ROOT"])

    pending_call = next(call for call in status_reporter.calls if call["state"] == "pending")
    assert pending_call["context"] == "popsicle/ci: ci"
    success_call = next(call for call in status_reporter.calls if call["state"] == "success")
    assert success_call["pipeline_id"] == 1


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
    pipeline_items = body.get("pipelines") or []
    assert len(pipeline_items) == 1
    failure_payload = pipeline_items[0]
    assert failure_payload["pipeline_id"] == 1
    assert failure_payload["status"] == "failed"
    assert failure_payload["workflow"] == "ci"

    pipeline = temp_store.get_pipeline(1)
    assert pipeline is not None
    assert pipeline.status == "failure"
    assert pipeline.workflow_name == "ci"

    assert orchestrator.invocations == []
    failure_call = next(call for call in status_reporter.calls if call["state"] == "failure")
    assert failure_call["pipeline_id"] == 1
    assert failure_call["context"] == "popsicle/ci: ci"


def test_multiple_config_files_create_individual_pipelines(
    tmp_path: Path,
    temp_store: SQLiteStore,
    status_reporter: RecordingStatusReporter,
) -> None:
    template_repo = tmp_path / "multi-template"
    template_repo.mkdir()
    _write_config(template_repo)
    (template_repo / ".popsicle" / "lint.yml").write_text(
        """
        version: 2.1
        jobs:
          lint:
            docker:
              - image: python:3.11
            steps:
              - run: echo lint
        workflows:
          version: 2
          lint_flow:
            jobs:
              - lint
        """,
        encoding="utf-8",
    )

    orchestrator = RecordingOrchestrator(temp_store, status_reporter)

    def fake_clone(_: str, destination: Path, __: str, ___: str) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template_repo, destination, dirs_exist_ok=True)

    app = create_app(
        store=temp_store,
        orchestrator=orchestrator,
        workspace_root=tmp_path / "workspaces",
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
    assert body["status"] == "queued"
    assert len(body["pipelines"]) == 2
    workflows = {item["workflow"] for item in body["pipelines"]}
    assert workflows == {"ci", "lint_flow"}
    config_paths = {item["config_path"] for item in body["pipelines"]}
    assert config_paths == {".popsicle/ci.yml", ".popsicle/lint.yml"}

    pipelines = temp_store.get_recent_pipelines(limit=2)
    assert {p.workflow_name for p in pipelines} == {"ci", "lint_flow"}

    assert len(orchestrator.invocations) == 2
    contexts = {call["context"] for call in status_reporter.calls if call["state"] == "pending"}
    assert contexts == {"popsicle/ci: ci", "popsicle/ci: lint_flow"}
