"""Assessment provider interface. The model is not packaged yet: StubProvider says so.

Display decisions (R7) belong to the provider because they belong to the model package: the provider returns
the rounded percentage, the band and the `DisplayPolicy` that produced them. The unrounded score
(`rank_score`) also comes from the provider but never leaves the server: the API exposes only a
response-local ordinal `rank_key` computed across the filtered booking list (ranking.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from .dvsa import DvsaHistory

BANDS = ("low", "medium", "high")


@dataclass(frozen=True)
class DisplayPolicy:
    """How a probability is shown. `band_edges` are percentage points on the ROUNDED scale:
    low < edges[0] <= medium < edges[1] <= high. `provisional` marks values that are not from a frozen
    model package (the stub's)."""
    rounding_pp: int
    band_edges: tuple[int, int]
    band_labels: dict = field(default_factory=lambda: {"low": "Low", "medium": "Medium", "high": "High"})
    provisional: bool = True
    # Explicit package-owned display authority. Unqualified real providers fail closed.
    display_state: str = "unavailable"  # unavailable | shadow | bands | simulated | percentage
    policy_version: str = "garage-display-v1"
    model_version: str | None = None
    suppression_reason: str | None = "model_not_qualified"
    operational_qualified: bool = False
    ranking_enabled: bool = False

    def band_for(self, percent_rounded: int) -> str:
        return "low" if percent_rounded < self.band_edges[0] else "medium" if percent_rounded < self.band_edges[1] else "high"

    def problem(self) -> str | None:
        """A reason this policy is unusable, or None."""
        if self.display_state not in ("unavailable", "shadow", "bands", "simulated", "percentage"):
            return "unknown display_state"
        if self.display_state in ("bands", "percentage") and (self.provisional or not self.operational_qualified
                or not self.model_version or not self.policy_version or self.suppression_reason):
            return "operational display requires qualified non-provisional versioned package"
        if self.ranking_enabled and self.display_state not in ("simulated", "bands", "percentage"):
            return "ranking requires permitted operational display"
        e = self.band_edges
        if self.rounding_pp != 5:
            return "rounding_pp must be 5 (R7)"
        if not (isinstance(e, tuple) and len(e) == 2 and all(isinstance(x, int) for x in e)
                and 0 < e[0] < e[1] <= 100 and all(x % self.rounding_pp == 0 for x in e)):
            return "band_edges must be two increasing multiples of rounding_pp in 1..100"
        if set(self.band_labels) != set(BANDS) or not all(isinstance(v, str) and v for v in self.band_labels.values()):
            return "band_labels must name low, medium and high"
        return None

    def to_json(self) -> dict:
        return {"rounding_pp": self.rounding_pp, "band_edges": list(self.band_edges),
                "band_labels": dict(self.band_labels), "provisional": self.provisional,
                "display_state": self.display_state, "policy_version": self.policy_version,
                "model_version": self.model_version, "suppression_reason": self.suppression_reason,
                "operational_qualified": self.operational_qualified, "ranking_enabled": self.ranking_enabled}


# Provisional until a frozen model package supplies its own (R7): Henri/lead replace this with the package's.
STUB_DISPLAY_POLICY = DisplayPolicy(rounding_pp=5, band_edges=(20, 35), provisional=True)


@dataclass(frozen=True)
class AssessmentRequest:
    registration: str
    booking_type: str
    appointment_date: date
    cutoff_date: date
    cutoff_at: datetime
    # Already truncated to tests strictly before the cutoff (R10): providers never see later tests.
    history: DvsaHistory


@dataclass(frozen=True)
class ProviderResult:
    state: str                      # available | unavailable | unsupported | failed
    reason: str | None = None
    reason_text: str | None = None
    percent_rounded: int | None = None   # multiple of rounding_pp (R7), set only when available
    band: str | None = None              # low | medium | high; edges live in the model package
    provider_name: str = "unknown"
    provider_version: str = "0"
    display_policy: DisplayPolicy | None = None   # required when available
    rank_score: float | None = None      # UNROUNDED probability in [0, 1]; server-side only; required when available


class AssessmentProvider(Protocol):
    name: str
    version: str

    def assess(self, req: AssessmentRequest) -> ProviderResult: ...


class StubProvider:
    name = "stub"
    version = "0"

    def assess(self, req: AssessmentRequest) -> ProviderResult:
        return ProviderResult(state="unavailable", reason="model_not_packaged",
                              reason_text="The assessment model is not packaged in this build.",
                              provider_name=self.name, provider_version=self.version,
                              display_policy=STUB_DISPLAY_POLICY)
