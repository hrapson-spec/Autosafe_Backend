"""candidate_feature.py — the agent's primary feature surface (not its only one).

The default place to write features and the loop's worked entry point. Under the
maximal-autonomy model the agent may ALSO add modules, helpers, taxonomies and
multi-feature candidates — it may edit anything EXCEPT the protected referee set
(see referee_guard.py). The referee (scorer, parity gate, held-out window) is
immutable; everything else, including this file, is the agent's to rewrite.

THE INTERFACE (the exact plug point used by the canonical serving FE — see
work/bakeoff_2026/r2b_build_v57.py:200-210, which calls
model_v55.engineer_features_with_stats(hist, pc, target_date)):

    compute_candidate_features(hist, postcode, target_date) -> dict[str, float|str]

  * `hist.mot_tests` is ALREADY filtered to tests strictly before `target_date`
    (r2b_build_v57.py:129 `h.test_date < t.tgt_date`). You CANNOT see same-cycle
    or future data — temporal leakage is structurally impossible here. This is not
    a creative limit; it just keeps your experiments valid.
  * Return a dict of NEW feature columns. They are appended to the cached v57.1
    frame, the model is retrained, and the segmented referee decides keep/revert.
  * Smoothed rates and missing-with-flag are strong DEFAULTS you may override per
    experiment (the referee will judge whether the raw form generalises).

Start from `compute_candidate_features` and grow from there — add helpers, modules,
even new files. The only off-limits code is the protected referee set.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # serving contract type; not imported at scaffold time
    from dvsa_client import VehicleHistory

MISSING = -1.0  # numeric sentinel; default pairs it with a `<name>_missing` flag


def smoothed_rate(events: float, exposure: float, global_rate: float,
                  alpha: float = 20.0) -> float:
    """Default form: (events + alpha*global)/(exposure + alpha). Override if you must."""
    return (events + alpha * global_rate) / (exposure + alpha)


def _first_use_anchor(hist: "VehicleHistory") -> tuple[datetime | None, int]:
    """Date contract (rev B finding 5): firstUsedDate primary, registrationDate
    fallback (+flag), manufactureDate never mixed. In the assembled history both
    registration_date and manufacture_date carry first_use_date (r2b:204); the
    real serving anchor must be confirmed against dvsa_client at wire-up.
    Returns (anchor_date, is_fallback)."""
    anchor = getattr(hist, "registration_date", None)
    if anchor is not None:
        return anchor, 0
    return getattr(hist, "manufacture_date", None), 1


def compute_candidate_features(hist: "VehicleHistory", postcode: str,
                               target_date: datetime) -> dict[str, Any]:
    """SEED CANDIDATE: raw vehicle age (Arm 1) — also the loop's positive control.

    The audit names raw age the cheapest within-segment lift (it currently enters
    only as a 5-band EB index; the model cannot split on age itself). This is the
    worked starting point; the agent is free to replace it with anything (see
    program.md — off-script invention is encouraged). As the positive control it
    MUST promote (adversarial_controls.md).
    """
    anchor, is_fallback = _first_use_anchor(hist)
    if anchor is None:
        return {"vehicle_age_years": MISSING,
                "vehicle_age_years_missing": 1,
                "age_anchor_is_fallback": 0}
    age_years = (target_date - anchor).days / 365.25
    return {
        "vehicle_age_years": round(age_years, 4),
        "vehicle_age_years_missing": 0,
        "age_anchor_is_fallback": is_fallback,
    }
