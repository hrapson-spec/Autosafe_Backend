import threading

import pytest

from .conftest import commit_and_run, day, import_rows, make_workshop, sql

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
    q = c.post(f"{V}/imports/{p['import_id']}/commit")
    assert q.status_code == 202 and q.json()["status"] == "committing" and q.json()["already_committed"] is False
    assert q.json()["progress"] == {"processed": 0, "total": 2}
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 0          # nothing is created until the job runs
    first = commit_and_run(c, p["import_id"])
    assert first["status"] == "committed" and len(first["created_booking_ids"]) == 2
    assert first["progress"] == {"processed": 2, "total": 2}
    again = c.post(f"{V}/imports/{p['import_id']}/commit")                       # same preview twice
    assert again.status_code == 200 and again.json()["already_committed"] is True
    assert again.json()["created_booking_ids"] == first["created_booking_ids"]
    # same rows in a brand-new import: all duplicates, nothing created
    p2 = man(c, rows).json()
    assert p2["counts"]["duplicate_existing"] == 2 and p2["counts"]["new"] == 0
    assert all(r["existing_booking_id"] for r in p2["rows"])
    r2 = commit_and_run(c, p2["import_id"])
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
        res.append(c.post(f"{V}/imports/{p['import_id']}/commit"))
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [r.status_code for r in res] == [202] * 4                     # all see "committing"
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind = 'import_commit'")[0]["n"] == 1   # one job only
    from garage.api import worker
    worker.drain(env.ctx, kinds=("import_commit",))
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 2
    again = c.post(f"{V}/imports/{p['import_id']}/commit")
    assert again.status_code == 200 and again.json()["already_committed"] is True


def test_two_previews_racing_do_not_double_create(env):
    c = make_workshop(env, "A", "o@a.example")
    rows = [("AB12CDE", str(day(2)), "mot")]
    p1, p2 = man(c, rows).json(), man(c, rows).json()           # both say "new"
    assert p1["counts"]["new"] == p2["counts"]["new"] == 1
    commit_and_run(c, p1["import_id"])
    r = commit_and_run(c, p2["import_id"])
    assert r["created_booking_ids"] == [] and r["counts"]["duplicate_existing"] == 1
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 1


def test_duplicate_rows_within_one_import(env):
    c = make_workshop(env, "A", "o@a.example")
    p = man(c, [("AB12CDE", str(day(2)), "mot"), ("ab12 cde", str(day(2)), "MOT"),
                ("AB12CDE", str(day(2)), "service")]).json()
    st = [r["status"] for r in p["rows"]]
    assert st == ["new", "duplicate_in_batch", "new"]            # different type is a different booking
    assert p["rows"][1]["duplicate_of_row"] == 1
    out = commit_and_run(c, p["import_id"])
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
    out = commit_and_run(c, p["import_id"])
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
    out = commit_and_run(c, p["import_id"])
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


# ------------------------------------------------------------------ asynchronous commit (contract gap 7)
def seed_rows(c, n, d=None):
    d = d or str(day(2))
    return man(c, [(f"AB{i:02d}CDE", d, "mot") for i in range(n)]).json()


def test_commit_returns_a_job_and_progress_advances_one_batch_per_worker_step(make_env):
    from garage.api import worker
    env = make_env(import_commit_batch_size=2)
    from .conftest import make_workshop as mk
    c = mk(env, "A", "o@a.example")
    p = seed_rows(c, 5)
    r = c.post(f"{V}/imports/{p['import_id']}/commit")
    assert r.status_code == 202 and r.json()["status"] == "committing" and r.json()["progress"] == {"processed": 0, "total": 5}
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 0
    seen = []
    while worker.run_once(env.ctx, kinds=("import_commit",)):
        g = c.get(f"{V}/imports/{p['import_id']}").json()
        seen.append((g["status"], g["progress"]["processed"], len(sql(env, "SELECT 1 FROM bookings"))))
    assert seen == [("committing", 2, 2), ("committing", 4, 4), ("committed", 5, 5)]
    final = c.get(f"{V}/imports/{p['import_id']}").json()
    assert final["progress"] == {"processed": 5, "total": 5} and len(final["created_booking_ids"]) == 5
    assert c.get(f"{V}/imports/{p['import_id']}").json()["counts"]["new"] == 5
    # committing again while/after: no duplicate job, no duplicate bookings
    assert c.post(f"{V}/imports/{p['import_id']}/commit").status_code == 200
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind = 'import_commit'")[0]["n"] == 1


