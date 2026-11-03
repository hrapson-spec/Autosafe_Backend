"""Rollback decision: evaluator outputs -> {continue, alert, fall_back_bands_only, fall_back_stub}.

A pure function. It reads the dicts returned by evaluate_g7 / evaluate_g6 / evaluate_bands and a
`RollbackPolicy`; it performs no action. The policy has NO defaults: every threshold or choice that the
frozen texts do not fix is an explicit argument, so an unset decision cannot be silently invented.

What the spec does fix (and so is hard-wired here):
  * A G7 trigger means "hide percentages and report, no retraining/recentring"
    (PLAN_rev3_20261026.md:96; protocol/AMENDMENT_DRAFT.md:57).
  * After hiding percentages, bands are kept only if the bands-only test holds (n >= 200 per band, strictly
    increasing rates, adjacent-difference bootstrap lower bound > 0; AMENDMENT_DRAFT.md:55); otherwise the
    product is unavailable, i.e. the stub (garage/api/provider.py StubProvider; providers/__init__.py:22-29).
  * Failed bundle verification/validity means no operational score or rank (AMENDMENT:55) -> stub.

What the policy must supply (open decisions): see RollbackPolicy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from ._stats import MonitoringInputError

MODES = ("percentages", "bands_only", "stub")
ACTIONS = ("continue", "alert", "fall_back_bands_only", "fall_back_stub")
_SEVERITY = {"continue": 0, "alert": 1, "fall_back_bands_only": 2, "fall_back_stub": 3}


@dataclass(frozen=True)
class RollbackPolicy:
    g7_clarification_approved: bool          # AMENDMENT:57 says Henri must approve; False caps G7 triggers at 'alert'
    on_insufficient_g7_data: str             # 'continue' | 'alert' when fewer than the required outcomes exist
    workshop_bias_action: str                # 'alert' | 'hide_percentages' for a per-workshop bias breach
    psi_alert_threshold: Optional[float]     # PSI above this (any input/score) -> alert; None = never
    psi_fallback_threshold: Optional[float]  # PSI above this -> hide percentages; None = never
    unsupported_share_action: str            # 'ignore' | 'alert' when the G6 unsupported-share guard fails

    def validate(self) -> None:
        if not isinstance(self.g7_clarification_approved, bool):
            raise MonitoringInputError("g7_clarification_approved must be a bool")
        if self.on_insufficient_g7_data not in ("continue", "alert"):
            raise MonitoringInputError("on_insufficient_g7_data must be 'continue' or 'alert'")
        if self.workshop_bias_action not in ("alert", "hide_percentages"):
            raise MonitoringInputError("workshop_bias_action must be 'alert' or 'hide_percentages'")
        if self.unsupported_share_action not in ("ignore", "alert"):
            raise MonitoringInputError("unsupported_share_action must be 'ignore' or 'alert'")
        for n in ("psi_alert_threshold", "psi_fallback_threshold"):
            v = getattr(self, n)
            if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
                raise MonitoringInputError(f"{n} must be a non-negative number or None")
        a, f = self.psi_alert_threshold, self.psi_fallback_threshold
        if a is not None and f is not None and f < a:
            raise MonitoringInputError("psi_fallback_threshold must be >= psi_alert_threshold")


def decide(policy: RollbackPolicy, *, current_mode: str, bundle_verified: bool,
           g7: Optional[Mapping[str, Any]] = None, g6: Optional[Mapping[str, Any]] = None,
           bands_only: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """current_mode: what the app shows now ('percentages' | 'bands_only' | 'stub').
    bundle_verified: result of the bundle/parity verification (False -> stub).
    g7 / g6: evaluator outputs (None = not evaluated). bands_only: evaluate_bands on the CUMULATIVE matched
    outcomes with the section-6 minimum (None = no evidence, treated as not qualified)."""
    policy.validate()
    if current_mode not in MODES:
        raise MonitoringInputError(f"current_mode must be one of {MODES}")
    if not isinstance(bundle_verified, bool):
        raise MonitoringInputError("bundle_verified must be a bool")

    reasons: List[Dict[str, Any]] = []
    hide: List[Dict[str, Any]] = []      # reasons that require percentages to be hidden
    alerts: List[Dict[str, Any]] = []
    force_stub: List[Dict[str, Any]] = []

    if not bundle_verified:
        force_stub.append({"source": "bundle", "rule": "bundle_not_verified"})

    if g7 is not None:
        if g7.get("status") == "insufficient_data":
            if policy.on_insufficient_g7_data == "alert":
                alerts.append({"source": "g7", "rule": "insufficient_data", "n": g7.get("n_matched_total")})
        for t in g7.get("triggers", []):
            r = {"source": "g7", "rule": t["trigger"], **{k: v for k, v in t.items() if k != "trigger"}}
            if t["trigger"] == "workshop_bias" and policy.workshop_bias_action == "alert":
                alerts.append(r)
            elif not policy.g7_clarification_approved:
                r = {**r, "capped_to_alert": "G7 clarification (AMENDMENT:57) not approved"}
                alerts.append(r)
            else:
                hide.append(r)

    if g6 is not None:
        psis = g6.get("psi", {})
        for name, e in psis.items():
            v = e.get("psi")
            if v is None:
                continue
            if policy.psi_fallback_threshold is not None and v > policy.psi_fallback_threshold:
                hide.append({"source": "g6", "rule": "psi_fallback", "input": name, "psi": v,
                             "threshold": policy.psi_fallback_threshold})
            elif policy.psi_alert_threshold is not None and v > policy.psi_alert_threshold:
                alerts.append({"source": "g6", "rule": "psi_alert", "input": name, "psi": v,
                               "threshold": policy.psi_alert_threshold})
        if policy.unsupported_share_action == "alert" and g6.get("unsupported_guard_ok") is False:
            alerts.append({"source": "g6", "rule": "unsupported_share_guard", "share": g6.get("unsupported_share")})

    if current_mode == "stub":
        # Nothing shown, so nothing to roll back; what would have forced a fall-back is surfaced as an alert.
        alerts.extend({**r, "note": "informational: already in stub mode"} for r in hide)
        hide = []
    bands_ok = bool(bands_only and bands_only.get("bands_only_qualified"))
    target = current_mode
    if current_mode != "stub":
        if force_stub:
            target = "stub"
        elif hide:
            target = "bands_only" if bands_ok else "stub"
        elif current_mode == "bands_only" and not bands_ok and bands_only is not None:
            target = "stub"
            force_stub.append({"source": "bands", "rule": "bands_only_no_longer_qualified",
                               "unmet": bands_only.get("bands_only_unmet")})
    if target != current_mode and target == "stub" and hide and not force_stub:
        reasons.append({"source": "bands", "rule": "bands_only_not_qualified", "unmet":
                        (bands_only or {}).get("bands_only_unmet", ["no band evidence supplied"])})
    reasons = [] if current_mode == "stub" else force_stub + hide + reasons

    if target == "stub" and current_mode != "stub":
        action = "fall_back_stub"
    elif target == "bands_only" and current_mode == "percentages":
        action = "fall_back_bands_only"
    else:
        action = "alert" if alerts else "continue"
    return {"action": action, "current_mode": current_mode, "target_mode": target,
            "reasons": reasons, "alerts": alerts,
            "g7_spec_status": (g7 or {}).get("spec_status"),
            "note": "No retraining or recentring is triggered by any rule (AMENDMENT:57)."}
