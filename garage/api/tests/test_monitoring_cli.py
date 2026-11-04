"""monitor-g6 / monitor-g7 commands: policy-file-only values, sha256 recorded, refusal on any missing value,
and the PRS-aware outcome path end to end on a migrated fixture database. Synthetic data only. The policy
values below are TEST values (open decisions are not answered by them)."""
import copy
import dataclasses
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from garage.api import cli, worker
from garage.api.dvsa import DvsaHistory, DvsaTest
from garage.api.monitoring import (G7Params, OutcomePolicy, PrsPairingRule, ReferenceArtefact, build_records,
                                   build_reference_dict, dumps_canonical, evaluate_settled)
from garage.api.monitoring.policy import PolicyError, g6_policy, g7_policy, read_policy
from garage.api.provider import STUB_DISPLAY_POLICY
from garage.api.providers import ShadowProviderResult
from .conftest import day, import_rows, make_due, make_workshop, sql

G7_POLICY = {
    "kind": "monitor_g7", "score_column": "shadow_probability",
    "g7": {"pooled_window": 300, "min_workshop_n": 100, "bias_bound_pp": 5.0, "auc_margin": 0.02,
           "workshop_window": 100, "min_band_n": 20, "baseline_auc": 0.5, "clarification_approved": False},
    "bootstrap": {"n_boot": 0, "seed": 1},
    "bands": {"edges": [20, 35], "rounding_pp": 5, "bands_only_min_n": 200},
    "scope": {"booking_types": ["mot"], "settled_check_day": 14, "settled_statuses": ["matched", "ambiguous"]},
    "outcome": {"endpoint": "fail_or_prs", "window_days": 14, "multi_event_rule": "exclude_unresolved",
                "prs_pairing": {"min_gap_seconds": 0, "max_gap_seconds": 2, "defect_rule": "prs_only_fail_required",
                                "hard_defect_types": ["MAJOR", "DANGEROUS", "FAIL"], "vehicle_rule": "ignore",
                                "station_rule": "ignore"}},
}
G6_POLICY = {
    "kind": "monitor_g6",
    "g6": {"min_supported": 500, "psi_threshold": 0.10, "max_unsupported_share": 0.30, "smoothing": 0.5},
    "bootstrap": {"n_boot": 0, "seed": 1, "alpha": 0.05},
    "reference_sha256": "", "outcome_access_start_utc": "NOT_STARTED", "population": "fixture",
}


def write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, indent=1))
    return str(p)


def paths(d, prefix=()):
    """Every leaf path of a nested dict."""
    for k, v in d.items():
        if isinstance(v, dict):
            yield from paths(v, prefix + (k,))
        else:
            yield prefix + (k,)


def without(d, path):
    d = copy.deepcopy(d)
    node = d
    for k in path[:-1]:
        node = node[k]
    del node[path[-1]]
    return d


def with_value(d, path, value):
    d = copy.deepcopy(d)
    node = d
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return d


# ------------------------------------------------------------------------------------------- policy files
def test_valid_g7_policy_parses_and_nothing_is_defaulted(tmp_path):
    parsed, sha = read_policy(write(tmp_path, "p.json", G7_POLICY), "monitor_g7")
    assert sha == hashlib.sha256((tmp_path / "p.json").read_bytes()).hexdigest()
    pol = g7_policy(parsed)
    assert pol["g7_params"].clarification_approved is False and pol["band_edges"] == (20, 35)
    assert pol["outcome_policy"].prs_rule.max_gap_seconds == 2


@pytest.mark.parametrize("path", list(paths(G7_POLICY)))
def test_g7_policy_refuses_each_missing_value(path):
    if path == ("kind",):
        pytest.skip("kind is checked by read_policy")
    with pytest.raises(PolicyError, match="missing required"):
        g7_policy(without(G7_POLICY, path))


