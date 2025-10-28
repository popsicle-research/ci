import sqlite3
from pathlib import Path

from popsicle.storage import SQLiteStore


def test_initializes_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    SQLiteStore(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {"pipelines", "jobs", "runners"}.issubset(tables)


def test_pipeline_and_job_lifecycle(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "store.db")

    pipeline_id = store.create_pipeline(
        repo="owner/repo",
        commit_sha="abc123",
        branch="main",
        status="pending",
        start_time="2023-01-01T00:00:00Z",
    )

    store.update_pipeline_status(pipeline_id, "running")
    pipeline = store.get_pipeline(pipeline_id)
    assert pipeline is not None
    assert pipeline.status == "running"

    job_id = store.create_job(pipeline_id, "build")
    store.update_job_status(
        job_id,
        status="running",
        start_time="2023-01-01T00:05:00Z",
    )
    store.append_job_log(job_id, "first line\n")
    store.append_job_log(job_id, "second line\n")
    store.set_job_log(job_id, "overwritten log\n")
    store.update_job_status(
        job_id,
        status="success",
        end_time="2023-01-01T00:10:00Z",
    )

    job = store.get_job(job_id)
    assert job is not None
    assert job.status == "success"
    assert job.log == "overwritten log\n"

    jobs = store.get_jobs_for_pipeline(pipeline_id)
    assert [j.id for j in jobs] == [job_id]

    recent = store.get_recent_pipelines(limit=1)
    assert [p.id for p in recent] == [pipeline_id]


def test_runner_management(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "store.db")

    runner_id = store.add_runner("localhost")
    store.set_runner_active(runner_id, False)

    runners = store.list_runners()
    assert len(runners) == 1
    assert runners[0].host == "localhost"
    assert runners[0].active is False
