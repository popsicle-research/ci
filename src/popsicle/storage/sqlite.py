"""SQLite storage helpers for pipeline and job persistence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_DB_PATH = Path("data") / "popsicle.db"


@dataclass(frozen=True)
class PipelineRecord:
    """Lightweight representation of a pipeline row."""

    id: int
    repo: str
    commit_sha: str
    branch: str
    workflow_name: str
    config_path: Optional[str]
    status: str
    start_time: Optional[str]
    end_time: Optional[str]


@dataclass(frozen=True)
class JobRecord:
    """Lightweight representation of a job row."""

    id: int
    pipeline_id: int
    job_name: str
    status: str
    start_time: Optional[str]
    end_time: Optional[str]
    log: Optional[str]


@dataclass(frozen=True)
class RunnerRecord:
    """Representation of a configured runner host."""

    id: int
    host: str
    active: bool


@dataclass(frozen=True)
class ProjectSummary:
    """Aggregate representation of a repository's pipeline activity."""

    repo: str
    total_pipelines: int
    last_run_at: Optional[str]
    last_status: Optional[str]


class SQLiteStore:
    """Small wrapper around sqlite3 for pipeline persistence."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        if self.db_path != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pipelines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repo TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    workflow_name TEXT NOT NULL DEFAULT 'default',
                    config_path TEXT,
                    status TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pipeline_id INTEGER NOT NULL,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    start_time TEXT,
                    end_time TEXT,
                    log TEXT,
                    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS runners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_pipelines_repo
                    ON pipelines (repo);
                CREATE INDEX IF NOT EXISTS idx_pipelines_repo_branch
                    ON pipelines (repo, branch);
                CREATE INDEX IF NOT EXISTS idx_pipelines_repo_start
                    ON pipelines (repo, start_time DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_pipeline
                    ON jobs (pipeline_id);
                """
            )
            self._ensure_column(
                conn,
                "pipelines",
                "workflow_name",
                "TEXT NOT NULL DEFAULT 'default'",
            )
            self._ensure_column(
                conn,
                "pipelines",
                "config_path",
                "TEXT",
            )

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        existing_columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _utc_now(self) -> str:
        return (
            datetime.now(tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

    def _row_to_pipeline(self, row: sqlite3.Row | None) -> Optional[PipelineRecord]:
        if row is None:
            return None
        keys = set(row.keys())
        return PipelineRecord(
            id=row["id"],
            repo=row["repo"],
            commit_sha=row["commit_sha"],
            branch=row["branch"],
            workflow_name=row["workflow_name"] if "workflow_name" in keys else "default",
            config_path=row["config_path"] if "config_path" in keys else None,
            status=row["status"],
            start_time=row["start_time"],
            end_time=row["end_time"],
        )

    def _row_to_job(self, row: sqlite3.Row | None) -> Optional[JobRecord]:
        if row is None:
            return None
        return JobRecord(
            id=row["id"],
            pipeline_id=row["pipeline_id"],
            job_name=row["job_name"],
            status=row["status"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            log=row["log"],
        )

    def _row_to_runner(self, row: sqlite3.Row | None) -> Optional[RunnerRecord]:
        if row is None:
            return None
        return RunnerRecord(
            id=row["id"],
            host=row["host"],
            active=bool(row["active"]),
        )

    def create_pipeline(
        self,
        repo: str,
        commit_sha: str,
        branch: str,
        workflow_name: str = "default",
        config_path: str | None = None,
        status: str = "pending",
        start_time: Optional[str] = None,
    ) -> int:
        start = start_time or self._utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pipelines (repo, commit_sha, branch, workflow_name, config_path, status, start_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (repo, commit_sha, branch, workflow_name, config_path, status, start),
            )
            return int(cursor.lastrowid)

    def update_pipeline_status(
        self, pipeline_id: int, status: str, end_time: Optional[str] = None
    ) -> None:
        end = end_time or (
            self._utc_now() if status in {"success", "failure"} else None
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pipelines
                   SET status = ?,
                       end_time = COALESCE(?, end_time)
                 WHERE id = ?
                """,
                (status, end, pipeline_id),
            )

    def get_pipeline(self, pipeline_id: int) -> Optional[PipelineRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pipelines WHERE id = ?",
                (pipeline_id,),
            ).fetchone()
        return self._row_to_pipeline(row)

    def get_recent_pipelines(self, limit: int = 10) -> List[PipelineRecord]:
        if limit <= 0:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pipelines
                ORDER BY start_time DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_pipeline(row) for row in rows if row is not None]

    def get_project_summaries(self, *, sort: str = "recent") -> List[ProjectSummary]:
        """Return project-level aggregates for all repositories."""

        order_by = "last_run_at DESC, repo" if sort != "name" else "repo ASC"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    p.repo AS repo,
                    COUNT(*) AS total_pipelines,
                    MAX(p.start_time) AS last_run_at,
                    (
                        SELECT p2.status
                          FROM pipelines AS p2
                         WHERE p2.repo = p.repo
                         ORDER BY p2.start_time DESC, p2.id DESC
                         LIMIT 1
                    ) AS last_status
                  FROM pipelines AS p
              GROUP BY p.repo
              ORDER BY {order_by}
                """
            ).fetchall()

        return [
            ProjectSummary(
                repo=row["repo"],
                total_pipelines=int(row["total_pipelines"] or 0),
                last_run_at=row["last_run_at"],
                last_status=row["last_status"],
            )
            for row in rows
        ]

    def list_pipelines_by_repo(
        self,
        repo: str,
        *,
        status: Optional[str] = None,
        branch: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[PipelineRecord]:
        if limit <= 0:
            return []

        query = ["SELECT * FROM pipelines WHERE repo = ?"]
        params: List[Any] = [repo]
        if status:
            query.append("AND status = ?")
            params.append(status)
        if branch:
            query.append("AND branch = ?")
            params.append(branch)
        query.append("ORDER BY start_time DESC, id DESC LIMIT ? OFFSET ?")
        params.extend([limit, max(offset, 0)])

        with self._connect() as conn:
            rows = conn.execute(" ".join(query), params).fetchall()
        return [self._row_to_pipeline(row) for row in rows if row is not None]

    def count_pipelines_by_repo(
        self,
        repo: str,
        *,
        status: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> int:
        query = ["SELECT COUNT(*) FROM pipelines WHERE repo = ?"]
        params: List[Any] = [repo]
        if status:
            query.append("AND status = ?")
            params.append(status)
        if branch:
            query.append("AND branch = ?")
            params.append(branch)

        with self._connect() as conn:
            row = conn.execute(" ".join(query), params).fetchone()
        return int(row[0] if row is not None else 0)

    def list_distinct_branches(self, repo: str) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT branch
                  FROM pipelines
                 WHERE repo = ?
              ORDER BY branch COLLATE NOCASE
                """,
                (repo,),
            ).fetchall()
        return [row["branch"] for row in rows if row is not None]

    def create_job(
        self,
        pipeline_id: int,
        job_name: str,
        status: str = "pending",
        start_time: Optional[str] = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (pipeline_id, job_name, status, start_time)
                VALUES (?, ?, ?, ?)
                """,
                (pipeline_id, job_name, status, start_time),
            )
            return int(cursor.lastrowid)

    def update_job_status(
        self,
        job_id: int,
        status: str,
        end_time: Optional[str] = None,
        start_time: Optional[str] = None,
    ) -> None:
        params: List[Any] = [status]
        set_clauses = ["status = ?"]
        if start_time is not None:
            set_clauses.append("start_time = ?")
            params.append(start_time)
        if end_time is None and status in {"success", "failed", "failure"}:
            end_time = self._utc_now()
        if end_time is not None:
            set_clauses.append("end_time = ?")
            params.append(end_time)
        params.append(job_id)
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE jobs
                   SET {', '.join(set_clauses)}
                 WHERE id = ?
                """,
                params,
            )

    def set_job_log(self, job_id: int, log: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET log = ? WHERE id = ?",
                (log, job_id),
            )

    def append_job_log(self, job_id: int, text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                   SET log = COALESCE(log, '') || ?
                 WHERE id = ?
                """,
                (text, job_id),
            )

    def get_jobs_for_pipeline(self, pipeline_id: int) -> List[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE pipeline_id = ? ORDER BY id",
                (pipeline_id,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows if row is not None]

    def get_job(self, job_id: int) -> Optional[JobRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._row_to_job(row)

    def add_runner(self, host: str, active: bool = True) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runners (host, active) VALUES (?, ?)",
                (host, int(active)),
            )
            return int(cursor.lastrowid)

    def set_runner_active(self, runner_id: int, active: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET active = ? WHERE id = ?",
                (int(active), runner_id),
            )

    def list_runners(self) -> List[RunnerRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runners ORDER BY id").fetchall()
        return [self._row_to_runner(row) for row in rows if row is not None]

    def as_dict(
        self, record: PipelineRecord | JobRecord | RunnerRecord
    ) -> Dict[str, Any]:
        return asdict(record)
