"""Flask application entrypoint for the POPSICLE CI/CD webhook service."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, request

from popsicle.common.git import GitCloneError, clone_repository
from popsicle.github import GitHubStatusReporter
from popsicle.orchestrator import PipelineOrchestrator
from popsicle.pipelines.config_parser import (
    PipelineConfig,
    PipelineConfigError,
    load_pipeline_config,
)
from popsicle.api.routes import register_api_routes
from popsicle.storage.sqlite import SQLiteStore
from popsicle.webui import register_ui

LOGGER = logging.getLogger(__name__)

BackgroundRunner = Callable[[Callable[[], None]], None]


def create_app(
    *,
    store: SQLiteStore | None = None,
    orchestrator: PipelineOrchestrator | None = None,
    status_reporter: GitHubStatusReporter | None = None,
    workspace_root: Path | str | None = None,
    git_clone: Callable[[str, Path, str, str], None] | None = None,
    config_loader: Callable[[Path], PipelineConfig] | None = None,
    background_runner: BackgroundRunner | None = None,
) -> Flask:
    """Create and configure the Flask application."""

    app = Flask(__name__)
    db_store = store or SQLiteStore()
    reporter = status_reporter or GitHubStatusReporter()
    pipeline_orchestrator = orchestrator or PipelineOrchestrator(
        db_store, status_reporter=reporter
    )
    if orchestrator is not None:
        pipeline_orchestrator.status_reporter = reporter
    config_loader = config_loader or load_pipeline_config

    workspace_base = Path(
        workspace_root or os.getenv("POPSICLE_WORKSPACE_ROOT", "workspaces")
    ).resolve()
    workspace_base.mkdir(parents=True, exist_ok=True)
    app.config.setdefault("WORKSPACE_ROOT", workspace_base)

    token = os.getenv("POPSICLE_GITHUB_TOKEN")

    def perform_clone(
        repo_url: str, destination: Path, commit: str, branch: str
    ) -> None:
        clone_repository(
            repo_url,
            destination,
            commit,
            branch=branch,
            token=token,
        )

    clone_fn = git_clone or perform_clone
    runner = background_runner or _spawn_thread

    register_api_routes(app, db_store)
    register_ui(app, db_store)

    @app.get("/health")
    def health_check() -> tuple[dict[str, str], int]:
        """Health check endpoint used for readiness probes."""
        return jsonify({"status": "ok"}), 200

    @app.post("/webhook")
    def handle_webhook() -> tuple[Any, int]:
        """Handle GitHub webhook events."""

        event = request.headers.get("X-GitHub-Event", "")
        if event != "push":
            LOGGER.info("Ignoring unsupported GitHub event: %s", event)
            return jsonify({"status": "ignored", "reason": "unsupported event"}), 202

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "invalid JSON payload"}), 400

        repository = payload.get("repository")
        if not isinstance(repository, dict):
            return jsonify({"error": "missing repository details"}), 400

        repo_full_name = repository.get("full_name")
        clone_url = repository.get("clone_url")
        commit_sha = payload.get("after")
        ref = payload.get("ref")

        if not all(
            isinstance(value, str)
            for value in (repo_full_name, clone_url, commit_sha, ref)
        ):
            return jsonify({"error": "push payload missing required fields"}), 400

        branch = _extract_branch(ref)
        pipeline_id = db_store.create_pipeline(
            repo=repo_full_name,
            commit_sha=commit_sha,
            branch=branch,
        )
        workspace_path = workspace_base / f"pipeline-{pipeline_id}"

        LOGGER.info(
            "Received push for %s@%s (pipeline %s)",
            repo_full_name,
            commit_sha,
            pipeline_id,
        )

        try:
            clone_fn(clone_url, workspace_path, commit_sha, branch)
            config = config_loader(workspace_path)
            for job in config.jobs.values():
                db_store.create_job(pipeline_id, job.name)
            reporter.report_pending(
                repo_full_name,
                commit_sha,
                pipeline_id,
            )
        except GitCloneError as exc:
            LOGGER.exception("Failed to clone repository for pipeline %s", pipeline_id)
            db_store.update_pipeline_status(pipeline_id, "failure")
            reporter.report_failure(
                repo_full_name,
                commit_sha,
                pipeline_id,
                description="Repository clone failed",
            )
            return (
                jsonify(
                    {
                        "status": "failed",
                        "pipeline_id": pipeline_id,
                        "error": str(exc),
                    }
                ),
                200,
            )
        except PipelineConfigError as exc:
            LOGGER.exception(
                "Pipeline configuration error for pipeline %s", pipeline_id
            )
            db_store.update_pipeline_status(pipeline_id, "failure")
            reporter.report_failure(
                repo_full_name,
                commit_sha,
                pipeline_id,
                description="Pipeline configuration invalid",
            )
            return (
                jsonify(
                    {
                        "status": "failed",
                        "pipeline_id": pipeline_id,
                        "error": str(exc),
                    }
                ),
                200,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error preparing pipeline %s", pipeline_id)
            db_store.update_pipeline_status(pipeline_id, "failure")
            reporter.report_failure(
                repo_full_name,
                commit_sha,
                pipeline_id,
                description="Unexpected pipeline setup error",
            )
            return (
                jsonify(
                    {
                        "status": "failed",
                        "pipeline_id": pipeline_id,
                        "error": "unexpected error",
                    }
                ),
                200,
            )

        def invoke_pipeline() -> None:
            pipeline_orchestrator.run_pipeline(pipeline_id, config, workspace_path)

        runner(invoke_pipeline)

        return jsonify({"status": "queued", "pipeline_id": pipeline_id}), 200

    return app


def _extract_branch(ref: str) -> str:
    if ref.startswith("refs/heads/"):
        return ref.split("/", 2)[-1]
    return ref


def _spawn_thread(target: Callable[[], None]) -> None:
    import threading

    thread = threading.Thread(target=target, daemon=True)
    thread.start()


app = create_app()
