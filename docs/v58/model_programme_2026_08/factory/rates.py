"""Rate estimators + the pinned prior-fitting surface.

THE RULE: no orphan ratios. Every emitted rate ships an observable numerator,
an observable denominator and a status column, and the ESTIMATOR MATCHES THE
DENOMINATOR TYPE. Calling one generic `alpha` smoother for both a bounded
proportion and a per-year rate is dimensionally wrong and is refused here by
having two separate functions with two separate parameter objects.

  bounded proportion   k of n events      ->  Beta-binomial   (k+a)/(n+a+b)
  exposure-time rate   k events in t yrs  ->  Gamma-Poisson   (k+a)/(t+b)

PRIOR-FITTING SURFACE (pinned, not "documented")
------------------------------------------------
All (a, b) are estimated from ONE DETERMINISTIC AS-OF ROW PER VEHICLE:

    the earliest target row inside the training window, excluding the
    valid fraction, ties broken by lowest tgt_id

`tgt_id` is a deterministic tiebreak only and carries no ordering claim
(dictionary convention 3: record counts are order-free set functions). The
selection is not outcome-dependent. Counting every target row would weight one
long-history vehicle many times over and estimate a prior for a population that
does not exist.

The rule, the resulting row count and every (a, b) are written to
BUILD_MANIFEST.json and asserted equal in every downstream build (falsifier
F15). Anyone rerunning the pre-registration reconstructs the identical
prior-fitting population without choosing among reasonable alternatives after
the fact.
"""
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Identifier of the prior-fitting selection rule, recorded in the manifest.
#: Change this string and F15 will refuse every downstream frame until the
#: training manifest is rebuilt -- which is the point.
PRIOR_SURFACE_RULE = "earliest_target_row_per_vehicle_train_only_tiebreak_lowest_tgt_id"


@dataclass(frozen=True)
class BetaPrior:
    """Prior for a BOUNDED PROPORTION: k successes out of n trials."""

    a: float
    b: float

    def __post_init__(self):
        if not (self.a > 0 and self.b > 0):
            raise ValueError(
                f"BetaPrior(a={self.a}, b={self.b}): both must be > 0. A zero "
                f"or negative pseudo-count is not a prior, it is a division "
                f"hazard dressed as one.")

    @property
    def mean(self) -> float:
        return self.a / (self.a + self.b)

    @property
    def strength(self) -> float:
        """Effective prior sample size, in events."""
        return self.a + self.b


@dataclass(frozen=True)
class GammaPrior:
    """Prior for an EXPOSURE-TIME RATE: k events observed over t years."""

    a: float          # prior events
    b: float          # prior exposure, in YEARS

    def __post_init__(self):
        if not (self.a > 0 and self.b > 0):
            raise ValueError(
                f"GammaPrior(a={self.a}, b={self.b}): both must be > 0.")

    @property
    def mean(self) -> float:
        """Prior mean rate, events per year."""
        return self.a / self.b


def smoothed_proportion(k: Optional[int], n: Optional[int],
                        prior: BetaPrior) -> Optional[float]:
    """(k + a) / (n + a + b). NULL when the denominator is unobservable.

    n == 0 with n OBSERVED (a vehicle with no outcome-observable prior days)
    still returns NULL, not the prior mean: emitting the prior mean would make
    a no-history vehicle indistinguishable from one measured at exactly the
    population rate. The companion count and status column carry that case.
    """
    if k is None or n is None or n <= 0:
        return None
    return (k + prior.a) / (n + prior.a + prior.b)


def smoothed_rate_per_year(k: Optional[int], years: Optional[float],
                           prior: GammaPrior) -> Optional[float]:
    """(k + a) / (t + b), events per year. NULL when exposure is unobservable."""
    if k is None or years is None or years <= 0:
        return None
    return (k + prior.a) / (years + prior.b)


def raw_proportion(k: Optional[int], n: Optional[int]) -> Optional[float]:
    """Unsmoothed k/n -- permitted ONLY for fixed bounded windows.

    The last-three family may use this because its denominator is explicit,
    bounded and emitted alongside (`*_n_outcome_observed` / `*_n_detail_observed`),
    so a 1-of-1 is legible as such rather than masquerading as a stable rate.
    """
    if k is None or n is None or n <= 0:
        return None
    return k / n


def fit_beta_prior(k_total: int, n_total: int, strength: float) -> BetaPrior:
    """Method-of-moments prior at a declared strength, from the pinned surface.

    `strength` (a + b) is the pre-registered effective prior sample size; the
    MEAN comes from the data, the STRENGTH does not. Splitting it this way is
    what keeps the tuning surface one declared number per semantic family
    instead of a free two-parameter search after seeing the result.
    """
    if n_total <= 0:
        raise ValueError("prior-fitting surface is empty: n_total must be > 0")
    if strength <= 0:
        raise ValueError(f"strength must be > 0, got {strength}")
    mean = k_total / n_total
    # Guard the degenerate ends: an all-pass or all-fail surface would produce
    # a zero pseudo-count and trip BetaPrior's own validation.
    mean = min(max(mean, 1e-6), 1 - 1e-6)
    return BetaPrior(a=mean * strength, b=(1.0 - mean) * strength)


