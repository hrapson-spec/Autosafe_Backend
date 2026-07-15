#!/usr/bin/env python3
"""
Seed synthetic release-acceptance rows into a staging Postgres database.

Feeds two consumers:
  - scripts/staging_acceptance.py (the GET-token 200/410 checks need two
    pre-existing tokens with a future and a past expires_at).
  - scripts/retention_sweep.py's staging rehearsal (docs/STAGING.md step 5
    needs real rows older than the retention window to pseudonymise).

Synthetic data ONLY: fake ZZ-prefixed VRMs, fake ZZ# #ZZ postcodes, a fake
'ZZTEST' make/'MODEL' model. Never point this at a real database -- it is a
staging/CI-only tool, not a production migration.

Seeds, in one run:
  - A six-row ``mot_risk`` fixture for the real PostgreSQL evidence ladder.
    Its exact-band cohort is pinned to 500 tests / 120 failures / 24%.
  - 20 "recent" rows (created_at spread over the last ~20 days): 16 v1-shaped
    (no report_token/contract columns -- simulates pre-v2-release data,
    still readable by legacy /api/admin/export-risk-checks) and 4 v2-shaped
    (report_token + a schema-valid report_payload).
  - 10 rows older than the retention window (created_at 25-34 months ago),
    ALL with registration/postcode/report_payload/report_token populated --
    these are the retention-sweep rehearsal's candidates.
  - 2 dedicated rows for the GET /api/v2/reports/{token} 200/410 checks,
    identified by fixed registrations so staging_acceptance.py can look them
    up without any token hand-off between scripts:
      registration='ZZ01FUT'  expires_at in the FUTURE  -> GET should 200
      registration='ZZ02PST'  expires_at in the PAST     -> GET should 410
                                                              (TTL-expired,
                                                              NOT pseudony-
                                                              mised -- a
                                                              different 410
                                                              path than the
                                                              retention-swept
                                                              rows above)
  - Five synthetic leads (three older than one month, two recent), one
    synthetic garage, and one assignment per lead for the deletion-order
    retention rehearsal.

report_payload is built via the real report_contract.py pydantic models
(never hand-rolled JSON), so every v2-shaped seeded row round-trips through
GET /api/v2/reports/{token} exactly like a genuinely-created report would.

Usage:
    DATABASE_URL="postgresql://..." python scripts/seed_staging_data.py
    DATABASE_URL="postgresql://..." BASE_URL="http://127.0.0.1:8100" \\
        python scripts/seed_staging_data.py --wipe-first
"""
import argparse
import asyncio
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_contract import (  # noqa: E402
    REPORT_CONTRACT_VERSION,
    SHARE_URL_PATH,
    ConfidenceLevel,
    MatchScope,
    MileageSource,
    PredictionSource,
    ReportComponents,
    ReportEvidence,
    ReportMileage,
    ReportMot,
    ReportPersistence,
    ReportResponse,
    ReportRisk,
    ReportVehicle,
    VehicleDataSource,
)

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8100")
N_RECENT = 20
N_OLD = 10
RETENTION_MONTHS = 24  # matches scripts/retention_sweep.py's DEFAULT_RETENTION_MONTHS
MOT_RISK_FIXTURE_MODEL = "ZZTEST MODEL"

STAGING_GARAGE_ID = uuid.UUID("00000000-0000-4000-8000-000000000100")
STAGING_LEAD_IDS = tuple(
    uuid.UUID(f"00000000-0000-4000-8000-{i:012d}") for i in range(201, 206)
)
STAGING_ASSIGNMENT_IDS = tuple(
    uuid.UUID(f"00000000-0000-4000-8000-{i:012d}") for i in range(301, 306)
)


MOT_RISK_DDL = """
    CREATE TABLE IF NOT EXISTS mot_risk (
        model_id VARCHAR(255),
        age_band VARCHAR(255),
        mileage_band VARCHAR(255),
        total_tests INTEGER,
        total_failures INTEGER,
        failure_risk REAL,
        risk_brakes REAL,
        risk_suspension REAL,
        risk_tyres REAL,
        risk_steering REAL,
        risk_visibility REAL,
        risk_lamps_reflectors_and_electrical_equipment REAL,
        risk_body_chassis_structure REAL
    )
"""

MOT_RISK_INSERT_SQL = """
    INSERT INTO mot_risk (
        model_id, age_band, mileage_band, total_tests, total_failures,
        failure_risk, risk_brakes, risk_suspension, risk_tyres,
        risk_steering, risk_visibility,
        risk_lamps_reflectors_and_electrical_equipment,
        risk_body_chassis_structure
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
"""

WIPE_RISK_CHECKS_SQL = """
    DELETE FROM risk_checks
    WHERE registration LIKE 'ZZ%' OR vehicle_make = 'ZZTEST'
"""


