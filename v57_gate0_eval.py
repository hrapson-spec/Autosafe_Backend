"""G1: score the v57 bundle on the Gate 0 fresh cycle-corrected panel.

Row construction is gate0_benchmark.py (frozen prereg) verbatim: same shard
glob/stride/walk-back/label guards/dedupe, joined to the saved v2 panel for
integrity (y agreement) and for V55's saved deployed-path scores. v57 scores
are produced through the REAL serving path: model_v57.load_model() +
engineer_features_with_stats on the vehicle's prior tests with
prediction_date = target test date (the bundle's end-to-end promise).

Primary readout mirrors Gate 0: fail-capture@top-20% on the veterans slice
(n_prior >= 1, gate0's full-history definition) vs heuristic
H = 100*last_fail + min(adv_minor_count, 99); bar = ratio >= 1.15.

Usage: python3.13 -u v57_gate0_eval.py
Output: models/v57/rca/gate0_frame_v57.json
"""

import glob
import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import model_v57
from dvsa_client import MOTTest, VehicleHistory
from feature_engineering_v57 import features_to_array
from model_contract_v57 import CATEGORICAL_FEATURES, FEATURE_NAMES

PROJECT_ROOT = Path("/Users/henrirapson/Library/Mobile Documents/com~apple~CloudDocs/AutoSafe")
OUT = PROJECT_ROOT / "models/v57/rca/gate0_frame_v57.json"
V2 = Path.home() / "autosafe/work/audit_2026/gf_data/fresh_panel_cycle_corrected_v2.parquet"
SHARDS = sorted(glob.glob("/tmp/dvsa_deltas/unzipped_*/*.json.gz"))
STRIDE = 3
WALK_T = 180
FAIL = {"F", "FAIL", "FAILED", "ABA", "ABR", "ABANDONED"}
PASS = {"P", "PASS", "PASSED", "PRS"}
ADV_TYPES = {"ADVISORY", "MINOR"}
GATE_RATIO = 1.15
TOP_FRAC = 0.20


def label(r):
    r = (r or "").upper()
    return 1 if r in FAIL else (0 if r in PASS else None)


def parse_date(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text)[:10].replace(".", "-"))
    except Exception:
        return None


def dt(t):
    return parse_date(t.get("completedDate"))


def walk(tests, T):
    ti = 0
    while ti + 1 < len(tests):
        d0, d1 = dt(tests[ti]), dt(tests[ti + 1])
        if d0 is None or d1 is None or (d0 - d1).days >= T:
            break
        ti += 1
    return ti


def heuristic_fields(prior_test):
    if prior_test is None:
        return 0, 0
    lf = 1 if label(prior_test.get("testResult")) == 1 else 0
    items = prior_test.get("defects") or prior_test.get("rfrAndComments") or []
    adv = sum(1 for d in items if (d.get("type") or "").upper() in ADV_TYPES)
    return lf, adv


def capture_at(scores, y, frac):
    n = len(scores)
    k = int(np.floor(frac * n))
    total_fails = y.sum()
    if k == 0 or total_fails == 0:
        return 0.0
    df = pd.DataFrame({"s": scores, "y": y})
    g = df.groupby("s", sort=True).agg(cnt=("y", "size"), fails=("y", "sum"))
    g = g.iloc[::-1]
    cum_above = g["cnt"].cumsum().shift(fill_value=0)
    full = cum_above + g["cnt"] <= k
    captured = float(g.loc[full, "fails"].sum())
    boundary = (cum_above < k) & ~full
    if boundary.any():
        b = g[boundary].iloc[0]
        f = (k - float(cum_above[boundary].iloc[0])) / float(b["cnt"])
        captured += f * float(b["fails"])
    return captured / float(total_fails)


def to_mot_test(t):
    return MOTTest(
        test_date=dt(t),
        test_result="FAILED" if label(t.get("testResult")) == 1 else "PASSED",
        expiry_date=parse_date(t.get("expiryDate")),
        odometer_value=int(t["odometerValue"]) if str(t.get("odometerValue", "")).isdigit() else None,
        odometer_unit=(t.get("odometerUnit") or "mi"),
        test_number=str(t.get("motTestNumber") or ""),
        defects=[{"type": (d.get("type") or ""), "text": (d.get("text") or "")}
                 for d in (t.get("defects") or t.get("rfrAndComments") or [])],
    )


