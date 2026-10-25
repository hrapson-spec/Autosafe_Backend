import threading

import pytest

from .conftest import day, import_rows, make_workshop, sql

V = "/api/garage/v1"


def preview(c, **body):
    r = c.post(f"{V}/imports/preview", json=body)
    return r


def man(c, rows):
    return preview(c, format="manual", rows=[{"registration": a, "date": b, "type": t} for a, b, t in rows])


def test_import_idempotent_across_imports_and_commits(env):
    c = make_workshop(env, "A", "o@a.example")
    rows = [("AB12 CDE", str(day(2)), "MOT"), ("XY19ZZZ", str(day(3)), "service + MOT")]
    p = man(c, rows).json()
    assert p["counts"] == {"total": 2, "new": 2, "duplicate_existing": 0, "duplicate_in_batch": 0, "invalid": 0}
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 0          # preview creates nothing
    first = c.post(f"{V}/imports/{p['import_id']}/commit").json()
    assert first["already_committed"] is False and len(first["created_booking_ids"]) == 2
    again = c.post(f"{V}/imports/{p['import_id']}/commit").json()                # same preview twice
    assert again["already_committed"] is True and again["created_booking_ids"] == first["created_booking_ids"]
    # same rows in a brand-new import: all duplicates, nothing created
    p2 = man(c, rows).json()
    assert p2["counts"]["duplicate_existing"] == 2 and p2["counts"]["new"] == 0
    assert all(r["existing_booking_id"] for r in p2["rows"])
    r2 = c.post(f"{V}/imports/{p2['import_id']}/commit").json()
    assert r2["created_booking_ids"] == []
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 2
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind='assess_booking'")[0]["n"] == 2


def test_assessment_and_outcome_jobs_not_duplicated_by_repeat(env):
    c = make_workshop(env, "A", "o@a.example")
    for _ in range(3):
        import_rows(c, [("AB12CDE", day(2), "mot")])
    assert sql(env, "SELECT count(*) AS n FROM assessments")[0]["n"] == 1
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind='outcome_check'")[0]["n"] == 4


def test_concurrent_commits_of_same_preview_create_one_set(env):
    c = make_workshop(env, "A", "o@a.example")
    p = man(c, [("AB12CDE", str(day(2)), "mot"), ("AB13CDE", str(day(2)), "mot")]).json()
    res = []

    def go():
        res.append(c.post(f"{V}/imports/{p['import_id']}/commit").json())
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r["already_committed"] for r in res) == [False, True, True, True]
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 2


def test_two_previews_racing_do_not_double_create(env):
    c = make_workshop(env, "A", "o@a.example")
    rows = [("AB12CDE", str(day(2)), "mot")]
    p1, p2 = man(c, rows).json(), man(c, rows).json()           # both say "new"
    assert p1["counts"]["new"] == p2["counts"]["new"] == 1
    c.post(f"{V}/imports/{p1['import_id']}/commit")
    r = c.post(f"{V}/imports/{p2['import_id']}/commit").json()
    assert r["created_booking_ids"] == [] and r["counts"]["duplicate_existing"] == 1
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 1


def test_duplicate_rows_within_one_import(env):
    c = make_workshop(env, "A", "o@a.example")
    p = man(c, [("AB12CDE", str(day(2)), "mot"), ("ab12 cde", str(day(2)), "MOT"),
                ("AB12CDE", str(day(2)), "service")]).json()
    st = [r["status"] for r in p["rows"]]
    assert st == ["new", "duplicate_in_batch", "new"]            # different type is a different booking
    assert p["rows"][1]["duplicate_of_row"] == 1
    out = c.post(f"{V}/imports/{p['import_id']}/commit").json()
    assert len(out["created_booking_ids"]) == 2


