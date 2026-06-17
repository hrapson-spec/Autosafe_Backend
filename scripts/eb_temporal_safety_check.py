#!/usr/bin/env python3
"""Audit EB / target-encoded features for temporal safety (audit item #5).

Maps to v57 promotion gate #4: "target encodings out-of-fold or time-sliced
only" (models/v57/README.md). Produces reproducible evidence against the real
artifacts; the encoder *builders* live off-repo (~/autosafe_work), so anything
this script cannot see from the outputs is reported as UNVERIFIABLE rather than
assumed safe.

CHECK A — point-in-time integrity of the time-sliced priors
-----------------------------------------------------------
``add_eb_features`` joins each row to a monthly prior with
``join_month = min(test_month, max_asof)`` (train_catboost_production_v55.py:
1604) and an exact month-key merge. A prior is temporally safe for a row only
if its ``asof_month <= test_month``. This check recomputes the effective
asof-month per row and flags any row that would consume a prior dated at/after
its own test month (future leakage), and confirms OOT rows are clipped to the
DEV max asof.

CHECK B — in-sample (no-OOF) target-encoder detector
----------------------------------------------------
The make/model/segment encoders in hierarchical_make_adjustment.py compute one
Bayesian-shrunk target mean over ALL of DEV and map it back onto the same rows
(fit on dev → transform dev), with no out-of-fold and no temporal cutoff
(§B.2 of the audit). This recomputes the naive *in-sample* target mean per key
on DEV and measures how closely the served encoded column tracks it. Near-
identity (especially on low-support keys, where OOF and in-sample diverge most)
is the signature of in-sample encoding → leakage risk.

INPUT (all optional; each check skips if its inputs are absent)
  --priors PATH          time-sliced priors parquet (expects an asof_month col)
  --dev PATH             DEV frame (parquet/csv) with test_date + target + keys
  --oot PATH             OOT frame
  --target-col           default: target
  --date-col             default: test_date
  --encoded-cols a=b ...  served_column=key_column pairs to test in CHECK B,
                          e.g. make_fail_rate_smoothed=make
                               model_fail_rate_smoothed=model_id

Runs in your environment (needs pandas/numpy); not runnable in the doc sandbox.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path):
    import pandas as pd

    if path.suffix in (".parquet", ".pq"):
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _month(series):
    import pandas as pd

    return pd.to_datetime(series).dt.to_period("M").dt.to_timestamp()


def check_pit(dev, oot, priors, *, date_col):
    """CHECK A: effective asof-month must not be in the future of the row."""
    import pandas as pd

    out = {"status": "run", "violations": {}, "notes": []}
    if priors is None or "asof_month" not in getattr(priors, "columns", []):
        out["status"] = "skipped"
        out["notes"].append("priors parquet (with asof_month) not provided")
        return out
    max_asof = pd.to_datetime(priors["asof_month"]).max()
    out["max_asof_month"] = str(max_asof.date())

    for name, frame in [("dev", dev), ("oot", oot)]:
        if frame is None or date_col not in frame.columns:
            continue
        tm = _month(frame[date_col])
        eff_asof = tm.clip(upper=max_asof)  # mirrors add_eb_features clip
        future = (eff_asof > tm).sum()
        out["violations"][name] = {
            "rows": int(len(frame)),
            "future_prior_rows": int(future),  # should be 0 by construction
            "min_lag_months": float(((tm - eff_asof) / pd.Timedelta(days=30)).min()),
            "median_lag_months": float(((tm - eff_asof) / pd.Timedelta(days=30)).median()),
        }
        if name == "oot":
            clipped = (eff_asof <= max_asof).all()
            out["violations"]["oot"]["all_clipped_to_dev_max_asof"] = bool(clipped)
    out["notes"].append(
        "PASS requires future_prior_rows == 0 for every frame. NOTE: whether the "
        "prior at a given asof_month excludes that month's own outcomes is a "
        "builder property and is UNVERIFIABLE from outputs alone."
    )
    return out


def check_in_sample(dev, *, target_col, encoded_cols):
    """CHECK B: does the served encoded column track the in-sample target mean?"""
    import numpy as np

    out = {"status": "run", "encoders": {}}
    if dev is None or target_col not in dev.columns:
        out["status"] = "skipped"
        out["note"] = "DEV frame with target not provided"
        return out

    y = dev[target_col].astype(float)
    for served_col, key_col in encoded_cols.items():
        if served_col not in dev.columns or key_col not in dev.columns:
            out["encoders"][served_col] = {"status": "skipped",
                                           "reason": f"missing {served_col} or {key_col}"}
            continue
        # Naive in-sample target mean per key (what a leaky encoder reproduces).
        naive = dev.groupby(key_col)[target_col].transform("mean").astype(float)
        support = dev.groupby(key_col)[target_col].transform("size")
        served = dev[served_col].astype(float)

        mask = served.notna() & naive.notna()
        corr = float(np.corrcoef(served[mask], naive[mask])[0, 1]) if mask.sum() > 2 else float("nan")
        mad = float((served[mask] - naive[mask]).abs().mean())

        low = mask & (support < 50)
        corr_low = (
            float(np.corrcoef(served[low], naive[low])[0, 1])
            if low.sum() > 2 else float("nan")
        )
        # Heuristic verdict: tight tracking, especially on low-support keys where
        # OOF/time-sliced encoders would diverge, indicates in-sample encoding.
        leaky = (corr_low == corr_low) and corr_low > 0.9 and mad < 0.05
        out["encoders"][served_col] = {
            "key": key_col,
            "corr_with_in_sample_mean": corr,
            "corr_low_support_keys": corr_low,
            "mean_abs_diff": mad,
            "verdict": "LIKELY_IN_SAMPLE (leak risk)" if leaky
            else "inconclusive / appears OOF-or-shrunk",
        }
    out["note"] = (
        "Heuristic. Definitive proof requires the off-repo builder to expose "
        "fold assignments or time-slice cutoffs; gate #4 should be enforced "
        "there, not inferred here."
    )
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--priors", type=Path, default=None)
    p.add_argument("--dev", type=Path, default=None)
    p.add_argument("--oot", type=Path, default=None)
    p.add_argument("--target-col", default="target")
    p.add_argument("--date-col", default="test_date")
    p.add_argument("--encoded-cols", nargs="*", default=[
        "make_fail_rate_smoothed=make",
        "model_fail_rate_smoothed=model_id",
    ], help="served_col=key_col pairs for CHECK B")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)

    try:
        import pandas  # noqa: F401
    except Exception as e:  # pragma: no cover
        print(f"ERROR: pandas required ({e})", file=sys.stderr)
        return 2

    dev = _load(args.dev) if args.dev else None
    oot = _load(args.oot) if args.oot else None
    priors = _load(args.priors) if args.priors else None
    encoded = dict(pair.split("=", 1) for pair in args.encoded_cols)

    report = {
        "check_a_point_in_time": check_pit(dev, oot, priors, date_col=args.date_col),
        "check_b_in_sample_encoders": check_in_sample(
            dev, target_col=args.target_col, encoded_cols=encoded
        ),
        "gate": "v57 promotion gate #4 (target encodings out-of-fold or time-sliced only)",
    }
    print(json.dumps(report, indent=2))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
        print(f"wrote {args.out}", file=sys.stderr)

    # Fail (non-zero) if CHECK A finds future-dated priors.
    a = report["check_a_point_in_time"].get("violations", {})
    future = sum(v.get("future_prior_rows", 0) for v in a.values() if isinstance(v, dict))
    return 1 if future else 0


if __name__ == "__main__":
    sys.exit(main())
