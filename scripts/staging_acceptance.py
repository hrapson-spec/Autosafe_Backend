#!/usr/bin/env python3
"""
AutoSafe staging acceptance-evidence suite (see docs/STAGING.md).

Exercises a running staging instance (postgres:16-alpine + `uvicorn
main:app`, per docs/STAGING.md's local recipe, or the full docker-compose
stack in CI) end-to-end and asserts the invariants the release depends on:
health/readiness, the v2 report contract (create / idempotency / fetch /
error taxonomy), legacy /api/risk /api/vehicle /api/stats byte-shape
parity, and the Postgres-vs-SQLite evidence-ladder split.

Reusable in CI: takes --base-url and reads DATABASE_URL from the
environment; pure stdlib + httpx + asyncpg (both already pinned in
requirements.txt, so no urllib fallback is needed here). Exits nonzero if
any check fails. Prints one PASS/FAIL line per named check as it runs,
plus the full JSON/row detail for the checks that need it, then a summary
table -- redirect stdout to a file to get a self-contained evidence
artifact.

Prerequisites this script assumes (see docs/STAGING.md "How to run"):
    - risk_checks / leads tables created (create_risk_checks_table.py,
      create_leads_table.py) and migrations/add_report_contract_columns.py
      applied.
    - scripts/seed_staging_data.py has been run (checks 4f/4h below look
      up its two fixed-registration seeded rows; check 4j looks up a
      seeded 'ZZTEST MODEL' mot_risk fixture -- see that check's docstring
      for the exact seed SQL if running outside this repo's evidence
      trail).
    - The app is up and DVSA env vars are UNSET (demo mode).

Usage:
    DATABASE_URL="postgresql://..." python scripts/staging_acceptance.py \\
        --base-url http://127.0.0.1:8100
"""
import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

try:
    import asyncpg
except ImportError:  # pragma: no cover -- asyncpg is pinned in requirements.txt; defensive only
    print("ERROR: asyncpg is required (see requirements.txt).", file=sys.stderr)
    raise

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# Check-runner scaffolding
# ---------------------------------------------------------------------------

class Runner:
    """Runs one named async check at a time, catching any exception
    (AssertionError from a failed `assert`, or anything else -- an
    unhandled TypeError etc. is just as much a check failure as a failed
    assertion) and recording PASS/FAIL without stopping the whole suite."""

    def __init__(self):
        self.results: List[Dict[str, Any]] = []

    async def run(self, check_id: str, description: str, coro: Awaitable[Any]) -> Optional[Any]:
        try:
            value = await coro
            self.results.append({"id": check_id, "description": description, "status": "PASS"})
            print(f"\nPASS [{check_id}] {description}")
            return value
        except Exception as e:  # noqa: BLE001 -- any exception is a check failure, by design
            detail = "".join(traceback.format_exception_only(type(e), e)).strip()
            self.results.append({"id": check_id, "description": description, "status": "FAIL", "detail": detail})
            print(f"\nFAIL [{check_id}] {description}\n      {detail}")
            return None

    def exit_code(self) -> int:
        print("\n=== SUMMARY ===")
        for r in self.results:
            print(f"  {r['status']:4s} [{r['id']}] {r['description']}")
        n_fail = sum(1 for r in self.results if r["status"] == "FAIL")
        n_pass = len(self.results) - n_fail
        print(f"\n{n_pass} passed, {n_fail} failed, {len(self.results)} total")
        return 1 if n_fail else 0


