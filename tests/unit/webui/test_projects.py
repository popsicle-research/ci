from __future__ import annotations

from flask import Flask

from fmg.storage.sqlite import SQLiteStore


def test_projects_page_lists_repositories(
    app_and_store: tuple[Flask, SQLiteStore],
) -> None:
    app, store = app_and_store

    first = store.create_pipeline(
        repo="alpha/repo",
        commit_sha="1111111",
        branch="main",
        start_time="2024-01-01T12:00:00Z",
    )
    store.update_pipeline_status(first, "success", end_time="2024-01-01T12:10:00Z")

    second = store.create_pipeline(
        repo="alpha/repo",
        commit_sha="2222222",
        branch="main",
        start_time="2024-01-02T12:00:00Z",
    )
    store.update_pipeline_status(second, "success", end_time="2024-01-02T12:05:00Z")

    latest = store.create_pipeline(
        repo="beta/repo",
        commit_sha="3333333",
        branch="develop",
        start_time="2024-02-01T08:00:00Z",
    )
    store.update_pipeline_status(latest, "failure", end_time="2024-02-01T08:20:00Z")

    client = app.test_client()
    response = client.get("/ui/projects")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert "alpha/repo" in html
    assert "beta/repo" in html
    assert 'Total pipelines: <span class="font-medium text-slate-800">2</span>' in html
    assert "Failure" in html

    # Default ordering should list the most recent project first (beta/repo)
    assert html.index("beta/repo") < html.index("alpha/repo")


def test_projects_page_sort_by_name(app_and_store: tuple[Flask, SQLiteStore]) -> None:
    app, store = app_and_store
    store.create_pipeline(
        repo="zeta/repo",
        commit_sha="aaaaaaa",
        branch="main",
        start_time="2024-01-01T00:00:00Z",
    )
    store.create_pipeline(
        repo="beta/repo",
        commit_sha="bbbbbbb",
        branch="main",
        start_time="2024-01-02T00:00:00Z",
    )

    client = app.test_client()
    response = client.get("/ui/projects?sort=name")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert html.index("beta/repo") < html.index("zeta/repo")


def test_projects_page_empty_state(app_and_store: tuple[Flask, SQLiteStore]) -> None:
    app, _ = app_and_store
    client = app.test_client()
    response = client.get("/ui/projects")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Nothing to show yet" in html
    assert "Trigger a pipeline" in html
