"""12-month retention sweep. A SYSTEM operation, not workshop-scoped by design: it deletes expired rows
across all workshops. It is the only unscoped writer of private tables and is allow-listed in the scope test.
"""
from __future__ import annotations

from .jobs import Done
from .timeutil import london_date


def run_sweep(ctx):
    purge(ctx.db, ctx.settings.retention_months, london_date(ctx.clock()))
    return Done()


def purge(db, months: int, today) -> dict:
    out = {}
    with db.tx() as cur:
        cur.execute("SET LOCAL garage.retention_sweep = 'on'")
        cur.execute("DELETE FROM bookings WHERE appointment_date < (%s::date - make_interval(months => %s))::date",
                    (today, months))
        out["bookings"] = cur.rowcount
        cur.execute("""DELETE FROM imports i WHERE i.created_at < now() - make_interval(months => %s)
                          AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.workshop_id = i.workshop_id
                                          AND b.import_id = i.id)""", (months,))
        out["imports"] = cur.rowcount
        cur.execute("DELETE FROM sessions WHERE expires_at < now() - interval '30 days'")
        cur.execute("DELETE FROM login_tokens WHERE expires_at < now() - interval '30 days'")
        cur.execute("DELETE FROM jobs WHERE status IN ('done', 'failed', 'cancelled') "
                    "AND finished_at < now() - interval '30 days'")
    return out
