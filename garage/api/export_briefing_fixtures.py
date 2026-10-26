"""Writes the briefing the REAL engine produces for each of the 50 fully synthetic raw-shape fixtures, for the web mock API.

    python -m garage.api.export_briefing_fixtures          # rewrites garage/web/src/mocks/briefingFixtures.json

The web mocks therefore serve genuine engine output (not hand-written briefings). A backend test regenerates
this file and fails if the committed copy is stale. Fixed cutoff, snapshot hash and fetch time keep it
deterministic; nothing here touches a database or the network.
"""
from __future__ import annotations

import json
import pathlib
from datetime import date, datetime, timezone

from . import briefing
from .dvsa import FIXTURE_DIR, DvsaHistory, DvsaTest, FakeDvsaClient
from .security import sha256_json

CUTOFF = date(2026, 10, 26)
CUTOFF_AT = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)
FETCHED_AT = "2026-10-26T10:00:00+00:00"
EVIDENCE_OUT = pathlib.Path(__file__).resolve().parents[1] / "web" / "src" / "mocks" / "fixtureEvidence.json"
OUT = pathlib.Path(__file__).resolve().parents[1] / "web" / "src" / "mocks" / "briefingFixtures.json"


def fixture_plates() -> list[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("ZZ[0-9][0-9]FXT.json"))


def fixture_briefings() -> dict:
    fake = FakeDvsaClient()
    out = {}
    for reg in fixture_plates():
        hist = fake.fetch_history(reg).to_json()
        sha = sha256_json(hist)
        out[reg] = _with_source(briefing.build(hist, "ok", sha, CUTOFF, CUTOFF_AT), sha, "ok")
    # Two edge cases the web mock needs for registrations that are not fixtures (also real engine output):
    out["__not_found__"] = _with_source(briefing.build(None, "not_found", sha256_json({"nf": "ZZ00NFX"}), CUTOFF, CUTOFF_AT),
                                        sha256_json({"nf": "ZZ00NFX"}), "not_found")
    later = DvsaHistory(registration="ZZ00NPX", tests=(DvsaTest(
        completed_at=datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc), result="PASSED", odometer_value=1000,
        odometer_unit="MI"),))
    out["__no_prior_test__"] = _with_source(
        briefing.build(later.to_json(), "ok", sha256_json(later.to_json()), CUTOFF, CUTOFF_AT),
        sha256_json(later.to_json()), "ok")
    return out


def _with_source(b: dict, sha: str, status: str) -> dict:
    b["history_cutoff_at"] = CUTOFF_AT.isoformat()
    b["source"] = {"snapshot_sha256": sha, "upstream_status": status, "fetched_at": FETCHED_AT}
    return b


def fixture_evidence() -> dict:
    fake = FakeDvsaClient()
    out = {}
    for reg in fixture_plates():
        tests = fake.fetch_history(reg).before(CUTOFF, CUTOFF_AT).tests
        out[reg] = {"prior_test_count": len(tests), "latest_prior_test_date": max(t.test_date for t in tests).isoformat() if tests else None,
                    "history_fetched_at": FETCHED_AT}
    return out


def render() -> str:
    return json.dumps(fixture_briefings(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":   # pragma: no cover
    OUT.write_text(render())
    EVIDENCE_OUT.write_text(json.dumps(fixture_evidence(), indent=1, sort_keys=True) + "\n")
    print("wrote", OUT, len(fixture_plates()), "briefings")
