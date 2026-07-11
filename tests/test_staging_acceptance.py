"""Tests for release-gating staging acceptance assertions."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional
import httpx
import pytest

from scripts.staging_acceptance import check_version
from scripts.seed_staging_data import build_rows


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
