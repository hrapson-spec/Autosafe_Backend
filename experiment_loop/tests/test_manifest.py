"""Unit tests for manifest.py — durable, content-addressed provenance + the ledger.

Key properties (review pts 1, 7, 9, 10):
  * manifest_id = hash of the DETERMINISTIC payload only; volatile run metadata
    (timestamps, run_id, paths) is recorded but excluded — so replay reproduces the id.
  * float metrics are rounded to a fixed precision so sub-precision noise does not break
    replay, while a real difference does.
  * promotion manifests are durable + content-addressed (resolvable by id).
  * read_ledger hides diagnostic_only rows by default (never mix smoke with promotion).
"""
from manifest import (manifest_id, build_manifest, read_ledger, append_ledger_row,
                      write_promotion_manifest, resolve_manifest)


def _payload(**over):
    p = dict(evaluator_version="1.0.0", control_battery_version="1.0.0",
             ledger_schema_version=1, data_snapshot_id="snap1", sample_fingerprint="fp1",
             seeds=[0, 1, 2, 3, 4], candidate_feature_hash="cfh1",
             baseline_contract_sha="bcs1", thresholds={"pooled_d_auc_min_pp": 0.30},
             controls={"positive_synthetic": "pass"},
             metrics={"pooled_d_auc_pp": 0.42, "median_seed_d_auc_pp": 0.31},
             seed_direction="stable_positive", verdict="promote")
    p.update(over)
    return p


def test_manifest_id_is_content_addressed_and_volatile_independent():
    a = build_manifest(deterministic_payload=_payload(), volatile={"run_id": "r1", "ts": "t1"})
    b = build_manifest(deterministic_payload=_payload(), volatile={"run_id": "r2", "ts": "t2"})
    assert a["manifest_id"] == b["manifest_id"]      # replay: same payload, diff volatile


def test_manifest_id_changes_when_a_deterministic_field_changes():
    a = manifest_id(_payload())
    assert a != manifest_id(_payload(verdict="dead"))
    assert a != manifest_id(_payload(data_snapshot_id="snap2"))


def test_round_absorbs_subprecision_noise_but_not_real_difference():
    a = manifest_id(_payload(metrics={"pooled_d_auc_pp": 0.42}))
    assert a == manifest_id(_payload(metrics={"pooled_d_auc_pp": 0.42 + 1e-12}))
    assert a != manifest_id(_payload(metrics={"pooled_d_auc_pp": 0.42 + 1e-3}))


def test_read_ledger_hides_diagnostic_by_default(tmp_path):
    p = tmp_path / "ledger.tsv"
    append_ledger_row(p, {"candidate_id": "a", "verdict": "promote", "diagnostic_only": "false"})
    append_ledger_row(p, {"candidate_id": "b", "verdict": "dead", "diagnostic_only": "true"})
    assert [r["candidate_id"] for r in read_ledger(p)] == ["a"]
    assert {r["candidate_id"] for r in read_ledger(p, include_diagnostic=True)} == {"a", "b"}


def test_promotion_manifest_round_trips_by_content_address(tmp_path):
    m = build_manifest(deterministic_payload=_payload(), volatile={"run_id": "r1"})
    path = write_promotion_manifest(m, tmp_path)
    assert m["manifest_id"] in str(path)
    resolved = resolve_manifest(m["manifest_id"], tmp_path)
    assert resolved["manifest_id"] == m["manifest_id"]
    assert resolved["deterministic_payload"]["verdict"] == "promote"
