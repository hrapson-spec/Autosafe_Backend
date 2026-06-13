#!/usr/bin/env python3
"""
Chronos-2 forward-looking segment-rate forecasting job (OFFLINE ONLY).
=====================================================================

Produces the forward-looking failure-rate forecasts that the CatBoost model
consumes as the ``chronos_seg_*_forecast`` features. This script is **excluded
from the serving image** (see .railwayignore) and depends on torch +
chronos-forecasting (requirements-forecast.txt) which are *not* in the serving
runtime. It must run on the host that holds the data substrate
(``~/autosafe_work/`` + the iCloud master store).

What it does
------------
1. Builds monthly rate **series** per vehicle segment:
   - overall fail rate: reused from the existing EB panel
     (``time_sliced_eb_priors_dual.parquet``, column ``eb_segment_long``);
   - per-component fail rates (brakes/suspension/tyres/steering/visibility/
     lamps/body): aggregated here from the longitudinal defect frames
     (``cycle_first_with_history.parquet`` + defect/advisory lakes).
2. Runs Chronos-2 in **rolling-origin** mode: for each origin month ``O`` it
   forecasts month ``O+1`` using only history ``<= O`` and known-future calendar
   covariates. Each forecast is stored under ``asof_month = O+1`` (the month it
   is valid FOR) so the trainer can join on a row's own test month and stay
   point-in-time honest.
3. Emits, at segment / make / global grain:
   - training parquets (joined by ``add_chronos_forecast`` in the trainer), and
   - a compact serving ``.pkl`` lookup (loaded by ``model_v55.load_model``).

PRIVACY: this job reads only aggregated, non-PII rate panels. It must NOT read
the registration-bearing raw deltas in ``/tmp/dvsa_deltas/``.

PARITY: segment keys use the *shared* canonical banding from
``feature_engineering_v55`` (``normalize_age_band`` / ``mileage_to_band``) so the
offline keys, the training join, and the serving lookup are byte-identical.

Usage
-----
    python forecast_segment_rates.py --dry-run            # coverage/horizon stats only
    python forecast_segment_rates.py                      # build + write artifacts
    python forecast_segment_rates.py --asof-max 2024-12   # cap origins (backtests)
"""
from __future__ import annotations

