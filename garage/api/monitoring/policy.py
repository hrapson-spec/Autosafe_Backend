"""Policy files for the monitor-g6 / monitor-g7 commands.

Every value the commands use comes from one JSON policy file whose raw-bytes sha256 is printed and recorded in
the command output. There are NO defaults: a missing key, an unknown key, a wrong type or an out-of-range value
refuses the run (PolicyError). Values the protocol adopts (500 supported bookings, PSI 0.10, 30% unsupported
guard, +0.5 smoothing; pooled 300, workshop 100, 5 pp, AUC 0.02, bands-only n 200) must still be written in the
file AND must equal the adopted value: a policy file cannot change an adopted gate. Sources are in psi.py, g7.py
and bands.py.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ._stats import MonitoringInputError
from .bands import BANDS_ONLY_MIN_N
from .classify import OutcomePolicy, PrsPairingRule
from .g7 import AUC_MARGIN, BIAS_BOUND_PP, MIN_WORKSHOP_N, POOLED_WINDOW, G7Params
from .reader import SCORE_COLUMNS
from .psi import MAX_UNSUPPORTED_SHARE, MIN_SUPPORTED_BOOKINGS, POPULATIONS, PSI_THRESHOLD, SMOOTHING


class PolicyError(MonitoringInputError):
    """The policy file is missing a value or carries one the commands refuse."""


def _obj(d: Any, keys: Sequence[str], where: str) -> Mapping[str, Any]:
    if not isinstance(d, dict):
        raise PolicyError(f"{where}: must be an object")
    missing = [k for k in keys if k not in d]
    extra = sorted(set(d) - set(keys))
    if missing:
        raise PolicyError(f"{where}: missing required value(s) {missing}")
    if extra:
        raise PolicyError(f"{where}: unknown key(s) {extra}")
    return d


def _int(v: Any, where: str, lo: int = 0) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise PolicyError(f"{where}: must be an integer >= {lo}")
    return v


def _num(v: Any, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PolicyError(f"{where}: must be a finite number")
    return float(v)


def _str(v: Any, where: str) -> str:
    if not isinstance(v, str) or not v:
        raise PolicyError(f"{where}: must be a non-empty string")
    return v


def _pinned(v: float, adopted: float, where: str) -> float:
    if abs(v - adopted) > 1e-12:
        raise PolicyError(f"{where}: {v} differs from the adopted value {adopted}; a policy file cannot change an "
                          f"adopted gate")
    return v


def _pinned_int(v: Any, adopted: int, where: str) -> int:
    _int(v, where, 1)
    _pinned(float(v), float(adopted), where)
    return v


def read_policy(path: str, kind: str) -> Tuple[Dict[str, Any], str]:
    """(parsed, sha256 of the raw file bytes). `kind` is 'monitor_g6' or 'monitor_g7' and must match the file."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise PolicyError(f"cannot read policy file {path!r}: {e}") from e
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PolicyError(f"policy file is not valid UTF-8 JSON: {e}") from e
    if not isinstance(parsed, dict) or parsed.get("kind") != kind:
        raise PolicyError(f"policy file must be an object with \"kind\": \"{kind}\"")
    return parsed, hashlib.sha256(raw).hexdigest()


def _score_column(v: Any) -> str:
    """The score is read from a column NAMED in the policy (no default): both columns hold the scorer's calibrated
    probability but are populated in different modes (reader.SCORE_COLUMNS)."""
    if v not in SCORE_COLUMNS:
        raise PolicyError(f"score_column: must be one of {sorted(SCORE_COLUMNS)}")
    return v


# ------------------------------------------------------------------------------------------------- G7
G7_KEYS = ("kind", "score_column", "g7", "bootstrap", "bands", "scope", "outcome")


