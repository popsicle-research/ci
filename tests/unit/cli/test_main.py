from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from fmg.cli.main import cli


class DummyResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any | None = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_data
        if text is None and json_data is not None:
            text = json.dumps(json_data)
        self.text = text or ""
        self.content = self.text.encode()

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("JSON payload not set")
        return self._json


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_list_command_displays_pipelines(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    def fake_get(url: str, timeout: int, **_: Any) -> DummyResponse:
        assert url == "http://localhost:5000/pipelines"
        return DummyResponse(
            json_data=[
                {
                    "id": 5,
                    "repo": "user/repo",
                    "branch": "main",
                    "commit_sha": "abc1234",
                    "status": "success",
                    "start_time": "2024-01-01T00:00:00Z",
                    "end_time": "2024-01-01T00:01:00Z",
                }
            ]
        )

    monkeypatch.setattr("fmg.cli.main.requests.get", fake_get)

    result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0
    assert "#5 [user/repo @ abc1234" in result.output


def test_logs_command_fetches_single_job_log(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    def fake_get(url: str, timeout: int, **_: Any) -> DummyResponse:
        if url == "http://localhost:5000/pipelines/12":
            return DummyResponse(
                json_data={
                    "id": 12,
                    "jobs": [{"id": 3, "job_name": "build"}],
                }
            )
        if url == "http://localhost:5000/pipelines/12/jobs/3/log":
            return DummyResponse(text="line1\nline2\n")
        pytest.fail(f"unexpected URL requested: {url}")

    monkeypatch.setattr("fmg.cli.main.requests.get", fake_get)

    result = runner.invoke(cli, ["logs", "12"])

    assert result.exit_code == 0
    assert '--- Log for pipeline 12, job "build" ---' in result.output
    assert "line1" in result.output


def test_logs_command_requires_job_name_when_multiple_jobs(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    def fake_get(url: str, timeout: int, **_: Any) -> DummyResponse:
        assert url == "http://localhost:5000/pipelines/7"
        return DummyResponse(
            json_data={
                "id": 7,
                "jobs": [
                    {"id": 1, "job_name": "lint"},
                    {"id": 2, "job_name": "test"},
                ],
            }
        )

    monkeypatch.setattr("fmg.cli.main.requests.get", fake_get)

    result = runner.invoke(cli, ["logs", "7"])

    assert result.exit_code != 0
    assert "Specify one of" in result.output


def test_logs_command_errors_for_missing_job(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    def fake_get(url: str, timeout: int, **_: Any) -> DummyResponse:
        assert url == "http://localhost:5000/pipelines/9"
        return DummyResponse(
            json_data={
                "id": 9,
                "jobs": [{"id": 4, "job_name": "build"}],
            }
        )

    monkeypatch.setattr("fmg.cli.main.requests.get", fake_get)

    result = runner.invoke(cli, ["logs", "9", "deploy"])

    assert result.exit_code != 0
    assert "Job name not found" in result.output


def test_configure_add_runner_posts_host(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    def fake_post(
        url: str, timeout: int, json: dict[str, Any], **_: Any
    ) -> DummyResponse:
        assert url == "http://localhost:5000/runners"
        assert json == {"host": "10.0.0.5"}
        return DummyResponse(
            status_code=201,
            json_data={"id": 2, "host": "10.0.0.5", "active": True},
        )

    monkeypatch.setattr("fmg.cli.main.requests.post", fake_post)

    result = runner.invoke(cli, ["configure", "add-runner", "10.0.0.5"])

    assert result.exit_code == 0
    assert "Runner 2 added" in result.output


def test_configure_list_runners_outputs_entries(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner
) -> None:
    def fake_get(url: str, timeout: int, **_: Any) -> DummyResponse:
        assert url == "http://localhost:5000/runners"
        return DummyResponse(
            json_data=[
                {"id": 1, "host": "localhost", "active": True},
                {"id": 2, "host": "10.0.0.5", "active": False},
            ]
        )

    monkeypatch.setattr("fmg.cli.main.requests.get", fake_get)

    result = runner.invoke(cli, ["configure", "list-runners"])

    assert result.exit_code == 0
    assert "1: localhost" in result.output
    assert "2: 10.0.0.5" in result.output
