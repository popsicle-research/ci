"""Docker-based runner implementation used by the orchestrator."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from popsicle.pipelines.config_parser import JobSpec

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerResult:
    """Result returned after executing a job."""

    success: bool
    output: str
    return_code: int | None = None


class Runner(Protocol):
    """Protocol describing the runner interface used by the orchestrator."""

    def run(self, job: JobSpec, workspace_path: Path) -> RunnerResult:
        """Execute a job inside the provided workspace and return the outcome."""


class DockerRunner:
    """Runner that executes job steps inside a Docker container."""

    def __init__(self, docker_binary: str = "docker", platform: str | None = None) -> None:
        self._docker_binary = docker_binary
        self._platform = platform
        self._container_workspace = "/workspace"

    def run(self, job: JobSpec, workspace_path: Path) -> RunnerResult:
        logs: list[str] = [f"[job] {job.name}\n", f"[image] {job.image}\n"]
        run_commands: list[str] = []

        for step in job.steps:
            if step.kind == "checkout":
                logs.append("[checkout] repository mounted into container\n")
                continue

            if step.kind != "run":
                message = f"Unsupported step '{step.kind}' in job '{job.name}'\n"
                logs.append(message)
                LOGGER.error(message.strip())
                return RunnerResult(success=False, output="".join(logs))

            command = (step.command or "").strip()
            if not command:
                message = f"Run step missing command in job '{job.name}'\n"
                logs.append(message)
                LOGGER.error(message.strip())
                return RunnerResult(success=False, output="".join(logs))

            run_commands.append(command)
            logs.append(f"$ {command}\n")

        combined_command: str
        if run_commands:
            #combined_command = "set -eo pipefail; " + " && ".join(run_commands)
            combined_command = " && ".join(run_commands)
        else:
            combined_command = "true"
            logs.append("[runner] no run steps defined; executing no-op\n")

        docker_command = [
            self._docker_binary,
            "run",
            "--rm",
            "-v",
            f"{str(workspace_path.resolve())}:{self._container_workspace}",
            "-w",
            self._container_workspace,
        ]

        if self._platform:
            docker_command.extend(["--platform", self._platform])

        docker_command.append(job.image)
        docker_command.extend(["sh", "-c", combined_command])

        try:
            completed = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            LOGGER.exception("Docker binary not found when executing job %s", job.name)
            logs.append(f"Docker binary not found: {exc}\n")
            return RunnerResult(success=False, output="".join(logs))
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Docker execution failed for job %s", job.name)
            logs.append(f"Docker execution raised: {exc}\n")
            return RunnerResult(success=False, output="".join(logs))

        if completed.stdout:
            logs.append(
                completed.stdout
                if completed.stdout.endswith("\n")
                else f"{completed.stdout}\n"
            )
        if completed.stderr:
            logs.append(
                completed.stderr
                if completed.stderr.endswith("\n")
                else f"{completed.stderr}\n"
            )

        success = completed.returncode == 0
        if not success:
            logs.append(f"Command exited with code {completed.returncode}\n")

        return RunnerResult(
            success=success,
            output="".join(logs),
            return_code=completed.returncode,
        )


__all__ = ["Runner", "RunnerResult", "DockerRunner"]
