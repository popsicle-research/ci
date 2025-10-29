"""Server-rendered views for the POPSICLE CI/CD dashboard."""

from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Dict, Tuple

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from popsicle.common.formatting import (
    format_duration,
    format_timestamp,
    humanize_status,
    short_sha,
    status_badge_class,
)
from popsicle.common.git import GitCloneError, clone_repository
from popsicle.pipelines.config_parser import (
    PipelineConfig,
    PipelineConfigError,
    load_pipeline_config,
)
from popsicle.storage.sqlite import JobRecord, PipelineRecord, ProjectSummary, SQLiteStore

ui_bp = Blueprint("ui", __name__, url_prefix="/ui", template_folder="templates")

MAX_LOG_PREVIEW_BYTES = 2 * 1024 * 1024
LOG_PREVIEW_LINE_LIMIT = 2000
STATUS_FILTERS = ["running", "success", "failure", "pending"]
RESTRICTED_STATUSES = {"pending", "running"}


def _get_store() -> SQLiteStore:
    store = current_app.config.get("POPSICLE_UI_STORE")
    if store is None:
        raise RuntimeError("Web UI store not configured on application")
    return store


def _get_clone_fn() -> Callable[[str, Path, str, str], None]:
    clone_fn = current_app.config.get("POPSICLE_CLONE_FN")
    if clone_fn is not None:
        return clone_fn

    def _default_clone(
        repo_url: str, destination: Path, commit_sha: str, branch: str
    ) -> None:
        clone_repository(repo_url, destination, commit_sha, branch=branch)

    return _default_clone


def _get_config_loader() -> Callable[[Path, Path | None], PipelineConfig]:
    loader = current_app.config.get("POPSICLE_CONFIG_LOADER")
    if loader is not None:
        return loader
    return load_pipeline_config


def _build_clone_url(repo: str) -> str:
    normalized = repo.strip()
    if normalized.startswith(("http://", "https://", "git@")):
        return normalized
    sanitized = normalized.rstrip("/")
    if sanitized.endswith(".git"):
        sanitized = sanitized[:-4]
    return f"https://github.com/{sanitized}.git"


def _preview_log(log: str | None) -> Tuple[str, bool]:
    if not log:
        return "", False

    encoded_length = len(log.encode("utf-8"))
    if encoded_length <= MAX_LOG_PREVIEW_BYTES:
        return log, False

    lines = log.splitlines()
    preview_lines = lines[:LOG_PREVIEW_LINE_LIMIT]
    preview = "\n".join(preview_lines)
    if len(lines) > LOG_PREVIEW_LINE_LIMIT:
        preview += "\n… truncated …"
    return preview, True


def _project_to_dict(project: ProjectSummary) -> Dict[str, object]:
    return {
        "repo": project.repo,
        "total_pipelines": project.total_pipelines,
        "last_run_at": format_timestamp(project.last_run_at),
        "last_status": humanize_status(project.last_status),
        "status_class": status_badge_class(project.last_status),
    }


def _pipeline_to_dict(pipeline: PipelineRecord) -> Dict[str, object]:
    return {
        "id": pipeline.id,
        "workflow": pipeline.workflow_name,
        "config_path": pipeline.config_path,
        "status_context": f"popsicle/ci: {pipeline.workflow_name}",
        "status": humanize_status(pipeline.status),
        "status_class": status_badge_class(pipeline.status),
        "branch": pipeline.branch,
        "commit_sha": short_sha(pipeline.commit_sha),
        "full_commit": pipeline.commit_sha,
        "start_time": format_timestamp(pipeline.start_time),
        "end_time": format_timestamp(pipeline.end_time),
    }


def _job_to_dict(job: JobRecord) -> Dict[str, object]:
    preview, truncated = _preview_log(job.log)
    return {
        "id": job.id,
        "name": job.job_name,
        "status": humanize_status(job.status),
        "status_class": status_badge_class(job.status),
        "start_time": format_timestamp(job.start_time),
        "end_time": format_timestamp(job.end_time),
        "log_preview": preview,
        "is_truncated": truncated,
        "has_log": bool(job.log),
    }


@ui_bp.get("/")
def home() -> Response:
    return redirect(url_for("ui.list_projects"))


