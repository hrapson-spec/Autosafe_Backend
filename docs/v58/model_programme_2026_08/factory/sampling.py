"""Deterministic salted-hash sampling: buckets, nested rungs, eval slices.

Rule of record (contract, Emission):

    h = hash(vehicle_id || 'mp2026s1')

The salt is CONCATENATED to the id before hashing. The bare
`hash(vehicle_id)` family is forbidden: the research-side 6.37x vehicle-overlap
enrichment (DATA_ASSESSMENT §7) is exactly that failure -- two "independent"
samples drawn with the same unsalted key select correlated vehicles.

Selection is by THRESHOLD on the unit interval u = h / 2^64, so rungs nest by
construction: t(250k) <= t(500k) <= t(1M) <= t(2M) implies the row sets nest.
Enrichment adds a second, wider threshold that only stratum-eligible rows may
use; the stratum is computed AS-OF the row's target date (falsifier 6), and
`inclusion_weight` is the Horvitz-Thompson weight t_base / p(row).

Buckets use a SEPARATE salt so the memory-management partition is independent
of sample membership -- otherwise bucket 0 would be rung 1.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

#: Salt literals, fixed in code (recorded in BUILD_MANIFEST).
SAMPLE_SALT = "mp2026s1"
EVAL_SALT = "mp2026eval"
CONFIRMATION_SALT = "mp2026conf"
BUCKET_SALT = "mp2026bucket"

UINT64 = 18446744073709551616.0  # 2**64

#: Enrichment strata, evaluated as-of the target date. Order is priority order:
#: the FIRST matching stratum wins, so a row has exactly one stratum.
ENRICHMENT_STRATA: Tuple[str, ...] = ("dangerous_prior", "recent_fail", "deep_history")
NO_STRATUM = "none"

#: Enriched rows may never exceed this share of a rung (contract: <= 25%).
MAX_ENRICHMENT_SHARE = 0.25


class RungError(ValueError):
    """A rung ladder that would not nest, or a share above the cap."""


def unit_hash_sql(id_col: str, salt: str) -> str:
    """SQL twin of the rule of record: u = hash(id || salt) / 2^64 in [0, 1)."""
    return f"(CAST(hash(CAST({id_col} AS VARCHAR) || '{salt}') AS DOUBLE) / {UINT64!r})"


def bucket_sql(id_col: str, n_buckets: int) -> str:
    """Memory-management bucket (separate salt, never the sample salt)."""
    if n_buckets < 1:
        raise ValueError("n_buckets must be >= 1")
    return (f"(CAST(hash(CAST({id_col} AS VARCHAR) || '{BUCKET_SALT}') % "
            f"{int(n_buckets)} AS INTEGER))")


def unit_hash(con, values: Sequence[int], salt: str = SAMPLE_SALT) -> List[float]:
    """Python-side evaluation of the SAME rule, via duckdb (never re-derived).

    There is deliberately no pure-Python re-implementation of duckdb's hash: a
    twin that drifted would silently change sample membership.
    """
    if not values:
        return []
    rows = ", ".join(f"({int(v)})" for v in values)
    sql = (f"SELECT {unit_hash_sql('v', salt)} FROM (VALUES {rows}) t(v)")
    return [float(r[0]) for r in con.execute(sql).fetchall()]


@dataclass(frozen=True)
class Rung:
    """One nested sample rung."""

    name: str
    #: base threshold on u; every row with u < base is in the rung
    base: float
    #: enrichment threshold (>= base); only stratum-eligible rows may use it
    enriched: float = 0.0
    #: nominal target row count, for the manifest (never enforced silently)
    target_rows: Optional[int] = None

    def __post_init__(self):
        if not (0.0 < self.base <= 1.0):
            raise RungError(f"rung {self.name}: base threshold {self.base} outside (0, 1]")
        if self.enriched and self.enriched < self.base:
            raise RungError(
                f"rung {self.name}: enriched threshold {self.enriched} < base "
                f"{self.base} would DESELECT stratum rows")

    def selects(self, u: float, stratum: str) -> bool:
        if u < self.base:
            return True
        return stratum != NO_STRATUM and u < self.enriched

    def inclusion_weight(self, u: float, stratum: str) -> float:
        """Horvitz-Thompson weight = base / P(selected), a function of the
        DESIGN CELL only -- never of the realised u.

        A stratum-eligible row is selected iff u < `enriched`, so its inclusion
        probability is `enriched` WHATEVER its u turned out to be. Branching on
        `u < base` (as this did until the 2026-08-12 adversarial review) hands
        weight 1.0 to eligible rows that happen to land low, inflating every
        weighted stratum total by 2 - base/enriched (up to 2x). `u` is retained
        in the signature so the call site cannot silently pass a design cell
        that disagrees with the row.
        """
        if stratum == NO_STRATUM or not self.enriched or self.enriched <= self.base:
            return 1.0
        return self.base / self.enriched


@dataclass
class RungLadder:
    """An ordered, nesting-checked ladder of rungs."""

    rungs: List[Rung] = field(default_factory=list)

    def __post_init__(self):
        for prev, nxt in zip(self.rungs, self.rungs[1:]):
            if nxt.base < prev.base:
                raise RungError(
                    f"rung {nxt.name} base {nxt.base} < {prev.name} base {prev.base}: "
                    f"the ladder would NOT nest (250k must be a subset of 500k)")
            if nxt.enriched < prev.enriched:
                raise RungError(
                    f"rung {nxt.name} enriched {nxt.enriched} < {prev.name} "
                    f"enriched {prev.enriched}: enriched rows would be dropped by "
                    f"a larger rung")

    @property
    def names(self) -> List[str]:
        return [r.name for r in self.rungs]

    @property
    def widest(self) -> float:
        return max([max(r.base, r.enriched) for r in self.rungs], default=0.0)

    def selecting(self, u: float, stratum: str) -> List[str]:
        return [r.name for r in self.rungs if r.selects(u, stratum)]


def ladder_from_fractions(fractions: Dict[str, Tuple[float, float]],
                          targets: Optional[Dict[str, int]] = None) -> RungLadder:
    """Build a ladder from {name: (base, enriched)} in ascending base order."""
    ordered = sorted(fractions.items(), key=lambda kv: (kv[1][0], kv[1][1]))
    targets = targets or {}
    return RungLadder([Rung(name=n, base=b, enriched=e, target_rows=targets.get(n))
                       for n, (b, e) in ordered])


#: Default ladder. Thresholds are PLACEHOLDERS in units of the unit interval;
#: `build.py --calibrate` measures the realised counts on the injected relation
#: and prints the fractions that hit 250k/500k/1M/2M, which the owner then pins.
DEFAULT_RUNG_TARGETS: Dict[str, int] = {"r250k": 250_000, "r500k": 500_000,
                                        "r1m": 1_000_000, "r2m": 2_000_000}


def enrichment_stratum(n_prior_dangerous_days: int, days_since_prior_fail_day: Optional[int],
                       n_prior_test_days: int,
                       deep_history_days: int = 6,
                       recent_fail_window_days: int = 730) -> str:
    """As-of enrichment stratum for one emitted row.

    EVERY input is an as_of_state quantity read BEFORE the target day's update,
    which is what makes falsifier 6 hold: a vehicle that only becomes deep-history
    in 2025 cannot be enrichment-eligible at a 2022 target.
    """
    if n_prior_dangerous_days > 0:
        return "dangerous_prior"
    if (days_since_prior_fail_day is not None
            and days_since_prior_fail_day <= recent_fail_window_days):
        return "recent_fail"
    if n_prior_test_days >= deep_history_days:
        return "deep_history"
    return NO_STRATUM


def check_enrichment_share(n_enriched: int, n_total: int,
                           cap: float = MAX_ENRICHMENT_SHARE) -> Tuple[bool, float]:
    """Contract cap: enriched rows <= 25% of the rung. Reported, not silent."""
    share = (n_enriched / n_total) if n_total else 0.0
    return (share <= cap + 1e-12), share
