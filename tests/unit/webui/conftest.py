from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from popsicle.storage.sqlite import SQLiteStore
from popsicle.webui import register_ui


@pytest.fixture
def app_and_store(tmp_path: Path) -> tuple[Flask, SQLiteStore]:
    db_path = tmp_path / "test.db"
    store = SQLiteStore(db_path)
    app = Flask(__name__)
    app.config["TESTING"] = True
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    app.config["WORKSPACE_ROOT"] = workspace_root

    class StubOrchestrator:
        def __init__(self) -> None:
            self.calls: list[tuple[int, Any, Path]] = []

        def run_pipeline(self, pipeline_id: int, config: Any, workspace: Path) -> None:
            self.calls.append((pipeline_id, config, workspace))

    class StubReporter:
        def __init__(self) -> None:
            self.pending: list[tuple[str, str, int, str | None]] = []
            self.failure: list[tuple[str, str, int, str | None]] = []

        def report_pending(
            self,
            repo: str,
            commit_sha: str,
            pipeline_id: int,
            *,
            description: str | None = None,
            context: str | None = None,
        ) -> bool:
            self.pending.append((repo, commit_sha, pipeline_id, context))
            return True

        def report_failure(
            self,
            repo: str,
            commit_sha: str,
            pipeline_id: int,
            *,
            description: str | None = None,
            context: str | None = None,
        ) -> bool:
            self.failure.append((repo, commit_sha, pipeline_id, context))
            return True

    clone_calls: list[tuple[str, str, str, str]] = []
    app.config["TEST_CLONE_CALLS"] = clone_calls

    def stub_clone(repo_url: str, destination: Path, commit: str, branch: str) -> None:
        clone_calls.append((repo_url, str(destination), commit, branch))
        (destination / ".popsicle").mkdir(parents=True, exist_ok=True)
        (destination / ".popsicle" / "ci.yml").write_text(
            """
version: 2.1
jobs:
  build:
    docker:
      - image: python:3.11
    steps:
      - checkout
      - run: echo "hello"
workflows:
  version: 2
  rerun_flow:
    jobs:
      - build
            """.strip(),
            encoding="utf-8",
        )

    orchestrator = StubOrchestrator()
    reporter = StubReporter()
    app.config["POPSICLE_ORCHESTRATOR"] = orchestrator
    app.config["POPSICLE_BACKGROUND_RUNNER"] = lambda func: func()
    app.config["POPSICLE_CLONE_FN"] = stub_clone
    app.config["POPSICLE_STATUS_REPORTER"] = reporter
    app.config["TEST_ORCHESTRATOR"] = orchestrator
    app.config["TEST_REPORTER"] = reporter

    register_ui(app, store)
    return app, store