def test_commit_while_committing_is_202_with_current_progress_and_queues_nothing_new(make_env):
    from garage.api import worker
    from .conftest import make_workshop as mk
    env = make_env(import_commit_batch_size=2)
    c = mk(env, "A", "o@a.example")
    p = seed_rows(c, 4)
    c.post(f"{V}/imports/{p['import_id']}/commit")
    worker.run_once(env.ctx, kinds=("import_commit",))
    again = c.post(f"{V}/imports/{p['import_id']}/commit")
    assert again.status_code == 202 and again.json()["progress"] == {"processed": 2, "total": 4}
    assert again.json()["already_committed"] is False
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind = 'import_commit'")[0]["n"] == 1


def test_a_crashing_batch_rolls_back_and_the_retry_resumes_without_duplicates(make_env, monkeypatch):
    from garage.api import service, worker
    from .conftest import make_workshop as mk
    env = make_env(import_commit_batch_size=2)
    c = mk(env, "A", "o@a.example")
    p = seed_rows(c, 5)
    real, calls = service._plan_new_booking, {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 4:                      # second row of the second batch, once
            raise RuntimeError("boom")
        return real(*a, **k)
    monkeypatch.setattr(service, "_plan_new_booking", flaky)
    c.post(f"{V}/imports/{p['import_id']}/commit")
    worker.drain(env.ctx, kinds=("import_commit",))
    rec = c.get(f"{V}/imports/{p['import_id']}").json()
    assert rec["status"] == "committed" and rec["progress"] == {"processed": 5, "total": 5}
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 5
    assert len(set(rec["created_booking_ids"])) == 5
    job = sql(env, "SELECT status, last_error FROM jobs WHERE kind = 'import_commit'")[0]
    assert job["status"] == "done" and job["last_error"].startswith("crash:RuntimeError")      # the crash was seen and retried


def test_a_job_that_keeps_failing_marks_the_import_failed_and_commit_again_continues(make_env, monkeypatch):
    from garage.api import service, worker
    from .conftest import make_workshop as mk
    env = make_env(import_commit_batch_size=2)
    c = mk(env, "A", "o@a.example")
    p = seed_rows(c, 5)
    real = service._plan_new_booking
    state = {"broken": False, "ok": 0}

    def hook(*a, **k):
        if state["broken"] and state["ok"] >= 2:
            raise RuntimeError("down")
        state["ok"] += 1
        return real(*a, **k)
    monkeypatch.setattr(service, "_plan_new_booking", hook)
    state["broken"] = True
    c.post(f"{V}/imports/{p['import_id']}/commit")
    worker.drain(env.ctx, kinds=("import_commit",))
    rec = c.get(f"{V}/imports/{p['import_id']}").json()
    assert rec["status"] == "failed" and rec["failure_reason"] and rec["progress"] == {"processed": 2, "total": 5}
    assert "created_booking_ids" not in rec
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 2                        # finished batches are kept
    assert "down" not in rec["failure_reason"]                                                # internal error text is not exposed
    state["broken"] = False
    r = c.post(f"{V}/imports/{p['import_id']}/commit")
    assert r.status_code == 202 and r.json()["status"] == "committing" and r.json()["progress"]["processed"] == 2
    worker.drain(env.ctx, kinds=("import_commit",))
    rec = c.get(f"{V}/imports/{p['import_id']}").json()
    assert rec["status"] == "committed" and len(rec["created_booking_ids"]) == 5
    assert sql(env, "SELECT count(*) AS n FROM bookings")[0]["n"] == 5


def test_bookings_are_attributed_to_whoever_pressed_commit(env):
    from .conftest import add_staff_and_sign_in
    owner = make_workshop(env, "A", "o@a.example")
    staff = add_staff_and_sign_in(env, owner, "s@a.example")
    p = seed_rows(owner, 2)
    commit_and_run(staff, p["import_id"])
    me = staff.get(f"{V}/me").json()["membership_id"]
    assert {r["created_by_membership_id"] for r in sql(env, "SELECT created_by_membership_id FROM bookings")} == {me}


def test_commit_with_nothing_new_still_completes(env):
    c = make_workshop(env, "A", "o@a.example")
    import_rows(c, [("AB12CDE", day(2), "mot")])
    p = man(c, [("AB12CDE", str(day(2)), "mot")]).json()
    assert p["counts"]["new"] == 0
    rec = commit_and_run(c, p["import_id"])
    assert rec["status"] == "committed" and rec["created_booking_ids"] == [] and rec["progress"] == {"processed": 0, "total": 0}


def test_preview_is_not_a_committing_state_and_expiry_still_applies(env):
    c = make_workshop(env, "A", "o@a.example")
    p = seed_rows(c, 1)
    assert p["progress"] is None and c.get(f"{V}/imports/{p['import_id']}").json()["status"] == "previewed"
    env.clock.advance(minutes=61)
    assert c.post(f"{V}/imports/{p['import_id']}/commit").status_code == 410
    assert sql(env, "SELECT count(*) AS n FROM jobs WHERE kind = 'import_commit'")[0]["n"] == 0
