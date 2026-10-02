"""Scanner-safe outcome links and explicit confirmation contract."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from utils import safe_log_path


@pytest.fixture
def outcome_client():
    assignment = {"garage_name": "Garage <script>alert(1)</script>", "vehicle_year": 2018,
                  "vehicle_make": "FORD", "vehicle_model": "FIESTA", "outcome": None}
    with patch.object(main.db, "get_lead_assignment_by_id", AsyncMock(return_value=assignment)), \
         patch.object(main.db, "is_postgres_available", AsyncMock(return_value=True)), \
         patch.object(main.db, "update_lead_assignment_outcome", AsyncMock(return_value=True)) as write:
        yield TestClient(main.app), write


@pytest.mark.parametrize("result", ["won", "lost", "no_response", "garbage"])
def test_email_link_and_prefetch_are_read_only(outcome_client, result):
    client, write = outcome_client
    for headers in ({}, {"Purpose": "prefetch", "User-Agent": "EmailLinkScanner"}):
        response = client.get(f"/api/garage/outcome/abc?result={result}", headers=headers)
        assert response.status_code == 200
        assert 'method="post"' in response.text
        assert 'name="confirmed"' in response.text
        assert "<script>alert(1)</script>" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
        assert response.headers["referrer-policy"] == "no-referrer"
    assert client.head(f"/api/garage/outcome/abc?result={result}").status_code == 405
    write.assert_not_awaited()


@pytest.mark.parametrize("body", [{"outcome": "won"}, {"outcome": "won", "confirmed": False},
                                  {"outcome": "won", "confirmed": 1}, {"outcome": "bogus", "confirmed": True},
                                  {"outcome": [], "confirmed": True}, {"outcome": "won", "confirmed": True, "extra": 1}])
def test_post_requires_explicit_valid_confirmation(outcome_client, body):
    client, write = outcome_client
    assert client.post("/api/garage/outcome/abc", json=body, headers={"Origin": "http://testserver"}).status_code == 400
    write.assert_not_awaited()


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://outside.example"},
                                     {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"}])
def test_cross_site_and_unattributed_posts_cannot_write(outcome_client, headers):
    client, write = outcome_client
    assert client.post("/api/garage/outcome/abc", json={"outcome": "won", "confirmed": True}, headers=headers).status_code == 403
    write.assert_not_awaited()


def test_confirmed_json_and_browser_form(outcome_client):
    client, write = outcome_client
    response = client.post("/api/garage/outcome/abc", json={"outcome": "won", "confirmed": True}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    write.assert_awaited_once_with("abc", "won")
    write.reset_mock()
    response = client.post("/api/garage/outcome/abc", data={"outcome": "lost", "confirmed": "yes"},
                           headers={"Sec-Fetch-Site": "same-origin"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/api/garage/outcome/abc"
    write.assert_awaited_once_with("abc", "lost")


def test_bearer_assignment_redacted_from_application_path_logs():
    assert safe_log_path("/api/garage/outcome/secret-assignment") == "/api/garage/outcome/{token}"
