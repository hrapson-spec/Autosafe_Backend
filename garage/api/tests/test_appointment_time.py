"""Optional appointment time (contract gap 6): display and ordering only."""
import pytest

from garage.api import importing, worker
from .conftest import commit_and_run, day, history, import_rows, make_workshop, sql

V = "/api/garage/v1"


def man(c, rows):
    return c.post(f"{V}/imports/preview", json={"format": "manual", "rows": [
        {"registration": r, "date": str(d), "type": t, **({"time": tm} if tm is not None else {})} for r, d, t, tm in rows]})


@pytest.mark.parametrize("raw,want", [("09:30", "09:30"), ("9:30", "09:30"), ("0930", "09:30"), ("9.30", "09:30"),
                                      ("00:00", "00:00"), ("23:59", "23:59"), (" 14:05 ", "14:05"), ("", None), (None, None)])
def test_parse_time_accepts_common_24_hour_forms(raw, want):
    t, errs = importing.parse_time(raw)
    assert errs == [] and (t.strftime("%H:%M") if t else None) == want


@pytest.mark.parametrize("raw", ["24:00", "9:60", "25:10", "noon", "9am", "930pm", "12:5", "1230pm"])
def test_parse_time_rejects_the_rest(raw):
    t, errs = importing.parse_time(raw)
    assert t is None and errs[0].code == "invalid_time"


def test_manual_rows_with_and_without_time_and_an_invalid_one(env):
    c = make_workshop(env, "A", "o@a.example")
    p = man(c, [("AB12CDE", day(2), "mot", "9:30"), ("AB13CDE", day(2), "mot", None), ("AB14CDE", day(2), "mot", "25:00"),
                ("AB15CDE", day(2), "mot", "")]).json()
    assert [r["appointment_time"] for r in p["rows"]] == ["09:30", None, None, None]
    assert [r["status"] for r in p["rows"]] == ["new", "new", "invalid", "new"]
    assert p["rows"][2]["errors"][0]["code"] == "invalid_time"
    rec = commit_and_run(c, p["import_id"])
    bks = {b["registration"]: b for b in c.get(f"{V}/bookings").json()["items"]}
    assert bks["AB12CDE"]["appointment_time"] == "09:30" and bks["AB13CDE"]["appointment_time"] is None
    assert len(rec["created_booking_ids"]) == 3


def test_paste_and_csv_carry_the_time(env):
    c = make_workshop(env, "A", "o@a.example")
    d = str(day(3))
    text = (f"Reg\tDate\tType\tTime\nAB12 CDE\t{d}\tMOT\t08:15\nAB13CDE, {d}, 10:45, service + MOT\n"
            f"AB14 CDE {d} 1130 retest\nAB15CDE {d} MOT 16.00\nAB16CDE;{d};service;\nAB17CDE, {d}, service + MOT\n")
    p = c.post(f"{V}/imports/preview", json={"format": "paste", "content": text}).json()
    assert [(r["registration"], r["booking_type"], r["appointment_time"]) for r in p["rows"]] == [
        ("AB12CDE", "mot", "08:15"), ("AB13CDE", "service_mot", "10:45"), ("AB14CDE", "retest", "11:30"),
        ("AB15CDE", "mot", "16:00"), ("AB16CDE", "service", None), ("AB17CDE", "service_mot", None)]
    csv = f"VRM,Appointment Time,Booking Type,Appointment Date\nAB21CDE,13:00,mot,{d}\nAB22CDE,,mot,{d}\nAB23CDE,bad,mot,{d}\n"
    q = c.post(f"{V}/imports/preview", json={"format": "csv", "content": csv}).json()
    assert [r["appointment_time"] for r in q["rows"]] == ["13:00", None, None]
    assert q["rows"][2]["status"] == "invalid" and q["rows"][2]["errors"][0]["code"] == "invalid_time"
    # a CSV without any time column still works
    assert c.post(f"{V}/imports/preview", json={"format": "csv", "content": f"registration,date,type\nAB24CDE,{d},mot\n"}).status_code == 200


def test_time_is_not_part_of_a_bookings_identity(env):
    c = make_workshop(env, "A", "o@a.example")
    first = man(c, [("AB12CDE", day(2), "mot", "09:00")]).json()
    commit_and_run(c, first["import_id"])
    again = man(c, [("AB12CDE", day(2), "mot", "15:00")]).json()
    assert again["rows"][0]["status"] == "duplicate_existing" and again["counts"]["new"] == 0
    body = {"registration": "AB12CDE", "date": str(day(2)), "type": "mot", "time": "17:00"}
    r = c.post(f"{V}/bookings", json=body)
    assert r.status_code == 200 and r.json()["appointment_time"] == "09:00"           # existing booking returned unchanged
    r = c.post(f"{V}/bookings", json={**body, "registration": "AB13CDE", "time": "17:00"})
    assert r.status_code == 201 and r.json()["appointment_time"] == "17:00"
    assert c.post(f"{V}/bookings", json={**body, "registration": "AB14CDE", "time": "99:00"}).status_code == 422


