"""HTTP API routes for inspecting pipelines, jobs, and runners."""

from __future__ import annotations

from typing import Iterable

from flask import Flask, Response, jsonify, request

from fmg.storage.sqlite import JobRecord, PipelineRecord, RunnerRecord, SQLiteStore


def register_api_routes(app: Flask, store: SQLiteStore) -> None:
    """Attach REST API routes that expose pipeline and job information."""

    @app.get("/pipelines")
    def list_pipelines() -> tuple[Response, int]:
        limit_value = request.args.get("limit", "20")
        try:
            limit = max(int(limit_value), 0)
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400

        pipelines = store.get_recent_pipelines(limit=limit or 20)
        payload = [_serialize_pipeline(pipeline) for pipeline in pipelines]
        return jsonify(payload), 200

    @app.get("/pipelines/<int:pipeline_id>")
    def get_pipeline_details(pipeline_id: int) -> tuple[Response, int]:
        pipeline = store.get_pipeline(pipeline_id)
        if pipeline is None:
            return jsonify({"error": "pipeline not found"}), 404

        jobs = store.get_jobs_for_pipeline(pipeline_id)
        payload = _serialize_pipeline(pipeline, jobs)
        return jsonify(payload), 200

    @app.get("/pipelines/<int:pipeline_id>/jobs/<int:job_id>/log")
    def get_job_log(pipeline_id: int, job_id: int) -> Response:
        job = store.get_job(job_id)
        if job is None or job.pipeline_id != pipeline_id:
            return Response("job not found", status=404, mimetype="text/plain")

        log_content = job.log or ""
        return Response(log_content, status=200, mimetype="text/plain")

    @app.get("/runners")
    def list_runners() -> tuple[Response, int]:
        runners = store.list_runners()
        payload = [_serialize_runner(runner) for runner in runners]
        return jsonify(payload), 200

    @app.post("/runners")
    def create_runner() -> tuple[Response, int]:
        payload = request.get_json(silent=True) or {}
        host = str(payload.get("host", "")).strip()
        if not host:
            return jsonify({"error": "host is required"}), 400

        runner_id = store.add_runner(host)
        runner = RunnerRecord(id=runner_id, host=host, active=True)
        return jsonify(_serialize_runner(runner)), 201


def _serialize_pipeline(
    pipeline: PipelineRecord, jobs: Iterable[JobRecord] | None = None
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": pipeline.id,
        "repo": pipeline.repo,
        "branch": pipeline.branch,
        "commit_sha": pipeline.commit_sha,
        "status": pipeline.status,
        "start_time": pipeline.start_time,
        "end_time": pipeline.end_time,
    }

    if jobs is not None:
        data["jobs"] = [_serialize_job(job) for job in jobs]
    return data


def _serialize_job(job: JobRecord) -> dict[str, object]:
    return {
        "id": job.id,
        "job_name": job.job_name,
        "status": job.status,
        "start_time": job.start_time,
        "end_time": job.end_time,
    }


def _serialize_runner(runner: RunnerRecord) -> dict[str, object]:
    return {
        "id": runner.id,
        "host": runner.host,
        "active": runner.active,
    }
