"""Tests for release-gating staging acceptance assertions."""

import asyncio
import httpx
import pytest

from scripts.staging_acceptance import check_version


def _version_transport(backend_sha: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        return httpx.Response(
            200,
            json={
                "backend_sha": backend_sha,
                "frontend_bundle_hash": "0123456789abcdef",
                "contract_version": "2.0",
                "app_version": "2.0.0",
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
