import re

import pytest

from garage.api import worker
from .conftest import add_staff_and_sign_in, day, import_rows, make_workshop, sql, history

V = "/api/garage/v1"


def test_decision_history_is_full_and_ordered(env):
    owner = make_workshop(env, "A", "o@a.example")
    staff = add_staff_and_sign_in(env, owner, "s@a.example")
    bid = import_rows(owner, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    assert owner.get(f"{V}/bookings/{bid}").json()["decision"] is None
    d1 = owner.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "check_repair_history", "note": "Ask about brakes"}).json()
    d2 = staff.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "proceed_as_booked"}).json()
    d3 = owner.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "adjust_booking_plan", "note": "  Add 30 min  "}).json()
    hist = owner.get(f"{V}/bookings/{bid}/decisions").json()["items"]
    assert [h["id"] for h in hist] == [d3["id"], d2["id"], d1["id"]]                 # nothing overwritten
    assert hist[0]["note"] == "Add 30 min" and hist[1]["note"] is None
    assert hist[0]["supersedes_id"] == d2["id"] and hist[1]["supersedes_id"] == d1["id"] and hist[2]["supersedes_id"] is None
    assert [h["actor"]["email"] for h in hist] == ["o@a.example", "s@a.example", "o@a.example"]
    assert hist[0]["created_at"] and hist[0]["assessment_id"]                         # linked to the assessment version seen
    assert owner.get(f"{V}/bookings/{bid}").json()["decision"]["id"] == d3["id"]      # current = newest


def test_all_four_decision_types_and_validation(env):
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    for t in ("check_repair_history", "offer_inspection", "adjust_booking_plan", "proceed_as_booked"):
        assert c.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": t}).status_code == 201
    assert c.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "cancel_customer"}).status_code == 422
    assert c.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "offer_inspection", "note": "x" * 501}).status_code == 422
    assert c.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "offer_inspection", "customer_name": "x"}).status_code == 422
    assert c.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "offer_inspection", "note": "x" * 500}).status_code == 201


def test_database_refuses_edit_or_delete_of_decisions(env):
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    c.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "offer_inspection", "note": "n"})
    with pytest.raises(Exception, match="append-only"):
        sql(env, "UPDATE decisions SET note = 'tampered'")
    with pytest.raises(Exception, match="append-only"):
        sql(env, "DELETE FROM decisions")
    assert c.get(f"{V}/bookings/{bid}/decisions").json()["items"][0]["note"] == "n"


# ---- pilot rulings (Henri, 2026-10-25): free-text notes; staff-only estimates; flows independent of state
def test_notes_are_free_text_stored_verbatim(env):
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    text = "Brakes: ask re pads / £120 est.  (unicode ok: é)"
    assert c.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "offer_inspection", "note": text}).json()["note"] == text


def test_no_export_share_or_public_estimate_surface(env):
    from .conftest import new_client
    router = new_client(env).app.state.garage_router
    paths = [r.path for r in router.routes]
    assert not [p for p in paths if re.search(r"export|share|print|csv|public|download", p, re.I)]
    # the only unauthenticated routes are the sign-in surface and health; none returns assessment data
    from garage.api import app as app_mod
    assert all(p.startswith(app_mod.PREFIX + "/auth/") or p.endswith("/health") for p in app_mod.PUBLIC_PATHS)
    # no route negotiates CSV, and booking JSON is the only representation of an estimate
    owner = make_workshop(env, "A", "o@a.example")
    bid = import_rows(owner, [("AB12CDE", day(3), "mot")])["created_booking_ids"][0]
    r = owner.get(f"{V}/bookings", headers={"Accept": "text/csv"})
    assert r.headers["content-type"].startswith("application/json")
    anon = new_client(env)
    for p in ("/bookings", f"/bookings/{bid}", f"/bookings/{bid}/assessments"):
        assert anon.get(V + p).status_code == 401


@pytest.mark.parametrize("scenario", ["pending", "available", "unavailable", "unsupported", "failed"])
def test_booking_and_import_flows_work_in_every_assessment_state(env, scenario):
    from garage.api.dvsa import DvsaTransientError
    from garage.api.provider import ProviderResult
    reg = "AB12CDE"
    env.dvsa.set_history(reg, history(reg, [("2023-01-01", "PASSED")]))
    if scenario == "available":
        env.provider.result = ProviderResult("available", percent_rounded=30, band="medium", provider_name="t", provider_version="1")
    elif scenario == "unsupported":
        env.provider.result = ProviderResult("unsupported", reason="first_mot")
    elif scenario == "failed":
        env.dvsa.errors[reg] = DvsaTransientError
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [(reg, day(3), "mot")])["created_booking_ids"][0]
    if scenario != "pending":
        worker.drain(env.ctx)
    assert c.get(f"{V}/bookings/{bid}").json()["assessment"]["state"] == scenario
    # re-import is a clean duplicate, a new import still commits, and all booking actions still work
    assert import_rows(c, [(reg, day(3), "mot"), ("XY19ZZZ", day(4), "service")])["counts"]["new"] == 1
    assert c.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "proceed_as_booked"}).status_code == 201
    assert c.patch(f"{V}/bookings/{bid}", json={"appointment_date": str(day(6))}).status_code == 200
    assert len(c.get(f"{V}/bookings").json()["items"]) == 2
    assert c.get(f"{V}/bookings/{bid}/outcomes").status_code == 200
    assert c.patch(f"{V}/bookings/{bid}", json={"status": "cancelled"}).json()["status"] == "cancelled"
