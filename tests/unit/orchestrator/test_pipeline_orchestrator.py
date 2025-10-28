"""Tests for the pipeline orchestrator job execution flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from popsicle.orchestrator import PipelineOrchestrator
from popsicle.pipelines.config_parser import JobSpec, PipelineConfig, StepSpec
from popsicle.storage.sqlite import SQLiteStore
from popsicle.runner import RunnerResult


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "orchestrator.db")


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def _build_config(commands: list[str]) -> PipelineConfig:
    steps = [StepSpec(kind="checkout")]
    steps.extend(StepSpec(kind="run", command=command) for command in commands)
    job = JobSpec(name="build", image="python:3.11", steps=tuple(steps))
    return PipelineConfig(jobs={"build": job}, job_order=("build",), dependencies={"build": ()})


class RecordingStatusReporter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int, str]] = []

    def report_pending(
        self, repo: str, commit_sha: str, pipeline_id: int, *, description: str, target_url: str | None = None
    ) -> bool:
        self.calls.append(("pending", repo, commit_sha, pipeline_id, description))
        return True

    def report_success(
        self, repo: str, commit_sha: str, pipeline_id: int, *, description: str, target_url: str | None = None
    ) -> bool:
        self.calls.append(("success", repo, commit_sha, pipeline_id, description))
        return True

    def report_failure(
        self, repo: str, commit_sha: str, pipeline_id: int, *, description: str, target_url: str | None = None
    ) -> bool:
        self.calls.append(("failure", repo, commit_sha, pipeline_id, description))
        return True


class StubRunner:
    """Simple runner used to simulate docker execution in unit tests."""

    def __init__(self, results: dict[str, RunnerResult]):
        self._results = results
        self.calls: list[str] = []

    def run(self, job: JobSpec, workspace_path: Path) -> RunnerResult:
        self.calls.append(job.name)
        return self._results[job.name]


def test_successful_pipeline_marks_job_success(store: SQLiteStore, workspace: Path) -> None:
    pipeline_id = store.create_pipeline(
        repo="example/repo",
        commit_sha="abc123",
        branch="main",
    )
    store.create_job(pipeline_id, "build")

    config = _build_config(["echo success"])
    runner = StubRunner(
        {
            "build": RunnerResult(
                success=True,
                output="[job] build\n$ echo success\nexecution ok\n",
                return_code=0,
            )
        }
    )
    reporter = RecordingStatusReporter()
    orchestrator = PipelineOrchestrator(store, runner=runner, status_reporter=reporter)

    orchestrator.run_pipeline(pipeline_id, config, workspace)

    pipeline = store.get_pipeline(pipeline_id)
    assert pipeline is not None
    assert pipeline.status == "success"

    jobs = store.get_jobs_for_pipeline(pipeline_id)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == "success"
    assert job.log is not None and "echo success" in job.log
    assert not workspace.exists()

    assert reporter.calls[0][0] == "pending"
    assert reporter.calls[-1][0] == "success"
    assert reporter.calls[-1][-1].startswith("Pipeline succeeded")


def test_failure_marks_remaining_jobs_skipped(store: SQLiteStore, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    pipeline_id = store.create_pipeline(
        repo="example/repo",
        commit_sha="def456",
        branch="main",
    )
    store.create_job(pipeline_id, "build")
    store.create_job(pipeline_id, "test")

    build_steps = (
        StepSpec(kind="checkout"),
        StepSpec(kind="run", command="false"),
    )
    test_steps = (
        StepSpec(kind="checkout"),
        StepSpec(kind="run", command="echo should not run"),
    )
    config = PipelineConfig(
        jobs={
            "build": JobSpec(name="build", image="python:3.11", steps=build_steps),
            "test": JobSpec(name="test", image="python:3.11", steps=test_steps),
        },
        job_order=("build", "test"),
        dependencies={"build": (), "test": ("build",)},
    )

    runner = StubRunner(
        {
            "build": RunnerResult(
                success=False,
                output="Command exited with code 1",
                return_code=1,
            ),
            "test": RunnerResult(success=True, output="should not run", return_code=0),
        }
    )
    reporter = RecordingStatusReporter()
    orchestrator = PipelineOrchestrator(store, runner=runner, status_reporter=reporter)
    orchestrator.run_pipeline(pipeline_id, config, workspace)

    pipeline = store.get_pipeline(pipeline_id)
    assert pipeline is not None
    assert pipeline.status == "failure"

    jobs = {job.job_name: job for job in store.get_jobs_for_pipeline(pipeline_id)}
    assert jobs["build"].status == "failure"
    assert jobs["test"].status == "skipped"
    assert jobs["build"].log is not None and "Command exited with code" in jobs["build"].log
    assert jobs["test"].log in (None, "")
    assert not workspace.exists()

    assert reporter.calls[0][0] == "pending"
    assert reporter.calls[-1][0] == "failure"
    assert "Job build failed" in reporter.calls[-1][-1]
