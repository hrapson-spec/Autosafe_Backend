#!/usr/bin/env python3
"""Quantify the train/serve mileage skew on a serving-faithful replay (audit #3).

THE SKEW
--------
Training and serving disagree on what ``test_mileage`` (and the derived
``annualized_mileage_v2``) mean for the row being predicted:

* TRAIN uses the odometer recorded **at the target test itself**:
  ``CAST(COALESCE(d.test_mileage, 50000) AS FLOAT) as test_mileage``
  (train_catboost_production_v55.py build_query_dev:1493), and
  ``annualized = (d.test_mileage - m_prev.test_mileage) / days * 365``
  (add_mileage_block_v36:1353-1364).

* SERVE uses the odometer of the **last completed test**, because at prediction
  time the upcoming test has not happened and the route takes no mileage input:
  ``test_mileage = latest_test.odometer_value``
  (feature_engineering_v55.py:241-262), annualized from the two most recent
  completed tests (feature_engineering_v55.py:550-559).

So for the same conceptual row, serving's mileage lags training's by one
inter-test interval. This script measures that gap on real per-test data.

INPUT
-----
A per-test table (parquet or csv) with, at minimum:
  --vehicle-col   vehicle id           (default: vehicle_id)
  --date-col      test date            (default: test_date)
  --mileage-col   odometer at the test (default: test_mileage)
  --unit-col      odometer unit mi/km  (optional; values converted to miles)
  --target-col    is_failure 0/1       (optional; enables fail-rate-by-skew)

For each test t (with >= 1 prior) we reconstruct:
  train_test_mileage  = odo_t
  serve_test_mileage  = odo_{t-1}                      (last completed test)
  train_annualized    = (odo_t   - odo_{t-1}) / days(t,   t-1) * 365
  serve_annualized    = (odo_{t-1}- odo_{t-2}) / days(t-1, t-2) * 365   (needs >=2 priors)

and report the distribution of (serve - train).

OUTPUT
------
Prints a summary and, with --out, writes a JSON report. No model is required;
this isolates the *feature-level* skew. (Model-score impact is a documented
follow-up: feed serve_* vs train_* mileage through the model and compare AUC.)

Runs in your environment (needs pandas); not runnable in the doc sandbox.
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


def _to_miles(series, unit_series):
    if unit_series is None:
        return series
    import numpy as np

    is_km = unit_series.astype(str).str.lower().eq("km")
    return np.where(is_km, (series * 0.621371).round(), series)


def replay(df, *, vehicle_col, date_col, mileage_col, unit_col, target_col):
    import numpy as np
    import pandas as pd

    df = df[[c for c in [vehicle_col, date_col, mileage_col, unit_col, target_col]
             if c]].copy()
    df[date_col] = pd.to_datetime(df[date_col])
    if unit_col:
        df[mileage_col] = _to_miles(df[mileage_col], df[unit_col])
    df = df.dropna(subset=[vehicle_col, date_col, mileage_col])
    df = df.sort_values([vehicle_col, date_col])

    g = df.groupby(vehicle_col, sort=False)
    # Prior-test references within each vehicle.
    df["odo_t"] = df[mileage_col].astype(float)
    df["odo_prev"] = g[mileage_col].shift(1)
    df["odo_prev2"] = g[mileage_col].shift(2)
    df["date_prev"] = g[date_col].shift(1)
    df["date_prev2"] = g[date_col].shift(2)

    # test_mileage: train = odo_t, serve = odo_prev (last completed test).
    has_prev = df["odo_prev"].notna()
    df["train_test_mileage"] = df["odo_t"]
    df["serve_test_mileage"] = df["odo_prev"]
    df["test_mileage_skew"] = df["serve_test_mileage"] - df["train_test_mileage"]

    # annualized: train uses (t, t-1); serve uses (t-1, t-2).
    days_t = (df[date_col] - df["date_prev"]).dt.days
    days_prev = (df["date_prev"] - df["date_prev2"]).dt.days
    with np.errstate(invalid="ignore", divide="ignore"):
        df["train_annualized"] = (df["odo_t"] - df["odo_prev"]) / days_t * 365.0
        df["serve_annualized"] = (df["odo_prev"] - df["odo_prev2"]) / days_prev * 365.0
    df["annualized_skew"] = df["serve_annualized"] - df["train_annualized"]

    rows = df[has_prev].copy()  # rows where serving has any prior to lean on

    def _stats(s):
        s = s.dropna()
        if len(s) == 0:
            return {}
        return {
            "n": int(len(s)),
            "mean": float(s.mean()),
            "median": float(s.median()),
            "p05": float(s.quantile(0.05)),
            "p95": float(s.quantile(0.95)),
            "mean_abs": float(s.abs().mean()),
            "share_nonzero": float((s.abs() > 1e-9).mean()),
        }

    pct = (rows["test_mileage_skew"] / rows["train_test_mileage"].replace(0, np.nan))
    report = {
        "n_tests_total": int(len(df)),
        "n_rows_with_prior": int(len(rows)),
        "n_single_test_vehicles": int((g.size() == 1).sum()),
        "test_mileage_skew_miles": _stats(rows["test_mileage_skew"]),
        "test_mileage_skew_pct": _stats(pct * 100.0),
        "annualized_skew": _stats(rows["annualized_skew"]),
    }
    if target_col:
        # Does the skew correlate with the outcome? (Are we understating mileage
        # most for exactly the rows that fail?)
        rr = rows.dropna(subset=["annualized_skew", target_col])
        if len(rr):
            report["fail_rate_overall"] = float(rr[target_col].mean())
            q = rr["annualized_skew"].abs().quantile(0.9)
            report["fail_rate_high_skew_decile"] = float(
                rr.loc[rr["annualized_skew"].abs() >= q, target_col].mean()
            )
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("data", type=Path, help="per-test parquet/csv")
    p.add_argument("--vehicle-col", default="vehicle_id")
    p.add_argument("--date-col", default="test_date")
    p.add_argument("--mileage-col", default="test_mileage")
    p.add_argument("--unit-col", default=None)
    p.add_argument("--target-col", default=None)
    p.add_argument("--out", type=Path, default=None, help="write JSON report here")
    args = p.parse_args(argv)

    try:
        import pandas  # noqa: F401
    except Exception as e:  # pragma: no cover
        print(f"ERROR: pandas required ({e})", file=sys.stderr)
        return 2

    df = _load(args.data)
    report = replay(
        df,
        vehicle_col=args.vehicle_col,
        date_col=args.date_col,
        mileage_col=args.mileage_col,
        unit_col=args.unit_col,
        target_col=args.target_col,
    )

    print(json.dumps(report, indent=2))
    sk = report["test_mileage_skew_miles"]
    if sk:
        print(
            f"\nSUMMARY: serving understates current mileage by a mean of "
            f"{-sk['mean']:.0f} miles (median {-sk['median']:.0f}) across "
            f"{sk['n']:,} rows with prior history; "
            f"annualized mileage skew mean_abs = "
            f"{report['annualized_skew'].get('mean_abs', float('nan')):.0f} mi/yr.",
            file=sys.stderr,
        )
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