@pytest.mark.parametrize("raw,code", [
    ("", "missing_registration"), ("   ", "missing_registration"), ("A", "invalid_registration"),
    ("ABCDEFGHI", "invalid_registration"), ("AB12-CDE", "invalid_registration"), ("AB12 C!E", "invalid_registration"),
    ("ÀB12CDE", "invalid_registration"),
])
def test_malformed_registrations_rejected_per_row(env, raw, code):
    c = make_workshop(env, "A", "o@a.example")
    p = man(c, [(raw, str(day(2)), "mot"), ("AB12CDE", str(day(2)), "mot")]).json()
    assert p["rows"][0]["status"] == "invalid" and p["rows"][0]["errors"][0]["code"] == code
    assert p["rows"][1]["status"] == "new"                        # one bad row does not poison the batch
    out = c.post(f"{V}/imports/{p['import_id']}/commit").json()
    assert len(out["created_booking_ids"]) == 1 and out["counts"]["invalid"] == 1


def test_nonstandard_but_valid_plate_warns_not_errors(env):
    c = make_workshop(env, "A", "o@a.example")
    p = man(c, [("A1", str(day(1)), "mot"), ("ZZZZZ", str(day(1)), "mot")]).json()
    assert p["rows"][0]["status"] == "new" and p["rows"][0]["warnings"] == []          # A1 is a dateless format
    assert p["rows"][1]["status"] == "new" and p["rows"][1]["warnings"][0]["code"] == "nonstandard_registration_format"


@pytest.mark.parametrize("raw,code", [
    ("", "missing_date"), (None, "missing_date"), ("tomorrow", "invalid_date"), ("2026-13-40", "invalid_date"),
    ("31/02/2027", "invalid_date"), ("11/02/26", "invalid_date"), ("1999-01-01", "date_out_of_range"),
    ("2099-01-01", "date_out_of_range"),
])
def test_missing_or_bad_dates(env, raw, code):
    c = make_workshop(env, "A", "o@a.example")
    r = preview(c, format="manual", rows=[{"registration": "AB12CDE", "date": raw or "", "type": "mot"}]) \
        if raw is not None else None
    if r is None:
        r = preview(c, format="csv", content="registration,date,type\nAB12CDE,,mot\n")
    row = r.json()["rows"][0]
    assert row["status"] == "invalid" and row["errors"][0]["code"] == code


def test_uk_and_iso_dates_and_mixed_booking_types(env):
    c = make_workshop(env, "A", "o@a.example")
    d = day(5)
    p = man(c, [("AB12CDE", d.strftime("%d/%m/%Y"), "MOT"), ("AB13CDE", str(d), "Service"),
                ("AB14CDE", str(d), "mot retest"), ("AB15CDE", str(d), "Service + MOT"),
                ("AB16CDE", str(d), "full service and mot"), ("AB17CDE", str(d), "valet")]).json()
    assert [r["booking_type"] for r in p["rows"]] == ["mot", "service", "retest", "service_mot", "service_mot", None]
    assert p["rows"][0]["appointment_date"] == str(d)
    assert p["rows"][5]["errors"][0]["code"] == "invalid_type"
    assert man(c, [("AB12CDE", str(d), "")]).json()["rows"][0]["errors"][0]["code"] == "missing_type"
    out = c.post(f"{V}/imports/{p['import_id']}/commit").json()
    bks = {b["registration"]: b for b in c.get(f"{V}/bookings").json()["items"]}
    assert bks["AB13CDE"]["assessment"]["state"] == "unsupported" and bks["AB13CDE"]["assessment"]["reason"] == "service_only"
    assert bks["AB14CDE"]["assessment"]["reason"] == "retest_booking"
    assert bks["AB12CDE"]["assessment"]["state"] == "pending" and bks["AB15CDE"]["assessment"]["state"] == "pending"
    # service-only bookings get no DVSA jobs and no outcome jobs
    assert not sql(env, "SELECT 1 FROM jobs j WHERE j.payload->>'booking_id' = %s", (bks["AB13CDE"]["id"],))


