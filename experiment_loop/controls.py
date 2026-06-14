"""controls.py — the adversarial control battery. PROTECTED referee module.

The battery proves the evaluator BITES before the loop is trusted (adversarial_controls.md;
review pts 3, 4, 12). Two tiers of positive control:
  * positive_synthetic_{obvious,nearthreshold} — planted, label-derived features that MUST
    promote if the evaluator can detect lift. FENCED: control_only=True / deployable=False;
    generated only here, can never enter the candidate registry or promotion path. The
    near-threshold one guards against "detects a sledgehammer but not a realistic lift".
  * positive_domain (vehicle_age_years) — a real feature expected to promote, WITH an escape
    hatch: if the synthetic positives pass but this fails, authority stays locked pending a
    ratified resolution_state (not "evaluator broken").

Negative / structural controls:
  * negative_gradient_gaming — a monotone (rank-preserving) transform adding no within-band
    info: pooled may rise but within-segment stays flat -> MUST be rejected (GF-8).
  * noop — a constant feature -> MUST be dead (~0 leakage drop).
  * negative_train_only — a serving-absent field -> blocked by gf17 parity. pending_not_enforced
    (the live assertion needs the P2 serving-FE rebuild).
  * replay — re-run a control at the same cohort/seed -> identical deterministic_payload_hash
    (checked by the CLI by hash equality, not a decision).

Per-control status ∈ {required_pass, pending_not_enforced}. battery_summary never reports
green while a required control fails, and lists pending controls separately — it must never
overstate protection (review pt 12).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DOMAIN_RESOLUTION_STATES = [
    "domain_control_invalid_redundant", "domain_control_replaced",
    "pipeline_injection_fault", "model_truth_age_redundant",
]


@dataclass(frozen=True)
class Control:
    name: str
    kind: str          # positive_synthetic | positive_domain | negative | noop | replay
    status: str        # required_pass | pending_not_enforced
    expected: str      # promote | reject | dead | replay
    control_only: bool
    deployable: bool
    resolution_states: tuple = ()


CONTROLS = [
    Control("positive_synthetic_obvious", "positive_synthetic", "required_pass",
            "promote", control_only=True, deployable=False),
    Control("positive_synthetic_nearthreshold", "positive_synthetic", "required_pass",
            "promote", control_only=True, deployable=False),
    Control("positive_domain", "positive_domain", "required_pass", "promote",
            control_only=False, deployable=True,
            resolution_states=tuple(DOMAIN_RESOLUTION_STATES)),
    Control("negative_gradient_gaming", "negative", "required_pass", "reject",
            control_only=True, deployable=False),
    Control("noop", "noop", "required_pass", "dead", control_only=True, deployable=False),
    Control("replay", "replay", "required_pass", "replay", control_only=True, deployable=False),
    Control("negative_train_only", "negative", "pending_not_enforced", "reject",
            control_only=True, deployable=False),
]

_BY_NAME = {c.name: c for c in CONTROLS}


def get_control(name: str) -> Control:
    return _BY_NAME[name]


def synthetic_feature(name: str, y, rng) -> np.ndarray:
    """Generate a fenced, label-derived control feature. NEVER deployable — harness only."""
    y = np.asarray(y, dtype=float)
    if name == "positive_synthetic_obvious":
        return y + rng.normal(0.0, 0.05, size=len(y))                  # AUC ~ 1
    if name == "positive_synthetic_nearthreshold":
        return 0.30 * (2 * y - 1) + rng.normal(0.0, 1.0, size=len(y))  # weak but real
    if name == "noop":
        return np.zeros(len(y))                                        # constant -> dead
    raise ValueError(f"no synthetic generator for control {name!r}")


def gradient_gaming_transform(values) -> np.ndarray:
    """A monotone, rank-preserving transform of an existing feature — adds no within-band
    ordering, so within-segment AUC stays flat while pooled may shift (GF-8 trap)."""
    v = np.asarray(values, dtype=float)
    return np.sqrt(v - np.min(v) + 1.0)


def classify_control_outcome(control: Control, decision) -> str:
    """pass/fail for a decision-based control (promote/reject/dead). Replay is checked by
    the caller (hash equality), not here."""
    if control.expected == "promote":
        return "pass" if decision.verdict == "promote" else "fail"
    if control.expected == "reject":
        return "pass" if not decision.promotable else "fail"
    if control.expected == "dead":
        return "pass" if decision.verdict == "dead" else "fail"
    raise ValueError(f"classify_control_outcome cannot handle expected={control.expected!r}")


def battery_summary(results: dict) -> dict:
    """Aggregate per-control outcomes. required_control_battery_green iff EVERY required_pass
    control has outcome 'pass'. Pending controls are listed separately and never counted as a
    pass or a fail (review pt 12)."""
    required = [c.name for c in CONTROLS if c.status == "required_pass"]
    pending = [c.name for c in CONTROLS if c.status == "pending_not_enforced"]
    green = all(results.get(n) == "pass" for n in required)
    return {"required_control_battery_green": green,
            "pending_controls": [n for n in pending if n in results],
            "per_control": dict(results)}