import argparse
import logging
import pickle
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Shared canonical banding + feature names — single source of truth for parity.
from feature_engineering_v55 import (
    CHRONOS_COMPONENTS,
    CHRONOS_FEATURE_NAMES,
    mileage_to_band,
    normalize_age_band,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("forecast_segment_rates")

# --- Paths (host data substrate) -------------------------------------------------
WORK = Path.home() / "autosafe_work"
EB_SEGMENT_PANEL = WORK / "time_sliced_eb_priors_dual.parquet"      # overall-rate source
LONGITUDINAL = WORK / "cycle_first_with_history.parquet"           # component-rate source
OUT_DIR = WORK / "chronos_features"
SEG_OUT = OUT_DIR / "chronos_asof_forecast.parquet"
MAKE_OUT = OUT_DIR / "chronos_asof_forecast_make.parquet"
GLOBAL_OUT = OUT_DIR / "chronos_asof_forecast_global.parquet"
PKL_OUT = OUT_DIR / "chronos_segment_forecast.pkl"
BACKTEST_OUT = OUT_DIR / "chronos_backtest_metrics.json"

# --- Config ----------------------------------------------------------------------
WINDOW_START = pd.Timestamp("2019-01-01")   # matches model_bundle.WINDOW_START
HORIZON = 1                                  # forecast next month (matches monthly join)
MIN_HISTORY_MONTHS = 12                      # thinner series fall through the hierarchy
DEFAULT_RATE = 0.28                          # base-rate fallback (matches serving)
QUANTILE_LEVELS = [0.1, 0.5, 0.9]            # point feature = median (0.5)
CHRONOS_MODEL = "amazon/chronos-2"
OVERALL = "__overall__"                      # internal label for the overall-rate series

# Maps a Chronos component token -> defect-text keywords used to classify the
# longitudinal defect rows. Mirrors feature_engineering_v55.COMPONENT_CATEGORIES,
# extended to the visibility/lamps/body components the app surfaces.
COMPONENT_KEYWORDS: Dict[str, List[str]] = {
    "brakes": ["brake", "braking", "disc", "pad", "caliper", "abs"],
    "suspension": ["suspension", "shock", "absorber", "spring", "wishbone", "arm", "bush", "bearing"],
    "tyres": ["tyre", "tire", "wheel", "rim"],
    "steering": ["steering", "rack", "tie rod", "track rod", "ball joint", "power steering"],
    "visibility": ["wiper", "washer", "mirror", "windscreen", "view", "visibility"],
    "lamps": ["lamp", "light", "indicator", "bulb", "reflector", "electrical"],
    "body": ["chassis", "subframe", "sill", "floor", "structural", "body", "corro"],
}


# =============================================================================
# Pure date / rolling-origin helpers (unit-tested without torch)
# =============================================================================
def month_floor(ts) -> pd.Timestamp:
    """First-of-month timestamp."""
    return pd.Timestamp(ts).to_period("M").to_timestamp()


def next_month(ts) -> pd.Timestamp:
    """First day of the month AFTER ``ts``'s month."""
    return month_floor(ts) + pd.offsets.MonthBegin(1)


def rolling_origins(months: List[pd.Timestamp], min_history: int = MIN_HISTORY_MONTHS,
                    asof_max: Optional[pd.Timestamp] = None) -> List[pd.Timestamp]:
    """Origin months O for which we forecast O+1 (need >= min_history months <= O)."""
    months = sorted(month_floor(m) for m in months)
    out = []
    for i, o in enumerate(months):
        if i + 1 < min_history:
            continue
        if asof_max is not None and o > month_floor(asof_max):
            continue
        out.append(o)
    return out


def assert_asof_invariant(history_max_month: pd.Timestamp, stored_valid_month: pd.Timestamp) -> None:
    """A forecast valid for month M must be produced from history strictly before M."""
    if not (month_floor(history_max_month) < month_floor(stored_valid_month)):
        raise AssertionError(
            f"as-of leak: history up to {history_max_month} used for forecast stored "
            f"under valid-month {stored_valid_month} (must be strictly earlier)"
        )


def build_future_covariates(valid_month: pd.Timestamp) -> Dict[str, float]:
    """Known-future calendar covariates for the forecast month (no outcome leakage)."""
    m = int(month_floor(valid_month).month)
    return {
        "month": m,
        "is_winter": 1 if m in (10, 11, 12, 1, 2, 3) else 0,
        "month_sin": float(np.sin(2 * np.pi * m / 12.0)),
        "month_cos": float(np.cos(2 * np.pi * m / 12.0)),
    }


# =============================================================================
# Series construction
# =============================================================================
def build_overall_series() -> pd.DataFrame:
    """Overall monthly fail-rate series per segment from the existing EB panel.

    Returns long format: [item_id, component, model_id, make, age_band,
    mileage_band, asof_month, rate].
    """
    if not EB_SEGMENT_PANEL.exists():
        raise FileNotFoundError(f"EB panel not found: {EB_SEGMENT_PANEL}")
    df = pd.read_parquet(EB_SEGMENT_PANEL)
    keep = ["asof_month", "model_id", "age_band", "mileage_band", "eb_segment_long"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df["asof_month"] = month_floor_series(df["asof_month"])
    df = df[df["asof_month"] >= WINDOW_START]
    # The EB panel is already keyed on canonical bands; normalise defensively.
    df["age_band"] = df["age_band"].map(normalize_age_band)
    df = df.rename(columns={"eb_segment_long": "rate"})
    df["component"] = OVERALL
    df["make"] = df["model_id"].astype(str).str.split().str[0]
    df["item_id"] = _item_id(df["model_id"], df["age_band"], df["mileage_band"], df["component"])
    return df


def build_component_series() -> pd.DataFrame:
    """Per-component monthly fail-rate series per segment from the longitudinal frames.

    Each MOT test contributes, per component, a binary "failed on this component"
    derived from its defect texts; we average within (segment, component, month).
    Returns the same long format as build_overall_series().
    """
    if not LONGITUDINAL.exists():
        logger.warning("Longitudinal frame %s missing - component series skipped", LONGITUDINAL)
        return pd.DataFrame(columns=["item_id", "component", "model_id", "make",
                                     "age_band", "mileage_band", "asof_month", "rate"])
    df = pd.read_parquet(LONGITUDINAL)
    df["asof_month"] = month_floor_series(df["test_date"])
    df = df[df["asof_month"] >= WINDOW_START]
    df["age_band"] = df["age_band"].map(normalize_age_band)
    df["mileage_band"] = df["test_mileage"].map(mileage_to_band)
    df["make"] = df["model_id"].astype(str).str.split().str[0]

    # Per-component failure flag from defect text. `defect_text` is the
    # concatenated defect strings for the test (built upstream); fall back to an
    # empty string when absent so the classifier is robust.
    text = df.get("defect_text", pd.Series("", index=df.index)).fillna("").str.lower()
    frames = []
    for comp, kws in COMPONENT_KEYWORDS.items():
        hit = text.apply(lambda t, kws=kws: any(k in t for k in kws))
        comp_fail = (df["is_failure"].astype(bool) & hit).astype(float)
        g = (pd.DataFrame({
                "model_id": df["model_id"], "make": df["make"], "age_band": df["age_band"],
                "mileage_band": df["mileage_band"], "asof_month": df["asof_month"],
                "comp_fail": comp_fail,
             })
             .groupby(["model_id", "make", "age_band", "mileage_band", "asof_month"], as_index=False)["comp_fail"]
             .mean()
             .rename(columns={"comp_fail": "rate"}))
        g["component"] = comp
        frames.append(g)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out["item_id"] = _item_id(out["model_id"], out["age_band"], out["mileage_band"], out["component"])
    return out


def month_floor_series(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.to_period("M").dt.to_timestamp()


def _item_id(model_id, age_band, mileage_band, component) -> pd.Series:
    return (model_id.astype(str) + "|" + age_band.astype(str) + "|"
            + mileage_band.astype(str) + "|" + component.astype(str))


# =============================================================================
# Chronos-2 forecasting (torch — lazy import so this module imports without it)
# =============================================================================
def load_chronos_pipeline(device: str = "cpu"):
    """Load the pretrained Chronos-2 pipeline. Lazy import keeps torch out of any
    consumer that only needs the pure helpers above (and out of the serving image).
    NOTE: verify the exact entry point / predict signature against the pinned
    chronos-forecasting version (plan risk #3)."""
    try:
        from chronos import BaseChronosPipeline  # Chronos-2 ships under chronos-forecasting
    except ImportError as e:  # pragma: no cover - host-only path
        raise ImportError(
            "chronos-forecasting not installed. Install offline deps: "
            "pip install -r requirements-forecast.txt"
        ) from e
    logger.info("Loading Chronos-2 (%s) on %s", CHRONOS_MODEL, device)
    return BaseChronosPipeline.from_pretrained(CHRONOS_MODEL, device_map=device)


def forecast_long(series_long: pd.DataFrame, asof_max: Optional[pd.Timestamp],
                  dry_run: bool) -> pd.DataFrame:
    """Rolling-origin forecast for every series in ``series_long``.

    Returns long format [item_id, component, model_id, make, age_band,
    mileage_band, asof_month(=valid month), rate] where ``rate`` is the median
    forecast. In --dry-run we skip the model and emit coverage stats only.
    """
    if series_long.empty:
        return series_long
    all_months = sorted(series_long["asof_month"].unique())
    origins = rolling_origins(all_months, MIN_HISTORY_MONTHS, asof_max)
    logger.info("Series=%d  months=%d  origins=%d (min_history=%d)",
                series_long["item_id"].nunique(), len(all_months), len(origins), MIN_HISTORY_MONTHS)
    if dry_run:
        logger.info("[dry-run] skipping Chronos inference")
        return pd.DataFrame(columns=series_long.columns)

    pipe = load_chronos_pipeline()
    meta_cols = ["item_id", "component", "model_id", "make", "age_band", "mileage_band"]
    meta = series_long[meta_cols].drop_duplicates("item_id").set_index("item_id")
    rows = []
    for origin in origins:
        valid = next_month(origin)
        hist = series_long[series_long["asof_month"] <= origin]
        if hist.empty:
            continue
        assert_asof_invariant(hist["asof_month"].max(), valid)
        # Long-format context + known-future covariates for `valid`.
        ctx = hist.rename(columns={"asof_month": "timestamp", "rate": "target"})[
            ["item_id", "timestamp", "target"]
        ]
        cov = build_future_covariates(valid)
        future = (pd.DataFrame({"item_id": ctx["item_id"].unique()})
                  .assign(timestamp=valid, **cov))
        # predict_df returns long-format quantile forecasts; take the median.
        pred = pipe.predict_df(
            ctx, future_df=future, id_column="item_id", timestamp_column="timestamp",
            target_column="target", quantile_levels=QUANTILE_LEVELS, prediction_length=HORIZON,
        )
        med = pred.rename(columns={"0.5": "rate"})[["item_id", "rate"]].copy()
        med["rate"] = med["rate"].clip(0.0, 1.0)
        med["asof_month"] = valid
        rows.append(med)
    if not rows:
        return pd.DataFrame(columns=series_long.columns)
    out = pd.concat(rows, ignore_index=True).join(meta, on="item_id")
    return out


# =============================================================================
# Aggregation to grains + artifact assembly
# =============================================================================
def to_wide_segment(forecast_long_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot long per-component forecasts to one row per (segment, month) with the
    CHRONOS_FORECAST columns."""
    if forecast_long_df.empty:
        return pd.DataFrame(columns=["asof_month", "model_id", "age_band", "mileage_band"] + CHRONOS_FEATURE_NAMES)
    df = forecast_long_df.copy()
    df["feature"] = df["component"].map(
        lambda c: "chronos_seg_fail_forecast" if c == OVERALL else f"chronos_seg_{c}_forecast"
    )
    wide = (df.pivot_table(index=["asof_month", "model_id", "age_band", "mileage_band"],
                           columns="feature", values="rate", aggfunc="mean")
              .reset_index())
    for col in CHRONOS_FEATURE_NAMES:
        if col not in wide.columns:
            wide[col] = np.nan
    return wide[["asof_month", "model_id", "age_band", "mileage_band"] + CHRONOS_FEATURE_NAMES]


def to_wide_make(forecast_long_df: pd.DataFrame) -> pd.DataFrame:
    if forecast_long_df.empty:
        return pd.DataFrame(columns=["asof_month", "make"] + CHRONOS_FEATURE_NAMES)
    df = forecast_long_df.copy()
    df["feature"] = df["component"].map(
        lambda c: "chronos_seg_fail_forecast" if c == OVERALL else f"chronos_seg_{c}_forecast")
    wide = (df.pivot_table(index=["asof_month", "make"], columns="feature", values="rate", aggfunc="mean")
              .reset_index())
    for col in CHRONOS_FEATURE_NAMES:
        if col not in wide.columns:
            wide[col] = np.nan
    return wide[["asof_month", "make"] + CHRONOS_FEATURE_NAMES]


def to_wide_global(forecast_long_df: pd.DataFrame) -> pd.DataFrame:
    if forecast_long_df.empty:
        return pd.DataFrame(columns=["asof_month"] + CHRONOS_FEATURE_NAMES)
    df = forecast_long_df.copy()
    df["feature"] = df["component"].map(
        lambda c: "chronos_seg_fail_forecast" if c == OVERALL else f"chronos_seg_{c}_forecast")
    wide = (df.pivot_table(index=["asof_month"], columns="feature", values="rate", aggfunc="mean")
              .reset_index())
    for col in CHRONOS_FEATURE_NAMES:
        if col not in wide.columns:
            wide[col] = np.nan
    return wide[["asof_month"] + CHRONOS_FEATURE_NAMES]


def assemble_serving_pkl(seg: pd.DataFrame, make: pd.DataFrame, glob: pd.DataFrame) -> Dict:
    """Build the tuple-keyed dict consumed by feature_engineering_v55.lookup_chronos_forecasts.

    Pure (no IO) so it is unit-tested directly.
    """
    def _mstr(ts) -> str:
        return month_floor(ts).strftime("%Y-%m-01")

    overall: Dict = {}
    components: Dict[str, Dict] = {c: {} for c in CHRONOS_COMPONENTS}
    for _, r in seg.iterrows():
        key = (r["model_id"], r["age_band"], r["mileage_band"], _mstr(r["asof_month"]))
        if pd.notna(r["chronos_seg_fail_forecast"]):
            overall[key] = float(r["chronos_seg_fail_forecast"])
        for c in CHRONOS_COMPONENTS:
            v = r[f"chronos_seg_{c}_forecast"]
            if pd.notna(v):
                components[c][key] = float(v)

    make_map: Dict = {}
    make_components: Dict[str, Dict] = {c: {} for c in CHRONOS_COMPONENTS}
    for _, r in make.iterrows():
        key = (r["make"], _mstr(r["asof_month"]))
        if pd.notna(r["chronos_seg_fail_forecast"]):
            make_map[key] = float(r["chronos_seg_fail_forecast"])
        for c in CHRONOS_COMPONENTS:
            v = r[f"chronos_seg_{c}_forecast"]
            if pd.notna(v):
                make_components[c][key] = float(v)

    global_map: Dict = {}
    global_components: Dict[str, Dict] = {c: {} for c in CHRONOS_COMPONENTS}
    for _, r in glob.iterrows():
        key = _mstr(r["asof_month"])
        if pd.notna(r["chronos_seg_fail_forecast"]):
            global_map[key] = float(r["chronos_seg_fail_forecast"])
        for c in CHRONOS_COMPONENTS:
            v = r[f"chronos_seg_{c}_forecast"]
            if pd.notna(v):
                global_components[c][key] = float(v)

    months = [m for m in [seg["asof_month"].max() if not seg.empty else None,
                          glob["asof_month"].max() if not glob.empty else None] if m is not None]
    latest = _mstr(max(months)) if months else None
    return {
        "version": "chronos2-v1",
        "model_name": CHRONOS_MODEL,
        "horizon": HORIZON,
        "default_rate": DEFAULT_RATE,
        "generated_at": datetime.utcnow().isoformat(),
        "latest_asof_month": latest,
        "overall": overall,
        "components": components,
        "make": make_map,
        "make_components": make_components,
        "global": global_map,
        "global_components": global_components,
    }


# =============================================================================
# Orchestration
# =============================================================================
def run(asof_max: Optional[str], dry_run: bool) -> None:
    asof_max_ts = month_floor(pd.Timestamp(asof_max)) if asof_max else None
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("Building series (overall + components)...")
    series = pd.concat([build_overall_series(), build_component_series()], ignore_index=True)
    series = series[series["rate"].notna()]

    fc = forecast_long(series, asof_max_ts, dry_run)
    if dry_run:
        logger.info("[dry-run] done (no artifacts written)")
        return

    seg = to_wide_segment(fc)
    make = to_wide_make(fc)
    glob = to_wide_global(fc)

    seg.to_parquet(SEG_OUT, index=False)
    make.to_parquet(MAKE_OUT, index=False)
    glob.to_parquet(GLOBAL_OUT, index=False)
    with open(PKL_OUT, "wb") as f:
        pickle.dump(assemble_serving_pkl(seg, make, glob), f)
    logger.info("Wrote %s, %s, %s, %s", SEG_OUT, MAKE_OUT, GLOBAL_OUT, PKL_OUT)


def main() -> None:
    ap = argparse.ArgumentParser(description="Chronos-2 offline segment-rate forecasts")
    ap.add_argument("--dry-run", action="store_true", help="coverage/horizon stats only; no writes")
    ap.add_argument("--asof-max", default=None, help="cap origin months (YYYY-MM), for backtests")
    args = ap.parse_args()
    run(asof_max=args.asof_max, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