def show(label: str, obj: Any) -> None:
    """Pretty-print a JSON-able object with a label, for evidence capture
    (stdout is expected to be redirected to a file by the caller)."""
    print(f"--- {label} ---")
    print(json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(2)
    return url.replace("postgres://", "postgresql://")


# ---------------------------------------------------------------------------
# 4a: /health, /ready, /api/version
# ---------------------------------------------------------------------------

async def check_health(client: httpx.AsyncClient) -> Dict:
    r = await client.get("/health")
    assert r.status_code == 200, f"status={r.status_code} body={r.text}"
    body = r.json()
    assert body.get("status") in ("ok", "degraded"), body
    show("GET /health", body)
    return body


async def check_ready(client: httpx.AsyncClient) -> Dict:
    r = await client.get("/ready")
    assert r.status_code == 200, f"status={r.status_code} body={r.text}"
    body = r.json()
    assert body.get("status") == "ok", body
    assert body.get("database") == "connected", body
    show("GET /ready", body)
    return body


async def check_version(client: httpx.AsyncClient, expect_frontend_bundle: bool) -> Dict:
    r = await client.get("/api/version")
    assert r.status_code == 200, f"status={r.status_code} body={r.text}"
    body = r.json()
    for key in ("backend_sha", "frontend_bundle_hash", "contract_version", "app_version", "started_at"):
        assert key in body, f"missing key {key!r} in {body}"
    if expect_frontend_bundle:
        assert body["frontend_bundle_hash"] is not None, (
            f"expected non-null frontend_bundle_hash (static/index.html exists on disk): {body}"
        )
    else:
        assert body["frontend_bundle_hash"] is None, (
            f"expected null frontend_bundle_hash (static/index.html absent -- documented honestly, not faked): {body}"
        )
    show("GET /api/version", body)
    return body


# ---------------------------------------------------------------------------
# 4b: openapi.json shape
# ---------------------------------------------------------------------------

async def check_openapi(client: httpx.AsyncClient) -> Dict:
    r = await client.get("/openapi.json")
    assert r.status_code == 200, f"status={r.status_code}"
    spec = r.json()

    risk_params = spec["paths"]["/api/risk"]["get"]["parameters"]
    required_names = sorted(p["name"] for p in risk_params if p.get("required"))
    all_names = sorted(p["name"] for p in risk_params)
    assert all_names == ["make", "model", "year"], f"legacy /api/risk params changed: {all_names}"
    assert required_names == ["make", "model", "year"], f"legacy /api/risk required-set changed: {required_names}"

    assert "post" in spec["paths"].get("/api/v2/reports", {}), "POST /api/v2/reports missing from openapi.json"
    assert "get" in spec["paths"].get("/api/v2/reports/{token}", {}), "GET /api/v2/reports/{token} missing"

    post_schema = spec["paths"]["/api/v2/reports"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in post_schema:
        ref_name = post_schema["$ref"].rsplit("/", 1)[-1]
        props = spec["components"]["schemas"][ref_name]["properties"]
    else:
        props = post_schema["properties"]
    assert "registration" in props, f"ReportCreateRequest schema missing 'registration': {props}"

    get_resp_schema = spec["paths"]["/api/v2/reports"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert get_resp_schema, "POST /api/v2/reports has no typed 200 response schema"

    result = {"risk_required_params": required_names, "v2_post_request_props": sorted(props.keys())}
    show("openapi.json summary", result)
    return result


# ---------------------------------------------------------------------------
# 4c: demo-mode POST /api/v2/reports
# ---------------------------------------------------------------------------

async def check_demo_post(client: httpx.AsyncClient, base_url: str) -> Dict:
    payload = {"registration": "ZZ99ABC", "postcode": "ZZ1 1ZZ"}
    r = await client.post("/api/v2/reports", json=payload)
    assert r.status_code == 200, f"status={r.status_code} body={r.text}"
    body = r.json()
    show("POST /api/v2/reports (demo)", body)

    assert body["vehicle_data_source"] == "demo", body["vehicle_data_source"]
    assert body["mileage"]["source"] in ("observed_mot", "estimated"), body["mileage"]
    match_scope = body["evidence"]["match_scope"]
    assert match_scope, body["evidence"]
    total_tests = body["evidence"]["total_tests"]
    if match_scope == "population_default":
        assert total_tests is None, f"population_default must carry total_tests=None (never 0), got {total_tests!r}"
    else:
        assert total_tests is None or (isinstance(total_tests, int) and total_tests > 0), total_tests
    assert body["risk"]["confidence"] in ("High", "Medium", "Low", "Very Low"), body["risk"]
    assert body["persistence"]["saved"] is True, body["persistence"]
    assert body["report_token"], "report_token was falsy"
    assert body["share_url"] and body["share_url"].startswith(base_url), body["share_url"]
    assert body["prediction_source"] in ("postgres", "sqlite"), (
        f"unexpected prediction_source {body['prediction_source']!r} "
        "(only postgres/sqlite are valid backing stores once Postgres is reachable)"
    )
    print(f"NOTE: prediction_source={body['prediction_source']!r} match_scope={match_scope!r} "
          f"-- see check 4j and docs/STAGING.md for why this run took that path.")
    return body


# ---------------------------------------------------------------------------
# 4d: DB row for the created report
# ---------------------------------------------------------------------------

async def check_db_row(pool: asyncpg.Pool, token: Optional[str], expected_registration: str) -> Dict:
    assert token, "no report_token available -- check 4c must pass first"
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM risk_checks WHERE report_token = $1", token)
        assert count == 1, f"expected exactly 1 row for token, found {count}"
        row = await conn.fetchrow("SELECT * FROM risk_checks WHERE report_token = $1", token)
    row_d = dict(row)
    assert row_d["registration"] == expected_registration, (
        f"registration not normalized: {row_d['registration']!r} != {expected_registration!r}"
    )
    assert row_d["postcode"] is not None, row_d
    required_populated = ["mileage_source", "match_scope", "effective_mileage", "contract_version", "expires_at"]
    if row_d["match_scope"] != "population_default":
        required_populated.append("total_tests")  # null-means-unknown is only valid under population_default
    missing = [c for c in required_populated if row_d[c] is None]
    assert not missing, f"unexpectedly NULL columns: {missing} in row {row_d}"
    show("risk_checks row for created token", {k: str(v)[:200] for k, v in row_d.items()})
    return row_d


# ---------------------------------------------------------------------------
# 4e: idempotency
# ---------------------------------------------------------------------------

async def check_idempotency(client: httpx.AsyncClient, pool: asyncpg.Pool) -> Dict:
    idem_key = f"staging-accept-{int(time.time() * 1000)}"
    payload = {"registration": "ZZ88DEF", "postcode": "ZZ1 1ZZ", "idempotency_key": idem_key}

    r1 = await client.post("/api/v2/reports", json=payload)
    assert r1.status_code == 200, f"first POST: status={r1.status_code} body={r1.text}"
    body1 = r1.json()

    async with pool.acquire() as conn:
        count1 = await conn.fetchval("SELECT COUNT(*) FROM risk_checks WHERE idempotency_key = $1", idem_key)
    assert count1 == 1, f"expected 1 row after first POST, found {count1}"

    r2 = await client.post("/api/v2/reports", json=payload)  # identical idempotency_key: must replay
    assert r2.status_code == 200, f"replay POST: status={r2.status_code} body={r2.text}"
    body2 = r2.json()
    assert body2["report_token"] == body1["report_token"], (
        f"replay minted a new token: {body1['report_token']!r} != {body2['report_token']!r}"
    )

    async with pool.acquire() as conn:
        count2 = await conn.fetchval("SELECT COUNT(*) FROM risk_checks WHERE idempotency_key = $1", idem_key)
    assert count2 == 1, f"row count changed on idempotent replay: {count1} -> {count2}"

    payload3 = dict(payload, idempotency_key=idem_key + "-different")
    r3 = await client.post("/api/v2/reports", json=payload3)
    assert r3.status_code == 200, f"different-key POST: status={r3.status_code} body={r3.text}"
    body3 = r3.json()
    assert body3["report_token"] != body1["report_token"], "a different idempotency_key produced the same token"

    async with pool.acquire() as conn:
        count3 = await conn.fetchval(
            "SELECT COUNT(*) FROM risk_checks WHERE idempotency_key IN ($1, $2)",
            idem_key, idem_key + "-different",
        )
    assert count3 == 2, f"expected 2 total rows across both keys, found {count3}"

    result = {
        "token1": body1["report_token"], "token2_replayed": body2["report_token"],
        "token3_different_key": body3["report_token"], "row_count_same_key": count2, "row_count_both_keys": count3,
    }
    show("idempotency check", result)
    return result


# ---------------------------------------------------------------------------
# 4f: GET token round-trip, 404, seeded 200/410
#
# Split into 4 independent checks (rather than one function with 4
# sequential asserts) deliberately: an early `assert` raises and aborts the
# rest of a Python function, so bundling all 4 into one check would mean a
# failure in the first (e.g. the round-trip byte-equality assert) silently
# prevents the other 3 from ever running -- masking whether they'd have
# passed. Independent checks means every sub-assertion is actually
# evaluated and reported, every run.
# ---------------------------------------------------------------------------

async def check_get_token_roundtrip(client: httpx.AsyncClient, created_body: Optional[Dict]) -> Dict:
    assert created_body, "no created report available -- check 4c must pass first"
    token = created_body["report_token"]
    r = await client.get(f"/api/v2/reports/{token}")
    assert r.status_code == 200, f"status={r.status_code} body={r.text}"
    fetched = r.json()
    show("GET /api/v2/reports/{token} (round-trip of the 4c-created report)", fetched)
    if fetched != created_body:
        diff = {
            k: {"post": created_body.get(k), "get": fetched.get(k)}
            for k in created_body
            if created_body.get(k) != fetched.get(k)
        }
        raise AssertionError(
            "GET payload is not byte-identical to the original POST payload -- differing keys: "
            f"{json.dumps(diff, default=str)}"
        )
    return fetched


async def check_get_token_404(client: httpx.AsyncClient) -> Dict:
    r404 = await client.get("/api/v2/reports/thisTokenDoesNotExist1234567890")
    assert r404.status_code == 404, f"status={r404.status_code} body={r404.text}"
    env404 = r404.json()
    assert env404.get("error_code") == "report_not_found", env404
    show("GET unknown token -> 404 envelope", env404)
    return env404


async def check_get_token_future_200(client: httpx.AsyncClient, pool: asyncpg.Pool) -> Dict:
    async with pool.acquire() as conn:
        future_token = await conn.fetchval(
            "SELECT report_token FROM risk_checks WHERE registration = 'ZZ01FUT' ORDER BY created_at DESC LIMIT 1"
        )
    assert future_token, "no seeded ZZ01FUT row found -- run scripts/seed_staging_data.py first"
    rf = await client.get(f"/api/v2/reports/{future_token}")
    assert rf.status_code == 200, f"seeded future-expiry token: status={rf.status_code} body={rf.text}"
    show("GET seeded future-expiry token -> 200", rf.json())
    return {"status": rf.status_code}


async def check_get_token_past_410(client: httpx.AsyncClient, pool: asyncpg.Pool) -> Dict:
    async with pool.acquire() as conn:
        past_token = await conn.fetchval(
            "SELECT report_token FROM risk_checks WHERE registration = 'ZZ02PST' ORDER BY created_at DESC LIMIT 1"
        )
    assert past_token, "no seeded ZZ02PST row found -- run scripts/seed_staging_data.py first"
    rp = await client.get(f"/api/v2/reports/{past_token}")
    assert rp.status_code == 410, f"seeded past-expiry token: status={rp.status_code} body={rp.text}"
    env410 = rp.json()
    assert env410.get("error_code") == "report_expired", env410
    show("GET seeded past-expiry token -> 410 envelope", env410)
    return env410


# ---------------------------------------------------------------------------
# 4g: undeclared query parameter guard
# ---------------------------------------------------------------------------

async def check_undeclared_params(client: httpx.AsyncClient) -> Dict:
    r1 = await client.post("/api/v2/reports", params={"extra": "1"}, json={"registration": "ZZ77GHI"})
    assert r1.status_code == 400, f"POST ?extra=1: status={r1.status_code} body={r1.text}"
    env1 = r1.json()
    assert env1.get("error_code") == "undeclared_parameter", env1

    r2 = await client.get("/api/v2/reports/someKnownShapeToken1", params={"x": "1"})
    assert r2.status_code == 400, f"GET ?x=1: status={r2.status_code} body={r2.text}"
    env2 = r2.json()
    assert env2.get("error_code") == "undeclared_parameter", env2

    show("undeclared query param -> 400 envelopes", {"post": env1, "get": env2})
    return {"post": env1, "get": env2}


# ---------------------------------------------------------------------------
# 4h: error taxonomy sanity (demo mode)
# ---------------------------------------------------------------------------

async def check_invalid_registration_literal(client: httpx.AsyncClient) -> Dict:
    """Literal check per the acceptance brief: registration='A' should
    produce a 400 invalid_registration envelope with a correlation_id.

    NOTE: report_contract.ReportCreateRequest.registration is declared
    `Field(min_length=2, max_length=12)`. A single-character value fails
    *pydantic's* request-body validation before report_routes.py's own
    `_normalize_vrn` (which raises the typed ReportAPIError this check
    expects) ever runs -- so this is asserting across a FastAPI-level
    validation boundary, not just report_routes.py's. Reported exactly as
    observed either way; see check 4h-diag for a same-shaped-but-regex-
    invalid input that unambiguously exercises `_normalize_vrn`."""
    r = await client.post("/api/v2/reports", json={"registration": "A"})
    body = r.json()
    show("POST registration='A'", {"status": r.status_code, "body": body})
    assert r.status_code == 400, f"status={r.status_code} body={body}"
    assert body.get("error_code") == "invalid_registration", body
    assert body.get("correlation_id"), body
    return {"status": r.status_code, "body": body}


async def check_invalid_registration_diag(client: httpx.AsyncClient) -> Dict:
    """Diagnostic (not one of the lettered a-j checks): a 2-character value
    clears ReportCreateRequest's min_length=2 gate but fails
    `_normalize_vrn`'s `^[A-Z0-9]{2,8}$` regex, so it exercises
    report_routes.py's own typed-error path unambiguously."""
    r = await client.post("/api/v2/reports", json={"registration": "A!"})
    body = r.json()
    show("POST registration='A!' (diagnostic)", {"status": r.status_code, "body": body})
    assert r.status_code == 400, f"status={r.status_code} body={body}"
    assert body.get("error_code") == "invalid_registration", body
    assert body.get("correlation_id"), body
    return {"status": r.status_code, "body": body}


# ---------------------------------------------------------------------------
# 4i: legacy parity
# ---------------------------------------------------------------------------

async def check_legacy_parity(client: httpx.AsyncClient, pool: asyncpg.Pool) -> Dict:
    r = await client.get("/api/risk", params={"make": "FORD", "model": "FIESTA", "year": 2018})
    assert r.status_code == 200, f"/api/risk: status={r.status_code} body={r.text}"
    body = r.json()
    expected_keys = {
        "vehicle", "year", "mileage", "last_mot_date", "last_mot_result",
        "failure_risk", "confidence_level", "risk_brakes", "risk_suspension",
        "risk_tyres", "risk_steering", "risk_visibility", "risk_lamps",
        "risk_body", "repair_cost_estimate",
    }
    missing = expected_keys - set(body.keys())
    assert not missing, f"legacy /api/risk missing keys: {missing}; got {sorted(body.keys())}"
    show("GET /api/risk (legacy shape)", body)

    rv = await client.get("/api/vehicle", params={"registration": "ZZ99ABC"})
    assert rv.status_code == 200, f"/api/vehicle: status={rv.status_code} body={rv.text}"
    vbody = rv.json()
    assert vbody.get("source") == "demo", vbody
    show("GET /api/vehicle (demo)", vbody)

    rs = await client.get("/api/stats")
    assert rs.status_code == 200, f"/api/stats: status={rs.status_code} body={rs.text}"
    sbody = rs.json()
    assert "total_checks" in sbody and "checks_this_month" in sbody, sbody

    async with pool.acquire() as conn:
        db_count = await conn.fetchval("SELECT COUNT(*) FROM risk_checks")

    note = (
        "/api/stats has a 5-minute in-process TTLCache (main.py _stats_cache); "
        "this run did NOT restart uvicorn to force a cache miss, so stats_endpoint.total_checks "
        "may lag behind the true DB count if a prior /api/stats call in this process is still within "
        "its 5-minute TTL. The true count is queried directly below for comparison -- documented, not hidden."
    )
    result = {"stats_endpoint": sbody, "stats_db_count_direct": db_count, "note": note}
    show("GET /api/stats vs direct DB COUNT", result)
    return result


# ---------------------------------------------------------------------------
# 4j: Postgres-path exact_band via the seeded ZZTEST MODEL fixture
# ---------------------------------------------------------------------------

async def check_postgres_path(pool: asyncpg.Pool) -> Dict:
    """Demo-mode identity (main.DEMO_VEHICLES / the generic demo fallback)
    can never resolve to 'ZZTEST MODEL' -- there is no VRM-hash-to-make/model
    mapping in report_routes._build_demo_history, only a fixed lookup table
    of 3 curated plates plus one generic fallback identity. So the Postgres
    exact_band rung is exercised directly here, against the real production
    function (database.get_risk_v2_banded), using the synthetic mot_risk
    fixture docs/STAGING.md's bootstrap step creates:

        CREATE TABLE mot_risk (model_id, age_band, mileage_band,
            total_tests, total_failures, failure_risk, risk_brakes,
            risk_suspension, risk_tyres, risk_steering, risk_visibility,
            risk_lamps_reflectors_and_electrical_equipment,
            risk_body_chassis_structure);
        -- 6 rows for model_id='ZZTEST MODEL' across several
        -- (age_band, mileage_band) combinations -- see
        -- docs/STAGING.md / the evidence trail's 08_create_seed_mot_risk.txt
        -- for the exact seeded values this check's assertions are pinned to.
    """
    async with pool.acquire() as conn:
        seed_present = await conn.fetchval("SELECT COUNT(*) FROM mot_risk WHERE model_id = 'ZZTEST MODEL'")
    assert seed_present and seed_present > 0, (
        "no 'ZZTEST MODEL' rows in mot_risk -- staging bootstrap prerequisite missing "
        "(see docs/STAGING.md step 2)"
    )

    import database as db  # local import: only needed for this one check

    result = await db.get_risk_v2_banded("ZZTEST MODEL", "3-5", "30k-60k")
    show("database.get_risk_v2_banded('ZZTEST MODEL', '3-5', '30k-60k')", result)
    assert result is not None, "expected an exact_band result for the seeded ZZTEST MODEL fixture"
    assert result["match_scope"] == "exact_band", result
    assert result["total_tests"] == 500, result
    assert result["total_failures"] == 120, result
    assert abs(result["failure_risk"] - 0.24) < 1e-6, result
    assert result["components_available"] is True, result
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> int:
    runner = Runner()
    pool = await asyncpg.create_pool(get_database_url(), min_size=1, max_size=5)

    if args.expect_frontend_bundle == "auto":
        expect_frontend_bundle = os.path.exists(os.path.join(REPO_ROOT, "static", "index.html"))
    else:
        expect_frontend_bundle = args.expect_frontend_bundle == "true"

    try:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
            await runner.run("4a-health", "GET /health -> ok", check_health(client))
            await runner.run("4a-ready", "GET /ready -> ok, database connected", check_ready(client))
            await runner.run(
                "4a-version", "GET /api/version shape + frontend_bundle_hash",
                check_version(client, expect_frontend_bundle),
            )
            await runner.run(
                "4b-openapi", "openapi.json: legacy /api/risk params + v2 routes typed",
                check_openapi(client),
            )

            created = await runner.run(
                "4c-demo-post", "POST /api/v2/reports demo flow", check_demo_post(client, args.base_url)
            )
            await runner.run(
                "4d-db-row", "risk_checks row for the created report token",
                check_db_row(pool, created.get("report_token") if created else None, "ZZ99ABC"),
            )
            await runner.run(
                "4e-idempotency", "idempotency replay (same key) + new row (different key)",
                check_idempotency(client, pool),
            )
            await runner.run(
                "4f-round-trip", "GET created token -> byte-identical to the POST payload",
                check_get_token_roundtrip(client, created),
            )
            await runner.run(
                "4f-404", "GET unknown token -> 404 report_not_found",
                check_get_token_404(client),
            )
            await runner.run(
                "4f-future-200", "GET seeded future-expiry token -> 200",
                check_get_token_future_200(client, pool),
            )
            await runner.run(
                "4f-past-410", "GET seeded past-expiry token (not yet pseudonymised) -> 410 report_expired",
                check_get_token_past_410(client, pool),
            )
            await runner.run(
                "4g-undeclared-params", "undeclared query param -> 400 undeclared_parameter",
                check_undeclared_params(client),
            )
            await runner.run(
                "4h-error-taxonomy", "registration='A' -> 400 invalid_registration + correlation_id",
                check_invalid_registration_literal(client),
            )
            await runner.run(
                "4h-diag-regex-reject", "[diagnostic] registration='A!' -> invalid_registration",
                check_invalid_registration_diag(client),
            )
            await runner.run(
                "4i-legacy-parity", "legacy /api/risk, /api/vehicle, /api/stats shapes",
                check_legacy_parity(client, pool),
            )
            await runner.run(
                "4j-postgres-path", "Postgres exact_band via seeded ZZTEST MODEL fixture",
                check_postgres_path(pool),
            )
    finally:
        await pool.close()

    return runner.exit_code()


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoSafe staging acceptance-evidence suite.")
    parser.add_argument("--base-url", required=True, help="e.g. http://127.0.0.1:8100")
    parser.add_argument(
        "--expect-frontend-bundle", choices=["auto", "true", "false"], default="auto",
        help="Whether GET /api/version's frontend_bundle_hash should be non-null. "
             "'auto' (default) checks for static/index.html on disk next to this script's repo root.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
