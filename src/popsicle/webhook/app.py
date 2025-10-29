"""Flask application entrypoint for the POPSICLE CI/CD webhook service."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, request

from popsicle.common.git import GitCloneError, clone_repository
from popsicle.github import GitHubStatusReporter
from popsicle.orchestrator import PipelineOrchestrator
from popsicle.pipelines.config_parser import (
    CONFIG_RELATIVE_PATH,
    PipelineConfig,
    PipelineConfigError,
    list_pipeline_config_paths,
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
    config_loader: Callable[[Path, Path | None], PipelineConfig] | None = None,
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
        LOGGER.info(
            "Received push for %s@%s (branch %s)",
            repo_full_name,
            commit_sha,
            branch,
        )

        def build_status_context(workflow_name: str) -> str:
            return f"popsicle/ci: {workflow_name}"

        pipeline_results: list[dict[str, object]] = []
        orchestrations: list[tuple[int, PipelineConfig, Path]] = []
        temp_workspace: Path | None = None

        try:
            temp_workspace = Path(
                tempfile.mkdtemp(prefix="pipeline-source-", dir=workspace_base)
            )
            clone_fn(clone_url, temp_workspace, commit_sha, branch)

            try:
                config_paths = list_pipeline_config_paths(temp_workspace)
            except PipelineConfigError as exc:
                workflow_name = CONFIG_RELATIVE_PATH.stem
                pipeline_id = db_store.create_pipeline(
                    repo=repo_full_name,
                    commit_sha=commit_sha,
                    branch=branch,
                    workflow_name=workflow_name,
                    config_path=str(CONFIG_RELATIVE_PATH),
                )
                db_store.update_pipeline_status(pipeline_id, "failure")
                reporter.report_failure(
                    repo_full_name,
                    commit_sha,
                    pipeline_id,
                    description="Pipeline configuration invalid",
                    context=build_status_context(workflow_name),
                )
                pipeline_results.append(
                    {
                        "pipeline_id": pipeline_id,
                        "workflow": workflow_name,
                        "config_path": str(CONFIG_RELATIVE_PATH),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                return jsonify(
                    {"status": "failed", "pipelines": pipeline_results}
                ), 200

            for relative_path in config_paths:
                try:
                    config = config_loader(temp_workspace, relative_path)
                except PipelineConfigError as exc:
                    workflow_name = relative_path.stem or "pipeline"
                    LOGGER.exception(
                        "Pipeline configuration error for %s at %s",
                        repo_full_name,
                        relative_path,
                    )
                    pipeline_id = db_store.create_pipeline(
                        repo=repo_full_name,
                        commit_sha=commit_sha,
                        branch=branch,
                        workflow_name=workflow_name,
                        config_path=str(relative_path),
                    )
                    db_store.update_pipeline_status(pipeline_id, "failure")
                    reporter.report_failure(
                        repo_full_name,
                        commit_sha,
                        pipeline_id,
                        description="Pipeline configuration invalid",
                        context=build_status_context(workflow_name),
                    )
                    pipeline_results.append(
                        {
                            "pipeline_id": pipeline_id,
                            "workflow": workflow_name,
                            "config_path": str(relative_path),
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
                    continue

                pipeline_id = db_store.create_pipeline(
                    repo=repo_full_name,
                    commit_sha=commit_sha,
                    branch=branch,
                    workflow_name=config.name,
                    config_path=str(config.config_path),
                )

                pipeline_workspace = workspace_base / f"pipeline-{pipeline_id}"
                try:
                    shutil.copytree(temp_workspace, pipeline_workspace, dirs_exist_ok=False)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception(
                        "Failed to prepare workspace for pipeline %s", pipeline_id
                    )
                    db_store.update_pipeline_status(pipeline_id, "failure")
                    reporter.report_failure(
                        repo_full_name,
                        commit_sha,
                        pipeline_id,
                        description="Workspace preparation failed",
                        context=build_status_context(config.name),
                    )
                    pipeline_results.append(
                        {
                            "pipeline_id": pipeline_id,
                            "workflow": config.name,
                            "config_path": str(config.config_path),
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
                    continue

                for job in config.jobs.values():
                    db_store.create_job(pipeline_id, job.name)

                reporter.report_pending(
                    repo_full_name,
                    commit_sha,
                    pipeline_id,
                    context=build_status_context(config.name),
                )
                orchestrations.append((pipeline_id, config, pipeline_workspace))
                pipeline_results.append(
                    {
                        "pipeline_id": pipeline_id,
                        "workflow": config.name,
                        "config_path": str(config.config_path),
                        "status": "queued",
                    }
                )

            for pipeline_id, config, workspace_path in orchestrations:
                def invoke_pipeline(
                    pid: int = pipeline_id,
                    cfg: PipelineConfig = config,
                    workspace: Path = workspace_path,
                ) -> None:
                    pipeline_orchestrator.run_pipeline(pid, cfg, workspace)

                runner(invoke_pipeline)

            status_value = (
                "queued"
                if any(result["status"] == "queued" for result in pipeline_results)
                else "failed"
            )
            return jsonify(
                {
                    "status": status_value,
                    "pipelines": pipeline_results,
                }
            ), 200
        except GitCloneError as exc:
            LOGGER.exception(
                "Failed to clone repository for %s@%s", repo_full_name, commit_sha
            )
            return (
                jsonify(
                    {
                        "status": "failed",
                        "error": str(exc),
                        "pipelines": [],
                    }
                ),
                200,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "Unexpected error preparing pipelines for %s@%s",
                repo_full_name,
                commit_sha,
            )
            return (
                jsonify(
                    {
                        "status": "failed",
                        "error": "unexpected error",
                        "pipelines": [],
                    }
                ),
                200,
            )
        finally:
            if temp_workspace is not None:
                shutil.rmtree(temp_workspace, ignore_errors=True)

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