@pytest.mark.parametrize("path", list(paths(G6_POLICY)))
def test_g6_policy_refuses_each_missing_value(path):
    if path == ("kind",):
        pytest.skip("kind is checked by read_policy")
    pol = with_value(G6_POLICY, ("reference_sha256",), "0" * 64)
    with pytest.raises(PolicyError, match="missing required"):
        g6_policy(without(pol, path))


def test_policy_refuses_unknown_keys_wrong_kind_and_changed_gates(tmp_path):
    with pytest.raises(PolicyError, match="unknown key"):
        g7_policy(with_value(G7_POLICY, ("g7", "pooled_windw"), 1))
    with pytest.raises(PolicyError, match="kind"):
        read_policy(write(tmp_path, "g6.json", G6_POLICY), "monitor_g7")
    with pytest.raises(PolicyError):
        read_policy(str(tmp_path / "nope.json"), "monitor_g7")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(PolicyError):
        read_policy(str(tmp_path / "bad.json"), "monitor_g7")
    for path, v in ((("g7", "pooled_window"), 250), (("g7", "min_workshop_n"), 50), (("g7", "bias_bound_pp"), 6.0),
                    (("g7", "auc_margin"), 0.05), (("bands", "bands_only_min_n"), 100)):
        with pytest.raises(PolicyError, match="adopted"):
            g7_policy(with_value(G7_POLICY, path, v))
    ok = with_value(G6_POLICY, ("reference_sha256",), "0" * 64)
    for path, v in ((("g6", "min_supported"), 400), (("g6", "psi_threshold"), 0.2),
                    (("g6", "max_unsupported_share"), 0.5), (("g6", "smoothing"), 1.0)):
        with pytest.raises(PolicyError, match="adopted"):
            g6_policy(with_value(ok, path, v))


@pytest.mark.parametrize("path,value", [
    (("g7", "clarification_approved"), "yes"), (("g7", "workshop_window"), 0), (("g7", "workshop_window"), 99),
    (("g7", "baseline_auc"), 1.5), (("g7", "min_band_n"), True), (("bootstrap", "n_boot"), -1),
    (("bands", "edges"), [35, 20]), (("scope", "booking_types"), []),
    (("outcome", "endpoint"), "hard_fail"), (("outcome", "multi_event_rule"), "guess"),
    (("outcome", "prs_pairing", "max_gap_seconds"), -1), (("outcome", "prs_pairing", "defect_rule"), "x"),
    (("outcome", "prs_pairing", "hard_defect_types"), [])])
def test_g7_policy_refuses_bad_values(path, value):
    with pytest.raises(PolicyError):
        g7_policy(with_value(G7_POLICY, path, value))


def test_g7_baseline_auc_null_is_an_explicit_choice():
    assert g7_policy(with_value(G7_POLICY, ("g7", "baseline_auc"), None))["g7_params"].baseline_auc is None


