"""Command line interface for the FMG CI/CD platform."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import click
import requests

DEFAULT_SERVER_URL = "http://localhost:5000"
SERVER_URL_ENV_VAR = "FMG_SERVER_URL"
REQUEST_TIMEOUT_SECONDS = 10


class APIClient:
    """Small helper around HTTP requests to the FMG REST API."""

    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise ValueError("base_url must be provided")
        self.base_url = base_url.rstrip("/")

    def _build_url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def get_json(self, path: str) -> Any:
        response = self._request("get", path)
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - defensive guard
            raise click.ClickException("Server returned invalid JSON payload.") from exc

    def get_text(self, path: str) -> str:
        response = self._request("get", path)
        return response.text

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        response = self._request("post", path, json=payload)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:  # pragma: no cover - server may not return JSON on success
            return None

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = self._build_url(path)
        request = getattr(requests, method)
        try:
            response = request(url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        except requests.RequestException as exc:
            raise click.ClickException(f"Failed to contact server: {exc}") from exc

        if response.status_code >= 400:
            raise click.ClickException(_format_http_error(response))
        return response


def _format_http_error(response: requests.Response) -> str:
    message = f"Server responded with {response.status_code}"
    try:
        data = response.json()
    except ValueError:
        data = None

    if isinstance(data, dict) and "error" in data:
        return str(data["error"])
    if response.text:
        return response.text.strip()
    return message


def _format_pipeline_summary(pipeline: dict[str, Any]) -> str:
    commit = (pipeline.get("commit_sha") or "")[:7] or "unknown"
    repo = pipeline.get("repo", "unknown")
    branch = pipeline.get("branch", "unknown")
    status = (pipeline.get("status") or "unknown").upper()
    started = pipeline.get("start_time") or "unknown"
    finished = pipeline.get("end_time")
    completed_note = f" finished {finished}" if finished else ""
    return f"#{pipeline.get('id')} [{repo} @ {commit} on {branch}] status: {status} (started {started}{completed_note})"


def _format_runner_summary(runner: dict[str, Any]) -> str:
    status = "active" if runner.get("active", False) else "inactive"
    return f"{runner.get('id')}: {runner.get('host')} [{status}]"


@click.group()
@click.option(
    "--server-url",
    envvar=SERVER_URL_ENV_VAR,
    default=DEFAULT_SERVER_URL,
    show_default=True,
    help="Base URL for the FMG API server.",
)
@click.pass_context
def cli(ctx: click.Context, server_url: str) -> None:
    """Interact with the FMG CI/CD platform."""

    ctx.obj = APIClient(server_url)


@cli.command("list")
@click.pass_obj
def list_pipelines(client: APIClient) -> None:
    """List recent pipeline runs."""

    payload = client.get_json("/pipelines")
    if not payload:
        click.echo("No pipelines recorded yet.")
        return

    if not isinstance(payload, list):
        raise click.ClickException("Unexpected server response when listing pipelines.")

    for pipeline in payload:
        if not isinstance(pipeline, dict):
            raise click.ClickException("Server returned malformed pipeline data.")
        click.echo(_format_pipeline_summary(pipeline))


@cli.command("logs")
@click.argument("pipeline_id", type=int)
@click.argument("job_name", required=False)
@click.pass_obj
def show_logs(client: APIClient, pipeline_id: int, job_name: str | None) -> None:
    """Fetch logs for a pipeline or a specific job."""

    pipeline = client.get_json(f"/pipelines/{pipeline_id}")
    if not isinstance(pipeline, dict):
        raise click.ClickException("Server returned malformed pipeline details.")

    jobs = pipeline.get("jobs") or []
    if not isinstance(jobs, list):
        raise click.ClickException("Server returned malformed job list.")

    selected_job = None
    if job_name is None:
        if len(jobs) == 1:
            selected_job = jobs[0]
        elif len(jobs) == 0:
            raise click.ClickException("Pipeline does not contain any jobs.")
        else:
            job_names = ", ".join(job.get("job_name", "unknown") for job in jobs)
            raise click.ClickException(
                "Pipeline contains multiple jobs. Specify one of: " + job_names
            )
    else:
        for job in jobs:
            if job.get("job_name") == job_name:
                selected_job = job
                break
        if selected_job is None:
            raise click.ClickException("Job name not found in pipeline.")

    if not isinstance(selected_job, dict) or "id" not in selected_job:
        raise click.ClickException("Server returned malformed job metadata.")

    log_text = client.get_text(
        f"/pipelines/{pipeline_id}/jobs/{selected_job['id']}/log"
    )
    click.echo(
        f"--- Log for pipeline {pipeline_id}, job \"{selected_job.get('job_name')}\" ---"
    )
    click.echo(log_text, nl=False)
    if not log_text.endswith("\n"):
        click.echo()


@cli.group()
def configure() -> None:
    """Manage runner configuration."""


@configure.command("add-runner")
@click.argument("host")
@click.pass_obj
def add_runner(client: APIClient, host: str) -> None:
    """Register a new runner host with the FMG server."""

    host_value = host.strip()
    if not host_value:
        raise click.ClickException("Host must be provided.")
    payload = client.post_json("/runners", {"host": host_value}) or {}
    runner_id = payload.get("id")
    response_host = payload.get("host", host_value)
    if runner_id is None:
        click.echo(f"Runner added with host {response_host}")
    else:
        click.echo(f"Runner {runner_id} added with host {response_host}")


@configure.command("list-runners")
@click.pass_obj
def list_runners(client: APIClient) -> None:
    """List registered runner hosts."""

    payload = client.get_json("/runners")
    if not payload:
        click.echo("No runners configured.")
        return

    if not isinstance(payload, list):
        raise click.ClickException("Server returned malformed runner list.")

    for runner in payload:
        if not isinstance(runner, dict):
            raise click.ClickException("Server returned malformed runner data.")
        click.echo(_format_runner_summary(runner))
