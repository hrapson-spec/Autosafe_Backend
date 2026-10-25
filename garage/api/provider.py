"""Assessment provider interface. The model is not packaged yet: StubProvider says so."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from .dvsa import DvsaHistory


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
    percent_rounded: int | None = None   # multiple of 5 (R7), set only when available
    band: str | None = None              # low | medium | high; edges live in the model package
    provider_name: str = "unknown"
    provider_version: str = "0"


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
                              provider_name=self.name, provider_version=self.version)