def test_patch_sets_clears_and_validates_the_time_without_marking_a_reschedule(env):
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(2), "mot", "09:00")])["created_booking_ids"][0]
    before_jobs = sql(env, "SELECT count(*) AS n FROM jobs WHERE kind = 'outcome_check' AND status = 'queued'")[0]["n"]
    r = c.patch(f"{V}/bookings/{bid}", json={"appointment_time": "11:15"})
    assert r.status_code == 200 and r.json()["appointment_time"] == "11:15" and r.json()["rescheduled"] is False
    assert c.patch(f"{V}/bookings/{bid}", json={"appointment_time": None}).json()["appointment_time"] is None
    assert c.patch(f"{V}/bookings/{bid}", json={"appointment_time": "7pm"}).status_code == 422
    assert c.patch(f"{V}/bookings/{bid}", json={}).status_code == 422                # still "nothing to update"
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind = 'outcome_check' AND status = 'queued'")[0]["n"] == before_jobs
    ev = sql(env, "SELECT detail FROM booking_events WHERE event = 'rescheduled' ORDER BY created_at")
    assert [e["detail"] for e in ev] == [{"time_from": "09:00", "time_to": "11:15"}, {"time_from": "11:15", "time_to": None}]
    # a date change keeps the time
    c.patch(f"{V}/bookings/{bid}", json={"appointment_time": "08:00"})
    r = c.patch(f"{V}/bookings/{bid}", json={"appointment_date": str(day(5))})
    assert r.json()["appointment_time"] == "08:00" and r.json()["rescheduled"] is True


def test_time_never_changes_the_assessment_cutoff(env):
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [(str(day(-30)), "PASSED"), (str(day(0)), "PASSED")]))
    c = make_workshop(env, "A", "o@a.example")
    for reg, tm in (("AB12CDE", None), ("AB13CDE", "00:01")):
        env.dvsa.set_history(reg, env.dvsa.histories["AB12CDE"].__class__(**{**env.dvsa.histories["AB12CDE"].__dict__, "registration": reg}))
        bid = import_rows(c, [(reg, day(0), "mot", tm)] if tm else [(reg, day(0), "mot")])["created_booking_ids"][0]
        worker.drain(env.ctx)
        a = c.get(f"{V}/bookings/{bid}").json()["assessment"]
        assert a["input_cutoff_date"] == str(day(0)) and a["evidence"]["prior_test_count"] == 1


def test_list_orders_by_date_then_time_then_untimed_then_id_and_paginates_stably(env):
    c = make_workshop(env, "A", "o@a.example")
    rows = [("AB01CDE", day(2), "mot", "14:00"), ("AB02CDE", day(2), "mot", None), ("AB03CDE", day(2), "mot", "08:30"),
            ("AB04CDE", day(1), "mot", None), ("AB05CDE", day(2), "mot", "08:30"), ("AB06CDE", day(3), "mot", "07:00"),
            ("AB07CDE", day(2), "mot", "00:00")]
    import_rows(c, rows)
    want_regs = ["AB04CDE", "AB07CDE", "AB03CDE", "AB05CDE", "AB01CDE", "AB02CDE", "AB06CDE"]
    full = c.get(f"{V}/bookings").json()["items"]
    got = [b["registration"] for b in full]
    assert got[:1] == ["AB04CDE"] and got[1:2] == ["AB07CDE"] and got[4:7] == ["AB01CDE", "AB02CDE", "AB06CDE"]
    assert set(got[2:4]) == {"AB03CDE", "AB05CDE"}                       # same time: id order (any, but stable)
    for limit in (1, 2, 3):
        seen, cursor = [], None
        for _ in range(20):
            r = c.get(f"{V}/bookings", params={"limit": limit, **({"cursor": cursor} if cursor else {})}).json()
            seen += [b["registration"] for b in r["items"]]
            cursor = r["next_cursor"]
            if not cursor:
                break
        assert seen == got, limit                                        # no gaps, no repeats across pages
    assert c.get(f"{V}/bookings", params={"cursor": "bm9wZQ=="}).status_code == 422
