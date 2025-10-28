"""Unit tests for the GitHubStatusReporter helper."""

from __future__ import annotations

from typing import Any, Callable

import pytest

from popsicle.github import GitHubStatusReporter


class DummyResponse:
    def __init__(self, status_code: int = 201, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class DummySession:
    def __init__(self, response_factory: Callable[[], DummyResponse] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._factory = response_factory or (lambda: DummyResponse())

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> DummyResponse:
        self.calls.append({
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        })
        return self._factory()


def test_missing_token_skips_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    session = DummySession()
    reporter = GitHubStatusReporter(token=None, session=session)

    result = reporter.report_pending("owner/repo", "abcdef", 42)

    assert result is False
    assert session.calls == []


def test_successful_post_uses_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token123")
    session = DummySession()
    reporter = GitHubStatusReporter(
        session=session,
        context="ci/test",
        target_url_builder=lambda pipeline_id: f"http://localhost:5000/ui/pipelines/{pipeline_id}",
    )

    result = reporter.report_success("acme/widget", "deadbeef", 7, description="All jobs green")

    assert result is True
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "https://api.github.com/repos/acme/widget/statuses/deadbeef"
    assert call["timeout"] == 10
    assert call["headers"] == {
        "Authorization": "token token123",
        "Accept": "application/vnd.github+json",
    }
    assert call["json"] == {
        "state": "success",
        "context": "ci/test",
        "description": "All jobs green",
        "target_url": "http://localhost:5000/ui/pipelines/7",
    }


def test_http_error_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token123")

    def _fail() -> DummyResponse:
        return DummyResponse(status_code=403, text="forbidden")

    session = DummySession(response_factory=_fail)
    reporter = GitHubStatusReporter(session=session)

    result = reporter.report_failure("acme/widget", "deadbeef", 3, description="Tests failed")

    assert result is False
    assert session.calls[0]["json"]["state"] == "failure"
