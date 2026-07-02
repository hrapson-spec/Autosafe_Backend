"""
Post-Deploy Canary — feat/capture-dont-discard acceptance checks
================================================================

Runnable ad-hoc against the production database to validate that the new
capture columns are actually being written after deploy.

Usage:
    DATABASE_URL="..." python scripts/post_deploy_canary.py
    DATABASE_URL="..." python scripts/post_deploy_canary.py --hours 48

Checks (PASS/FAIL per check):
    1. Rows written to risk_checks in the window (traffic sanity)
    2. Non-null counts per new field on DVSA-sourced rows
       (vehicle_colour, engine_size, registration_date, latest_test_station,
        n_dangerous_defects_latest, odometer_result_type, history_json)
    3. NULL-vs-zero breakdown for n_dangerous_defects_latest specifically
       (NULL = detection unproven OR conflicting signals; 0 = affirmatively
        none — distinct meanings)
    3b. Severity-signal conflicts: sums n_severity_conflicts across
        history_json; nonzero = WARN — capture a live response and establish
        which field is authoritative before trusting dangerous counts
    4. JSONL-backup repetition alarm: growth of risk_checks_backup.jsonl in the
       window signals INSERT failures (column mismatch / migration not run)
    5. Throughput proxy: rows-per-hour this window vs same window one week ago

HARD ACCEPTANCE GATE (station):
    Station capture is UNVERIFIED until a live API response proves the field
    name. If DVSA-sourced rows exist in the window and latest_test_station is
    all-NULL, the feature is NOT CAPTURED (not "sparse") — the field name must
    be fixed against a captured live response before any downstream use.

Honest limitations (not observable from the DB alone):
    - Request latency, HTTP error rates, and DVSA API failure ratios need app
      metrics/logs (Railway logs), not this script.
    - A drop in rows-per-hour can be traffic seasonality, not a regression.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

NEW_FIELDS = [
    "vehicle_colour",
    "engine_size",
    "registration_date",
    "latest_test_station",
    "n_dangerous_defects_latest",
    "odometer_result_type",
    "history_json",
]

BACKUP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "risk_checks_backup.jsonl",
)


def report_line(status: str, name: str, detail: str):
    print(f"[{status:>4}] {name}: {detail}")


def check_rows_written(cur, hours: int) -> int:
    """Check 1: any rows at all in the window."""
    cur.execute(
        "SELECT COUNT(*) FROM risk_checks WHERE created_at > NOW() - INTERVAL '1 hour' * %s",
        (hours,),
    )
    total = cur.fetchone()[0]
    status = "PASS" if total > 0 else "FAIL"
    report_line(status, "rows_written", f"{total} risk_checks rows in last {hours}h")
    return total


def check_field_coverage(cur, hours: int) -> dict:
    """Check 2: non-null counts per new field on DVSA-sourced rows only.

    Fallback rows (prediction_source != 'dvsa') legitimately carry NULLs for
    DVSA-derived fields, so they are excluded from coverage expectations.
    """
    cols = ", ".join(f"COUNT({f}) AS {f}" for f in NEW_FIELDS)
    cur.execute(
        f"""
        SELECT COUNT(*) AS dvsa_rows, {cols}
        FROM risk_checks
        WHERE created_at > NOW() - INTERVAL '1 hour' * %s
          AND prediction_source = 'dvsa'
        """,
        (hours,),
    )
    row = cur.fetchone()
    colnames = [d[0] for d in cur.description]
    result = dict(zip(colnames, row))
    dvsa_rows = result["dvsa_rows"]

    if dvsa_rows == 0:
        report_line("WARN", "field_coverage", "no DVSA-sourced rows in window — coverage unmeasurable")
        return result

    for field in NEW_FIELDS:
        non_null = result[field]
        pct = 100.0 * non_null / dvsa_rows

        if field == "latest_test_station":
            # HARD ACCEPTANCE GATE: all-NULL station on real traffic means the
            # field name is wrong — feature is NOT CAPTURED, not "sparse".
            if non_null == 0:
                report_line(
                    "FAIL",
                    "station_hard_gate",
                    f"latest_test_station is all-NULL over {dvsa_rows} DVSA rows — "
                    "feature NOT CAPTURED (unverified field name). Capture a live "
                    "API response and fix the field name in dvsa_client.py before "
                    "any downstream use.",
                )
            else:
                report_line(
                    "PASS",
                    "station_hard_gate",
                    f"latest_test_station non-null on {non_null}/{dvsa_rows} rows ({pct:.1f}%)",
                )
            continue

        # Colour and history_json should be near-universal on DVSA rows;
        # the rest may legitimately vary (engine_size missing on some vehicles,
        # n_dangerous NULL where severity detection unproven).
        expect_high = field in ("vehicle_colour", "history_json")
        if expect_high:
            status = "PASS" if pct >= 50.0 else "FAIL"
        else:
            status = "PASS" if non_null > 0 else "WARN"
        report_line(status, f"coverage_{field}", f"{non_null}/{dvsa_rows} non-null ({pct:.1f}%)")

    return result


def check_dangerous_null_vs_zero(cur, hours: int):
    """Check 3: NULL-vs-zero breakdown for n_dangerous_defects_latest.

    NULL  = severity detection unproven (API shape not matched) — expected to
            dominate ONLY if the API uses an unrecognized defect encoding.
    0     = detection fired, affirmatively none dangerous.
    >0    = dangerous defects counted.
    """
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE n_dangerous_defects_latest IS NULL)  AS n_null,
            COUNT(*) FILTER (WHERE n_dangerous_defects_latest = 0)      AS n_zero,
            COUNT(*) FILTER (WHERE n_dangerous_defects_latest > 0)      AS n_positive,
            COUNT(*)                                                    AS total
        FROM risk_checks
        WHERE created_at > NOW() - INTERVAL '1 hour' * %s
          AND prediction_source = 'dvsa'
        """,
        (hours,),
    )
    n_null, n_zero, n_positive, total = cur.fetchone()

    if total == 0:
        report_line("WARN", "dangerous_null_vs_zero", "no DVSA rows in window")
        return

    detail = (
        f"NULL(unproven)={n_null} zero(known-none)={n_zero} "
        f"positive={n_positive} of {total} DVSA rows"
    )
    # If EVERY row is NULL, severity detection never fired — the defect
    # encoding assumption is wrong and needs a captured live response.
    if n_null == total:
        report_line(
            "FAIL",
            "dangerous_null_vs_zero",
            detail + " — detection never proven; verify defect field shape "
            "against a live API response",
        )
    else:
        report_line("PASS", "dangerous_null_vs_zero", detail)


