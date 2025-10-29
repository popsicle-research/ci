from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from popsicle.pipelines import (
    CONFIG_DIRECTORY,
    CONFIG_RELATIVE_PATH,
    JobSpec,
    PipelineConfigError,
    StepSpec,
    discover_pipeline_configs,
    load_pipeline_config,
)


def write_config(
    tmp_path: Path,
    content: str,
    *,
    relative_path: Path | None = None,
) -> Path:
    target_relative = relative_path or CONFIG_RELATIVE_PATH
    config_path = tmp_path / target_relative
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")
    return config_path


def test_load_single_job_pipeline(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
        version: 2.1
        jobs:
          build:
            docker:
              - image: python:3.11
            steps:
              - checkout
              - run: echo "hello"
        """,
    )

    config = load_pipeline_config(tmp_path)

    assert config.name == "ci"
    assert config.config_path == CONFIG_RELATIVE_PATH
    assert list(config.job_order) == ["build"]
    job = config.jobs["build"]
    assert isinstance(job, JobSpec)
    assert job.image == "python:3.11"
    assert job.steps[0] == StepSpec(kind="checkout")
    assert job.steps[1] == StepSpec(kind="run", command='echo "hello"')


def test_load_workflow_with_dependencies(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
        version: 2.1
        jobs:
          build:
            docker:
              - image: python:3.11
            steps:
              - run: make build
          test:
            docker:
              - image: python:3.11
            steps:
              - run: make test
        workflows:
          version: 2
          pipeline:
            jobs:
              - build
              - test:
                  requires:
                    - build
        """,
    )

    config = load_pipeline_config(tmp_path)

    assert config.name == "pipeline"
    assert list(config.job_order) == ["build", "test"]
    assert list(config.dependencies["test"]) == ["build"]


def test_workflow_name_defaults_to_file_stem(tmp_path: Path) -> None:
    custom_relative = CONFIG_DIRECTORY / "lint.yml"
    write_config(
        tmp_path,
        """
        version: 2.1
        jobs:
          lint:
            docker:
              - image: python:3.11
            steps:
              - run: echo lint
        """,
        relative_path=custom_relative,
    )

    config = load_pipeline_config(tmp_path, custom_relative)
    assert config.name == "lint"


def test_discover_pipeline_configs_handles_multiple_files(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
        version: 2.1
        jobs:
          build:
            docker:
              - image: python:3.11
            steps:
              - run: echo build
        workflows:
          version: 2
          build_flow:
            jobs:
              - build
        """,
    )
    write_config(
        tmp_path,
        """
        version: 2.1
        jobs:
          test:
            docker:
              - image: python:3.11
            steps:
              - run: echo test
        workflows:
          version: 2
          test_flow:
            jobs:
              - test
        """,
        relative_path=CONFIG_DIRECTORY / "test_a.yml",
    )
    write_config(
        tmp_path,
        """
        version: 2.1
        jobs:
          deploy:
            docker:
              - image: python:3.11
            steps:
              - run: echo deploy
        workflows:
          version: 2
          deploy_flow:
            jobs:
              - deploy
        """,
        relative_path=CONFIG_DIRECTORY / "test_b.yaml",
    )

    configs = discover_pipeline_configs(tmp_path)

    names = [config.name for config in configs]
    paths = [config.config_path.name for config in configs]
    assert names == ["build_flow", "test_flow", "deploy_flow"]
    assert paths == ["ci.yml", "test_a.yml", "test_b.yaml"]


@pytest.mark.parametrize(
    "config_body, expected_message",
    [
        ("{}", "must define at least one job"),
        (
            """
            jobs:
              build: []
            """,
            "Job 'build' must be a mapping",
        ),
        (
            """
            jobs:
              build:
                docker: []
            """,
            "must declare at least one docker image",
        ),
        (
            """
            jobs:
              build:
                docker:
                  - image: python:3.11
                steps: []
            """,
            "must define at least one step",
        ),
    ],
)
def test_invalid_configurations_raise_error(
    tmp_path: Path, config_body: str, expected_message: str
) -> None:
    write_config(tmp_path, config_body)

    with pytest.raises(PipelineConfigError) as exc:
        load_pipeline_config(tmp_path)

    assert expected_message in str(exc.value)


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PipelineConfigError) as exc:
        load_pipeline_config(tmp_path)

    assert "not found" in str(exc.value)
