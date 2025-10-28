from __future__ import annotations

from flask import Flask

from fmg.storage.sqlite import SQLiteStore


def _create_pipeline(
    store: SQLiteStore,
    *,
    repo: str,
    branch: str,
    status: str,
    start_time: str,
    commit: str,
) -> int:
    pipeline_id = store.create_pipeline(
        repo=repo,
        commit_sha=commit,
        branch=branch,
        start_time=start_time,
    )
    store.update_pipeline_status(
        pipeline_id,
        status,
        end_time="2024-01-02T00:00:00Z",
    )
    return pipeline_id


def test_pipeline_list_filters(app_and_store: tuple[Flask, SQLiteStore]) -> None:
    app, store = app_and_store
    _create_pipeline(
        store,
        repo="octo/repo",
        branch="main",
        status="success",
        start_time="2024-01-02T10:00:00Z",
        commit="aaaaaaa",
    )
    _create_pipeline(
        store,
        repo="octo/repo",
        branch="develop",
        status="failure",
        start_time="2024-01-03T10:00:00Z",
        commit="bbbbbbb",
    )
    _create_pipeline(
        store,
        repo="octo/repo",
        branch="develop",
        status="running",
        start_time="2024-01-04T10:00:00Z",
        commit="ccccccc",
    )

    client = app.test_client()

    response = client.get("/ui/projects/octo/repo")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "#" in html
    assert "Page 1" in html

    response = client.get("/ui/projects/octo/repo?status=failure")
    html = response.get_data(as_text=True)
    assert "Failure" in html
    assert "Running</span>" not in html

    response = client.get("/ui/projects/octo/repo?branch=main")
    html = response.get_data(as_text=True)
    assert "main" in html
    assert ">develop</td>" not in html

    response = client.get("/ui/projects/octo/repo?status=running&branch=develop")
    html = response.get_data(as_text=True)
    assert "Running" in html
    assert "Failure</span>" not in html

    assert "View details" in html


def test_pipeline_list_pagination(app_and_store: tuple[Flask, SQLiteStore]) -> None:
    app, store = app_and_store

    for index in range(25):
        _create_pipeline(
            store,
            repo="acme/repo",
            branch="main",
            status="success",
            start_time=f"2024-01-{25 - index:02d}T12:00:00Z",
            commit=f"commit{index:02d}",
        )

    client = app.test_client()
    first_page = client.get("/ui/projects/acme/repo?per_page=10&page=1")
    assert first_page.status_code == 200
    html_first = first_page.get_data(as_text=True)
    assert "Page 1" in html_first
    assert "commit00" in html_first
    assert "commit10" not in html_first

    second_page = client.get("/ui/projects/acme/repo?per_page=10&page=2")
    assert second_page.status_code == 200
    html_second = second_page.get_data(as_text=True)
    assert "Page 2" in html_second
    assert "commit10" in html_second
    assert "commit00" not in html_second

    last_page = client.get("/ui/projects/acme/repo?per_page=10&page=5")
    assert last_page.status_code == 200
    html_last = last_page.get_data(as_text=True)
    # Page greater than total pages should clamp to final page (3)
    assert "Page 3" in html_last
    assert "commit24" in html_last
