from pathlib import Path

import pytest

from fmg.pipelines import (
    CONFIG_RELATIVE_PATH,
    JobSpec,
    PipelineConfigError,
    StepSpec,
    load_pipeline_config,
)


def write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / CONFIG_RELATIVE_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")
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

    assert list(config.job_order) == ["build", "test"]
    assert config.dependencies["test"] == ["build"]


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