def build_mot_risk_fixture():
    """Return the deterministic synthetic cohorts used by acceptance.

    Only the ``ZZTEST MODEL`` namespace is replaced, so this remains safe
    alongside a fuller staging copy of ``mot_risk``.
    """
    return [
        (
            MOT_RISK_FIXTURE_MODEL, "3-5", "30k-60k", 500, 120, 0.24,
            0.08, 0.06, 0.05, 0.02, 0.03, 0.07, 0.04,
        ),
        (
            MOT_RISK_FIXTURE_MODEL, "3-5", "0-30k", 200, 40, 0.20,
            0.06, 0.05, 0.04, 0.01, 0.02, 0.05, 0.03,
        ),
        (
            MOT_RISK_FIXTURE_MODEL, "3-5", "60k-100k", 300, 90, 0.30,
            0.10, 0.08, 0.07, 0.03, 0.04, 0.09, 0.05,
        ),
        (
            MOT_RISK_FIXTURE_MODEL, "6-10", "30k-60k", 400, 120, 0.30,
            0.10, 0.09, 0.06, 0.03, 0.04, 0.08, 0.06,
        ),
        (
            MOT_RISK_FIXTURE_MODEL, "0-2", "0-30k", 100, 15, 0.15,
            0.04, 0.03, 0.03, 0.01, 0.02, 0.04, 0.02,
        ),
        (
            MOT_RISK_FIXTURE_MODEL, "11-15", "100k+", 250, 100, 0.40,
            0.14, 0.12, 0.09, 0.04, 0.05, 0.11, 0.08,
        ),
    ]


def build_lead_fixture(now: datetime):
    """Return deterministic, manifestly synthetic lead-retention rows."""
    garage = (
        STAGING_GARAGE_ID,
        "RC1 Synthetic Garage",
        "rc1-garage@staging.invalid",
        "ZZ1 1ZZ",
        now - timedelta(days=90),
    )
    ages = (45, 60, 75, 5, 10)
    leads = [
        (
            lead_id,
            f"rc1-lead-{i}@staging.invalid",
            f"ZZ{i + 1} {i + 1}ZZ",
            "RC1 Synthetic Lead",
            "garage",
            "ZZTEST",
            "MODEL",
            True,
            now - timedelta(days=ages[i]),
        )
        for i, lead_id in enumerate(STAGING_LEAD_IDS)
    ]
    assignments = [
        (
            assignment_id,
            lead_id,
            STAGING_GARAGE_ID,
            now - timedelta(days=ages[i]),
        )
        for i, (assignment_id, lead_id) in enumerate(
            zip(STAGING_ASSIGNMENT_IDS, STAGING_LEAD_IDS)
        )
    ]
    return garage, leads, assignments


async def seed_mot_risk_fixture(conn) -> int:
    await conn.execute(MOT_RISK_DDL)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mot_risk_model_age_mileage "
        "ON mot_risk(model_id, age_band, mileage_band)"
    )
    await conn.execute("DELETE FROM mot_risk WHERE model_id = $1", MOT_RISK_FIXTURE_MODEL)
    rows = build_mot_risk_fixture()
    await conn.executemany(MOT_RISK_INSERT_SQL, rows)
    return len(rows)


