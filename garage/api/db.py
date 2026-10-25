"""PostgreSQL access (sync psycopg2, already a root requirement) and migrations."""
from __future__ import annotations

import contextlib
import pathlib
import threading

import psycopg2
import psycopg2.extras
import psycopg2.pool

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"
_MIGRATION_LOCK_KEY = 7_201_025  # arbitrary advisory-lock id


class Database:
    def __init__(self, dsn: str, minconn: int = 1, maxconn: int = 10):
        self.dsn = dsn
        self._pool = psycopg2.pool.ThreadedConnectionPool(minconn, maxconn, dsn)
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def tx(self):
        """One transaction; commits on success, rolls back on any exception."""
        conn = self._pool.getconn()
        try:
            conn.autocommit = False
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SET LOCAL TIME ZONE 'UTC'")
                yield cur
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def close(self) -> None:
        self._pool.closeall()


def migration_files() -> list[pathlib.Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def migrate(dsn: str) -> list[str]:
    """Apply pending migrations in order, once each, under an advisory lock."""
    applied_now: list[str] = []
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
        cur.execute("CREATE TABLE IF NOT EXISTS garage_schema_migrations ("
                    "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        cur.execute("SELECT version FROM garage_schema_migrations")
        done = {r[0] for r in cur.fetchall()}
        for f in migration_files():
            if f.stem in done:
                continue
            cur.execute(f.read_text())
            cur.execute("INSERT INTO garage_schema_migrations (version) VALUES (%s)", (f.stem,))
            applied_now.append(f.stem)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return applied_now
