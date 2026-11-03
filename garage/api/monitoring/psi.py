"""G6 evaluator: input and score PSI against a hash-pinned reference, with uncertainty and multiplicity.

Spec sources (ROOT = work/garage_mvp):
  DECISIONS.md:50                ">=500 bookings with PSI <=0.10 on every input and on the score"
  protocol/AMENDMENT_DRAFT.md:49 bins, +0.5 smoothing, unseen/rare categories, null simulations, no change of
                                 .10 to a null quantile, small-n/multiplicity may delay a pass (disclosed),
                                 unsupported share <=30% as an operational guard, "no local fixture can
                                 satisfy this gate"
  PLAN_rev3_20261026.md:70       bins / rare-unseen / uncertainty / multiplicity must be defined

DESIGN CHOICES
  Population. Supported bookings only (unsupported ones are counted, not binned). Bookings are pooled across
  workshops; duplicates of booking_id are refused rather than silently dropped.
  Pass rule. n_supported >= 500 AND point PSI <= 0.10 for EVERY input and the score (the amendment states a
  point threshold; it is not tightened or loosened here).
  Uncertainty. Vehicle-cluster percentile bootstrap of the CURRENT sample against the fixed reference
  (reference counts are treated as fixed; they come from a far larger development/2024 sample, and the
  expected finite-sample inflation incl. the reference's 1/m term is reported separately). Bootstrap was
  chosen over a delta-method interval because (a) several bookings per vehicle break the multinomial
  assumption, (b) PSI is a non-linear, non-negative function with sparse cells where the delta method is poor,
  and (c) the same replicates give the interval of max-PSI across inputs. Caveat: PSI is biased upward in small
  samples and a percentile interval inherits that bias; intervals are descriptive and never change `status`.
  Multiplicity. The gate is an intersection rule over k = (#inputs + 1) PSIs, so a no-drift false breach
  becomes more likely as k grows. AMENDMENT:49 forbids moving the .10 threshold, so none is moved. Disclosure
  instead: Bonferroni-simultaneous interval per input (level 1 - alpha/k), bootstrap interval of max PSI, and
  the offline `simulate_null` false-alarm rate at 500 vehicles.
  Fixture guard. `population` must be attested by the caller; anything other than 'prospective_pilot', or a
  'fixture' reference, can never return status 'pass' (it returns 'fixture_only').
"""
from __future__ import annotations

import math
import random
from collections import Counter
from operator import itemgetter
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ._stats import (DEFAULT_N_BOOT, DEFAULT_SEED, ClusterSampler, MonitoringInputError, percentile_interval,
                     quantile)
from .reference import SCORE_KEY, Binner, ReferenceArtefact, parse_utc

MIN_SUPPORTED_BOOKINGS = 500        # DECISIONS.md:50; AMENDMENT_DRAFT.md:49
PSI_THRESHOLD = 0.10                # DECISIONS.md:50; AMENDMENT_DRAFT.md:49 (do not change without approval)
MAX_UNSUPPORTED_SHARE = 0.30        # AMENDMENT_DRAFT.md:49 (operational guard)
SMOOTHING = 0.5                     # AMENDMENT_DRAFT.md:49
_EPS = 1e-12
POPULATIONS = ("prospective_pilot", "fixture")
NOT_STARTED = "NOT_STARTED"


def psi_from_counts(cur: Sequence[float], ref: Sequence[float], smoothing: float = SMOOTHING) -> float:
    """PSI = sum (p_cur - p_ref) ln(p_cur / p_ref) after adding `smoothing` to every cell of both samples."""
    if len(cur) != len(ref):
        raise MonitoringInputError("count vectors differ in length")
    k = len(cur)
    nc, nr = sum(cur) + smoothing * k, sum(ref) + smoothing * k
    s = 0.0
    for c, r in zip(cur, ref):
        pc, pr = (c + smoothing) / nc, (r + smoothing) / nr
        s += (pc - pr) * math.log(pc / pr)
    return s


