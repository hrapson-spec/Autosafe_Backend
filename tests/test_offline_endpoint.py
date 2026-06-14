"""End-to-end offline test of GET /api/risk/v55 via DVSA replay mode.

Sets DVSA_FIXTURE_DIR so the whole FastAPI app serves predictions from local
fixtures with no network and no credentials. The replay seam removes any need
for an HTTP-mocking library.
"""
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

GOLDEN_DIR = REPO / "tests/fixtures/golden"
GOLDEN_PRED = json.loads((REPO / "tests/fixtures/golden_predictions.json").read_text())
GOLDEN_IDS = [k for k in GOLDEN_PRED if k != "_meta"]


@pytest.fixture(scope="module")
def client():
    os.environ["DVSA_FIXTURE_DIR"] = str(GOLDEN_DIR)
    os.environ.setdefault("PREDICTIONS_ENABLED", "true")
    # The DVSA client is a module-level singleton whose is_configured is fixed
    # at construction. An earlier test in the suite may have built it without
    # replay mode, so force a rebuild now that DVSA_FIXTURE_DIR is set, and drop
    # any response-cache entries a prior test left behind.
    import dvsa_client as dc
    dc._dvsa_client = None
    from fastapi.testclient import TestClient
    import main
    main._cache.clear()
    with TestClient(main.app) as c:
        yield c
    os.environ.pop("DVSA_FIXTURE_DIR", None)
    dc._dvsa_client = None
    main._cache.clear()


@pytest.mark.parametrize("tid", GOLDEN_IDS[:6])
def test_endpoint_matches_golden(client, tid):
    """The live endpoint (replayed) returns the recorded calibrated risk."""
    r = client.get(f"/api/risk/v55?registration={tid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_version"] == "v55", body
    assert body["failure_risk"] == pytest.approx(
        GOLDEN_PRED[tid]["failure_risk"], abs=2e-4), body


def test_endpoint_unknown_vrm_falls_back(client):
    """A VRM with no fixture exercises the not-found fallback (lookup/avg),
    not a 500."""
    r = client.get("/api/risk/v55?registration=ZZ99ZZZ")
    assert r.status_code == 200, r.text
    assert r.json()["model_version"] == "lookup"


def test_kill_switch_offline(client, monkeypatch):
    """Kill switch still gates predictions in replay mode."""
    import main
    monkeypatch.setattr(main, "PREDICTIONS_ENABLED", False)
    r = client.get(f"/api/risk/v55?registration={GOLDEN_IDS[0]}")
    assert r.status_code == 503
