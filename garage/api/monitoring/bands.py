"""Band-monotonicity check: observed F-or-PRS rate should be non-decreasing low -> medium -> high.

Specs (ROOT = work/garage_mvp):
  AMENDMENT_DRAFT.md:57   G7: hide percentages on non-monotone band outcome rates; an empty band makes
                          monotonicity unassessable and percentages are withheld pending support.
  AMENDMENT_DRAFT.md:55   bands-only fallback allowed only when all three bands have n >= 200, rates are
                          strictly increasing and each adjacent difference's vehicle-bootstrap 95% lower
                          bound is > 0.

Two different questions, both answered here:
  monotone_nondecreasing  the G7 trigger. Point rates, ties allowed. Assessable only if every band has at
                          least `min_n_assessable` outcomes. The minimum is NOT given by the amendment, so it
                          is a required argument (open decision).
  bands_only_qualified    the section-6 test above (n >= 200 per band is specified).
Uncertainty: Wilson interval per band rate (single proportion, analytic is adequate), and a
vehicle-cluster percentile bootstrap interval for each adjacent difference (clustering matters there).
The interval for the "95% lower bound" is the lower end of the two-sided 95% interval (more conservative than
a one-sided 5% bound); this reading of the wording is an open item.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ._stats import (DEFAULT_N_BOOT, DEFAULT_SEED, ClusterSampler, MonitoringInputError, percentile_interval,
                     wilson_interval)

BANDS = ("low", "medium", "high")
BANDS_ONLY_MIN_N = 200       # AMENDMENT_DRAFT.md:55


def _check(records: Sequence[Mapping[str, Any]]) -> None:
    for r in records:
        if r.get("band") not in BANDS:
            raise MonitoringInputError(f"band must be one of {BANDS}, got {r.get('band')!r}")
        if r.get("y") not in (0, 1):
            raise MonitoringInputError("y must be 0 or 1 (F-or-PRS indicator)")


def evaluate_bands(records: Sequence[Mapping[str, Any]], *, min_n_assessable: int,
                   bands_only_min_n: int = BANDS_ONLY_MIN_N, n_boot: int = DEFAULT_N_BOOT,
                   seed: int = DEFAULT_SEED, alpha: float = 0.05) -> Dict[str, Any]:
    """records: [{band, y, vehicle_id?}]. min_n_assessable is REQUIRED (open decision, see module docstring)."""
    if not isinstance(min_n_assessable, int) or isinstance(min_n_assessable, bool) or min_n_assessable < 1:
        raise MonitoringInputError("min_n_assessable must be an integer >= 1")
    records = list(records)
    _check(records)
    n = {b: 0 for b in BANDS}
    k = {b: 0 for b in BANDS}
    for r in records:
        n[r["band"]] += 1
        k[r["band"]] += int(r["y"])
    out: Dict[str, Any] = {"min_n_assessable": min_n_assessable, "bands_only_min_n": bands_only_min_n,
                           "n_records": len(records), "bands": {}}
    for b in BANDS:
        lo, hi = wilson_interval(k[b], n[b])
        out["bands"][b] = {"n": n[b], "k": k[b], "rate": (k[b] / n[b]) if n[b] else None,
                           "wilson_lo": lo, "wilson_hi": hi}
    empty = [b for b in BANDS if n[b] == 0]
    thin = [b for b in BANDS if 0 < n[b] < min_n_assessable]
    out["empty_bands"], out["thin_bands"] = empty, thin
    out["assessable"] = not empty and not thin
    rate = {b: out["bands"][b]["rate"] for b in BANDS}
    out["monotone_nondecreasing"] = (rate["low"] <= rate["medium"] <= rate["high"]) if out["assessable"] else None

    pairs = list(zip(BANDS, BANDS[1:]))
    adj: List[Dict[str, Any]] = []
    for a, c in pairs:
        diff = (rate[c] - rate[a]) if (n[a] and n[c]) else None
        adj.append({"from": a, "to": c, "diff": diff, "ci_lo": None, "ci_hi": None, "n_boot_valid": 0})
    if n_boot > 0 and records:
        sampler = ClusterSampler([r.get("vehicle_id") for r in records])
        rng = random.Random(seed)
        bands_l = [r["band"] for r in records]
        ys = [int(r["y"]) for r in records]
        dists: List[List[float]] = [[] for _ in pairs]
        for _ in range(n_boot):
            idx = sampler.sample(rng)
            nn = {b: 0 for b in BANDS}
            kk = {b: 0 for b in BANDS}
            for i in idx:
                nn[bands_l[i]] += 1
                kk[bands_l[i]] += ys[i]
            for j, (a, c) in enumerate(pairs):
                if nn[a] and nn[c]:
                    dists[j].append(kk[c] / nn[c] - kk[a] / nn[a])
        for j, d in enumerate(dists):
            adj[j]["n_boot_valid"] = len(d)
            if d:
                adj[j]["ci_lo"], adj[j]["ci_hi"] = percentile_interval(d, 1.0 - alpha)
        out["bootstrap"] = {"n_boot": n_boot, "seed": seed, "alpha": alpha, "method": "percentile",
                            "cluster": "vehicle_id (row if absent)"}
    for a in adj:
        a["violation"] = (a["diff"] < 0) if a["diff"] is not None else None
        a["violation_ci_excludes_zero"] = (a["ci_hi"] < 0) if a["ci_hi"] is not None else None
    out["adjacent"] = adj

    reasons: List[str] = []
    if any(n[b] < bands_only_min_n for b in BANDS):
        reasons.append("a band has n < %d" % bands_only_min_n)
    strict = all(rate[a] is not None and rate[c] is not None and rate[a] < rate[c] for a, c in pairs)
    out["strictly_increasing"] = strict if all(n[b] for b in BANDS) else None
    if not strict:
        reasons.append("rates are not strictly increasing")
    if n_boot <= 0:
        reasons.append("no bootstrap interval computed")
    elif any(a["ci_lo"] is None or not a["ci_lo"] > 0 for a in adj):
        reasons.append("an adjacent difference's bootstrap lower bound is not > 0")
    out["bands_only_qualified"] = not reasons
    out["bands_only_unmet"] = reasons
    return out