def g7_policy(parsed: Mapping[str, Any]) -> Dict[str, Any]:
    d = _obj(parsed, G7_KEYS, "policy")
    g = _obj(d["g7"], ("pooled_window", "min_workshop_n", "bias_bound_pp", "auc_margin", "workshop_window",
                       "min_band_n", "baseline_auc", "clarification_approved"), "g7")
    if not isinstance(g["clarification_approved"], bool):
        raise PolicyError("g7.clarification_approved: must be true or false")
    base = g["baseline_auc"]
    if base is not None:
        base = _num(base, "g7.baseline_auc")
        if not 0.0 <= base <= 1.0:
            raise PolicyError("g7.baseline_auc: must be in [0, 1] or null (null = per-record baseline_score)")
    params = G7Params(
        workshop_window=_int(g["workshop_window"], "g7.workshop_window", 1),
        min_band_n=_int(g["min_band_n"], "g7.min_band_n", 1),
        baseline_auc=base, clarification_approved=g["clarification_approved"],
        pooled_window=_pinned_int(g["pooled_window"], POOLED_WINDOW, "g7.pooled_window"),
        min_workshop_n=_pinned_int(g["min_workshop_n"], MIN_WORKSHOP_N, "g7.min_workshop_n"),
        bias_bound_pp=_pinned(_num(g["bias_bound_pp"], "g7.bias_bound_pp"), BIAS_BOUND_PP, "g7.bias_bound_pp"),
        auc_margin=_pinned(_num(g["auc_margin"], "g7.auc_margin"), AUC_MARGIN, "g7.auc_margin"))
    try:
        params.validate()
    except MonitoringInputError as e:
        raise PolicyError(f"g7: {e}") from e
    b = _obj(d["bootstrap"], ("n_boot", "seed"), "bootstrap")
    n_boot, seed = _int(b["n_boot"], "bootstrap.n_boot", 0), _int(b["seed"], "bootstrap.seed", 0)
    bd = _obj(d["bands"], ("edges", "rounding_pp", "bands_only_min_n"), "bands")
    edges = bd["edges"]
    if not (isinstance(edges, list) and len(edges) == 2):
        raise PolicyError("bands.edges: must be [low_medium_edge, medium_high_edge]")
    lo, hi = _int(edges[0], "bands.edges[0]", 1), _int(edges[1], "bands.edges[1]", 1)
    if not lo < hi:
        raise PolicyError("bands.edges: must be strictly increasing")
    rounding = _int(bd["rounding_pp"], "bands.rounding_pp", 1)
    bands_only = _pinned_int(bd["bands_only_min_n"], BANDS_ONLY_MIN_N, "bands.bands_only_min_n")
    sc = _obj(d["scope"], ("booking_types", "settled_check_day", "settled_statuses"), "scope")
    for k in ("booking_types", "settled_statuses"):
        v = sc[k]
        if not (isinstance(v, list) and v and all(isinstance(x, str) and x for x in v)):
            raise PolicyError(f"scope.{k}: must be a non-empty list of strings")
    oc = _obj(d["outcome"], ("endpoint", "window_days", "multi_event_rule", "prs_pairing"), "outcome")
    pr = _obj(oc["prs_pairing"], ("min_gap_seconds", "max_gap_seconds", "defect_rule", "hard_defect_types",
                                  "vehicle_rule", "station_rule"), "outcome.prs_pairing")
    hdt = pr["hard_defect_types"]
    if not (isinstance(hdt, list) and all(isinstance(x, str) for x in hdt)):
        raise PolicyError("outcome.prs_pairing.hard_defect_types: must be a list of strings")
    outcome = OutcomePolicy(
        endpoint=_str(oc["endpoint"], "outcome.endpoint"),
        window_days=_int(oc["window_days"], "outcome.window_days", 0),
        multi_event_rule=_str(oc["multi_event_rule"], "outcome.multi_event_rule"),
        prs_rule=PrsPairingRule(
            min_gap_seconds=_num(pr["min_gap_seconds"], "outcome.prs_pairing.min_gap_seconds"),
            max_gap_seconds=_num(pr["max_gap_seconds"], "outcome.prs_pairing.max_gap_seconds"),
            defect_rule=_str(pr["defect_rule"], "outcome.prs_pairing.defect_rule"),
            hard_defect_types=tuple(hdt),
            vehicle_rule=_str(pr["vehicle_rule"], "outcome.prs_pairing.vehicle_rule"),
            station_rule=_str(pr["station_rule"], "outcome.prs_pairing.station_rule")))
    try:
        outcome.validate()
    except MonitoringInputError as e:
        raise PolicyError(f"outcome: {e}") from e
    return {"score_column": _score_column(d["score_column"]), "g7_params": params, "n_boot": n_boot, "seed": seed, "band_edges": (lo, hi), "rounding_pp": rounding,
            "bands_only_min_n": bands_only, "booking_types": list(sc["booking_types"]),
            "settled_check_day": _int(sc["settled_check_day"], "scope.settled_check_day", 1),
            "settled_statuses": list(sc["settled_statuses"]), "outcome_policy": outcome}


# ------------------------------------------------------------------------------------------------- G6
G6_KEYS = ("kind", "g6", "bootstrap", "reference_sha256", "outcome_access_start_utc", "population")


