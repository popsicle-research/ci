"""Pipeline orchestrator responsible for executing jobs sequentially."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Sequence

from fmg.github import GitHubStatusReporter
from fmg.pipelines.config_parser import PipelineConfig
from fmg.runner import DockerRunner, Runner
from fmg.storage.sqlite import SQLiteStore

LOGGER = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class PipelineOrchestrator:
    """Coordinate execution of pipeline jobs using a runner implementation."""

    def __init__(
        self,
        store: SQLiteStore,
        runner: Runner | None = None,
        status_reporter: GitHubStatusReporter | None = None,
    ) -> None:
        self._store = store
        self._runner = runner or DockerRunner()
        self._status_reporter = status_reporter

    @property
    def status_reporter(self) -> GitHubStatusReporter | None:
        return self._status_reporter

    @status_reporter.setter
    def status_reporter(self, reporter: GitHubStatusReporter | None) -> None:
        self._status_reporter = reporter

    def run_pipeline(
        self, pipeline_id: int, config: PipelineConfig, workspace_path: Path
    ) -> None:
        LOGGER.info("Starting pipeline %s", pipeline_id)
        pipeline = self._store.get_pipeline(pipeline_id)
        if pipeline is None:
            LOGGER.error(
                "Pipeline %s not found in storage when attempting to run", pipeline_id
            )
            return

        self._store.update_pipeline_status(pipeline_id, "running")

        self._report_status(
            "pending",
            pipeline.repo,
            pipeline.commit_sha,
            pipeline_id,
            "Pipeline is running",
        )

        order = self._execution_order(config)
        job_ids = self._ensure_job_records(pipeline_id, order)
        completed_jobs: list[str] = []
        failed_job: str | None = None

        try:
            for job_name in order:
                job_spec = config.jobs[job_name]
                job_id = job_ids[job_name]
                start_time = _utc_now()
                self._store.update_job_status(job_id, "running", start_time=start_time)
                LOGGER.info("Running job %s (pipeline %s)", job_name, pipeline_id)

                try:
                    result = self._runner.run(job_spec, workspace_path)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception(
                        "Runner raised an exception while executing job %s", job_name
                    )
                    log_message = f"Runner raised unexpected error: {exc}\n"
                    self._store.set_job_log(job_id, log_message)
                    self._store.update_job_status(job_id, "failure")
                    failed_job = job_name
                    break

                log_output = result.output or ""
                self._store.set_job_log(job_id, log_output)
                final_status = "success" if result.success else "failure"
                self._store.update_job_status(job_id, final_status)

                if result.success:
                    completed_jobs.append(job_name)
                    continue

                failed_job = job_name
                LOGGER.info(
                    "Job %s failed for pipeline %s with return code %s",
                    job_name,
                    pipeline_id,
                    result.return_code,
                )
                break

            if failed_job is None:
                self._store.update_pipeline_status(pipeline_id, "success")
                self._report_status(
                    "success",
                    pipeline.repo,
                    pipeline.commit_sha,
                    pipeline_id,
                    f"Pipeline succeeded ({len(order)} jobs)",
                )
                LOGGER.info("Pipeline %s completed successfully", pipeline_id)
                return

            for job_name in order:
                if job_name in completed_jobs or job_name == failed_job:
                    continue
                job_id = job_ids[job_name]
                self._store.update_job_status(job_id, "skipped")

            self._store.update_pipeline_status(pipeline_id, "failure")
            description = "Pipeline failed"
            if failed_job:
                description = f"Job {failed_job} failed"
            self._report_status(
                "failure",
                pipeline.repo,
                pipeline.commit_sha,
                pipeline_id,
                description,
            )
            LOGGER.info("Pipeline %s marked as failure", pipeline_id)
        finally:
            self._cleanup_workspace(workspace_path)

    def _ensure_job_records(
        self, pipeline_id: int, job_names: Sequence[str]
    ) -> Dict[str, int]:
        existing = {
            job.job_name: job.id
            for job in self._store.get_jobs_for_pipeline(pipeline_id)
        }
        for job_name in job_names:
            if job_name not in existing:
                existing[job_name] = self._store.create_job(pipeline_id, job_name)
        return existing

    def _execution_order(self, config: PipelineConfig) -> Sequence[str]:
        return config.job_order or tuple(config.jobs.keys())


    def _cleanup_workspace(self, workspace_path: Path) -> None:
        try:
            shutil.rmtree(workspace_path)
        except FileNotFoundError:
            return
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "Failed to clean workspace at %s: %s", workspace_path, exc
            )

    def _report_status(
        self,
        state: str,
        repo: str,
        commit_sha: str,
        pipeline_id: int,
        description: str,
    ) -> None:
        if self._status_reporter is None:
            return
        try:
            if state == "success":
                self._status_reporter.report_success(
                    repo,
                    commit_sha,
                    pipeline_id,
                    description=description,
                )
            elif state == "failure":
                self._status_reporter.report_failure(
                    repo,
                    commit_sha,
                    pipeline_id,
                    description=description,
                )
            else:
                self._status_reporter.report_pending(
                    repo,
                    commit_sha,
                    pipeline_id,
                    description=description,
                )
        except Exception:  # noqa: BLE001
            LOGGER.warning(
                "Failed to publish %s status for pipeline %s", state, pipeline_id
            )


__all__ = ["PipelineOrchestrator"]