def main():
    assert SHARDS, "no DVSA delta shards under /tmp/dvsa_deltas"
    assert model_v57.load_model(), "v57 bundle failed to load"

    rows = {}
    feature_rows = []
    n_seen = 0
    for si, shard in enumerate(SHARDS):
        with gzip.open(shard, "rt") as fh:
            for line in fh:
                n_seen += 1
                if n_seen % STRIDE:
                    continue
                try:
                    v = json.loads(line)
                except Exception:
                    continue
                tests = sorted(v.get("motTests") or [],
                               key=lambda t: t.get("completedDate") or "",
                               reverse=True)
                if not tests:
                    continue
                ti = walk(tests, WALK_T)
                target = tests[ti]
                y = label(target.get("testResult"))
                td = dt(target)
                if y is None or td is None or td < datetime(2025, 1, 1):
                    continue
                reg = v.get("registration", "")
                key = (hashlib.sha256(reg.encode()).hexdigest(),
                       td.date().isoformat())
                if key in rows:
                    continue
                priors = tests[ti + 1:]
                n_prior = len(priors)
                lf, adv = heuristic_fields(tests[ti + 1] if n_prior else None)

                history = VehicleHistory(
                    registration=reg,
                    make=(v.get("make") or "UNKNOWN"),
                    model=(v.get("model") or ""),
                    fuel_type=v.get("fuelType"),
                    colour=v.get("primaryColour"),
                    registration_date=parse_date(v.get("registrationDate")),
                    manufacture_date=parse_date(v.get("manufactureDate")),
                    engine_size=None,
                    mot_tests=[mt for mt in (to_mot_test(t) for t in priors)
                               if mt.test_date is not None],
                )
                feats = model_v57.engineer_features_with_stats(
                    history, "", prediction_date=td)
                feature_rows.append(features_to_array(feats, validate=True))
                rows[key] = (y, ti, n_prior, lf, adv)
        if (si + 1) % 200 == 0:
            print(f"  shards {si + 1}/{len(SHARDS)}: rows={len(rows):,}", flush=True)

    rb = pd.DataFrame(
        [(k[0], k[1], *v) for k, v in rows.items()],
        columns=["reg_sha", "tdate", "y_rb", "walked_rb", "n_prior",
                 "last_fail", "adv_minor_count"])
    X = pd.DataFrame(feature_rows, columns=FEATURE_NAMES)
    for c in CATEGORICAL_FEATURES:
        X[c] = X[c].astype(str)
    print(f"rebuild: rows={len(rb):,}; scoring v57 in batch...", flush=True)
    rb["v57_raw"] = model_v57._model.predict_proba(X)[:, 1]

    v2 = pd.read_parquet(V2)
    m = v2.merge(rb, on=["reg_sha", "tdate"], how="inner")
    coverage = len(m) / len(v2)
    assert coverage >= 0.995, f"join coverage {coverage:.4%} < 99.5%"
    assert (m["y"] == m["y_rb"]).all(), "y mismatch on joined rows"
    print(f"join coverage {len(m):,}/{len(v2):,} = {coverage:.4%}", flush=True)

    m["H"] = 100 * m["last_fail"] + m["adv_minor_count"].clip(upper=99)
    vet = m[m["n_prior"] >= 1].reset_index(drop=True)
    first = m[m["n_prior"] == 0]
    yv = vet["y"].to_numpy()

    cap = {name: capture_at(vet[col].to_numpy(), yv, TOP_FRAC)
           for name, col in (("v57", "v57_raw"), ("v55_saved", "raw"), ("H", "H"))}
    ratio_v57 = cap["v57"] / cap["H"]
    ratio_v55 = cap["v55_saved"] / cap["H"]

    rng = np.random.default_rng(0)
    boots = []
    s57, sh = vet["v57_raw"].to_numpy(), vet["H"].to_numpy()
    for _ in range(1000):
        idx = rng.integers(0, len(vet), len(vet))
        ch = capture_at(sh[idx], yv[idx], TOP_FRAC)
        boots.append(capture_at(s57[idx], yv[idx], TOP_FRAC) / ch if ch > 0 else np.nan)
    ci = np.nanpercentile(boots, [2.5, 97.5])

    out = {
        "gate_frame": "gate0 fresh cycle-corrected v2 (prereg row construction verbatim)",
        "utc": datetime.now(timezone.utc).isoformat(),
        "join_coverage": coverage,
        "vet_n": int(len(vet)),
        "vet_base_rate": float(yv.mean()),
        "primary": {
            "capture20_v57": cap["v57"],
            "capture20_v55_saved": cap["v55_saved"],
            "capture20_H": cap["H"],
            "ratio_v57_over_H": ratio_v57,
            "ratio_v55_over_H": ratio_v55,
            "gate_ratio": GATE_RATIO,
            "ratio_v57_ci95_bootstrap": [float(ci[0]), float(ci[1])],
            "verdict_v57": "PASS" if ratio_v57 >= GATE_RATIO else "FAIL",
        },
        "secondaries": {
            "auc_v57_vet": float(roc_auc_score(yv, vet["v57_raw"])),
            "auc_v55_vet": float(roc_auc_score(yv, vet["raw"])),
            "auc_H_vet": float(roc_auc_score(yv, vet["H"])),
            "first_mot_n": int(len(first)),
            "first_mot_auc_v57": float(roc_auc_score(first["y"], first["v57_raw"]))
            if first["y"].nunique() > 1 else None,
            "first_mot_auc_v55": float(roc_auc_score(first["y"], first["raw"]))
            if first["y"].nunique() > 1 else None,
            "pooled_auc_v57": float(roc_auc_score(m["y"], m["v57_raw"])),
            "pooled_auc_v55": float(roc_auc_score(m["y"], m["raw"])),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out["primary"], indent=2))
    print(json.dumps(out["secondaries"], indent=2))
    print(f"\nG1 VERDICT (v57 vs H @ gate 1.15): {out['primary']['verdict_v57']} "
          f"(ratio {ratio_v57:.3f}; v55 saved ratio {ratio_v55:.3f})")
    print(f"evidence -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
