"""Turns a verified QualifiedBundle v2 (vendored `garage.api.qual_v2`, T2c route-to-live step 7) into a qualified `DisplayPolicy`.

Fail closed: `load_qualification` returns (QualifiedDisplay, None) only when the sidecar, every hash binding, the receipt and the
out-of-band pin all verify; otherwise (None, code). The code is a stable qual_v2 `QualError.code` or one of this module's own
codes below. Callers log the CODE only (never data values, paths' contents, pins or probabilities) and stay shadow/unqualified.

The pin is the trust anchor. It comes from deployment configuration (GARAGE_MODEL_BUNDLE_PIN) and is never read from the bundle.
`allow_synthetic` is code-injected for fixture tests; it is not environment-configurable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ..provider import DisplayPolicy
from ..qual_v2.bundle_v2 import SIDECAR, try_read_bundle_v2

CODE_NO_SIDECAR = "no_sidecar"
CODE_POLICY_INVALID = "display_policy_invalid"
CODE_LOADER_ERROR = "unexpected_loader_error"

_POLICY_KEYS = {"rounding_pp", "band_edges", "band_labels", "provisional", "display_state", "policy_version", "model_version",
                "suppression_reason", "operational_qualified", "ranking_enabled"}


@dataclass(frozen=True)
class QualifiedDisplay:
    policy: DisplayPolicy
    receipt_sha256: str
    model_version: str


def _policy_from(dp: dict, model_version: str) -> DisplayPolicy | None:
    if not isinstance(dp, dict) or set(dp) != _POLICY_KEYS:
        return None
    # DisplayPolicy.problem() does not check types; a qualified policy must be exactly typed.
    if not all(type(dp[k]) is bool for k in ("provisional", "operational_qualified", "ranking_enabled")) \
            or type(dp["rounding_pp"]) is not int or not isinstance(dp["band_edges"], list) \
            or not all(type(x) is int for x in dp["band_edges"]) or not isinstance(dp["band_labels"], dict) \
            or not all(isinstance(dp[k], str) and dp[k] for k in ("display_state", "policy_version", "model_version")):
        return None
    try:
        pol = DisplayPolicy(rounding_pp=dp["rounding_pp"], band_edges=tuple(dp["band_edges"]), band_labels=dict(dp["band_labels"]),
                            provisional=dp["provisional"], display_state=dp["display_state"], policy_version=dp["policy_version"],
                            model_version=dp["model_version"], suppression_reason=dp["suppression_reason"],
                            operational_qualified=dp["operational_qualified"], ranking_enabled=dp["ranking_enabled"])
    except (TypeError, ValueError):
        return None
    if pol.problem() is not None:
        return None
    if pol.display_state not in ("bands", "percentage") or pol.operational_qualified is not True or pol.provisional is not False \
            or pol.suppression_reason is not None or pol.model_version != model_version:
        return None
    return pol


def load_qualification(directory, *, pin, allow_synthetic: bool = False,
                       verify_base_files: bool = True) -> tuple[QualifiedDisplay | None, str | None]:
    """Never raises. `pin` None/empty => qual_v2 refuses with pin_missing."""
    try:
        if not os.path.lexists(os.path.join(os.fspath(directory), SIDECAR)):
            return None, CODE_NO_SIDECAR
        pin = (pin or "").strip().lower() or None
        bundle, code = try_read_bundle_v2(directory, pin=pin, allow_synthetic=allow_synthetic, verify_base_files=verify_base_files)
        if bundle is None:
            return None, code or CODE_LOADER_ERROR
        model_version = bundle.base["manifest"]["model_version"]
        pol = _policy_from(bundle.display_policy, model_version)
        if pol is None:
            return None, CODE_POLICY_INVALID
        return QualifiedDisplay(policy=pol, receipt_sha256=bundle.receipt_sha256, model_version=model_version), None
    except Exception:  # noqa: BLE001 - fail closed; never let a qualification problem stop the app
        return None, CODE_LOADER_ERROR