# ----------------------------------------------------------------------------------------------- G6 command
def test_monitor_g6_prints_policy_hash_and_refuses_incomplete_policy(tmp_path, capsys):
    rng = random.Random(3)
    rows = [{"booking_id": f"b{i}", "vehicle_id": f"v{i}", "workshop_id": "w", "supported": True,
             "features": {"age": rng.gauss(8, 3)}, "score": min(0.99, max(0.01, rng.gauss(0.2, 0.05)))}
            for i in range(600)]
    d = build_reference_dict([{**r["features"], "score": r["score"]} for r in rows], {"age": {"kind": "continuous"}},
                             artefact_id="ref-cli", reference_source="fixture", bins_frozen_utc="2026-10-30T00:00:00Z")
    raw = dumps_canonical(d)
    (tmp_path / "ref.json").write_bytes(raw)
    (tmp_path / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    pol = with_value(G6_POLICY, ("reference_sha256",), hashlib.sha256(raw).hexdigest())
    pp = write(tmp_path, "g6.json", pol)
    cli.main(["monitor-g6", "--policy", pp, "--rows", str(tmp_path / "rows.jsonl"), "--reference",
              str(tmp_path / "ref.json")])
    cap = capsys.readouterr()
    sha = hashlib.sha256((tmp_path / "g6.json").read_bytes()).hexdigest()
    out = json.loads(cap.out)
    assert out["policy"]["sha256"] == sha and out["policy"]["values"] == pol and f"policy_sha256={sha}" in cap.err
    assert out["status"] == "fixture_only" and out["population"] == "fixture"
    # a missing value refuses: non-zero exit, nothing on stdout
    bad = write(tmp_path, "g6bad.json", without(pol, ("bootstrap", "alpha")))
    with pytest.raises(SystemExit) as e:
        cli.main(["monitor-g6", "--policy", bad, "--rows", str(tmp_path / "rows.jsonl"), "--reference",
                  str(tmp_path / "ref.json")])
    cap = capsys.readouterr()
    assert e.value.code == 2 and cap.out == "" and "REFUSED" in cap.err and "alpha" in cap.err
    # the old per-value flags are gone: no value can arrive any other way
    with pytest.raises(SystemExit):
        cli.main(["monitor-g6", "--rows", "x", "--reference", "y", "--reference-sha256", "z",
                  "--outcome-access-start", "NOT_STARTED", "--population", "fixture"])
    capsys.readouterr()


def test_monitor_g6_reference_hash_comes_from_the_policy_not_the_file(tmp_path, capsys):
    (tmp_path / "ref.json").write_text("{}")
    (tmp_path / "rows.jsonl").write_text("")
    pp = write(tmp_path, "g6.json", with_value(G6_POLICY, ("reference_sha256",), "1" * 64))
    with pytest.raises(SystemExit):
        cli.main(["monitor-g6", "--policy", pp, "--rows", str(tmp_path / "rows.jsonl"), "--reference",
                  str(tmp_path / "ref.json")])
    assert "REFUSED" in capsys.readouterr().err


# ------------------------------------------------------------------------------- settled rows (no database)
def _rows(n, *, prs_share, seed=5):
    """n settled rows over bands; a share of the positives is a PRS pair (status ambiguous), the rest pure fails."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        p = rng.choice([0.1, 0.3, 0.6])
        base = datetime(2027, 3, 11, 10, tzinfo=timezone.utc)
        kind = rng.random()
        if kind < 0.1:
            tests, status = [{"completed_at": base.isoformat(), "result": "FAILED", "defects": [{"type": "MAJOR"}]}], "matched"
        elif kind < 0.1 + prs_share:
            tests = [{"completed_at": base.isoformat(), "result": "FAILED", "defects": [{"type": "PRS"}]},
                     {"completed_at": (base + timedelta(seconds=1)).isoformat(), "result": "PASSED"}]
            status = "ambiguous"
        else:
            tests, status = [{"completed_at": base.isoformat(), "result": "PASSED"}], "matched"
        out.append({"booking_id": f"b{i}", "workshop_id": "w1", "vehicle_id": f"v{i}", "status": status,
                    "schedule_date": "2027-03-10", "observed_utc": (base + timedelta(days=4, minutes=i)).isoformat(),
                    "score": p, "tests": tests})
    return out


def _g7p():
    return G7Params(workshop_window=100, min_band_n=20, baseline_auc=0.5, clarification_approved=False)


def _op(endpoint="fail_or_prs", multi="exclude_unresolved"):
    return OutcomePolicy(endpoint=endpoint, window_days=14, multi_event_rule=multi,
                         prs_rule=PrsPairingRule(0, 2, "prs_only_fail_required", ("MAJOR", "DANGEROUS", "FAIL"),
                                                 "ignore", "ignore"))


def test_g7_and_bands_consume_prs_positives_that_a_matched_only_feed_would_drop():
    rows = _rows(600, prs_share=0.3)
    res = evaluate_settled(rows, _g7p(), _op("fail_or_prs"), band_edges=(20, 35), rounding_pp=5,
                           bands_only_min_n=200, n_boot=0, seed=1)
    only_matched = [r for r in rows if r["status"] == "matched"]
    res_m = evaluate_settled(only_matched, _g7p(), _op("fail_or_prs"), band_edges=(20, 35), rounding_pp=5,
                             bands_only_min_n=200, n_boot=0, seed=1)
    oc = res["outcome_classification"]
    assert oc["n_settled_rows"] == 600 and oc["n_used"] == 600 and oc["classifier_labels"]["prs_pair"] > 120
    assert oc["n_used_by_label"]["prs_pair"] == oc["classifier_labels"]["prs_pair"]
    # the PRS positives are in the observed rate and in the band counts; the matched-only feed drops them
    assert res["g7"]["pooled_bias"]["observed_rate"] > res_m["g7"]["pooled_bias"]["observed_rate"] + 0.15
    assert sum(b["k"] for b in res["bands_cumulative"]["bands"].values()) == oc["n_positive"]
    assert res["g7"]["pooled_bias"]["n"] == 300 and res["outcome_policy"]["endpoint"] == "fail_or_prs"
    # fail_only: the same rows, PRS pairs are negatives
    res_f = evaluate_settled(rows, _g7p(), _op("fail_only"), band_edges=(20, 35), rounding_pp=5,
                             bands_only_min_n=200, n_boot=0, seed=1)
    assert res_f["outcome_classification"]["n_positive"] == sum(
        1 for r in rows if r["tests"][0]["result"] == "FAILED" and r["tests"][0]["defects"][0]["type"] == "MAJOR")


def test_unresolved_and_inconsistent_rows_are_excluded_and_counted():
    base = datetime(2027, 3, 11, 10, tzinfo=timezone.utc)
    mk = lambda i, tests, status: {"booking_id": f"b{i}", "workshop_id": "w", "vehicle_id": f"v{i}", "status": status,  # noqa: E731
                                   "schedule_date": "2027-03-10", "observed_utc": base.isoformat(),
                                   "score": 0.3, "tests": tests}
    fail = {"completed_at": base.isoformat(), "result": "FAILED", "defects": [{"type": "MAJOR"}]}
    later = {"completed_at": (base + timedelta(days=3)).isoformat(), "result": "PASSED"}
    rows = [mk(0, [fail, later], "ambiguous"),          # unresolved under exclude_unresolved
            mk(1, [fail], "ambiguous"),                  # stored status says 2+ tests, history says 1
            mk(2, [], "matched"),                        # stored status says 1 test, history says none
            mk(3, [fail], "matched")]                    # fine
    recs, s = build_records(rows, _op(), band_edges=(20, 35), rounding_pp=5)
    assert [r["booking_id"] for r in recs] == ["b3"]
    assert s["excluded_by_reason"] == {"status_disagrees_with_history": 2, "unresolved_ambiguous_non_prs": 1}
    recs, s = build_records(rows[:1], _op(multi="first_event"), band_edges=(20, 35), rounding_pp=5)
    assert len(recs) == 1 and recs[0]["y"] == 1
    with pytest.raises(Exception):
        build_records([{"booking_id": "x"}], _op(), band_edges=(20, 35), rounding_pp=5)


# ---------------------------------------------------------------------------------- end to end on a database
def _shadow(p):
    pol = dataclasses.replace(STUB_DISPLAY_POLICY, display_state="shadow", suppression_reason="model_not_qualified",
                              ranking_enabled=False)
    return ShadowProviderResult(state="unavailable", reason="model_not_qualified", provider_name="t",
                                provider_version="1", display_policy=pol, shadow_margin=0.0, shadow_probability=p,
                                shadow_bundle_sha256="a" * 64, shadow_model_version="synthetic-1")


def _t(d, hh, mm, ss, res, defects=()):
    return DvsaTest(completed_at=datetime.fromisoformat(f"{d}T{hh:02d}:{mm:02d}:{ss:02d}+00:00"), result=res,
                    test_number=f"{abs(hash((d, hh, mm, ss, res))) % 10**12:012d}", defects=tuple(defects))


def test_monitor_g7_end_to_end_counts_the_prs_pair_the_matcher_calls_ambiguous(env, tmp_path, monkeypatch, capsys):
    appt = day(-6)
    c = make_workshop(env, "A", "o@a.example")
    a = str(appt)
    cases = {                                         # reg -> (history tests after the appointment, shadow p)
        "PA11PAS": ([_t(a, 10, 0, 0, "PASSED")], 0.10),
        "PR22PRS": ([_t(a, 10, 0, 0, "FAILED", [{"type": "PRS", "text": "x"}]), _t(a, 10, 0, 1, "PASSED")], 0.30),
        "FA33FAL": ([_t(a, 10, 0, 0, "FAILED", [{"type": "MAJOR", "text": "y"}])], 0.60),
        "AM44AMB": ([_t(a, 10, 0, 0, "FAILED", [{"type": "MAJOR", "text": "y"}]),
                     _t(str(appt + timedelta(days=3)), 10, 0, 0, "PASSED")], 0.30),
    }
    prob = {}
    for reg, (tests, p) in cases.items():
        prior = _t(str(day(-400)), 10, 0, 0, "PASSED")
        env.dvsa.set_history(reg, DvsaHistory(registration=reg, make="FORD", model="FOCUS",
                                              tests=tuple(sorted((prior, *tests), key=lambda t: t.completed_at))))
        prob[reg] = p
    env.provider.result = lambda req: _shadow(prob[req.registration])
    bids = import_rows(c, [(r, appt, "mot") for r in cases])["created_booking_ids"]
    worker.drain(env.ctx, kinds=("assess_booking",))
    make_due(env)
    worker.drain(env.ctx)
    st = {r["registration"]: r["status"] for r in sql(
        env, "SELECT b.registration, o.status FROM outcomes o JOIN bookings b ON b.id = o.booking_id "
             "WHERE o.check_day = 14")}
    assert st == {"PA11PAS": "matched", "FA33FAL": "matched", "PR22PRS": "ambiguous", "AM44AMB": "ambiguous"}
    assert len(sql(env, "SELECT 1 FROM assessments WHERE shadow_probability IS NOT NULL")) == 4

    pp = write(tmp_path, "g7.json", G7_POLICY)
    monkeypatch.setenv("GARAGE_DATABASE_URL", env.dsn)
    cli.main(["monitor-g7", "--policy", pp])
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    sha = hashlib.sha256((tmp_path / "g7.json").read_bytes()).hexdigest()
    assert out["policy"]["sha256"] == sha and out["policy"]["values"] == G7_POLICY and f"policy_sha256={sha}" in cap.err
    oc = out["outcome_classification"]
    assert oc["n_settled_rows"] == 4 and oc["classifier_labels"] == {
        "no_test": 0, "pass": 1, "fail": 1, "prs_pair": 1, "ambiguous_non_prs": 1}
    assert oc["n_used"] == 3 and oc["n_positive"] == 2                  # PRS pair counted; matched-only would give 1
    assert oc["excluded_by_reason"] == {"unresolved_ambiguous_non_prs": 1}
    assert out["g7"]["n_matched_total"] == 3 and out["g7"]["status"] == "insufficient_data"

    # fail_only reading: the PRS pair becomes a negative
    p2 = write(tmp_path, "g7b.json", with_value(G7_POLICY, ("outcome", "endpoint"), "fail_only"))
    cli.main(["monitor-g7", "--policy", p2])
    assert json.loads(capsys.readouterr().out)["outcome_classification"]["n_positive"] == 1

    # a missing value refuses before any database access result is printed
    p3 = write(tmp_path, "g7c.json", without(G7_POLICY, ("outcome", "prs_pairing", "max_gap_seconds")))
    with pytest.raises(SystemExit) as e:
        cli.main(["monitor-g7", "--policy", p3])
    cap = capsys.readouterr()
    assert e.value.code == 2 and cap.out == "" and "max_gap_seconds" in cap.err

    # the command only reads: no row changed
    assert len(sql(env, "SELECT 1 FROM outcomes WHERE status = 'ambiguous'")) == 8   # 2 bookings x 4 check days