def g6_policy(parsed: Mapping[str, Any]) -> Dict[str, Any]:
    d = _obj(parsed, G6_KEYS, "policy")
    g = _obj(d["g6"], ("min_supported", "psi_threshold", "max_unsupported_share", "smoothing"), "g6")
    _pinned(_num(g["min_supported"], "g6.min_supported"), MIN_SUPPORTED_BOOKINGS, "g6.min_supported")
    _pinned(_num(g["psi_threshold"], "g6.psi_threshold"), PSI_THRESHOLD, "g6.psi_threshold")
    _pinned(_num(g["max_unsupported_share"], "g6.max_unsupported_share"), MAX_UNSUPPORTED_SHARE,
            "g6.max_unsupported_share")
    _pinned(_num(g["smoothing"], "g6.smoothing"), SMOOTHING, "g6.smoothing")
    b = _obj(d["bootstrap"], ("n_boot", "seed", "alpha"), "bootstrap")
    alpha = _num(b["alpha"], "bootstrap.alpha")
    if not 0.0 < alpha < 1.0:
        raise PolicyError("bootstrap.alpha: must be in (0, 1)")
    pop = d["population"]
    if pop not in POPULATIONS:
        raise PolicyError(f"population: must be one of {POPULATIONS}")
    sha = _str(d["reference_sha256"], "reference_sha256")
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise PolicyError("reference_sha256: must be 64 lowercase hex characters")
    return {"n_boot": _int(b["n_boot"], "bootstrap.n_boot", 0), "seed": _int(b["seed"], "bootstrap.seed", 0),
            "alpha": alpha, "reference_sha256": sha,
            "outcome_access_start_utc": _str(d["outcome_access_start_utc"], "outcome_access_start_utc"),
            "population": pop}


# ------------------------------------------------------------------------------------------------- G6 replay
G6_REPLAY_KEYS = ("kind", "score_column", "g6", "bootstrap", "reference_sha256", "outcome_access_start_utc", "population",
                  "window", "scope", "replay")


def _hex64(v: Any, where: str) -> str:
    s = _str(v, where)
    if len(s) != 64 or any(c not in "0123456789abcdef" for c in s):
        raise PolicyError(f"{where}: must be 64 lowercase hex characters")
    return s


def g6_replay_policy(parsed: Mapping[str, Any]) -> Dict[str, Any]:
    """Policy of `monitor-g6-replay`. Every key is required; the four adopted G6 values must equal the adopted ones."""
    from .reference import parse_utc
    d = _obj(parsed, G6_REPLAY_KEYS, "policy")
    core = g6_policy({"kind": "monitor_g6", "g6": d["g6"], "bootstrap": d["bootstrap"],
                      "reference_sha256": d["reference_sha256"], "outcome_access_start_utc": d["outcome_access_start_utc"],
                      "population": d["population"]})
    w = _obj(d["window"], ("start_utc", "end_utc"), "window")
    try:
        start, end = parse_utc(w["start_utc"], "window.start_utc"), parse_utc(w["end_utc"], "window.end_utc")
    except MonitoringInputError as e:
        raise PolicyError(str(e)) from e
    if not start < end:
        raise PolicyError("window: start_utc must precede end_utc")
    sc = _obj(d["scope"], ("booking_types",), "scope")
    bt = sc["booking_types"]
    if not (isinstance(bt, list) and bt and all(isinstance(x, str) and x for x in bt)):
        raise PolicyError("scope.booking_types: must be a non-empty list of strings")
    r = _obj(d["replay"], ("timeout_s", "max_output_bytes", "max_bookings", "unsupported_error_codes",
                           "bundle_manifest_sha256", "feature_schema_sha256"), "replay")
    timeout = _num(r["timeout_s"], "replay.timeout_s")
    if not 0 < timeout <= 3600:
        raise PolicyError("replay.timeout_s: must be in (0, 3600]")
    codes = r["unsupported_error_codes"]
    if not (isinstance(codes, list) and all(isinstance(x, str) and x for x in codes)):
        raise PolicyError("replay.unsupported_error_codes: must be a list of strings (may be empty)")
    return {**core, "score_column": _score_column(d["score_column"]), "window_start": start, "window_end": end,
            "booking_types": list(bt),
            "timeout_s": timeout, "max_output_bytes": _int(r["max_output_bytes"], "replay.max_output_bytes", 1024),
            "max_bookings": _int(r["max_bookings"], "replay.max_bookings", 1),
            "unsupported_error_codes": list(codes),
            "bundle_manifest_sha256": _hex64(r["bundle_manifest_sha256"], "replay.bundle_manifest_sha256"),
            "feature_schema_sha256": _hex64(r["feature_schema_sha256"], "replay.feature_schema_sha256")}
