"""Postgres job queue: SELECT ... FOR UPDATE SKIP LOCKED."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg2.extras import Json


@dataclass
class Done:
    pass


@dataclass
class Defer:               # not a failure: try again later without using an attempt
    run_at: datetime


@dataclass
class Retry:
    error: str
    delay_seconds: int


@dataclass
class Fail:
    error: str


def claim_one(db, worker_id: str, visibility_timeout_s: int, kinds: tuple | None = None):
    with db.tx() as cur:
        # Recover jobs whose worker died mid-run.
        cur.execute("""UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
                              last_error = 'worker_lost', locked_at = NULL, locked_by = NULL,
                              finished_at = CASE WHEN attempts >= max_attempts THEN now() END
                        WHERE status = 'running' AND locked_at < now() - make_interval(secs => %s)""",
                    (visibility_timeout_s,))
        cur.execute("""UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_at = now(), locked_by = %s
                        WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_at <= now()
                                      AND (%s::text[] IS NULL OR kind = ANY(%s::text[]))
                                    ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
                    RETURNING *""", (worker_id, list(kinds) if kinds else None, list(kinds) if kinds else None))
        return cur.fetchone()


def finish(db, job_id: int, result) -> None:
    with db.tx() as cur:
        if isinstance(result, Done):
            cur.execute("UPDATE jobs SET status = 'done', finished_at = now(), locked_at = NULL, locked_by = NULL "
                        "WHERE id = %s", (job_id,))
        elif isinstance(result, Defer):
            cur.execute("UPDATE jobs SET status = 'queued', run_at = %s, attempts = greatest(attempts - 1, 0), "
                        "locked_at = NULL, locked_by = NULL WHERE id = %s", (result.run_at, job_id))
        elif isinstance(result, Retry):
            cur.execute("""UPDATE jobs SET last_error = %s, locked_at = NULL, locked_by = NULL,
                                  status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
                                  run_at = now() + make_interval(secs => %s * attempts),
                                  finished_at = CASE WHEN attempts >= max_attempts THEN now() END
                            WHERE id = %s""", (result.error[:500], result.delay_seconds, job_id))
        else:
            cur.execute("UPDATE jobs SET status = 'failed', last_error = %s, finished_at = now(), locked_at = NULL, "
                        "locked_by = NULL WHERE id = %s", (result.error[:500], job_id))


def enqueue_system(db, kind: str, payload: dict, dedupe_key: str | None = None) -> bool:
    """System jobs (no workshop). Only retention_sweep is allowed by the table constraint."""
    with db.tx() as cur:
        cur.execute("""INSERT INTO jobs (workshop_id, kind, payload, dedupe_key) VALUES (NULL, %s, %s, %s)
                       ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')
                       DO NOTHING RETURNING id""", (kind, Json(payload), dedupe_key))
        return cur.fetchone() is not None
