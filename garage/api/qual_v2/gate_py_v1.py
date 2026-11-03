"""Evaluator 'gate_py_v1': re-derive every pass/fail boolean of a T2c/gate.py report from the report's own numbers, using gate.py's FROZEN constants.

The constants are copied (this package must run in the app image without T2c) and pinned to gate.py's sha256 from T2c/FROZEN_MANIFEST.json; tests parse
the real gate.py with ast and fail if any constant or the hash differs. Nothing here is a new threshold.

Known difference from the amendment (reported, not fixed): gate.py uses age bands <3/3-6/6-10/10-15/15+, the amendment (section 6) proposes <=5/>5-10/>10-15/>15;
gate.py does not compute Brier-vs-logistic-baseline, the top-20% usefulness gate or band monotonicity. A receipt can only cite gate.py's rules, via rules.assessment_2024.code_sha256.
"""
from __future__ import annotations
from .common import QualError, is_bool

FROZEN_CODE_SHA256 = "2a44c66a1d0d30ee6a6d1eac25d77d43d1d915a60fe04c80671611b4c1b1b895"   # T2c/FROZEN_MANIFEST.json:7
BIAS_MAX_PP = 1.0
ECE_MAX_PP = 1.5
SLOPE_RANGE = (0.90, 1.10)
SUBGROUP_BIAS_MAX_PP = 3.0
MIN_SUBGROUP_N = 1000
BAND_HALF_WIDTH_PP = 2.5
MIN_BAND_N = 200
GATE_KEYS = ("bias", "ece", "slope", "subgroup_bias", "display_bands", "delta_auc")
CAL_KEYS = ("bias", "ece", "slope", "subgroup_bias", "display_bands")


def _num(x, what):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float("inf"), float("-inf")):
        raise QualError("gate_report_invalid", what)
    return float(x)


def evaluate(report):
    """Return dict(gates, extra_gates, CALIBRATION_PASS, MODEL_PASS, vacuous) recomputed from numbers; raise QualError if the report's stored values disagree."""
    if not isinstance(report, dict):
        raise QualError("gate_report_invalid", "not an object")
    try:
        bias = _num(report["bias_pp"], "bias_pp"); ece = _num(report["ece_pp"], "ece_pp"); slope = _num(report["calibration_slope"], "slope")
        subs = report["subgroups"]; bands = report["display_bands"]; ci = report["delta_auc"]["ci95"]
        bm = _num(report["brier_model"], "brier_model"); bb = _num(report["brier_base_rate"], "brier_base_rate")
    except (KeyError, TypeError):
        raise QualError("gate_report_invalid", "missing key")
    if not isinstance(subs, dict) or not isinstance(bands, dict) or not isinstance(ci, list) or len(ci) != 2:
        raise QualError("gate_report_invalid", "shape")
    sub_ok, n_gated_sub = True, 0
    for name, s in subs.items():
        try:
            n = int(s["n"]); gated = s["gated"]; stored = s["bias_ok"]; b = _num(s["bias_pp"], name)
        except (KeyError, TypeError, ValueError):
            raise QualError("gate_report_invalid", "subgroup " + str(name)[:40])
        if not is_bool(gated) or gated != (n >= MIN_SUBGROUP_N):
            raise QualError("gate_report_inconsistent", "subgroup gated flag " + str(name)[:40])
        if gated:
            n_gated_sub += 1
            ok = abs(b) <= SUBGROUP_BIAS_MAX_PP
            if stored is not ok:
                raise QualError("gate_report_inconsistent", "subgroup bias_ok " + str(name)[:40])
            sub_ok = sub_ok and ok
        elif stored is not None:
            raise QualError("gate_report_inconsistent", "ungated subgroup has bias_ok " + str(name)[:40])
    band_ok, n_gated_band = True, 0
    for name, b in bands.items():
        try:
            n = int(b["n"]); gated = b["gated"]; dev = _num(b["abs_dev_pp"], name)
        except (KeyError, TypeError, ValueError):
            raise QualError("gate_report_invalid", "band " + str(name)[:40])
        if not is_bool(gated) or gated != (n >= MIN_BAND_N):
            raise QualError("gate_report_inconsistent", "band gated flag " + str(name)[:40])
        if gated:
            n_gated_band += 1
            band_ok = band_ok and dev <= BAND_HALF_WIDTH_PP
    gates = {"bias": abs(bias) <= BIAS_MAX_PP, "ece": ece <= ECE_MAX_PP, "slope": SLOPE_RANGE[0] <= slope <= SLOPE_RANGE[1],
             "subgroup_bias": sub_ok, "display_bands": band_ok, "delta_auc": _num(ci[0], "ci95[0]") > 0}
    extra = {"brier_below_base_rate": bm < bb}
    cal = all(gates[k] for k in CAL_KEYS)
    out = {"gates": gates, "extra_gates": extra, "CALIBRATION_PASS": cal, "MODEL_PASS": bool(cal and gates["delta_auc"] and extra["brier_below_base_rate"]),
           "vacuous": {"subgroup_bias": n_gated_sub == 0, "display_bands": n_gated_band == 0}}
    if report.get("gates") != gates or report.get("extra_gates") != extra or report.get("CALIBRATION_PASS") is not cal or report.get("MODEL_PASS") is not out["MODEL_PASS"]:
        raise QualError("gate_report_inconsistent", "stored pass flags differ from values recomputed from the report's own numbers")
    return out
