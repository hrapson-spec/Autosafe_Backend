"""Tests for release-gating staging acceptance assertions."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional
import httpx
import pytest

from scripts.staging_acceptance import check_demo_post, check_version
from scripts.seed_staging_data import (
    MOT_RISK_FIXTURE_MODEL,
    WIPE_RISK_CHECKS_SQL,
    build_lead_fixture,
    build_mot_risk_fixture,
    build_rows,
)


def _version_transport(
    backend_sha: str,
    frontend_sha: Optional[str] = None,
    build_timestamp: Optional[str] = "2026-07-11T10:00:00Z",
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        return httpx.Response(
            200,
            json={
                "backend_sha": backend_sha,
                "frontend_sha": frontend_sha or backend_sha,
                "frontend_bundle_hash": "0" * 64,
                "contract_version": "2.0",
                "app_version": "2.0.0",
                "build_timestamp": build_timestamp,
                "started_at": "2026-07-11T10:00:00+00:00",
            },
        )

    return httpx.MockTransport(handler)


def test_version_check_accepts_the_expected_release_sha():
    async def run_check():
        async with httpx.AsyncClient(
            transport=_version_transport("abc123"), base_url="http://staging"
        ) as client:
            return await check_version(
                client,
                expect_frontend_bundle=True,
                expected_backend_sha="abc123",
            )

    body = asyncio.run(run_check())
    assert body["backend_sha"] == "abc123"
    assert body["frontend_sha"] == "abc123"
    assert body["build_timestamp"] == "2026-07-11T10:00:00Z"


def test_version_check_rejects_a_different_release_sha():
    async def run_check():
        async with httpx.AsyncClient(
            transport=_version_transport("wrong-sha"), base_url="http://staging"
        ) as client:
            return await check_version(
                client,
                expect_frontend_bundle=True,
                expected_backend_sha="abc123",
            )

    with pytest.raises(AssertionError, match="expected backend_sha='abc123'"):
        asyncio.run(run_check())


def test_version_check_rejects_unknown_as_a_release_identity():
    async def run_check():
        async with httpx.AsyncClient(
            transport=_version_transport("unknown"), base_url="http://staging"
        ) as client:
            return await check_version(
                client,
                expect_frontend_bundle=True,
                expected_backend_sha="unknown",
            )

    with pytest.raises(AssertionError, match="must be a concrete commit SHA"):
        asyncio.run(run_check())


def test_version_check_rejects_a_mismatched_frontend_sha():
    async def run_check():
        async with httpx.AsyncClient(
            transport=_version_transport("abc123", frontend_sha="different"),
            base_url="http://staging",
        ) as client:
            return await check_version(
                client,
                expect_frontend_bundle=True,
                expected_backend_sha="abc123",
            )

    with pytest.raises(AssertionError, match="expected frontend_sha='abc123'"):
        asyncio.run(run_check())


def test_version_check_rejects_missing_build_timestamp():
    async def run_check():
        async with httpx.AsyncClient(
            transport=_version_transport("abc123", build_timestamp=None),
            base_url="http://staging",
        ) as client:
            return await check_version(
                client,
                expect_frontend_bundle=True,
                expected_backend_sha="abc123",
            )

    with pytest.raises(AssertionError, match="build_timestamp"):
        asyncio.run(run_check())


def test_demo_post_validates_the_public_share_base_not_the_internal_transport():
    response = {
        "vehicle_data_source": "demo",
        "mileage": {"source": "observed_mot"},
        "evidence": {"match_scope": "population_default", "total_tests": None},
        "risk": {"confidence": "Very Low"},
        "persistence": {"saved": True},
        "report_token": "opaque-token",
        "share_url": "http://public-staging/app/report/opaque-token",
        "prediction_source": "dataset_reference",
    }

    async def run_check():
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json=response)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://internal-app:8000",
        ) as client:
            return await check_demo_post(client, "http://public-staging")

    assert asyncio.run(run_check()) == response


def test_seeded_saved_payloads_share_the_database_row_identity():
    rows, _future_token, _past_token = build_rows(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    )

    for row in rows:
        payload_json = row[15]
        if payload_json is None:
            continue
        payload = json.loads(payload_json)
        row_id = row[22]
        assert row_id is not None
        assert payload["report_id"] == str(row_id)


def test_mot_risk_fixture_pins_the_exact_postgres_acceptance_cohort():
    rows = build_mot_risk_fixture()

    exact = [
        row
        for row in rows
        if row[0] == MOT_RISK_FIXTURE_MODEL
        and row[1] == "3-5"
        and row[2] == "30k-60k"
    ]

    assert len(rows) == 6
    assert len(exact) == 1
    assert exact[0][3:6] == (500, 120, 0.24)
    assert all(value is not None for value in exact[0][6:])


def test_lead_retention_fixture_has_old_and_recent_rows_with_assignments():
    now = datetime(2026, 7, 11, 12, 0)
    garage, leads, assignments = build_lead_fixture(now)

    assert garage[2].endswith("@staging.invalid")
    assert len(leads) == 5
    assert len(assignments) == 5
    assert len({row[0] for row in leads}) == 5
    assert {row[1] for row in assignments} == {row[0] for row in leads}
    assert sum(row[8] < now.replace(day=1) for row in leads) == 3
    assert all(row[1].endswith("@staging.invalid") for row in leads)


def test_staging_wipe_never_matches_every_pseudonymised_row():
    assert "vrm_hmac IS NOT NULL" not in WIPE_RISK_CHECKS_SQL
    assert "vehicle_make = 'ZZTEST'" in WIPE_RISK_CHECKS_SQL
