from __future__ import annotations

from flask import Flask

from popsicle.storage.sqlite import SQLiteStore


def _seed_pipeline(store: SQLiteStore) -> int:
    pipeline_id = store.create_pipeline(
        repo="demo/repo",
        commit_sha="abcdef1",
        branch="main",
        workflow_name="main_flow",
        config_path=".popsicle/ci.yml",
        start_time="2024-03-01T10:00:00Z",
    )
    store.update_pipeline_status(
        pipeline_id, "success", end_time="2024-03-01T10:05:00Z"
    )
    return pipeline_id


def test_pipeline_details_renders_jobs(
    app_and_store: tuple[Flask, SQLiteStore],
) -> None:
    app, store = app_and_store
    pipeline_id = _seed_pipeline(store)

    build_job = store.create_job(pipeline_id, "build")
    store.update_job_status(
        build_job,
        "success",
        start_time="2024-03-01T10:00:30Z",
        end_time="2024-03-01T10:01:00Z",
    )
    store.set_job_log(
        build_job, "Build started\n<script>alert('x')</script>\nBuild finished"
    )

    test_job = store.create_job(pipeline_id, "test")
    store.update_job_status(
        test_job,
        "failure",
        start_time="2024-03-01T10:01:10Z",
        end_time="2024-03-01T10:02:00Z",
    )
    store.set_job_log(test_job, "Tests failed")

    client = app.test_client()
    response = client.get(f"/ui/pipelines/{pipeline_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "Pipeline #" in html
    assert "demo/repo" in html
    assert "main_flow" in html
    assert "popsicle/ci: main_flow" in html
    assert ".popsicle/ci.yml" in html
    assert "Tests failed" in html
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in html
    assert "Copy log" in html


def test_pipeline_log_truncation_and_download(
    app_and_store: tuple[Flask, SQLiteStore],
) -> None:
    app, store = app_and_store
    pipeline_id = _seed_pipeline(store)
    large_log = "line\n" * 600000  # ~3MB

    job_id = store.create_job(pipeline_id, "deploy")
    store.update_job_status(job_id, "failure")
    store.set_job_log(job_id, large_log)

    client = app.test_client()
    response = client.get(f"/ui/pipelines/{pipeline_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Showing first 2000 lines" in html
    assert "… truncated …" in html

    download = client.get(f"/ui/pipelines/{pipeline_id}/jobs/{job_id}/download")
    assert download.status_code == 200
    assert download.mimetype == "text/plain"
    assert download.headers["Content-Disposition"].startswith(
        f"attachment; filename=pipeline-{pipeline_id}-job-{job_id}.log"
    )
    download_text = download.get_data(as_text=True)
    assert download_text.startswith("line\n")
    assert len(download_text) == len(large_log)


def test_pipeline_details_not_found(app_and_store: tuple[Flask, SQLiteStore]) -> None:
    app, _ = app_and_store
    response = app.test_client().get("/ui/pipelines/999")
    assert response.status_code == 404
    assert "Pipeline not found" in response.get_data(as_text=True)


def test_download_job_mismatch_returns_404(
    app_and_store: tuple[Flask, SQLiteStore],
) -> None:
    app, store = app_and_store
    pipeline_one = _seed_pipeline(store)
    pipeline_two = store.create_pipeline(
        repo="demo/repo",
        commit_sha="abcdef2",
        branch="feature",
        workflow_name="feature_flow",
        config_path=".popsicle/feature.yml",
        start_time="2024-03-02T10:00:00Z",
    )
    job_in_other_pipeline = store.create_job(pipeline_two, "lint")
    store.update_job_status(job_in_other_pipeline, "success")

    response = app.test_client().get(
        f"/ui/pipelines/{pipeline_one}/jobs/{job_in_other_pipeline}/download"
    )
    assert response.status_code == 404


def test_retry_pipeline_creates_new_run(
    app_and_store: tuple[Flask, SQLiteStore],
) -> None:
    app, store = app_and_store
    pipeline_id = store.create_pipeline(
        repo="demo/repo",
        commit_sha="abcdef1",
        branch="main",
        workflow_name="rerun_flow",
        config_path=".popsicle/ci.yml",
        start_time="2024-03-01T12:00:00Z",
    )
    store.update_pipeline_status(pipeline_id, "failure", end_time="2024-03-01T12:05:00Z")
    store.create_job(pipeline_id, "build")

    client = app.test_client()
    response = client.post(f"/ui/pipelines/{pipeline_id}/retry")
    assert response.status_code == 302

    orchestrator = app.config["TEST_ORCHESTRATOR"]
    assert orchestrator.calls, "Expected orchestrator to be invoked"
    new_pipeline_id, config, workspace = orchestrator.calls[0]
    assert new_pipeline_id != pipeline_id
    assert config.name == "rerun_flow"
    assert workspace.exists()

    clone_calls = app.config["TEST_CLONE_CALLS"]
    assert clone_calls
    clone_url, _, commit_sha, branch = clone_calls[0]
    assert clone_url.startswith("https://github.com/")
    assert commit_sha == "abcdef1"
    assert branch == "main"

    reporter = app.config["TEST_REPORTER"]
    assert any(call[2] == new_pipeline_id for call in reporter.pending)

    new_pipeline = store.get_pipeline(new_pipeline_id)
    assert new_pipeline is not None
    assert new_pipeline.repo == "demo/repo"
    assert new_pipeline.config_path == ".popsicle/ci.yml"

    jobs = store.get_jobs_for_pipeline(new_pipeline_id)
    assert [job.job_name for job in jobs] == ["build"]

    expected_redirect = f"/ui/pipelines/{new_pipeline_id}"
    assert response.headers["Location"].endswith(expected_redirect)


def test_retry_pipeline_rejects_running_pipeline(
    app_and_store: tuple[Flask, SQLiteStore],
) -> None:
    app, store = app_and_store
    pipeline_id = store.create_pipeline(
        repo="demo/repo",
        commit_sha="abcdef2",
        branch="main",
        workflow_name="rerun_flow",
        config_path=".popsicle/ci.yml",
        start_time="2024-03-02T10:00:00Z",
    )
    store.update_pipeline_status(pipeline_id, "running")

    client = app.test_client()
    response = client.post(
        f"/ui/pipelines/{pipeline_id}/retry", follow_redirects=True
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Wait until it finishes" in html

    orchestrator = app.config["TEST_ORCHESTRATOR"]
    assert not orchestrator.calls