def test_paste_formats_and_header(env):
    c = make_workshop(env, "A", "o@a.example")
    d = str(day(4))
    text = f"Reg\tDate\tType\nAB12 CDE\t{d}\tMOT\n\nAB13CDE, {d}, service + MOT\nAB14 CDE {d} retest\nAB15CDE;{d};service\n"
    p = preview(c, format="paste", content=text).json()
    assert p["counts"]["total"] == 4 and p["counts"]["new"] == 4
    assert [r["booking_type"] for r in p["rows"]] == ["mot", "service_mot", "retest", "service"]
    assert p["rows"][2]["registration"] == "AB14CDE"


def test_csv_columns_any_order_aliases_quotes(env):
    c = make_workshop(env, "A", "o@a.example")
    d = str(day(4))
    csv = f'VRM,Booking Type,Appointment Date,Notes\n"AB12 CDE","MOT, annual",{d},ignored\nAB13CDE,service,{d},\n'
    p = preview(c, format="csv", content=csv).json()
    assert p["rows"][0]["status"] == "invalid" and p["rows"][0]["errors"][0]["code"] == "invalid_type"  # "MOT, annual"
    assert p["rows"][1]["status"] == "new" and p["rows"][1]["booking_type"] == "service"
    bad = preview(c, format="csv", content="registration,when\nAB12CDE,x\n")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"


def test_row_limit_200(env):
    c = make_workshop(env, "A", "o@a.example")
    d = str(day(3))
    ok = [(f"AB{i:02d}CDE", d, "mot") for i in range(200)]
    assert man(c, ok).json()["counts"]["new"] == 200
    r = man(c, ok + [("ZZ99ZZZ", d, "mot")])
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_rows"
    assert sql(env, "SELECT count(*) AS n FROM imports")[0]["n"] == 1           # rejected request stored nothing
    csv = "registration,date,type\n" + "\n".join(f"AB{i:03d}CD,{d},mot" for i in range(201))
    assert preview(c, format="csv", content=csv).json()["error"]["code"] == "too_many_rows"
    assert preview(c, format="paste", content="").status_code == 422


def test_preview_expires(env):
    c = make_workshop(env, "A", "o@a.example")
    p = man(c, [("AB12CDE", str(day(2)), "mot")]).json()
    env.clock.advance(minutes=61)
    r = c.post(f"{V}/imports/{p['import_id']}/commit")
    assert r.status_code == 410 and r.json()["error"]["code"] == "import_expired"
    assert c.get(f"{V}/imports/{p['import_id']}").json()["status"] == "expired"


def test_manual_single_booking_idempotent(env):
    c = make_workshop(env, "A", "o@a.example")
    body = {"registration": "ab12 cde", "date": str(day(2)), "type": "mot"}
    r1, r2 = c.post(f"{V}/bookings", json=body), c.post(f"{V}/bookings", json=body)
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert c.post(f"{V}/bookings", json={**body, "date": ""}).status_code == 422


def test_reimport_of_old_date_after_reschedule_is_duplicate(env):
    c = make_workshop(env, "A", "o@a.example")
    bid = import_rows(c, [("AB12CDE", day(2), "mot")])["created_booking_ids"][0]
    assert c.patch(f"{V}/bookings/{bid}", json={"appointment_date": str(day(9))}).status_code == 200
    for d in (day(2), day(9)):                                    # original date and current date both match
        assert man(c, [("AB12CDE", str(d), "mot")]).json()["rows"][0]["status"] == "duplicate_existing"


def test_no_customer_pii_columns_anywhere(env):
    cols = sql(env, "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'")
    bad = [(c["table_name"], c["column_name"]) for c in cols
           if any(w in c["column_name"] for w in ("phone", "mobile", "customer", "surname", "firstname"))]
    assert bad == []
    allowed_email_name = {("users", "email"), ("workshops", "name")}
    assert {(c["table_name"], c["column_name"]) for c in cols if c["column_name"] in ("email", "name")} == allowed_email_name
    # the import contract accepts only registration/date/type
    assert preview(make_workshop(env, "B", "b@b.example"), format="manual",
                   rows=[{"registration": "AB12CDE", "date": str(day(1)), "type": "mot", "customer": "X"}]).status_code == 422
