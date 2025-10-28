"""Server-rendered views for the POPSICLE CI/CD dashboard."""

from __future__ import annotations

import math
from typing import Dict, Tuple

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
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
from popsicle.storage.sqlite import JobRecord, PipelineRecord, ProjectSummary, SQLiteStore

ui_bp = Blueprint("ui", __name__, url_prefix="/ui", template_folder="templates")

MAX_LOG_PREVIEW_BYTES = 2 * 1024 * 1024
LOG_PREVIEW_LINE_LIMIT = 2000
STATUS_FILTERS = ["running", "success", "failure", "pending"]


def _get_store() -> SQLiteStore:
    store = current_app.config.get("POPSICLE_UI_STORE")
    if store is None:
        raise RuntimeError("Web UI store not configured on application")
    return store


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

    return render_template(
        "pipeline_details.html",
        pipeline=pipeline_dict,
        jobs=jobs,
        repo=pipeline.repo,
        LOG_PREVIEW_LINE_LIMIT=LOG_PREVIEW_LINE_LIMIT,
    )


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