async def seed_lead_fixture(conn, now: datetime) -> int:
    """Replace only fixed-ID staging fixtures; never match ordinary rows."""
    garage, leads, assignments = build_lead_fixture(now)
    await conn.execute(
        "DELETE FROM lead_assignments WHERE lead_id = ANY($1::uuid[])",
        list(STAGING_LEAD_IDS),
    )
    await conn.execute("DELETE FROM leads WHERE id = ANY($1::uuid[])", list(STAGING_LEAD_IDS))
    await conn.execute("DELETE FROM garages WHERE id = $1", STAGING_GARAGE_ID)
    await conn.execute(
        """
        INSERT INTO garages (id, name, email, postcode, created_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        *garage,
    )
    await conn.executemany(
        """
        INSERT INTO leads (
            id, email, postcode, name, lead_type, vehicle_make,
            vehicle_model, consent_given, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        leads,
    )
    await conn.executemany(
        """
        INSERT INTO lead_assignments (id, lead_id, garage_id, created_at)
        VALUES ($1, $2, $3, $4)
        """,
        assignments,
    )
    return len(leads)


def _share_url(token: str) -> str:
    return BASE_URL.rstrip("/") + SHARE_URL_PATH.format(token=token)


def _fake_vrm(i: int) -> str:
    return f"ZZ{i:02d}SYN"  # <= 8 chars always (risk_checks.registration is VARCHAR(8))


def _fake_postcode(i: int) -> str:
    return f"ZZ{i % 9 + 1} {i % 9}ZZ"


def _build_payload(*, report_id, token, registration, created_at, expires_at, failure_risk=0.24, total_tests=500):
    """A schema-valid ReportResponse JSON payload, via the real pydantic
    models -- guarantees seeded rows validate exactly like a genuine
    report_routes.py-created one when fetched through GET."""
    response = ReportResponse(
        report_id=str(report_id),
        report_token=token,
        share_url=_share_url(token),
        created_at=created_at.isoformat(),
        expires_at=expires_at.isoformat(),
        registration=registration,
        vehicle=ReportVehicle(make="ZZTEST", model="MODEL", year=2018, fuel_type="PETROL", colour="BLUE"),
        mot=ReportMot(expiry_date=None, last_test_date=None, last_result=None),
        mileage=ReportMileage(
            effective_value=45000, source=MileageSource.ESTIMATED,
            observed_at=None, unit_converted=False, anomaly=False,
        ),
        evidence=ReportEvidence(
            match_scope=MatchScope.EXACT_BAND, age_band="3-5", mileage_band="30k-60k",
            total_tests=total_tests, total_failures=int(total_tests * failure_risk),
        ),
        risk=ReportRisk(failure_risk=failure_risk, confidence=ConfidenceLevel.HIGH),
        components=ReportComponents(available=False, items=None),
        repair_estimate=None,
        persistence=ReportPersistence(saved=True, share_available=True),
        prediction_source=PredictionSource.POSTGRES,
        vehicle_data_source=VehicleDataSource.DEMO,
        note=None,
    )
    return response.model_dump(mode="json")


INSERT_SQL = """
    INSERT INTO risk_checks (
        created_at, registration, postcode,
        vehicle_make, vehicle_model, vehicle_year, vehicle_fuel_type, mileage,
        failure_risk, confidence_level, model_version, prediction_source, is_dvsa_data,
        contract_version, report_token, report_payload,
        mileage_source, effective_mileage, match_scope, total_tests, total_failures,
        expires_at, id
    ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
        $14, $15, $16::jsonb, $17, $18, $19, $20, $21, $22,
        COALESCE($23, gen_random_uuid())
    )
"""


def _row(*, created_at, registration, postcode, vehicle_year=2018, vehicle_fuel_type="PETROL",
         mileage=45000, failure_risk=0.28, confidence="Low", model_version="lookup_v1",
         prediction_source="sqlite", contract_version=None, token=None, payload_json=None,
         mileage_source=None, effective_mileage=None, match_scope=None, total_tests=None,
         total_failures=None, expires_at=None, report_id=None):
    return (
        created_at, registration, postcode,
        "ZZTEST", "MODEL", vehicle_year, vehicle_fuel_type, mileage,
        failure_risk, confidence, model_version, prediction_source, False,
        contract_version, token, payload_json,
        mileage_source, effective_mileage, match_scope, total_tests, total_failures,
        expires_at, report_id,
    )


def build_rows(now: datetime):
    rows = []

    # ~20 recent rows: 4 of every 5 (i % 5 == 0) get a v2-shaped payload;
    # the rest simulate legacy v1 rows (no report_token/contract columns).
    for i in range(N_RECENT):
        created_at = now - timedelta(days=i, hours=i % 5)
        vrm, postcode = _fake_vrm(i), _fake_postcode(i)
        if i % 5 == 0:
            report_id = uuid.uuid4()
            token = secrets.token_urlsafe(16)
            expires_at = created_at + timedelta(days=90)
            payload = _build_payload(
                report_id=report_id, token=token, registration=vrm,
                created_at=created_at, expires_at=expires_at,
            )
            rows.append(_row(
                created_at=created_at, registration=vrm, postcode=postcode,
                failure_risk=0.24, confidence="High", model_version="lookup_v2",
                prediction_source="postgres", contract_version=REPORT_CONTRACT_VERSION,
                token=token, payload_json=json.dumps(payload),
                mileage_source="estimated", effective_mileage=45000, match_scope="exact_band",
                total_tests=500, total_failures=120, expires_at=expires_at,
                report_id=report_id,
            ))
        else:
            rows.append(_row(created_at=created_at, registration=vrm, postcode=postcode))

    # ~10 rows older than the retention window, WITH registration/postcode/
    # report_payload/report_token populated -- retention-sweep candidates.
    for i in range(N_OLD):
        months_old = RETENTION_MONTHS + 1 + i  # 25..34 months old
        created_at = now - timedelta(days=months_old * 30)
        vrm, postcode = _fake_vrm(100 + i), _fake_postcode(100 + i)
        report_id = uuid.uuid4()
        token = secrets.token_urlsafe(16)
        expires_at = created_at + timedelta(days=90)  # long TTL-expired too, realistically
        payload = _build_payload(
            report_id=report_id, token=token, registration=vrm,
            created_at=created_at, expires_at=expires_at,
            failure_risk=0.33, total_tests=300,
        )
        rows.append(_row(
            created_at=created_at, registration=vrm, postcode=postcode,
            vehicle_year=2016, vehicle_fuel_type="DIESEL", mileage=80000,
            failure_risk=0.33, confidence="Medium", model_version="lookup_v2",
            prediction_source="postgres", contract_version=REPORT_CONTRACT_VERSION,
            token=token, payload_json=json.dumps(payload),
            mileage_source="observed_mot", effective_mileage=80000, match_scope="exact_band",
            total_tests=300, total_failures=99, expires_at=expires_at,
            report_id=report_id,
        ))

    # 2 dedicated rows for the GET-token 200/410 checks (fixed registrations
    # so staging_acceptance.py can find them without a token hand-off).
    future_report_id = uuid.uuid4()
    future_token = secrets.token_urlsafe(16)
    future_created = now - timedelta(hours=1)
    future_expires = now + timedelta(days=30)
    future_payload = _build_payload(
        report_id=future_report_id, token=future_token, registration="ZZ01FUT",
        created_at=future_created, expires_at=future_expires,
    )
    rows.append(_row(
        created_at=future_created, registration="ZZ01FUT", postcode="ZZ1 1ZZ",
        failure_risk=0.24, confidence="High", model_version="lookup_v2", prediction_source="postgres",
        contract_version=REPORT_CONTRACT_VERSION, token=future_token, payload_json=json.dumps(future_payload),
        mileage_source="estimated", effective_mileage=45000, match_scope="exact_band",
        total_tests=500, total_failures=120, expires_at=future_expires,
        report_id=future_report_id,
    ))

    past_report_id = uuid.uuid4()
    past_token = secrets.token_urlsafe(16)
    past_created = now - timedelta(days=95)
    past_expires = now - timedelta(days=5)
    past_payload = _build_payload(
        report_id=past_report_id, token=past_token, registration="ZZ02PST",
        created_at=past_created, expires_at=past_expires,
    )
    rows.append(_row(
        created_at=past_created, registration="ZZ02PST", postcode="ZZ2 2ZZ",
        failure_risk=0.24, confidence="High", model_version="lookup_v2", prediction_source="postgres",
        contract_version=REPORT_CONTRACT_VERSION, token=past_token, payload_json=json.dumps(past_payload),
        mileage_source="estimated", effective_mileage=45000, match_scope="exact_band",
        total_tests=500, total_failures=120, expires_at=past_expires,
        report_id=past_report_id,
    ))

    return rows, future_token, past_token


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)
    return url.replace("postgres://", "postgresql://")


