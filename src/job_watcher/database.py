from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator

from .models import Job, Source, SourceResult, reasons_json


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"数据库版本 {current} 高于程序支持的 {SCHEMA_VERSION}")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_key TEXT PRIMARY KEY,
                    company TEXT NOT NULL,
                    track TEXT NOT NULL,
                    ats TEXT NOT NULL,
                    url TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 2,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    cities_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    last_status TEXT NOT NULL DEFAULT 'never',
                    last_error TEXT NOT NULL DEFAULT '',
                    last_checked_at TEXT
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    total_sources INTEGER NOT NULL DEFAULT 0,
                    ok_sources INTEGER NOT NULL DEFAULT 0,
                    job_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    updated_count INTEGER NOT NULL DEFAULT 0,
                    closed_count INTEGER NOT NULL DEFAULT 0,
                    error_summary TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS source_runs (
                    run_id INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    source_key TEXT NOT NULL REFERENCES sources(source_key) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    complete INTEGER NOT NULL DEFAULT 0,
                    job_count INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (run_id, source_key)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_key TEXT PRIMARY KEY,
                    source_key TEXT NOT NULL REFERENCES sources(source_key) ON DELETE CASCADE,
                    external_id TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL,
                    track TEXT NOT NULL,
                    title TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    department TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    raw_text TEXT NOT NULL DEFAULT '',
                    publish_date TEXT NOT NULL DEFAULT '',
                    deadline TEXT NOT NULL DEFAULT '',
                    job_type TEXT NOT NULL DEFAULT '',
                    campus_year TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    score_reasons_json TEXT NOT NULL DEFAULT '[]',
                    content_hash TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    closed_at TEXT,
                    miss_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    job_key TEXT NOT NULL REFERENCES jobs(job_key) ON DELETE CASCADE,
                    observed_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    UNIQUE (run_id, job_key)
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status_track_score
                    ON jobs(status, track, score DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_source_last_seen
                    ON jobs(source_key, last_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_observations_run_event
                    ON observations(run_id, event_type);
                """
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("PRAGMA optimize")

    def begin_run(self, total_sources: int) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runs(started_at, total_sources) VALUES (?, ?)",
                (utc_now(), total_sources),
            )
            return int(cursor.lastrowid)

    def upsert_source(self, source: Source) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sources(
                    source_key, company, track, ats, url, priority, enabled, cities_json, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    company=excluded.company,
                    track=excluded.track,
                    ats=excluded.ats,
                    url=excluded.url,
                    priority=excluded.priority,
                    enabled=excluded.enabled,
                    cities_json=excluded.cities_json,
                    notes=excluded.notes
                """,
                (
                    source.key,
                    source.company,
                    source.track,
                    source.ats,
                    source.url,
                    source.priority,
                    int(source.enabled),
                    json.dumps(source.cities, ensure_ascii=False),
                    source.notes,
                ),
            )

    def record_source_result(self, run_id: int, result: SourceResult) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO source_runs(
                    run_id, source_key, status, complete, job_count, duration_ms, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.source.key,
                    result.status,
                    int(result.complete),
                    len(result.jobs),
                    result.duration_ms,
                    result.error[:1000],
                ),
            )
            conn.execute(
                """
                UPDATE sources
                SET last_status=?, last_error=?, last_checked_at=?
                WHERE source_key=?
                """,
                (result.status, result.error[:1000], now, result.source.key),
            )

    def active_count_for_source(self, source_key: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE source_key=? AND status='active'",
                (source_key,),
            ).fetchone()
            return int(row["n"] or 0)

    def upsert_job(self, run_id: int, job: Job) -> str:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT content_hash, status FROM jobs WHERE job_key=?", (job.job_key,)
            ).fetchone()
            if existing is None:
                event = "NEW"
            elif existing["status"] == "closed":
                event = "REOPENED"
            elif existing["content_hash"] != job.content_hash:
                event = "UPDATED"
            else:
                event = "SEEN"

            conn.execute(
                """
                INSERT INTO jobs(
                    job_key, source_key, external_id, company, track, title, location,
                    department, description, raw_text, publish_date, deadline, job_type,
                    campus_year, url, score, score_reasons_json, content_hash,
                    first_seen_at, last_seen_at, status, closed_at, miss_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, 0)
                ON CONFLICT(job_key) DO UPDATE SET
                    external_id=excluded.external_id,
                    company=excluded.company,
                    track=excluded.track,
                    title=excluded.title,
                    location=excluded.location,
                    department=excluded.department,
                    description=excluded.description,
                    raw_text=excluded.raw_text,
                    publish_date=excluded.publish_date,
                    deadline=excluded.deadline,
                    job_type=excluded.job_type,
                    campus_year=excluded.campus_year,
                    url=excluded.url,
                    score=excluded.score,
                    score_reasons_json=excluded.score_reasons_json,
                    content_hash=excluded.content_hash,
                    last_seen_at=excluded.last_seen_at,
                    status='active',
                    closed_at=NULL,
                    miss_count=0
                """,
                (
                    job.job_key,
                    job.source_key,
                    job.external_id,
                    job.company,
                    job.track,
                    job.title,
                    job.location,
                    job.department,
                    job.description,
                    job.raw_text,
                    job.publish_date,
                    job.deadline,
                    job.job_type,
                    job.campus_year,
                    job.url,
                    job.score,
                    reasons_json(job.score_reasons),
                    job.content_hash,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO observations(
                    run_id, job_key, observed_at, content_hash, event_type
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, job.job_key, now, job.content_hash, event),
            )
            return event

    def mark_missing(self, run_id: int, source_key: str, seen_keys: Iterable[str]) -> int:
        seen = set(seen_keys)
        closed = 0
        now = utc_now()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT job_key, miss_count FROM jobs WHERE source_key=? AND status='active'",
                (source_key,),
            ).fetchall()
            for row in rows:
                if row["job_key"] in seen:
                    continue
                miss_count = int(row["miss_count"]) + 1
                if miss_count >= 2:
                    conn.execute(
                        "UPDATE jobs SET status='closed', closed_at=?, miss_count=? WHERE job_key=?",
                        (now, miss_count, row["job_key"]),
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO observations(
                            run_id, job_key, observed_at, content_hash, event_type
                        ) SELECT ?, job_key, ?, content_hash, 'CLOSED' FROM jobs WHERE job_key=?
                        """,
                        (run_id, now, row["job_key"]),
                    )
                    closed += 1
                else:
                    conn.execute(
                        "UPDATE jobs SET miss_count=? WHERE job_key=?",
                        (miss_count, row["job_key"]),
                    )
        return closed

    def finish_run(self, run_id: int, *, error_summary: str = "") -> None:
        with self.connect() as conn:
            counts = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status='success' AND complete=1 THEN 1 ELSE 0 END) AS ok_sources,
                    COUNT(*) AS finished_sources,
                    COALESCE(SUM(job_count), 0) AS job_count
                FROM source_runs WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            events = conn.execute(
                """
                SELECT event_type, COUNT(*) AS n
                FROM observations WHERE run_id=? GROUP BY event_type
                """,
                (run_id,),
            ).fetchall()
            event_counts = {row["event_type"]: row["n"] for row in events}
            ok_sources = int(counts["ok_sources"] or 0)
            finished_sources = int(counts["finished_sources"] or 0)
            status = "success" if finished_sources and ok_sources == finished_sources else "partial"
            if ok_sources == 0:
                status = "failed"
            conn.execute(
                """
                UPDATE runs SET
                    finished_at=?, status=?, ok_sources=?, job_count=?,
                    new_count=?, updated_count=?, closed_count=?, error_summary=?
                WHERE run_id=?
                """,
                (
                    utc_now(),
                    status,
                    ok_sources,
                    int(counts["job_count"] or 0),
                    int(event_counts.get("NEW", 0)),
                    int(event_counts.get("UPDATED", 0) + event_counts.get("REOPENED", 0)),
                    int(event_counts.get("CLOSED", 0)),
                    error_summary[:2000],
                    run_id,
                ),
            )
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
            conn.execute("PRAGMA optimize")

    def report_data(self, run_id: int | None = None) -> dict:
        with self.connect() as conn:
            if run_id is None:
                row = conn.execute("SELECT MAX(run_id) AS run_id FROM runs").fetchone()
                run_id = int(row["run_id"] or 0)
            run = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            jobs = conn.execute(
                """
                SELECT j.*, COALESCE(o.event_type, 'SEEN') AS latest_event
                FROM jobs j
                JOIN sources enabled_source ON enabled_source.source_key=j.source_key
                LEFT JOIN observations o ON o.job_key=j.job_key AND o.run_id=?
                WHERE j.status='active' AND enabled_source.enabled=1
                ORDER BY j.score DESC, j.first_seen_at DESC, j.company, j.title
                """,
                (run_id,),
            ).fetchall()
            sources = conn.execute(
                """
                SELECT s.*, sr.status AS run_status, sr.complete, sr.job_count,
                       sr.duration_ms, sr.error AS run_error
                FROM sources s
                LEFT JOIN source_runs sr ON sr.source_key=s.source_key AND sr.run_id=?
                WHERE s.enabled=1
                ORDER BY s.priority, s.company
                """,
                (run_id,),
            ).fetchall()
            return {
                "run": dict(run) if run else {},
                "jobs": [dict(row) for row in jobs],
                "sources": [dict(row) for row in sources],
            }
