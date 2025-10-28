"""Formatting helpers for presenting pipeline metadata in the Web UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

STATUS_BADGE_MAP = {
    "success": "bg-green-100 text-green-800",
    "failure": "bg-red-100 text-red-800",
    "failed": "bg-red-100 text-red-800",
    "running": "bg-amber-100 text-amber-800",
    "pending": "bg-gray-100 text-gray-800",
}


def _coerce_iso_to_datetime(value: str) -> datetime:
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_timestamp(value: Optional[str]) -> str:
    """Convert an ISO8601 timestamp to a human friendly string."""

    if not value:
        return "—"
    try:
        dt = _coerce_iso_to_datetime(value)
    except ValueError:
        return value
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_duration(start: Optional[str], end: Optional[str]) -> str:
    """Return the elapsed duration between two ISO8601 timestamps."""

    if not start or not end:
        return "—"
    try:
        start_dt = _coerce_iso_to_datetime(start)
        end_dt = _coerce_iso_to_datetime(end)
    except ValueError:
        return "—"
    delta = end_dt - start_dt
    if delta.total_seconds() < 0:
        return "—"
    seconds = int(delta.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes:d}m {seconds:02d}s"
    return f"{seconds:d}s"


def status_badge_class(status: Optional[str]) -> str:
    """Return Tailwind classes for a pipeline status."""

    if not status:
        return "bg-gray-100 text-gray-800"
    return STATUS_BADGE_MAP.get(status.lower(), "bg-gray-100 text-gray-800")


def humanize_status(status: Optional[str]) -> str:
    if not status:
        return "Unknown"
    return status.replace("_", " ").title()


def short_sha(sha: Optional[str]) -> str:
    if not sha:
        return "—"
    return sha[:7]
