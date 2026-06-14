"""decision.py — pure promotion-decision logic. PROTECTED referee module.

Two pure functions, no harness/data dependency, so they are exhaustively unit-tested
(test pyramid L1). This is where the **F3 fix** lives: seed direction is a 4-way
CLASSIFICATION, not a boolean. The old `seed_stable = all(d>0) or all(d<0)` treated a
consistently-harmful feature (both seeds negative) as "stable" and could KEEP a
pooled-negative feature. Here `stable_negative` is its own class and never promotes;
promotion requires `stable_positive`.

Units: every *_pp value is ΔAUC in PERCENTAGE POINTS (e.g. +0.30pp == +0.0030 AUC).
ECE thresholds elsewhere are absolute (`*_abs`); callers pass the boolean `ece_breach`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

STABLE_POSITIVE = "stable_positive"
STABLE_NEGATIVE = "stable_negative"
MIXED_UNSTABLE = "mixed_unstable"
FLAT_OR_NOISE = "flat_or_noise"


def classify_seed_direction(deltas, seed_dead_zone_pp: float) -> str:
    """Classify per-seed ΔAUC deltas (percentage points) into one of four directions.

    The dead-zone is inclusive: |d| <= seed_dead_zone_pp counts as flat. A seed is
    "positive" only if d > dead_zone and "negative" only if d < -dead_zone.

        all positive          -> stable_positive
        all negative          -> stable_negative
        all within dead-zone  -> flat_or_noise
        anything else         -> mixed_unstable   (incl. positive-mixed-with-flat:
                                                    stable_positive is STRICT — EVERY
                                                    seed must clear the dead-zone)

    Raises ValueError on empty input or any non-finite delta — a broken run must never
    be silently classified as promotable.
    """
    if not deltas:
        raise ValueError("classify_seed_direction: empty deltas")
    vals = [float(d) for d in deltas]
    if any(not math.isfinite(v) for v in vals):
        raise ValueError("classify_seed_direction: non-finite delta")
    dz = float(seed_dead_zone_pp)
    if all(v > dz for v in vals):
        return STABLE_POSITIVE
    if all(v < -dz for v in vals):
        return STABLE_NEGATIVE
    if all(abs(v) <= dz for v in vals):
        return FLAT_OR_NOISE
    return MIXED_UNSTABLE


@dataclass(frozen=True)
class Decision:
    verdict: str          # "promote" | "dead" | "discard"
    promotable: bool
    seed_direction: str
    reasons: dict


def decide(*, seed_direction: str, pooled_d_auc_pp: float,
           median_seed_d_auc_pp: float, within_segment_wins: int, ece_breach: bool,
           leakage_drop_pp: float, thresholds) -> Decision:
    """Pure promotion decision over scalar summaries.

    Promotable iff `stable_positive` AND pooled >= pooled_d_auc_min_pp AND
    median-seed >= median_seed_d_auc_min_pp AND within_segment_wins >=
    within_segment_min_slices AND no ECE breach. The within-segment requirement is an
    AND (not an OR with pooled): a pooled-only gain with flat within-segment slices is
    gradient-gaming and must NOT promote (GF-8 defense). Controls, faithful injection,
    clean worktree and a durable manifest are orchestrator-level gates, not decided
    here. A non-promotable candidate is `dead` when the leakage shuffle barely moves
    AUC (feature unused), else `discard`.
    """
    checks = {
        "is_stable_positive": seed_direction == STABLE_POSITIVE,
        "pooled_ok": pooled_d_auc_pp >= thresholds["pooled_d_auc_min_pp"],
        "median_ok": median_seed_d_auc_pp >= thresholds["median_seed_d_auc_min_pp"],
        "within_segment_ok": within_segment_wins >= thresholds["within_segment_min_slices"],
        "no_ece_breach": not ece_breach,
    }
    promotable = all(checks.values())
    if promotable:
        verdict = "promote"
    elif leakage_drop_pp < thresholds["leakage_min_auc_drop_pp"]:
        verdict = "dead"
    else:
        verdict = "discard"
    return Decision(verdict=verdict, promotable=promotable,
                    seed_direction=seed_direction, reasons=checks)


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def summarize_report(report) -> dict:
    """Reduce an arm0_harness segmented report to the scalars decide() needs, safely.

    A slice with no `d_auc_pp` (status unavailable_on_frame / too_small) is ignored. A
    flat slice (d_auc_pp == 0) is not a win. A NaN d_auc_pp is never a win. A NaN/missing
    d_ece is treated as a calibration breach (worst_d_ece := +inf) so it cannot be
    certified clean; a missing/NaN pooled delta is treated as 0 (no gain). Fail-safe by
    construction: ambiguity never counts toward promotion.
    """
    slices = report.get("slices", {})
    scored = {k: v for k, v in slices.items() if isinstance(v, dict) and "d_auc_pp" in v}
    within_wins = sorted(
        k for k, v in scored.items()
        if isinstance(v["d_auc_pp"], (int, float)) and not _is_nan(v["d_auc_pp"])
        and v["d_auc_pp"] > 0)
    d_eces = [v.get("d_ece") for v in scored.values() if "d_ece" in v]
    if any(e is None or _is_nan(e) for e in d_eces):
        worst_d_ece = math.inf
    else:
        worst_d_ece = max(d_eces, default=0.0)
    pooled = report.get("pooled", {}).get("d_auc_pp", 0.0)
    if pooled is None or _is_nan(pooled):
        pooled = 0.0
    return {"within_wins": within_wins, "worst_d_ece": worst_d_ece,
            "pooled_d_auc_pp": pooled}
