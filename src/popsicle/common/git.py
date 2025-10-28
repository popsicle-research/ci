"""Git helper utilities used by the webhook and orchestrator flows."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class GitCloneError(RuntimeError):
    """Raised when cloning a repository fails."""


def clone_repository(
    clone_url: str,
    destination: Path,
    commit_sha: str,
    *,
    branch: str | None = None,
    token: str | None = None,
) -> None:
    """Clone a repository to the destination and check out the desired commit.

    Args:
        clone_url: HTTPS URL for the repository to clone.
        destination: Path on disk where the repository should be cloned.
        commit_sha: Commit SHA to checkout after cloning.
        branch: Optional branch ref to optimise the fetch.
        token: Optional GitHub token for private repositories.

    Raises:
        GitCloneError: If the clone or checkout command fails.
    """

    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    repo_url = _inject_token(clone_url, token)

    clone_cmd = ["git", "clone", "--no-checkout", repo_url, str(destination)]
    if branch:
        clone_cmd.insert(3, "--branch")
        clone_cmd.insert(4, branch)

    LOGGER.info("Cloning repository %s into %s", clone_url, destination)
    _run_command(clone_cmd, "Failed to clone repository", secret=token)

    checkout_cmd = ["git", "-C", str(destination), "checkout", commit_sha]
    LOGGER.info("Checking out commit %s", commit_sha)
    _run_command(checkout_cmd, "Failed to checkout commit", secret=token)


def _run_command(command: list[str], error_message: str, *, secret: str | None = None) -> None:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        stdout = _mask_secret(exc.stdout, secret)
        stderr = _mask_secret(exc.stderr, secret)
        details = "; ".join(part for part in (stdout, stderr) if part)
        raise GitCloneError(f"{error_message}: {details}" or error_message) from exc


def _mask_secret(value: str | None, secret: str | None) -> str:
    text = (value or "").strip()
    if secret:
        text = text.replace(secret, "***")
    return text


def _inject_token(clone_url: str, token: str | None) -> str:
    if not token or "@" in clone_url:
        return clone_url
    if clone_url.startswith("https://"):
        prefix, remainder = clone_url.split("://", 1)
        sanitized_token = os.environ.get("POPSICLE_GITHUB_TOKEN", token)
        return f"{prefix}://{sanitized_token}@{remainder}"
    return clone_url