@ui_bp.get("/projects")
def list_projects() -> Response:
    sort_param = request.args.get("sort", "recent")
    sort = "name" if sort_param == "name" else "recent"
    store = _get_store()
    projects = [
        _project_to_dict(project) for project in store.get_project_summaries(sort=sort)
    ]
    return render_template(
        "projects.html",
        projects=projects,
        sort=sort,
    )


@ui_bp.get("/projects/<path:repo>")
def project_pipelines(repo: str) -> Response:
    store = _get_store()
    status_filter = request.args.get("status")
    if status_filter not in STATUS_FILTERS:
        status_filter = None

    branch_filter = request.args.get("branch") or None

    per_page_raw = request.args.get("per_page", "20")
    page_raw = request.args.get("page", "1")
    try:
        per_page = max(min(int(per_page_raw), 100), 1)
    except ValueError:
        per_page = 20
    try:
        page = max(int(page_raw), 1)
    except ValueError:
        page = 1

    total = store.count_pipelines_by_repo(
        repo, status=status_filter, branch=branch_filter
    )
    total_pages = math.ceil(total / per_page) if total else 0
    if total_pages and page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    pipelines = store.list_pipelines_by_repo(
        repo,
        status=status_filter,
        branch=branch_filter,
        limit=per_page,
        offset=offset,
    )
    pipeline_dicts = [_pipeline_to_dict(p) for p in pipelines]

    branches = store.list_distinct_branches(repo)

    return render_template(
        "pipelines_list.html",
        repo=repo,
        pipelines=pipeline_dicts,
        branches=branches,
        selected_status=status_filter or "",
        selected_branch=branch_filter or "",
        status_filters=STATUS_FILTERS,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        total=total,
    )


@ui_bp.get("/pipelines/<int:pipeline_id>")
def pipeline_details(pipeline_id: int) -> Response:
    store = _get_store()
    pipeline = store.get_pipeline(pipeline_id)
    if pipeline is None:
        return (
            render_template(
                "error.html",
                title="Pipeline not found",
                message="The requested pipeline does not exist.",
                back_href=url_for("ui.list_projects"),
                back_label="Back to projects",
            ),
            404,
        )

    jobs = [_job_to_dict(job) for job in store.get_jobs_for_pipeline(pipeline_id)]
    pipeline_dict = _pipeline_to_dict(pipeline)
    pipeline_dict["duration"] = format_duration(pipeline.start_time, pipeline.end_time)
    can_retrigger = (
        pipeline.status not in RESTRICTED_STATUSES and pipeline.config_path is not None
    )

    return render_template(
        "pipeline_details.html",
        pipeline=pipeline_dict,
        jobs=jobs,
        repo=pipeline.repo,
        can_retrigger=can_retrigger,
        LOG_PREVIEW_LINE_LIMIT=LOG_PREVIEW_LINE_LIMIT,
    )


