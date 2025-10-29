"""Helpers for posting commit statuses to GitHub."""

from __future__ import annotations

import logging
import os
from typing import Callable

import requests

LOGGER = logging.getLogger(__name__)

TargetURLBuilder = Callable[[int], str | None]

def build_pipeline_url(pipeline_id: int) -> str:
    return f"http://127.0.0.1:5000/ui/pipelines/{pipeline_id}"

class GitHubStatusReporter:
    """Post commit statuses to GitHub's Status API."""

    def __init__(
        self,
        *,
        token: str | None = None,
        context: str = "popsicle/ci",
        api_base_url: str = "https://api.github.com",
        session: requests.Session | None = None,
        target_url_builder: TargetURLBuilder | None = None,
    ) -> None:
        self._token = token or os.getenv("GITHUB_TOKEN")
        self._context = context
        self._api_base = api_base_url.rstrip("/")
        self._session = session or requests.Session()
        self._target_url_builder = build_pipeline_url

    def report_pending(
        self,
        repo: str,
        commit_sha: str,
        pipeline_id: int,
        *,
        description: str = "Pipeline is running",
        target_url: str | None = None,
        context: str | None = None,
    ) -> bool:
        return self._post_status(
            repo,
            commit_sha,
            "pending",
            description,
            pipeline_id=pipeline_id,
            explicit_target_url=target_url,
            context=context,
        )

    def report_success(
        self,
        repo: str,
        commit_sha: str,
        pipeline_id: int,
        *,
        description: str = "Pipeline succeeded",
        target_url: str | None = None,
        context: str | None = None,
    ) -> bool:
        return self._post_status(
            repo,
            commit_sha,
            "success",
            description,
            pipeline_id=pipeline_id,
            explicit_target_url=target_url,
            context=context,
        )

    def report_failure(
        self,
        repo: str,
        commit_sha: str,
        pipeline_id: int,
        *,
        description: str = "Pipeline failed",
        target_url: str | None = None,
        context: str | None = None,
    ) -> bool:
        return self._post_status(
            repo,
            commit_sha,
            "failure",
            description,
            pipeline_id=pipeline_id,
            explicit_target_url=target_url,
            context=context,
        )

    def _post_status(
        self,
        repo: str,
        commit_sha: str,
        state: str,
        description: str,
        *,
        pipeline_id: int,
        explicit_target_url: str | None,
        context: str | None,
    ) -> bool:
        token = self._token
        if not token:
            LOGGER.info(
                "Skipping GitHub status update for %s@%s because no token is configured",
                repo,
                commit_sha,
            )
            return False

        url = f"{self._api_base}/repos/{repo}/statuses/{commit_sha}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }

        target_url = explicit_target_url
        if target_url is None and self._target_url_builder is not None:
            target_url = self._target_url_builder(pipeline_id)

        payload = {
            "state": state,
            "context": context or self._context,
            "description": description,
        }
        if target_url:
            payload["target_url"] = target_url

        try:
            response = self._session.post(url, json=payload, headers=headers, timeout=10)
        except requests.RequestException as exc:
            LOGGER.warning(
                "Failed to send GitHub status for %s@%s: %s", repo, commit_sha, exc
            )
            return False

        if response.status_code >= 400:
            LOGGER.warning(
                "GitHub status API responded with %s for %s@%s: %s",
                response.status_code,
                repo,
                commit_sha,
                response.text,
            )
            return False

        LOGGER.debug(
            "Reported %s status for %s@%s with payload %s",
            state,
            repo,
            commit_sha,
            payload,
        )
        return True