async def _amain(wipe_first: bool) -> int:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows, future_token, past_token = build_rows(now)

    conn = await asyncpg.connect(get_database_url())
    try:
        async with conn.transaction():
            deleted = None
            if wipe_first:
                deleted = await conn.execute(WIPE_RISK_CHECKS_SQL)
            mot_risk_n = await seed_mot_risk_fixture(conn)
            lead_n = await seed_lead_fixture(conn, now)
            for row in rows:
                await conn.execute(INSERT_SQL, *row)

        total = await conn.fetchval("SELECT COUNT(*) FROM risk_checks")
        recent_n = await conn.fetchval("SELECT COUNT(*) FROM risk_checks WHERE created_at > NOW() - INTERVAL '25 days'")
        old_n = await conn.fetchval(
            "SELECT COUNT(*) FROM risk_checks WHERE created_at < NOW() - INTERVAL '24 months' "
            "AND registration IS NOT NULL AND report_token IS NOT NULL"
        )
    finally:
        await conn.close()

    if deleted is not None:
        print(f"--wipe-first: {deleted}")
    print("Seed summary:")
    print(f"  mot_risk fixture rows : {mot_risk_n}")
    print(f"  rows inserted this run : {len(rows)}")
    print(f"  lead fixture rows : {lead_n}")
    print(f"  risk_checks total rows : {total}")
    print(f"  rows created within 25 days : {recent_n}")
    print(f"  rows older than 24 months w/ registration+token : {old_n}")
    print(f"  FUTURE_TOKEN (registration=ZZ01FUT) = {future_token}")
    print(f"  PAST_TOKEN   (registration=ZZ02PST) = {past_token}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Seed synthetic staging risk_checks rows.")
    parser.add_argument(
        "--wipe-first", action="store_true",
        help="Delete existing synthetic risk-check rows before seeding (safe re-run).",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(args.wipe_first))


if __name__ == "__main__":
    sys.exit(main())