def expected_null_psi(n_cur: int, n_ref: int, n_cells: int) -> float:
    """First-order expected PSI when both samples come from one distribution: (k-1)(1/n + 1/m). ESTIMATED."""
    return (n_cells - 1) * (1.0 / n_cur + 1.0 / n_ref)


def _validate_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    seen = set()
    for r in rows:
        bid = r.get("booking_id")
        if bid is None:
            raise MonitoringInputError("every row needs a booking_id")
        if bid in seen:
            raise MonitoringInputError(f"duplicate booking_id {bid!r}: de-duplicate before pooling")
        seen.add(bid)
        if not isinstance(r.get("supported"), bool):
            raise MonitoringInputError("every row needs a boolean 'supported'")
        if not isinstance(r.get("features"), Mapping):
            raise MonitoringInputError("every row needs a 'features' mapping")


def _bin_rows(rows: Sequence[Mapping[str, Any]], binners: Mapping[str, Binner]):
    """cells[name][i], plus per-input unparseable/other/unseen counts."""
    cells: Dict[str, List[int]] = {n: [] for n in binners}
    flags: Dict[str, Counter] = {n: Counter() for n in binners}
    for name, b in binners.items():
        if name != SCORE_KEY and rows and not any(name in r["features"] for r in rows):
            raise MonitoringInputError(f"input {name!r} is absent from every row (schema/name drift?)")
    for r in rows:
        for name, b in binners.items():
            v = r.get("score") if name == SCORE_KEY else r["features"].get(name)
            c, f = b.classify(v)
            cells[name].append(c)
            if f:
                flags[name][f] += 1
    return cells, flags


def _counts(cell_list: Sequence[int], idx: Optional[Sequence[int]], k: int) -> List[int]:
    if idx is None:
        c = Counter(cell_list)
    elif not idx:
        c = Counter()
    elif len(idx) == 1:
        c = Counter([cell_list[idx[0]]])
    else:
        c = Counter(itemgetter(*idx)(cell_list))
    return [c.get(i, 0) for i in range(k)]


