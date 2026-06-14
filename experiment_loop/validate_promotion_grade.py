"""validate_promotion_grade.py — the promotion-grade orchestrator (thin CLI). PROTECTED.

This is the ONLY promotion entry point and it composes the same shared modules the dev
scorer uses — there is no parallel train/score logic (review pt 13). Flow:

    run_guard (clean source) -> deterministic cohort + snapshot id -> control battery on
    real frames (faithful injection, >=N seeds) -> decision -> authority_decision ->
    durable content-addressed manifest -> exit 0 (activated) or non-zero (locked).

`make test-evaluator-gates` proves the machinery (pytest on synthetic data, must pass for
the PR). `make validate-promotion-authority` runs THIS on real frames and MAY lock safely
if the domain positive is genuinely redundant — a locked loop is the correct safe state,
not a PR failure (review pt 2).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from controls import battery_summary, get_control  # noqa: E402


def authority_decision(per_control: dict) -> dict:
    """Gate vs authority (review pt 2). Activated iff the required battery is green. On a
    lock, diagnose: a synthetic-positive failure => the evaluator itself is broken; a domain
    failure (synthetics passing) => locked pending a ratified resolution state, NOT broken;
    any other required failure => required_control_failed."""
    summary = battery_summary(per_control)
    if summary["required_control_battery_green"]:
        return {"status": "activated", "reason": "all_required_controls_green",
                "battery": summary}
    syn = ["positive_synthetic_obvious", "positive_synthetic_nearthreshold"]
    syn_fail = [n for n in syn if per_control.get(n) == "fail"]
    if syn_fail:
        return {"status": "locked", "reason": "evaluator_broken", "failed": syn_fail,
                "battery": summary}
    if per_control.get("positive_domain") == "fail":
        return {"status": "locked", "reason": "domain_control_failed",
                "failed": ["positive_domain"],
                "resolution_states": list(get_control("positive_domain").resolution_states),
                "battery": summary}
    failed = sorted(n for n, o in per_control.items() if o == "fail")
    return {"status": "locked", "reason": "required_control_failed", "failed": failed,
            "battery": summary}


# --------------------------------------------------------------------------------------
# Heavy on-frame orchestration (not CI-tested; exercised by make validate-promotion-authority)
# --------------------------------------------------------------------------------------
def _score_decision(dev, oot, base_cols, cand_cols, seeds, P, cfg, lbl):
    import numpy as np
    import score_core
    import decision
    sc = score_core.score_candidate(dev, oot, base_cols, cand_cols, seeds=seeds,
                                     params=cfg.PARAMS, cat_features=cfg.CAT_FEATURES,
                                     split=cfg.SPLIT, label_col=lbl)
    summ = decision.summarize_report(
        __import__("arm0_harness").segmented_report(
            oot, oot[lbl].values, sc.cand_proba, sc.base_proba))
    drop = score_core.leakage_drop_pp(sc.last_model, oot, sc.full_cols, cand_cols, seeds[0],
                                      label_col=lbl)
    seed_dir = decision.classify_seed_direction(sc.deltas_pp, P["seed_dead_zone_pp"])
    dec = decision.decide(seed_direction=seed_dir, pooled_d_auc_pp=summ["pooled_d_auc_pp"],
                          median_seed_d_auc_pp=float(np.median(sc.deltas_pp)),
                          within_segment_wins=len(summ["within_wins"]),
                          ece_breach=summ["worst_d_ece"] > P["ece_worsen_max_abs"],
                          leakage_drop_pp=drop, thresholds=P)
    metrics = {"seed_direction": seed_dir, "seed_deltas_pp": [round(d, 6) for d in sc.deltas_pp],
               "pooled_d_auc_pp": round(summ["pooled_d_auc_pp"], 6),
               "within_segment_wins": len(summ["within_wins"]),
               "worst_d_ece": round(summ["worst_d_ece"], 6), "leakage_drop_pp": round(drop, 6),
               "verdict": dec.verdict, "promotable": dec.promotable}
    return dec, metrics


def _inject(dev, oot, col, vals_dev, vals_oot):
    d, o = dev.copy(), oot.copy()
    d[col] = vals_dev
    o[col] = vals_oot
    return d, o, [col]


def main(argv=None):
    import argparse
    import hashlib
    import json
    import numpy as np
    from datetime import datetime, timezone

    import config as cfg
    import referee_config as rc
    import candidate_feature as cf
    import controls as ctl
    import faithful_inject
    import manifest as mf
    import run_guard

    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", default="vehicle_age_years")
    ap.add_argument("--seeds", default="0,1,2,3,4")
    ap.add_argument("--sample", type=int, default=30000, help="dev bound; 0 = full frame")
    ap.add_argument("--repo-root", default=str(HERE.parent))
    a = ap.parse_args(argv)
    seeds = [int(s) for s in a.seeds.split(",")]
    P, lbl = rc.PROMOTION, rc.LABEL_COL
    if len(seeds) < P["promotion_min_seeds"]:
        print(f"LOCKED: promotion requires >= {P['promotion_min_seeds']} seeds, got {len(seeds)}")
        return 2

    # (1) clean source tree (artifacts allowlisted) — abort loudly if dirty
    run_guard.assert_source_clean(a.repo_root)

    # (2) frames + deterministic snapshot/cohort fingerprints
    import evaluate
    dev, oot = evaluate.load_frames(a.sample)
    base_cols = evaluate.base_feature_cols()
    rng = np.random.default_rng(0)
    ids = sorted(int(i) for i in set(dev["test_id"]) | set(oot["test_id"]))
    fp = __import__("sampling").sample_fingerprint(ids)
    snap = mf.manifest_id({"dev": str(rc.DEV_FRAME), "oot": str(rc.OOT_FRAME),
                           "n": len(dev) + len(oot), "fp": fp})
    print(f"frames dev={len(dev):,} oot={len(oot):,} seeds={seeds} snapshot={snap[:12]}", flush=True)

    # (3) control battery on real frames
    per_control, control_metrics = {}, {}

    def run(name, dvc, otc, cols):
        dec, m = _score_decision(dvc, otc, base_cols, cols, seeds, P, cfg, lbl)
        per_control[name] = ctl.classify_control_outcome(ctl.get_control(name), dec)
        control_metrics[name] = m
        print(f"  {name:32s} -> {dec.verdict:8s} ({per_control[name]})", flush=True)
        return dec, m

    # domain (faithful injection) + replay
    dev_f, oot_f, age_cols = faithful_inject.faithful_inject_frames(
        dev, oot, cf.compute_candidate_features, str(cfg.PACKETS))
    dom_dec, dom_m = run("positive_domain", dev_f, oot_f, age_cols)
    _, replay_m = _score_decision(dev_f, oot_f, base_cols, age_cols, seeds, P, cfg, lbl)
    per_control["replay"] = "pass" if replay_m == dom_m else "fail"
    print(f"  {'replay':32s} -> {per_control['replay']}", flush=True)

    # synthetic positives + noop (fenced, label-derived, injected directly)
    for nm in ["positive_synthetic_obvious", "positive_synthetic_nearthreshold", "noop"]:
        col = f"__ctrl_{nm}"
        d, o, cols = _inject(dev, oot,
                             col, ctl.synthetic_feature(nm, dev[lbl].values, rng),
                             ctl.synthetic_feature(nm, oot[lbl].values, rng))
        run(nm, d, o, cols)

    # gradient gaming: a monotone transform of faithful age (no within-band info)
    gg_dev = ctl.gradient_gaming_transform(dev_f[age_cols[0]].values)
    gg_oot = ctl.gradient_gaming_transform(oot_f[age_cols[0]].values)
    d, o, cols = _inject(dev_f, oot_f, "__ctrl_gradient_gaming", gg_dev, gg_oot)
    run("negative_gradient_gaming", d, o, cols)

    per_control["negative_train_only"] = "pending"  # P2 gf17 — pending_not_enforced

    # (4) authority decision
    decision_out = authority_decision(per_control)

    # (5) durable, content-addressed manifest
    cand_src_hash = hashlib.sha256(Path(cf.__file__).read_bytes()).hexdigest()
    contract_sha = hashlib.sha256(Path(cfg.CONTRACT).read_bytes()).hexdigest()
    det = {"evaluator_version": mf.EVALUATOR_VERSION,
           "control_battery_version": mf.CONTROL_BATTERY_VERSION,
           "ledger_schema_version": mf.LEDGER_SCHEMA_VERSION, "data_snapshot_id": snap,
           "sample_fingerprint": fp, "seeds": seeds, "candidate_feature_hash": cand_src_hash,
           "baseline_contract_sha": contract_sha, "thresholds": P,
           "controls": per_control, "control_metrics": control_metrics,
           "domain_metrics": dom_m, "authority": {k: v for k, v in decision_out.items()
                                                   if k != "battery"}}
    vol = {"run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
           "timestamp": datetime.now(timezone.utc).isoformat(), "python": sys.version,
           "frames": {"dev": str(rc.DEV_FRAME), "oot": str(rc.OOT_FRAME), "sample": a.sample}}
    man = mf.build_manifest(deterministic_payload=det, volatile=vol)

    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"authority_{man['manifest_id'][:16]}.json").write_text(json.dumps(man, indent=2))

    print("\n=== AUTHORITY:", decision_out["status"].upper(), "—", decision_out["reason"], "===")
    print("battery:", json.dumps(decision_out["battery"], indent=2, default=str))
    if decision_out["status"] == "activated":
        path = mf.write_promotion_manifest(man, HERE / "promotion_manifests")
        mf.append_ledger_row(HERE / "promotions.tsv", {
            "candidate_id": a.candidate, "manifest_id": man["manifest_id"],
            "data_snapshot_id": snap, "sample_fingerprint": fp, "n_seeds": len(seeds)})
        print("promotion manifest committed-ready at", path)
        return 0
    if decision_out["reason"] == "domain_control_failed":
        print("Next: ratify a domain resolution_state:", decision_out["resolution_states"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
