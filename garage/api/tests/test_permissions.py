"""Owner vs staff."""
from .conftest import add_staff_and_sign_in, day, import_rows, make_workshop

V = "/api/garage/v1"


def test_owner_vs_staff_matrix(env):
    owner = make_workshop(env, "A", "o@a.example")
    staff = add_staff_and_sign_in(env, owner, "s@a.example")
    out = import_rows(owner, [("AA19AAA", day(3), "mot")])
    bid = out["created_booking_ids"][0]
    mid = next(m["membership_id"] for m in owner.get(f"{V}/staff").json()["items"] if m["role"] == "staff")

    # owner-only
    assert staff.post(f"{V}/staff", json={"email": "x@a.example"}).status_code == 403
    assert staff.delete(f"{V}/staff/{mid}").status_code == 403
    assert staff.patch(f"{V}/workshop", json={"name": "Hijack"}).status_code == 403
    assert owner.patch(f"{V}/workshop", json={"name": "Renamed"}).json()["name"] == "Renamed"
    # staff may do day-to-day work
    assert staff.get(f"{V}/staff").status_code == 200
    assert staff.get(f"{V}/workshop").status_code == 200
    assert import_rows(staff, [("BB20BBB", day(5), "service_mot")])["counts"]["new"] == 1
    assert staff.post(f"{V}/bookings/{bid}/decisions", json={"decision_type": "offer_inspection"}).status_code == 201
    assert staff.post(f"{V}/bookings/{bid}/assessments").status_code in (202, 409)
    assert staff.patch(f"{V}/bookings/{bid}", json={"appointment_date": str(day(6))}).status_code == 200
    # the actor on a decision is the staff member, with their role
    d = staff.get(f"{V}/bookings/{bid}/decisions").json()["items"][0]
    assert d["actor"]["email"] == "s@a.example" and d["actor"]["role"] == "staff"


def test_staff_cannot_revoke_other_sessions_but_owner_can_not_remove_self(env):
    owner = make_workshop(env, "A", "o@a.example")
    staff = add_staff_and_sign_in(env, owner, "s@a.example")
    osid = next(x["id"] for x in owner.get(f"{V}/sessions").json()["items"] if x["email"] == "o@a.example")
    assert staff.delete(f"{V}/sessions/{osid}").status_code == 404
    oid = next(m["membership_id"] for m in owner.get(f"{V}/staff").json()["items"] if m["role"] == "owner")
    r = owner.delete(f"{V}/staff/{oid}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "cannot_remove_owner"


def test_add_existing_member_conflicts_and_removed_member_can_return(env):
    owner = make_workshop(env, "A", "o@a.example")
    add_staff_and_sign_in(env, owner, "s@a.example")
    assert owner.post(f"{V}/staff", json={"email": "S@A.example"}).status_code == 409   # case-insensitive
    mid = next(m["membership_id"] for m in owner.get(f"{V}/staff").json()["items"] if m["role"] == "staff")
    owner.delete(f"{V}/staff/{mid}")
    assert owner.post(f"{V}/staff", json={"email": "s@a.example"}).status_code == 201
