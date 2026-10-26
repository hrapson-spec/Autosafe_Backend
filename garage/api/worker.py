"""Job worker: `python -m garage.api.worker` (loop) or run_once()/drain() from tests."""
from __future__ import annotations

import logging
import os
import socket
import time

from . import handlers, jobs
from .context import Ctx
from .timeutil import london_date

log = logging.getLogger("garage.worker")


def run_once(ctx: Ctx, worker_id: str | None = None, kinds: tuple | None = None) -> bool:
    """Claim and run one due job (optionally only of these kinds). True if a job was processed."""
    wid = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    job = jobs.claim_one(ctx.db, wid, ctx.settings.job_visibility_timeout_seconds, kinds)
    if job is None:
        return False
    try:
        result = handlers.dispatch(ctx, job)
    except Exception as e:   # a crashing handler is a retryable failure, never a crashed worker
        log.exception("job %s crashed", job["id"])
        result = jobs.Retry(f"crash:{type(e).__name__}", ctx.settings.job_backoff_seconds)
    jobs.finish(ctx.db, job["id"], result)
    return True


def drain(ctx: Ctx, limit: int = 10_000, worker_id: str | None = None, kinds: tuple | None = None) -> int:
    n = 0
    while n < limit and run_once(ctx, worker_id, kinds):
        n += 1
    return n


def main() -> None:   # pragma: no cover
    from .app import build_ctx_from_env
    logging.basicConfig(level=logging.INFO)
    ctx = build_ctx_from_env()
    last_sweep = None
    while True:
        today = london_date(ctx.clock())
        if last_sweep != today:
            jobs.enqueue_system(ctx.db, "retention_sweep", {}, dedupe_key=f"retention:{today}")
            last_sweep = today
        if not run_once(ctx):
            time.sleep(2)


if __name__ == "__main__":   # pragma: no cover
    main()