@ui_bp.post("/pipelines/<int:pipeline_id>/retry")
def retry_pipeline(pipeline_id: int) -> Response:
    store = _get_store()
    pipeline = store.get_pipeline(pipeline_id)
    if pipeline is None:
        flash("Pipeline not found.", "error")
        return redirect(url_for("ui.list_projects"))

    if pipeline.status in RESTRICTED_STATUSES:
        flash(
            "Pipeline is still running. Wait until it finishes before re-triggering.",
            "info",
        )
        return redirect(url_for("ui.pipeline_details", pipeline_id=pipeline_id))

    if not pipeline.config_path:
        flash(
            "This pipeline cannot be re-triggered because no configuration path was recorded.",
            "error",
        )
        return redirect(url_for("ui.pipeline_details", pipeline_id=pipeline_id))

    orchestrator = current_app.config.get("POPSICLE_ORCHESTRATOR")
    background_runner = current_app.config.get("POPSICLE_BACKGROUND_RUNNER")
    if orchestrator is None or background_runner is None:
        flash(
            "Re-triggering pipelines is not configured on this deployment.",
            "error",
        )
        return redirect(url_for("ui.pipeline_details", pipeline_id=pipeline_id))

    clone_fn = _get_clone_fn()
    config_loader = _get_config_loader()
    reporter = current_app.config.get("POPSICLE_STATUS_REPORTER")
    workspace_root_value = current_app.config.get("WORKSPACE_ROOT", Path("workspaces"))
    workspace_root = Path(workspace_root_value).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    temp_workspace = Path(
        tempfile.mkdtemp(prefix=f"pipeline-{pipeline_id}-retry-", dir=workspace_root)
    )
    new_pipeline_id: int | None = None
    config: PipelineConfig | None = None

    try:
        clone_url = _build_clone_url(pipeline.repo)
        clone_fn(clone_url, temp_workspace, pipeline.commit_sha, pipeline.branch)
        config_path = Path(pipeline.config_path)
        try:
            config = config_loader(temp_workspace, config_path)
        except PipelineConfigError as exc:
            flash(f"Failed to load pipeline configuration: {exc}", "error")
            return redirect(url_for("ui.pipeline_details", pipeline_id=pipeline_id))

        new_pipeline_id = store.create_pipeline(
            repo=pipeline.repo,
            commit_sha=pipeline.commit_sha,
            branch=pipeline.branch,
            workflow_name=config.name,
            config_path=str(config.config_path),
        )

        pipeline_workspace = workspace_root / f"pipeline-{new_pipeline_id}"
        try:
            shutil.copytree(temp_workspace, pipeline_workspace, dirs_exist_ok=False)
        except Exception:  # noqa: BLE001
            store.update_pipeline_status(new_pipeline_id, "failure")
            if reporter is not None:
                try:
                    reporter.report_failure(
                        pipeline.repo,
                        pipeline.commit_sha,
                        new_pipeline_id,
                        description="Workspace preparation failed",
                        context=f"popsicle/ci: {config.name}",
                    )
                except Exception:  # noqa: BLE001
                    current_app.logger.warning(
                        "Failed to publish failure status for pipeline %s",
                        new_pipeline_id,
                    )
            flash("Failed to prepare workspace for the new pipeline run.", "error")
            return redirect(url_for("ui.pipeline_details", pipeline_id=pipeline_id))

        for job in config.jobs.values():
            store.create_job(new_pipeline_id, job.name)

        if reporter is not None:
            try:
                reporter.report_pending(
                    pipeline.repo,
                    pipeline.commit_sha,
                    new_pipeline_id,
                    description="Pipeline re-triggered",
                    context=f"popsicle/ci: {config.name}",
                )
            except Exception:  # noqa: BLE001
                current_app.logger.warning(
                    "Failed to publish pending status for pipeline %s", new_pipeline_id
                )

        def invoke_pipeline(
            pid: int = new_pipeline_id,
            cfg: PipelineConfig = config,
            workspace: Path = pipeline_workspace,
        ) -> None:
            orchestrator.run_pipeline(pid, cfg, workspace)

        background_runner(invoke_pipeline)
    except GitCloneError as exc:
        flash(f"Failed to clone repository: {exc}", "error")
        return redirect(url_for("ui.pipeline_details", pipeline_id=pipeline_id))
    except PipelineConfigError as exc:
        flash(f"Failed to load pipeline configuration: {exc}", "error")
        return redirect(url_for("ui.pipeline_details", pipeline_id=pipeline_id))
    except Exception:  # noqa: BLE001
        current_app.logger.exception(
            "Unexpected error while re-triggering pipeline %s", pipeline_id
        )
        if new_pipeline_id is not None:
            store.update_pipeline_status(new_pipeline_id, "failure")
        flash("Unexpected error while re-triggering pipeline.", "error")
        return redirect(url_for("ui.pipeline_details", pipeline_id=pipeline_id))
    finally:
        shutil.rmtree(temp_workspace, ignore_errors=True)

    flash(
        f"Pipeline #{pipeline_id} re-triggered as pipeline #{new_pipeline_id}.",
        "success",
    )
    return redirect(url_for("ui.pipeline_details", pipeline_id=new_pipeline_id))


@ui_bp.get("/pipelines/<int:pipeline_id>/jobs/<int:job_id>/download")
def download_job_log(pipeline_id: int, job_id: int) -> Response:
    store = _get_store()
    job = store.get_job(job_id)
    if job is None or job.pipeline_id != pipeline_id:
        abort(404)

    content = job.log or ""
    response = Response(content, mimetype="text/plain")
    response.headers["Content-Disposition"] = (
        f"attachment; filename=pipeline-{pipeline_id}-job-{job_id}.log"
    )
    return response
