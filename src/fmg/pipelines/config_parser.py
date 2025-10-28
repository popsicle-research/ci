"""Pipeline configuration parser for CircleCI-style YAML files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence

import yaml


CONFIG_RELATIVE_PATH = Path(".popsicle/ci.yml")


class PipelineConfigError(RuntimeError):
    """Raised when the pipeline configuration file is invalid or missing."""


@dataclass(frozen=True)
class StepSpec:
    """Representation of a single job step."""

    kind: str
    command: Optional[str] = None


@dataclass(frozen=True)
class JobSpec:
    """Representation of a CircleCI-style job definition."""

    name: str
    image: str
    steps: Sequence[StepSpec]


@dataclass(frozen=True)
class PipelineConfig:
    """Parsed pipeline configuration."""

    jobs: Mapping[str, JobSpec]
    job_order: Sequence[str]
    dependencies: Mapping[str, Sequence[str]]


def load_pipeline_config(repo_root: Path) -> PipelineConfig:
    """Load and validate the pipeline configuration for a repository.

    Args:
        repo_root: Filesystem path to the cloned repository root.

    Returns:
        A :class:`PipelineConfig` representing the parsed YAML content.

    Raises:
        PipelineConfigError: If the configuration file is missing or malformed.
    """

    config_path = repo_root / CONFIG_RELATIVE_PATH
    if not config_path.exists():
        raise PipelineConfigError(
            f"Pipeline configuration file not found at {CONFIG_RELATIVE_PATH}"
        )

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PipelineConfigError("Invalid YAML in pipeline configuration") from exc

    if not isinstance(data, MutableMapping):
        raise PipelineConfigError("Pipeline configuration must be a mapping")

    jobs_section = data.get("jobs")
    if not isinstance(jobs_section, MutableMapping) or not jobs_section:
        raise PipelineConfigError("Pipeline configuration must define at least one job")

    jobs: Dict[str, JobSpec] = {}
    for job_name, job_body in jobs_section.items():
        if not isinstance(job_body, MutableMapping):
            raise PipelineConfigError(f"Job '{job_name}' must be a mapping")

        docker_images = job_body.get("docker")
        if not isinstance(docker_images, list) or not docker_images:
            raise PipelineConfigError(
                f"Job '{job_name}' must declare at least one docker image"
            )
        first_image = docker_images[0]
        if not isinstance(first_image, MutableMapping) or "image" not in first_image:
            raise PipelineConfigError(
                f"Job '{job_name}' docker definition must include an 'image'"
            )
        image = first_image["image"]
        if not isinstance(image, str):
            raise PipelineConfigError(
                f"Job '{job_name}' docker image value must be a string"
            )

        steps_section = job_body.get("steps")
        if not isinstance(steps_section, list) or not steps_section:
            raise PipelineConfigError(f"Job '{job_name}' must define at least one step")

        steps: List[StepSpec] = []
        for raw_step in steps_section:
            if isinstance(raw_step, str):
                step = _parse_simple_step(raw_step)
            elif isinstance(raw_step, MutableMapping):
                step = _parse_mapping_step(raw_step)
            else:
                raise PipelineConfigError(
                    f"Unsupported step format in job '{job_name}': {raw_step!r}"
                )
            steps.append(step)

        jobs[job_name] = JobSpec(name=job_name, image=image, steps=tuple(steps))

    dependencies = _extract_dependencies(data, jobs.keys())
    job_order = _topological_sort(jobs.keys(), dependencies)

    return PipelineConfig(jobs=jobs, job_order=tuple(job_order), dependencies=dependencies)


def _parse_simple_step(step_name: str) -> StepSpec:
    normalized = step_name.strip()
    if normalized != "checkout":
        raise PipelineConfigError(f"Unsupported step '{normalized}'")
    return StepSpec(kind="checkout")


def _parse_mapping_step(step_mapping: Mapping[str, object]) -> StepSpec:
    if len(step_mapping) != 1:
        raise PipelineConfigError(f"Unsupported step mapping: {step_mapping!r}")

    (step_type, value), = step_mapping.items()
    if step_type != "run":
        raise PipelineConfigError(f"Unsupported step type '{step_type}'")

    if isinstance(value, str):
        command = value
    elif isinstance(value, MutableMapping) and isinstance(value.get("command"), str):
        command = value["command"]
    else:
        raise PipelineConfigError(f"Run step must define a command: {step_mapping!r}")

    return StepSpec(kind="run", command=command)


def _extract_dependencies(
    data: Mapping[str, object],
    job_names: Iterable[str],
) -> Dict[str, List[str]]:
    dependencies: Dict[str, List[str]] = {job: [] for job in job_names}

    workflows_section = data.get("workflows")
    if not isinstance(workflows_section, MutableMapping):
        return dependencies

    workflow_def = _first_workflow_definition(workflows_section)
    if workflow_def is None:
        return dependencies

    jobs_list = workflow_def.get("jobs")
    if not isinstance(jobs_list, list) or not jobs_list:
        raise PipelineConfigError("Workflow must define a non-empty jobs list")

    for entry in jobs_list:
        if isinstance(entry, str):
            job_name = entry
            requires: List[str] = []
        elif isinstance(entry, MutableMapping) and len(entry) == 1:
            (job_name, payload), = entry.items()
            if not isinstance(payload, MutableMapping):
                raise PipelineConfigError(
                    f"Workflow job '{job_name}' must use a mapping configuration"
                )
            requires = payload.get("requires", [])
            if requires is None:
                requires = []
            if not isinstance(requires, list) or not all(
                isinstance(dep, str) for dep in requires
            ):
                raise PipelineConfigError(
                    f"Workflow job '{job_name}' has invalid requires list"
                )
        else:
            raise PipelineConfigError(
                f"Unsupported workflow job declaration: {entry!r}"
            )

        if job_name not in dependencies:
            raise PipelineConfigError(
                f"Workflow references unknown job '{job_name}'"
            )

        dependencies[job_name] = list(requires)

    return dependencies


def _first_workflow_definition(
    workflows_section: Mapping[str, object]
) -> Optional[Mapping[str, object]]:
    for key, value in workflows_section.items():
        if key == "version":
            continue
        if isinstance(value, MutableMapping):
            return value
        raise PipelineConfigError("Workflow definition must be a mapping")
    return None


def _topological_sort(
    job_names: Iterable[str],
    dependencies: Mapping[str, Sequence[str]],
) -> List[str]:
    remaining = {job: set(dependencies.get(job, [])) for job in job_names}
    resolved: List[str] = []

    while remaining:
        ready = [job for job, deps in remaining.items() if set(deps) <= set(resolved)]
        if not ready:
            unresolved = ", ".join(
                f"{job} -> {sorted(deps)}" for job, deps in remaining.items()
            )
            raise PipelineConfigError(
                f"Circular or unsatisfied job dependencies detected: {unresolved}"
            )

        for job in ready:
            resolved.append(job)
            del remaining[job]

    return resolved

