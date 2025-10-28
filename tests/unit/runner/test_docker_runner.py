"""Unit tests for the Docker-based runner implementation."""

import subprocess
from pathlib import Path

import pytest

from fmg.pipelines.config_parser import JobSpec, StepSpec
from fmg.runner import DockerRunner


def _job_with_steps(*commands: str) -> JobSpec:
    steps = [StepSpec(kind="checkout")]
    steps.extend(StepSpec(kind="run", command=cmd) for cmd in commands)
    return JobSpec(name="build", image="python:3.11", steps=tuple(steps))


def test_docker_runner_builds_expected_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = DockerRunner()
    job = _job_with_steps("echo hi", "pytest")

    result = runner.run(job, tmp_path)

    assert result.success
    assert "echo hi" in result.output
    assert captured["cmd"][0] == "docker"
    assert "python:3.11" in captured["cmd"]
    assert "&&" in captured["cmd"][-1]
    assert f"{tmp_path.resolve()}:" in next(part for part in captured["cmd"] if part.startswith(str(tmp_path.resolve())))


def test_docker_runner_handles_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(cmd: list[str], capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = DockerRunner()
    job = _job_with_steps("exit 1")

    result = runner.run(job, tmp_path)

    assert not result.success
    assert result.return_code == 1
    assert "boom" in result.output
    assert "Command exited with code" in result.output


def test_docker_runner_rejects_unknown_steps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_run(cmd: list[str], capture_output: bool, text: bool, check: bool) -> subprocess.CompletedProcess[str]:  # pragma: no cover - should not run
        raise AssertionError("docker should not be invoked for invalid steps")

    monkeypatch.setattr(subprocess, "run", fail_run)

    job = JobSpec(
        name="build",
        image="python:3.11",
        steps=(StepSpec(kind="checkout"), StepSpec(kind="persist_to_workspace")),
    )

    runner = DockerRunner()
    result = runner.run(job, tmp_path)

    assert not result.success
    assert "Unsupported step" in result.output
