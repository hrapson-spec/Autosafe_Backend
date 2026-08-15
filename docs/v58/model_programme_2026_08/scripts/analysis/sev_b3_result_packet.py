#!/usr/bin/env python3
"""B3 result packet + integrity audit.

Builds ONE immutable packet describing the four-seed B3 experiment, and audits the claim
that the only intended causal difference between the experimental and control arms is the
TRAINING TARGET -- not data, features, evaluation population or leakage handling.

If any audit check fails the script exits non-zero and writes NOTHING. It never repairs a
discrepancy silently.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from factory.runners import metrics as M  # noqa: E402

SEEDS = [101, 202, 303, 404]
SEED_NOT_RUN = 505
TREAT, CONTROL = "sev.B3", "sev.CTRL"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/B3_RESULT_PACKET.json")
    a = ap.parse_args()
    fail = []

    # ---- configs: the ONLY difference must be `label` ------------------------
    cfg_t = json.loads((ROOT / f"out/configs/{TREAT}.json").read_text())
    cfg_c = json.loads((ROOT / f"out/configs/{CONTROL}.json").read_text())
    diff = sorted(k for k in set(cfg_t) | set(cfg_c) if cfg_t.get(k) != cfg_c.get(k))
    if diff != ["label"]:
        fail.append(f"config diff is {diff}, expected ['label']")
    if (cfg_t["label"], cfg_c["label"]) != ("y_b3", "y_final"):
        fail.append(f"labels are {cfg_t['label']}/{cfg_c['label']}")

    # ---- per-seed fits: everything except label/auroc must match ------------
    per_seed, fits = [], {}
    for s in SEEDS:
        ft = json.loads((ROOT / f"out/fits/sev/{TREAT}.seed{s}.json").read_text())
        fc = json.loads((ROOT / f"out/fits/sev/{CONTROL}.seed{s}.json").read_text())
        fits[s] = (ft, fc)
        for k in ("rung_rows", "n_features", "surface", "max_train_target_date", "arch",
                  "preset", "grade"):
            if ft.get(k) != fc.get(k):
                fail.append(f"seed {s}: {k} differs ({ft.get(k)} vs {fc.get(k)})")
        for k in ("era_pre2018_share", "covid_hole_rows", "n_train_rows",
                  "n_train_vehicles"):
            if ft["train_mixture"].get(k) != fc["train_mixture"].get(k):
                fail.append(f"seed {s}: train_mixture.{k} differs")
        if ft["train_mixture"]["covid_hole_rows"] != 0:
            fail.append(f"seed {s}: covid_hole_rows != 0")
        if ft["train_mixture"]["era_pre2018_share"] != 0.0:
            fail.append(f"seed {s}: pre-2018 training rows present")

    # ---- training row sets must be IDENTICAL between arms -------------------
    for s in SEEDS:
        pt = ROOT / f"out/fits/sev/preds/{TREAT}.seed{s}.train_ids.parquet"
        pc = ROOT / f"out/fits/sev/preds/{CONTROL}.seed{s}.train_ids.parquet"
        it = set(pq.read_table(pt).column("test_id").to_pylist())
        ic = set(pq.read_table(pc).column("test_id").to_pylist())
        if it != ic:
            fail.append(f"seed {s}: training row sets differ "
                        f"(+{len(it - ic)} / -{len(ic - it)})")

    # ---- labels, eval population, metrics ------------------------------------
    lab = pq.read_table(ROOT / "out/TARGET_SEVERITY_LABELS.parquet")
    L = {n: np.asarray(lab.column(n)) for n in lab.schema.names}
    y = (L["n_major_or_dangerous"] >= 3).astype(np.int8)
    ids = [int(t) for t in L["test_id"]]
    eval_ids = None
    for s in SEEDS:
        rows = {}
        for tag, cell in (("treat", TREAT), ("control", CONTROL)):
            t = pq.read_table(
                ROOT / f"out/fits/sev/preds/{cell}.seed{s}.parquet").to_pydict()
            ix = {int(v): i for i, v in enumerate(t["test_id"])}
            if eval_ids is None:
                eval_ids = set(ix)
            elif set(ix) != eval_ids:
                fail.append(f"seed {s} {tag}: eval row set differs")
            sel = np.array([ix[i] for i in ids])
            rows[tag] = M.auroc(y, M.as_stored(np.asarray(t["p"], float)[sel]))
            train = set(pq.read_table(
                ROOT / f"out/fits/sev/preds/{cell}.seed{s}.train_ids.parquet"
            ).column("test_id").to_pylist())
            if train & eval_ids:
                fail.append(f"seed {s} {tag}: train/eval overlap "
                            f"{len(train & eval_ids)}")
        per_seed.append({"seed": s, "treat_auroc": rows["treat"],
                         "control_auroc": rows["control"],
                         "delta": rows["treat"] - rows["control"]})

    if fail:
        print("INTEGRITY AUDIT FAILED — packet NOT written:")
        for f in fail:
            print("  -", f)
        return 2

    d = np.array([r["delta"] for r in per_seed])
    ft0 = fits[SEEDS[0]][0]
    packet = {
        "title": "B3 direct-training result packet",
        "status": "CONFIRMED-AT-K4",
        "status_note": (
            f"Pre-registered gate specified k=5 with sign consistency. Seed {SEED_NOT_RUN} "
            "was NEVER RUN (stopped early by owner decision). Strongly replicated, NOT "
            "formally k=5-confirmed. sigma_hat is LOW-K-PROVISIONAL "
            "(mde.FLOOR_MEASURED_MIN_K = 5). This status is recorded as-run and must not "
            "be altered retrospectively."),
        "target_definition": {
            "name": "B3",
            "rule": "target initial MOT has >= 3 major-or-dangerous failing items",
            "expression": "count(items WHERE rfr_type_code IN ('F','P')) >= 3",
            "severity_note": (
                "major_or_dangerous is the F+P initial-presentation basis. n_dangerous is "
                "FAIL-GATED (disposition in F/P AND dangerous_mark='D'); a D-marked "
                "ADVISORY is NOT dangerous."),
            "builder": "scripts/analysis/severity_collect.py",
        },
        "population": {
            "target_population": "DVSA initial tests only (test_type='NT')",
            "outcomes_included": ["PASS", "FAIL", "PRS"],
            "exclusions": ["ABANDONED", "ABORTED", "ABORTED_VE", "retests (RT/PL/PV)",
                           "appeals (EI/ES)"],
            "row_filter_covid_hole": ft0["train_mixture"] and cfg_t["row_filter"],
            "train_window": "2020-01-02 .. 2023-12-31 (COVID hole removed)",
            "eval_window": "2024-01-01 .. 2024-12-31",
            "regime": "100% post-2018-05-20; era_pre2018_share = 0.0",
        },
        "counts": {
            "train_rows": ft0["rung_rows"],
            "train_vehicles": ft0["train_mixture"]["n_train_vehicles"],
            "train_b3_prevalence": ft0["train_mixture"]["positive_rate"],
            "eval_rows": len(ids),
            "eval_vehicles": int(np.unique(
                pq.read_table(ROOT / f"out/fits/sev/preds/{TREAT}.seed101.parquet")
                .column("vehicle_id")).size),
            "eval_b3_positives": int(y.sum()),
            "eval_b3_prevalence": float(y.mean()),
        },
        "feature_manifest": {
            "n_features": ft0["n_features"],
            "resolution_note": (
                "featureset=[] with an EXPLICIT 241-column list. blocks.py resolves "
                "B1..B6 to 247 today because the B2 item-observability index was added "
                "after the benchmark ran; the v1 frame lacks those 6 columns, so the "
                "banked 241 was recovered by intersecting the resolved set with the frame "
                "schema (deviation D-S2-1)."),
            "columns_sha256": hashlib.sha256(
                json.dumps(cfg_t["extra_columns"]).encode()).hexdigest(),
            "extra_frame": cfg_t["extra_frame"],
            "extra_eval_frame": cfg_t["extra_eval_frame"],
        },
        "arms": {"treatment": TREAT, "control": CONTROL,
                 "only_intended_difference": "training target (label)",
                 "config_diff": diff},
        "per_seed": per_seed,
        "seeds_run": SEEDS, "seed_not_run": SEED_NOT_RUN,
        "mean_delta": float(d.mean()),
        "sd_delta": float(d.std(ddof=1)),
        "min_delta": float(d.min()), "max_delta": float(d.max()),
        "spread": float(d.max() - d.min()),
        "all_positive": bool((d > 0).all()),
        "config_sha": {TREAT: fits[101][0]["config_sha"],
                       CONTROL: fits[101][1]["config_sha"]},
        "artifact_sha256": {
            p: sha(ROOT / p) for p in [
                f"out/configs/{TREAT}.json", f"out/configs/{CONTROL}.json",
                "out/TARGET_SEVERITY_LABELS.parquet",
                "out/TRAIN_SEVERITY_LABELS.parquet",
                "scripts/analysis/severity_collect.py",
                "scripts/analysis/sev_confirm_verdict.py",
            ] + [f"out/fits/sev/{c}.seed{s}.json" for s in SEEDS for c in (TREAT, CONTROL)]
        },
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip(),
        "interpreter": "/Users/henrirapson/autosafe-v58/.venv/bin/python (duckdb 1.5.5)",
        "audit": {"checks_failed": 0, "verdict": "PASS — the only intended causal "
                  "difference between arms is the training target"},
    }
    (ROOT / a.out).write_text(json.dumps(packet, indent=1, default=str))
    print("INTEGRITY AUDIT: PASS")
    print(f"  arms differ only in: {diff}")
    print(f"  train row sets identical across arms at all {len(SEEDS)} seeds")
    print(f"  train/eval overlap: 0 | covid_hole_rows: 0 | pre-2018 share: 0.0")
    print(f"  mean delta {packet['mean_delta']:+.6f}  sd {packet['sd_delta']:.3e}  "
          f"spread {packet['spread']:.3e}  all positive {packet['all_positive']}")
    print(f"  status: {packet['status']} (seed {SEED_NOT_RUN} not run)")
    print(f"  packet -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