def check_severity_conflicts(cur, hours: int):
    """Check 3b: contradictory severity signals recorded by the parse layer.

    Each history_json test entry carries n_severity_conflicts — the number of
    defects where the `dangerous` boolean and the `type` string contradicted
    each other. Conflicts resolve to NULL counts, never to either signal.
    Nonzero conflicts mean the defect encoding is ambiguous in the wild.
    """
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE COALESCE((history_json->0->>'n_severity_conflicts')::int, 0) > 0
            ) AS rows_latest_conflict,
            COALESCE(SUM(row_conflicts), 0) AS total_conflicts,
            COUNT(*) AS rows_with_history
        FROM (
            SELECT
                history_json,
                (
                    SELECT SUM(COALESCE((t->>'n_severity_conflicts')::int, 0))
                    FROM jsonb_array_elements(history_json) AS t
                ) AS row_conflicts
            FROM risk_checks
            WHERE created_at > NOW() - INTERVAL '1 hour' * %s
              AND prediction_source = 'dvsa'
              AND history_json IS NOT NULL
        ) sub
        """,
        (hours,),
    )
    rows_latest_conflict, total_conflicts, rows_with_history = cur.fetchone()

    if rows_with_history == 0:
        report_line("WARN", "severity_conflicts", "no rows with history_json in window")
        return

    detail = (
        f"{total_conflicts} conflicting-signal defects across "
        f"{rows_with_history} rows ({rows_latest_conflict} rows with a "
        f"latest-test conflict)"
    )
    if total_conflicts and int(total_conflicts) > 0:
        report_line(
            "WARN",
            "severity_conflicts",
            detail + " — capture a live response and establish which field "
            "is authoritative before trusting dangerous counts",
        )
    else:
        report_line("PASS", "severity_conflicts", detail)


def check_backup_growth(hours: int):
    """Check 4: JSONL backup file growth = INSERT-failure alarm.

    database.py appends to risk_checks_backup.jsonl whenever the INSERT fails
    (e.g. migration not run -> column mismatch). Growth in the window means
    saves are failing silently.
    """
    if not os.path.exists(BACKUP_PATH):
        report_line("PASS", "backup_alarm", "risk_checks_backup.jsonl does not exist (no failed inserts)")
        return

    mtime = datetime.fromtimestamp(os.path.getmtime(BACKUP_PATH))
    cutoff = datetime.now() - timedelta(hours=hours)

    # Count entries whose backup_timestamp falls inside the window
    recent = 0
    total = 0
    try:
        with open(BACKUP_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    ts = json.loads(line).get("backup_timestamp", "")
                    if ts and datetime.fromisoformat(ts) > cutoff:
                        recent += 1
                except (ValueError, json.JSONDecodeError):
                    continue
    except OSError as e:
        report_line("WARN", "backup_alarm", f"could not read backup file: {e}")
        return

    detail = (
        f"{recent} failed-insert backups in last {hours}h "
        f"(file total={total}, mtime={mtime.isoformat()})"
    )
    if recent > 0:
        report_line(
            "FAIL",
            "backup_alarm",
            detail + " — INSERTs are failing; check migration ran and column "
            "list matches (database.py positional INSERT)",
        )
    else:
        report_line("PASS", "backup_alarm", detail)


def check_throughput(cur, hours: int):
    """Check 5: rows-per-hour vs the same window one week earlier.

    A proxy only: a big drop MAY indicate 500s at the endpoint (save path
    raising before insert), but can also be ordinary traffic variation.
    Latency and HTTP error rates are NOT observable from the DB — check
    Railway app logs for those.
    """
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE created_at > NOW() - INTERVAL '1 hour' * %s
            ) AS current_window,
            COUNT(*) FILTER (
                WHERE created_at BETWEEN NOW() - INTERVAL '7 days' - INTERVAL '1 hour' * %s
                                     AND NOW() - INTERVAL '7 days'
            ) AS prior_week_window
        FROM risk_checks
        """,
        (hours, hours),
    )
    current, prior = cur.fetchone()
    cur_rate = current / hours
    prior_rate = prior / hours

    detail = (
        f"current={current} rows ({cur_rate:.2f}/h) vs "
        f"same {hours}h window last week={prior} rows ({prior_rate:.2f}/h)"
    )
    if prior > 0 and current < 0.5 * prior:
        report_line(
            "WARN",
            "throughput_proxy",
            detail + " — >50% drop; could be traffic OR endpoint errors. "
            "NOT conclusive: check Railway logs for latency/5xx (not "
            "observable from DB)",
        )
    else:
        report_line("PASS", "throughput_proxy", detail)


def main():
    parser = argparse.ArgumentParser(
        description="Post-deploy canary for feat/capture-dont-discard"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        metavar="N",
        help="Observation window in hours (default: 24)",
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        print("Error: DATABASE_URL not set")
        print("Usage: DATABASE_URL='...' python scripts/post_deploy_canary.py [--hours 24]")
        sys.exit(1)

    url = DATABASE_URL.replace("postgres://", "postgresql://")
    conn = psycopg2.connect(url)
    cur = conn.cursor()

    print(f"=== Post-deploy canary — window: last {args.hours}h ===\n")

    total = check_rows_written(cur, args.hours)
    if total > 0:
        check_field_coverage(cur, args.hours)
        check_dangerous_null_vs_zero(cur, args.hours)
        check_severity_conflicts(cur, args.hours)
    check_backup_growth(args.hours)
    check_throughput(cur, args.hours)

    print(
        "\nNot observable from DB (check Railway app logs instead): request "
        "latency, HTTP 5xx rate, DVSA API error ratio."
    )

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
