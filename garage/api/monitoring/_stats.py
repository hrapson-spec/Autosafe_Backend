"""Small stdlib-only statistics helpers (the app image has no numpy/scipy).

Everything here is deterministic given a seed. Nothing reads the database or the network.
"""
from __future__ import annotations

import math
import random
from operator import itemgetter
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

DEFAULT_SEED = 20261025          # AMENDMENT_DRAFT.md:59 (paired-bootstrap seed); a reproducibility choice only
DEFAULT_N_BOOT = 2000            # AMENDMENT_DRAFT.md:45,49,59 (2000 replicates)
Z975 = 1.959963984540054


class MonitoringInputError(ValueError):
    """The caller supplied data or parameters the evaluators refuse to interpret."""


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (numpy 'linear', Hyndman-Fan type 7) of an ascending sequence."""
    if not sorted_vals:
        raise MonitoringInputError("quantile of an empty sequence")
    if not 0.0 <= q <= 1.0:
        raise MonitoringInputError("q must be in [0, 1]")
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def percentile_interval(values: Sequence[float], level: float = 0.95) -> Tuple[float, float]:
    """Two-sided percentile interval at `level` coverage."""
    s = sorted(values)
    a = (1.0 - level) / 2.0
    return quantile(s, a), quantile(s, 1.0 - a)


def wilson_interval(k: int, n: int, z: float = Z975) -> Tuple[Optional[float], Optional[float]]:
    """Wilson score interval for a binomial proportion (None, None when n == 0)."""
    if n <= 0:
        return None, None
    p = k / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def auc(scores: Sequence[float], labels: Sequence[int]) -> Optional[float]:
    """Mann-Whitney AUC with mid-rank ties; None when a class is empty."""
    n = len(scores)
    n_pos = sum(1 for y in labels if y == 1)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    order = sorted(range(n), key=scores.__getitem__)
    rank_sum = 0.0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        mid = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            if labels[order[t]] == 1:
                rank_sum += mid
        i = j + 1
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


class ClusterSampler:
    """Cluster (vehicle) bootstrap: resample whole clusters with replacement, return row indices.

    `cluster_ids[i]` is the cluster of row i (None -> the row is its own cluster).
    """

    def __init__(self, cluster_ids: Sequence[Optional[Hashable]]):
        groups: Dict[Hashable, List[int]] = {}
        for i, c in enumerate(cluster_ids):
            groups.setdefault(("row", i) if c is None else ("c", c), []).append(i)
        self.groups = list(groups.values())
        self.n_clusters = len(self.groups)

    def sample(self, rng: random.Random) -> List[int]:
        out: List[int] = []
        m = self.n_clusters
        for _ in range(m):
            out.extend(self.groups[rng.randrange(m)])
        return out


def pick(seq: Sequence, idx: Sequence[int]) -> list:
    """seq[idx] at C speed (itemgetter), safe for 0/1-length index lists."""
    if not idx:
        return []
    if len(idx) == 1:
        return [seq[idx[0]]]
    return list(itemgetter(*idx)(seq))
