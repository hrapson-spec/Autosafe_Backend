"""Score provenance (APP_COMPLETION_V1 / T1b task 1): the scorer's `probability` is the calibrated probability; the providers now
carry it under that name in shadow AND qualified mode; the G7 reader takes the column the policy names; and the writer (a proposed
patch to store.py/handlers.py, see REPORT_T1b.md) is tested here whenever it is applied. Synthetic fixtures only."""
import dataclasses
import hashlib
import inspect
import json
import os
import pathlib
import shutil

import pytest

from garage.api import cli, store, worker
from garage.api.handlers import _validate_provider
from garage.api.provider import STUB_DISPLAY_POLICY
from garage.api.providers import BundleProvider, QualifiedProviderResult, ShadowProviderResult
from .conftest import day, history, import_rows, make_due, make_workshop, sql
from .test_bundle_provider import PROB, request as make_request, write_bundle as write_shadow_bundle
from .test_monitoring_cli import G7_POLICY, _shadow, _t, write
from .test_qualified_provider import BANDS, FIX, PCT, PINS, FakeScorer, qualified

WRITER_APPLIED = "calibrated" in inspect.signature(store.Scoped.complete_assessment).parameters
needs_writer = pytest.mark.skipif(not WRITER_APPLIED, reason="the store/handlers writer patch (REPORT_T1b.md) is not applied")
needs_exe = pytest.mark.skipif(not os.access("/usr/bin/python3", os.X_OK), reason="sample bundles name /usr/bin/python3")


@pytest.fixture()
def bundle(tmp_path):
    def _copy(name=BANDS):
        dst = tmp_path / name
        shutil.copytree(FIX / name, dst)
        return dst
    return _copy


# ----------------------------------------------------------------------------------------------- providers
def test_shadow_result_carries_the_scorer_probability_as_the_calibrated_probability(tmp_path):
    b = write_shadow_bundle(tmp_path)
    p = BundleProvider(b)
    r = p.assess(make_request())
    assert isinstance(r, ShadowProviderResult)
    assert r.calibrated_probability == r.shadow_probability == PROB                  # one value, two names
    assert r.calibrated_bundle_sha256 == r.shadow_bundle_sha256 == p.manifest_sha256


@needs_exe
@pytest.mark.parametrize("name,prob", [(BANDS, 0.2537), (PCT, 0.575)])
def test_qualified_result_carries_the_unrounded_probability_even_when_ranking_is_off(bundle, monkeypatch, name, prob):
    b = bundle(name)
    FakeScorer(monkeypatch, b, p=prob)
    prov = qualified(b)
    r = prov.assess(make_request())
    assert isinstance(r, QualifiedProviderResult) and r.state == "available"
    assert r.calibrated_probability == prob == r.rank_score
    assert r.calibrated_bundle_sha256 == prov.manifest_sha256
    assert (r.display_policy.ranking_enabled) is (name == PCT)
    assert _validate_provider(r) is None
    assert "shadow_probability" not in {f.name for f in dataclasses.fields(r)}      # no shadow fields on an available result


@needs_exe
def test_a_scorer_refusal_carries_no_calibrated_probability(bundle, monkeypatch):
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b, p=0.3)
    prov = qualified(b)
    monkeypatch.setattr(prov, "_dir", b / "nonexistent")
    r = prov.assess(make_request())
    assert r.state == "unavailable" and getattr(r, "calibrated_probability", None) is None


# ---------------------------------------------------------------- what the app stores today (evidence for the report)
@needs_exe
def test_today_a_qualified_bands_only_result_leaves_no_calibrated_probability_in_the_database(env, bundle, monkeypatch):
    """Documents the gap that migration 0005 + the writer patch close: with the QualifiedBundle v2 reader and a bands-only policy,
    state is 'available', shadow_* are NULL by the 0003 CHECK and score_unrounded is NULL because ranking is disabled."""
    b = bundle(BANDS)
    FakeScorer(monkeypatch, b, p=0.2537)
    env.ctx.provider = qualified(b)
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    import_rows(c, [("AB12CDE", day(2), "mot")])
    worker.drain(env.ctx)
    row = sql(env, "SELECT * FROM assessments")[0]
    assert row["state"] == "available"
    assert (row["shadow_probability"], row["shadow_margin"], row["score_unrounded"]) == (None, None, None)
    if WRITER_APPLIED:
        assert row["calibrated_probability"] == 0.2537
    else:
        assert row["calibrated_probability"] is None


# ------------------------------------------------------------------------------------------ the writer (when applied)
@needs_writer
def test_writer_stores_the_calibrated_probability_for_a_shadow_result(env, tmp_path):
    from .test_bundle_provider import _run
    b = write_shadow_bundle(tmp_path)
    c = make_workshop(env, "A", "o@a.example")
    p = BundleProvider(b)
    _run(env, c, p)
    row = sql(env, "SELECT * FROM assessments")[0]
    assert row["calibrated_probability"] == row["shadow_probability"] == PROB
    assert row["calibrated_bundle_sha256"] == row["shadow_bundle_sha256"] == p.manifest_sha256


