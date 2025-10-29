"""Pipeline configuration parser for CircleCI-style YAML files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import yaml


CONFIG_DIRECTORY = Path(".popsicle")
DEFAULT_CONFIG_FILENAME = "ci.yml"
CONFIG_RELATIVE_PATH = CONFIG_DIRECTORY / DEFAULT_CONFIG_FILENAME
CONFIG_GLOB_PATTERNS = ("*.yml", "*.yaml")


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

    name: str
    config_path: Path
    jobs: Mapping[str, JobSpec]
    job_order: Sequence[str]
    dependencies: Mapping[str, Sequence[str]]


def list_pipeline_config_paths(repo_root: Path) -> Sequence[Path]:
    """Return all pipeline configuration file paths relative to ``repo_root``."""

    config_dir = repo_root / CONFIG_DIRECTORY
    if not config_dir.exists():
        raise PipelineConfigError("Pipeline configuration directory '.popsicle' not found")

    candidate_paths: List[Path] = []
    for pattern in CONFIG_GLOB_PATTERNS:
        candidate_paths.extend(config_dir.glob(pattern))

    config_files = sorted(path for path in candidate_paths if path.is_file())
    if not config_files:
        raise PipelineConfigError("No pipeline configuration files found under '.popsicle'")

    seen: set[Path] = set()
    relative_paths: List[Path] = []
    for absolute_path in config_files:
        relative_path = absolute_path.relative_to(repo_root)
        if relative_path in seen:
            continue
        seen.add(relative_path)
        relative_paths.append(relative_path)
    return relative_paths


def discover_pipeline_configs(repo_root: Path) -> Sequence[PipelineConfig]:
    """Locate and load all pipeline configuration files under ``.popsicle``."""

    relative_paths = list_pipeline_config_paths(repo_root)
    return [load_pipeline_config(repo_root, path) for path in relative_paths]


def load_pipeline_config(repo_root: Path, relative_path: Path | None = None) -> PipelineConfig:
    """Load and validate a single pipeline configuration file."""

    resolved_relative = relative_path or CONFIG_RELATIVE_PATH
    if resolved_relative.is_absolute():
        raise PipelineConfigError("Pipeline configuration path must be relative to the repository root")

    config_path = repo_root / resolved_relative
    if not config_path.exists():
        raise PipelineConfigError(
            f"Pipeline configuration file not found at {resolved_relative}"
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

    default_name = resolved_relative.stem or "pipeline"
    workflow_name, dependencies = _extract_workflow_details(
        data,
        jobs.keys(),
        default_name=default_name,
    )
    job_order = _topological_sort(jobs.keys(), dependencies)

    frozen_dependencies = {job: tuple(requires) for job, requires in dependencies.items()}
    return PipelineConfig(
        name=workflow_name,
        config_path=resolved_relative,
        jobs=jobs,
        job_order=tuple(job_order),
        dependencies=frozen_dependencies,
    )


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


def _extract_workflow_details(
    data: Mapping[str, object],
    job_names: Iterable[str],
    *,
    default_name: str,
) -> Tuple[str, Dict[str, List[str]]]:
    dependencies: Dict[str, List[str]] = {job: [] for job in job_names}

    workflows_section = data.get("workflows")
    if not isinstance(workflows_section, MutableMapping):
        return default_name, dependencies

    workflow_name: Optional[str] = None
    workflow_def: Optional[Mapping[str, object]] = None
    for key, value in workflows_section.items():
        if key == "version":
            continue
        if not isinstance(value, MutableMapping):
            raise PipelineConfigError("Workflow definition must be a mapping")
        workflow_name = key
        workflow_def = value
        break

    if workflow_def is None or workflow_name is None:
        return default_name, dependencies

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

    return workflow_name, dependencies


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