def fit_gamma_prior(k_total: int, years_total: float, strength_years: float) -> GammaPrior:
    """Prior rate from the pinned surface, at a declared prior exposure."""
    if years_total <= 0:
        raise ValueError("prior-fitting surface has no exposure: years_total must be > 0")
    if strength_years <= 0:
        raise ValueError(f"strength_years must be > 0, got {strength_years}")
    rate = max(k_total / years_total, 1e-9)
    return GammaPrior(a=rate * strength_years, b=strength_years)


@dataclass(frozen=True)
class PriorSet:
    """Every fitted prior for one build, plus the provenance that pins it."""

    surface_rule: str
    surface_rows: int
    beta: Dict[str, BetaPrior]
    gamma: Dict[str, GammaPrior]

    def to_manifest(self) -> Dict[str, Any]:
        return {
            "surface_rule": self.surface_rule,
            "surface_rows": self.surface_rows,
            "beta": {k: asdict(v) for k, v in sorted(self.beta.items())},
            "gamma": {k: asdict(v) for k, v in sorted(self.gamma.items())},
        }

    @staticmethod
    def from_manifest(payload: Dict[str, Any]) -> "PriorSet":
        return PriorSet(
            surface_rule=payload["surface_rule"],
            surface_rows=int(payload["surface_rows"]),
            beta={k: BetaPrior(**v) for k, v in payload.get("beta", {}).items()},
            gamma={k: GammaPrior(**v) for k, v in payload.get("gamma", {}).items()},
        )


def assert_priors_match(training: PriorSet, downstream: PriorSet) -> None:
    """F15: a downstream build MUST reuse the training priors exactly.

    Refitting on the evaluation frame would let the eval population's own base
    rate leak into its features -- a small effect, invisible in every schema
    check, and fatal to a paired contrast.
    """
    if training.surface_rule != downstream.surface_rule:
        raise AssertionError(
            f"prior-fitting rule drift: training used {training.surface_rule!r}, "
            f"downstream used {downstream.surface_rule!r}")
    if training.surface_rows != downstream.surface_rows:
        raise AssertionError(
            f"prior-fitting surface size drift: training fitted on "
            f"{training.surface_rows} rows, downstream on {downstream.surface_rows}")
    for name, prior in sorted(training.beta.items()):
        other = downstream.beta.get(name)
        if other != prior:
            raise AssertionError(
                f"beta prior {name!r} drifted: training {prior}, downstream {other}")
    for name, prior in sorted(training.gamma.items()):
        other = downstream.gamma.get(name)
        if other != prior:
            raise AssertionError(
                f"gamma prior {name!r} drifted: training {prior}, downstream {other}")
    extra = (set(downstream.beta) - set(training.beta)) | (set(downstream.gamma) - set(training.gamma))
    if extra:
        raise AssertionError(f"downstream build invented priors absent from training: {sorted(extra)}")


#: Every prior the emitters require, by semantic family. A build that does not
#: supply all of them fails loud rather than defaulting one silently.
REQUIRED_BETA = ("fail_day_share", "major_day_share", "dangerous_day_share",
                 "advisory_day_share", "escalation_share",
                 "initial_fail_share", "initial_adverse_share")
REQUIRED_GAMMA = ("fail_days_per_year", "major_days_per_year",
                  "initial_fail_days_per_year", "fail_items_per_initial_event")

#: PLACEHOLDER ONLY. Fixture tests need *a* prior so schema and invariant tests
#: can run without a lake pass; a real build MUST fit from the pinned surface
#: and `assert_fitted` refuses this object by name. Every value is a weak,
#: deliberately round number so a leaked placeholder is obvious in a manifest.
PROVISIONAL_PRIORS = PriorSet(
    surface_rule="PROVISIONAL_PLACEHOLDER_NOT_FITTED",
    surface_rows=0,
    beta={k: BetaPrior(a=1.0, b=4.0) for k in REQUIRED_BETA},
    gamma={k: GammaPrior(a=1.0, b=4.0) for k in REQUIRED_GAMMA},
)


def assert_fitted(priors: PriorSet) -> None:
    """Refuse a real build that is still carrying the fixture placeholder."""
    if priors.surface_rule == PROVISIONAL_PRIORS.surface_rule:
        raise AssertionError(
            "PROVISIONAL_PRIORS reached a real build. Fit from the pinned "
            "surface (rates.select_prior_surface) and record the result in "
            "BUILD_MANIFEST.json; the placeholder exists only for fixtures.")
    missing = ([k for k in REQUIRED_BETA if k not in priors.beta]
               + [k for k in REQUIRED_GAMMA if k not in priors.gamma])
    if missing:
        raise AssertionError(f"prior set is missing required families: {missing}")


def select_prior_surface(rows: Sequence[dict]) -> List[dict]:
    """The pinned rule, applied. One row per vehicle; deterministic.

    `rows` are training-window target rows ALREADY excluding the valid
    fraction (the caller owns that split; doing it here would hide it).
    """
    best: Dict[int, dict] = {}
    for row in rows:
        vid = row["vehicle_id"]
        incumbent = best.get(vid)
        key = (row["tgt_date"], row["tgt_id"])
        if incumbent is None or key < (incumbent["tgt_date"], incumbent["tgt_id"]):
            best[vid] = row
    return [best[v] for v in sorted(best)]