@needs_writer
@needs_exe
@pytest.mark.parametrize("name,ranked", [(BANDS, False), (PCT, True)])
def test_writer_stores_the_calibrated_probability_for_a_qualified_result(env, bundle, monkeypatch, name, ranked):
    b = bundle(name)
    FakeScorer(monkeypatch, b, p=0.2537)
    prov = qualified(b)
    env.ctx.provider = prov
    c = make_workshop(env, "A", "o@a.example")
    env.dvsa.set_history("AB12CDE", history("AB12CDE", [("2023-01-01", "PASSED")]))
    bid = import_rows(c, [("AB12CDE", day(2), "mot")])["created_booking_ids"][0]
    worker.drain(env.ctx)
    row = sql(env, "SELECT * FROM assessments")[0]
    assert row["state"] == "available" and row["shadow_probability"] is None
    assert (row["calibrated_probability"], row["calibrated_bundle_sha256"]) == (0.2537, prov.manifest_sha256)
    assert (row["score_unrounded"] == 0.2537) is ranked
    V = "/api/garage/v1"
    for r in (c.get(f"{V}/bookings/{bid}"), c.get(f"{V}/bookings")):
        assert "calibrated" not in r.text.lower() and "2537" not in r.text


@needs_writer
def test_writer_stores_nothing_for_a_refused_scoring(env, tmp_path):
    from .test_bundle_provider import _run
    b = write_shadow_bundle(tmp_path, "exit1")
    c = make_workshop(env, "A", "o@a.example")
    _run(env, c, BundleProvider(b))
    row = sql(env, "SELECT * FROM assessments")[0]
    assert row["calibrated_probability"] is None and row["calibrated_bundle_sha256"] is None


# -------------------------------------------------------------------------------------------- G7 takes the named column
def _g7_flow(env, tmp_path):
    appt = day(-6)
    c = make_workshop(env, "A", "o@a.example")
    a = str(appt)
    cases = {"PA11PAS": ([_t(a, 10, 0, 0, "PASSED")], 0.10),
             "FA33FAL": ([_t(a, 10, 0, 0, "FAILED", [{"type": "MAJOR", "text": "y"}])], 0.60)}
    prob = {}
    from garage.api.dvsa import DvsaHistory
    for reg, (tests, p) in cases.items():
        prior = _t(str(day(-400)), 10, 0, 0, "PASSED")
        env.dvsa.set_history(reg, DvsaHistory(registration=reg, make="FORD", model="FOCUS",
                                              tests=tuple(sorted((prior, *tests), key=lambda t: t.completed_at))))
        prob[reg] = p
    env.provider.result = lambda req: _shadow(prob[req.registration])
    import_rows(c, [(r, appt, "mot") for r in cases])
    worker.drain(env.ctx, kinds=("assess_booking",))
    make_due(env)
    worker.drain(env.ctx)


def test_g7_reads_exactly_the_column_the_policy_names(env, tmp_path, monkeypatch, capsys):
    _g7_flow(env, tmp_path)
    # give the calibrated column other values (the freeze trigger is lifted for this fixture edit only)
    sql(env, "ALTER TABLE assessments DISABLE TRIGGER assessments_freeze_t")
    sql(env, "UPDATE assessments SET calibrated_probability = 0.9, calibrated_bundle_sha256 = repeat('d', 64) "
             "WHERE shadow_probability IS NOT NULL")
    monkeypatch.setenv("GARAGE_DATABASE_URL", env.dsn)

    def run(col, *extra):
        pol = dict(G7_POLICY, score_column=col)
        cli.main(["monitor-g7", "--policy", write(tmp_path, f"{col}.json", pol), *extra])
        return json.loads(capsys.readouterr().out)

    shadow, cal = run("shadow_probability"), run("calibrated_probability", "--record")
    assert shadow["outcome_classification"]["n_used"] == cal["outcome_classification"]["n_used"] == 2
    assert json.dumps(shadow["bands_cumulative"], sort_keys=True) != json.dumps(cal["bands_cumulative"], sort_keys=True)
    # --record: one aggregate row; the result holds no booking, vehicle or registration identifier
    runs = sql(env, "SELECT * FROM monitoring_runs")
    assert len(runs) == 1 and runs[0]["kind"] == "g7" and cal["monitoring_run_id"] == str(runs[0]["id"])
    stored = json.dumps(runs[0]["result"])
    for reg in ("PA11PAS", "FA33FAL"):
        assert reg not in stored
    for r in sql(env, "SELECT id::text i, encode(sha256(convert_to(registration,'UTF8')),'hex') h FROM bookings"):
        assert r["i"] not in stored and r["h"] not in stored
    assert runs[0]["reference_sha256"] is None and runs[0]["bundle_manifest_sha256"] is None


def test_a_g7_policy_without_or_with_a_wrong_score_column_is_refused(tmp_path, capsys, monkeypatch):
    for pol in ({k: v for k, v in G7_POLICY.items() if k != "score_column"}, dict(G7_POLICY, score_column="margin"),
                dict(G7_POLICY, score_column=None)):
        monkeypatch.setenv("GARAGE_DATABASE_URL", "postgresql://unused")
        with pytest.raises(SystemExit) as e:
            cli.main(["monitor-g7", "--policy", write(tmp_path, "p.json", pol)])
        cap = capsys.readouterr()
        assert e.value.code == 2 and cap.out == "" and "score_column" in cap.err