def evaluate_g6(rows: Sequence[Mapping[str, Any]], reference: ReferenceArtefact, *,
                outcome_access_start_utc: str, population: str,
                n_boot: int = DEFAULT_N_BOOT, seed: int = DEFAULT_SEED, alpha: float = 0.05) -> Dict[str, Any]:
    """Evaluate G6 on `rows` = [{booking_id, supported, features{input: v}, score, vehicle_id?, workshop_id?}].

    Required, no defaults: `reference` (hash-pinned artefact), `outcome_access_start_utc` (ISO timestamp, or
    'NOT_STARTED') and `population` ('prospective_pilot' | 'fixture'). Returns a JSON-serialisable dict.
    status: insufficient_n | fail | pass | fixture_only.  n_boot=0 skips intervals.
    """
    if not isinstance(reference, ReferenceArtefact):
        raise MonitoringInputError("reference must be a ReferenceArtefact (use ReferenceArtefact.from_bytes with a pinned hash)")
    if population not in POPULATIONS:
        raise MonitoringInputError(f"population must be one of {POPULATIONS}")
    if outcome_access_start_utc == NOT_STARTED:
        freeze_ok, oa = True, NOT_STARTED
    else:
        oa_dt = parse_utc(outcome_access_start_utc, "outcome_access_start_utc")
        freeze_ok, oa = reference.bins_frozen_utc < oa_dt, oa_dt.isoformat()
    if not freeze_ok:
        raise MonitoringInputError("reference bins_frozen_utc is not before outcome access: the reference/bins "
                                   "must be fixed before outcome access (DECISIONS.md:50, rev3:70)")
    rows = list(rows)
    _validate_rows(rows)
    sup = [r for r in rows if r["supported"]]
    n_sup, n_all = len(sup), len(rows)
    unsup_share = (n_all - n_sup) / n_all if n_all else 0.0
    by_ws = dict(Counter(r.get("workshop_id") for r in sup))

    binners = reference.binners()
    names = list(binners)
    k_tests = len(names)
    out: Dict[str, Any] = {
        "spec": {"min_supported": MIN_SUPPORTED_BOOKINGS, "psi_threshold": PSI_THRESHOLD,
                 "max_unsupported_share": MAX_UNSUPPORTED_SHARE, "smoothing": SMOOTHING,
                 "sources": ["DECISIONS.md:50", "protocol/AMENDMENT_DRAFT.md:49"]},
        "reference": {"artefact_id": reference.artefact_id, "sha256": reference.sha256,
                      "reference_source": reference.reference_source, "reference_n": reference.reference_n,
                      "bins_frozen_utc": reference.bins_frozen_utc.isoformat()},
        "outcome_access_start_utc": oa, "freeze_precedes_outcome_access": freeze_ok,
        "population": population,
        "n_total": n_all, "n_supported": n_sup, "n_unsupported": n_all - n_sup,
        "unsupported_share": unsup_share, "unsupported_guard_ok": unsup_share <= MAX_UNSUPPORTED_SHARE,
        "n_supported_by_workshop": {str(k): v for k, v in by_ws.items()},
        "n_tests_multiplicity": k_tests, "psi": {},
    }
    notes = [
        "Multiplicity: pass requires all %d point PSIs <= %.2f; the threshold is NOT adjusted (AMENDMENT:49). "
        "Simultaneous intervals use Bonferroni (level 1-alpha/%d); see simulate_null for the no-drift false-alarm rate."
        % (k_tests, PSI_THRESHOLD, k_tests),
        "Intervals are descriptive (vehicle-cluster percentile bootstrap) and do not change status.",
        "Unsupported-share guard is reported separately and is not folded into status.",
    ]
    if n_sup == 0:
        out.update(status="insufficient_n", criteria_met=False, max_psi=None, max_psi_input=None, notes=notes)
        return out

    cells, flags = _bin_rows(sup, binners)
    psis: Dict[str, float] = {}
    for name in names:
        b = binners[name]
        cur = _counts(cells[name], None, b.n_cells)
        ref = reference.ref_counts(name)
        psis[name] = psi_from_counts(cur, ref)
        entry = {"psi": psis[name], "breach": psis[name] > PSI_THRESHOLD + _EPS, "n_cells": b.n_cells,
                 "expected_null_psi": expected_null_psi(n_sup, reference.reference_n, b.n_cells),
                 "missing_rate": cur[-1] / n_sup,
                 "n_unparseable": flags[name].get("unparseable", 0)}
        if b.kind == "categorical":
            other = flags[name].get("other", 0) + flags[name].get("unseen", 0)
            entry["other_rate"] = other / n_sup
            entry["unseen_rate"] = flags[name].get("unseen", 0) / n_sup if b.known is not None else None
        out["psi"][name] = entry

    mx = max(names, key=lambda n: psis[n])
    out["max_psi"], out["max_psi_input"] = psis[mx], mx
    out["criteria_met"] = n_sup >= MIN_SUPPORTED_BOOKINGS and all(not out["psi"][n]["breach"] for n in names)

    if n_boot > 0:
        sampler = ClusterSampler([r.get("vehicle_id") for r in sup])
        rng = random.Random(seed)
        dist: Dict[str, List[float]] = {n: [] for n in names}
        maxes: List[float] = []
        refs = {n: reference.ref_counts(n) for n in names}
        for _ in range(n_boot):
            idx = sampler.sample(rng)
            m = 0.0
            for name in names:
                v = psi_from_counts(_counts(cells[name], idx, binners[name].n_cells), refs[name])
                dist[name].append(v)
                m = max(m, v)
            maxes.append(m)
        level_simul = 1.0 - alpha / k_tests
        for name in names:
            lo, hi = percentile_interval(dist[name], 1.0 - alpha)
            slo, shi = percentile_interval(dist[name], level_simul)
            out["psi"][name].update(ci_lo=lo, ci_hi=hi, ci_simultaneous_lo=slo, ci_simultaneous_hi=shi)
        out["max_psi_ci"] = list(percentile_interval(maxes, 1.0 - alpha))
        out["bootstrap"] = {"n_boot": n_boot, "seed": seed, "alpha": alpha, "cluster": "vehicle_id (row if absent)",
                            "method": "percentile", "n_clusters": sampler.n_clusters,
                            "simultaneous": "Bonferroni, level %.6f" % level_simul}
        out["any_simultaneous_upper_above_threshold"] = any(
            out["psi"][n]["ci_simultaneous_hi"] > PSI_THRESHOLD for n in names)

    if n_sup < MIN_SUPPORTED_BOOKINGS:
        status = "insufficient_n"
    elif not out["criteria_met"]:
        status = "fail"
    elif population != "prospective_pilot" or reference.reference_source == "fixture":
        status = "fixture_only"
        notes.append("Numerically met on a fixture population/reference: cannot satisfy the gate "
                     "(AMENDMENT:49 'No local fixture can satisfy this gate').")
    else:
        status = "pass"
    out["status"], out["notes"] = status, notes
    return out


