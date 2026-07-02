"""
Add capture-dont-discard columns to risk_checks table.

Additive ALTER TABLE only — safe to run against a live table.
Rollback: stop writing the new columns (no DROP needed; additive schema change).

Run with:
    DATABASE_URL="..." python migrations/add_capture_columns.py

New columns:
    vehicle_colour          TEXT      — primaryColour from DVSA vehicle record
    engine_size             TEXT      — engineSize from DVSA (string; e.g. "1598")
    registration_date       TEXT      — ISO date string from DVSA registrationDate
    latest_test_station     TEXT      — station identifier from most recent test
                                        (testStationId / testStationName / testStation)
    n_dangerous_defects_latest  INT   — count of dangerous defects in latest test
    odometer_result_type    TEXT      — odometerResultType from latest test
    history_json            JSONB     — compact array of all parsed tests
                                        (per test: date, result, odometer, odo_type,
                                         station, n_defects, n_advisories, n_dangerous)

Privacy / licensing note:
    vehicle_colour, engine_size, registration_date: derived from DVSA MOT History API
    under the existing OGL-licensed data flow (same basis as make/model/fuel_type).
    latest_test_station: station identifiers in the DVSA MOT Trade API are included
    under the Trade API terms — confirm with the DVSA data-sharing agreement before
    surfacing station data externally. Internal storage for calibration only.
    history_json: compact summary only; no raw API payload persisted.
"""
import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")


def migrate():
    if not DATABASE_URL:
        print("DATABASE_URL not set, skipping migration")
        return

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    print("Adding capture-dont-discard columns to risk_checks...")

    # Scalar columns — TEXT and INT
    for col, col_type in [
        ("vehicle_colour", "TEXT"),
        ("engine_size", "TEXT"),
        ("registration_date", "TEXT"),
        ("latest_test_station", "TEXT"),
        ("n_dangerous_defects_latest", "INTEGER"),
        ("odometer_result_type", "TEXT"),
    ]:
        cur.execute(
            f"ALTER TABLE risk_checks ADD COLUMN IF NOT EXISTS {col} {col_type}"
        )
        print(f"  + {col} {col_type}")

    # JSONB column — nullable compact history array
    cur.execute(
        "ALTER TABLE risk_checks ADD COLUMN IF NOT EXISTS history_json JSONB"
    )
    print("  + history_json JSONB")

    # Supporting indexes for future analytics queries
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_risk_checks_colour "
        "ON risk_checks(vehicle_colour)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_risk_checks_station "
        "ON risk_checks(latest_test_station)"
    )

    cur.close()
    conn.close()
    print("Capture columns migration complete.")


if __name__ == "__main__":
    migrate()