def simulate_null(dev_rows: Sequence[Mapping[str, Any]], reference: ReferenceArtefact, *,
                  n_vehicles: int = 500, reps: int = 2000, seed: int = DEFAULT_SEED) -> Dict[str, Any]:
    """Offline null simulation (AMENDMENT:49): draw `n_vehicles` distinct vehicles (one row each, without
    replacement) from development rows, compute PSI against `reference`, repeat `reps` times. Reports per-input
    PSI quantiles, the max-over-inputs distribution, the share of replicates with any PSI above 0.10 (the
    no-drift false-alarm rate; it does NOT replace the threshold), and OTHER rates. dev_rows need
    vehicle_id (unique), features, score. Intended for the training side; pure Python, ~O(reps*n*inputs)."""
    rows = list(dev_rows)
    ids = [r.get("vehicle_id") for r in rows]
    if any(i is None for i in ids) or len(set(ids)) != len(ids):
        raise MonitoringInputError("dev_rows need a unique vehicle_id on every row (one row per vehicle)")
    if len(rows) < n_vehicles:
        raise MonitoringInputError("fewer development vehicles than n_vehicles")
    binners = reference.binners()
    names = list(binners)
    cells, _ = _bin_rows([{"features": r["features"], "score": r.get("score")} for r in rows], binners)
    refs = {n: reference.ref_counts(n) for n in names}
    rng = random.Random(seed)
    dist: Dict[str, List[float]] = {n: [] for n in names}
    maxes: List[float] = []
    other: Dict[str, List[float]] = {n: [] for n in names if binners[n].kind == "categorical"}
    all_idx = list(range(len(rows)))
    for _ in range(reps):
        idx = rng.sample(all_idx, n_vehicles)
        m = 0.0
        for name in names:
            cnt = _counts(cells[name], idx, binners[name].n_cells)
            v = psi_from_counts(cnt, refs[name])
            dist[name].append(v)
            m = max(m, v)
            if name in other:
                other[name].append(cnt[-2] / n_vehicles)
        maxes.append(m)

    def summ(v: List[float]) -> Dict[str, float]:
        s = sorted(v)
        return {"mean": sum(s) / len(s), "q025": quantile(s, .025), "q50": quantile(s, .5),
                "q95": quantile(s, .95), "q975": quantile(s, .975), "q99": quantile(s, .99), "max": s[-1]}

    return {"n_vehicles": n_vehicles, "reps": reps, "seed": seed, "n_tests": len(names),
            "per_input": {n: summ(dist[n]) for n in names},
            "max_over_inputs": summ(maxes),
            "false_alarm_rate_any_psi_above_threshold": sum(1 for m in maxes if m > PSI_THRESHOLD + _EPS) / reps,
            "other_rate": {n: summ(v) for n, v in other.items()},
            "note": "Descriptive only: the .10 threshold is not replaced by a null quantile (AMENDMENT:49)."}
