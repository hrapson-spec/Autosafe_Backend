"""
AutoSafe report contract (v2).

This module is the versioned v2 report contract: the request/response
schema, enums, and error envelope used to create, retrieve, and share a
vehicle report. It is the single source of truth for field names, enum
values, and validation bounds on that contract.

Null-means-unknown
-------------------
``ReportEvidence.total_tests`` and ``ReportEvidence.total_failures`` use
null-means-unknown semantics. ``None`` means the underlying evidence count
was not available at report time; that is a distinct state from zero, and
the two must never be conflated. Rendering a missing count as ``0`` is a
contract violation.

No fabricated defaults
-----------------------
No field on this contract may be populated with a fabricated default to
mask missing evidence. Where a value is not known, the contract must say
so explicitly — via ``None``, or an honest enum member such as
``MatchScope.UNAVAILABLE``, ``MatchScope.POPULATION_DEFAULT``, or
``PredictionSource.UNAVAILABLE`` — rather than substituting a
plausible-looking value in its place. A fully degraded report (nothing
known about the vehicle) must still validate against this contract.

This is a pure data module: pydantic and the standard library only. No
I/O, no web-framework imports, no database imports.
"""
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

REPORT_CONTRACT_VERSION = "2.0"
REPORT_TTL_DAYS = 90
POPULATION_DEFAULT_FAILURE_RISK = 0.28   # UK population average, matches legacy default_response
SHARE_URL_PATH = "/app/report/{token}"


class MileageSource(str, Enum):
    """Provenance of the mileage value used to produce a report."""

    USER_ENTERED = "user_entered"
    OBSERVED_MOT = "observed_mot"
    ESTIMATED = "estimated"
    MISSING = "missing"


class MatchScope(str, Enum):
    """How closely the served evidence matches this specific vehicle.

    Ordered from most to least specific; ``UNAVAILABLE`` and
    ``POPULATION_DEFAULT`` both signal degraded evidence and must be
    rendered honestly, not hidden behind a narrower-looking scope.
    """

    EXACT_BAND = "exact_band"
    AGE_BAND_ONLY = "age_band_only"
    MODEL_AVERAGE = "model_average"
    POPULATION_DEFAULT = "population_default"
    UNAVAILABLE = "unavailable"


class ConfidenceLevel(str, Enum):
    """Sample-size confidence classification.

    Values must match confidence.classify_confidence exactly.
    """

    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    VERY_LOW = "Very Low"


class PredictionSource(str, Enum):
    """Which backing store served the report's risk figure."""

    POSTGRES = "postgres"
    SQLITE = "sqlite"
    UNAVAILABLE = "unavailable"


class VehicleDataSource(str, Enum):
    """Which backing store served the report's vehicle details."""

    DVSA = "dvsa"
    DEMO = "demo"


class ErrorCode(str, Enum):
    """Machine-readable error codes for the v2 report API."""

    INVALID_REGISTRATION = "invalid_registration"
    VEHICLE_NOT_FOUND = "vehicle_not_found"
    DVSA_UNAVAILABLE = "dvsa_unavailable"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
    REPORT_NOT_FOUND = "report_not_found"
    REPORT_EXPIRED = "report_expired"
    STORAGE_UNAVAILABLE = "storage_unavailable"


ERROR_CODE_STATUS: Dict[ErrorCode, int] = {
    ErrorCode.INVALID_REGISTRATION: 400,
    ErrorCode.VEHICLE_NOT_FOUND: 404,
    ErrorCode.DVSA_UNAVAILABLE: 503,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.REPORT_NOT_FOUND: 404,
    ErrorCode.REPORT_EXPIRED: 410,
    ErrorCode.STORAGE_UNAVAILABLE: 503,
}


class ReportCreateRequest(BaseModel):
    """Inbound request to create (or idempotently fetch) a report."""

    model_config = ConfigDict(extra="forbid")

    registration: str = Field(min_length=2, max_length=12)
    postcode: Optional[str] = Field(default=None, max_length=10)
    mileage_user: Optional[int] = Field(default=None, ge=0, le=500000)
    idempotency_key: Optional[str] = Field(default=None, max_length=100)


class ReportVehicle(BaseModel):
    """Vehicle identity fields shown on a report."""

    model_config = ConfigDict(extra="forbid")

    make: str
    model: str
    year: Optional[int]
    fuel_type: Optional[str] = None
    colour: Optional[str] = None


class ReportMot(BaseModel):
    """Most recent known MOT status for the vehicle."""

    model_config = ConfigDict(extra="forbid")

    expiry_date: Optional[str] = None
    last_test_date: Optional[str] = None
    last_result: Optional[str] = None


class ReportMileage(BaseModel):
    """Mileage figure actually used to produce the report, with provenance."""

    model_config = ConfigDict(extra="forbid")

    effective_value: Optional[int]
    source: MileageSource
    observed_at: Optional[str] = None
    unit_converted: bool = False
    anomaly: bool = False


class ReportEvidence(BaseModel):
    """Evidence backing the report's risk figure.

    total_tests / total_failures are null-means-unknown: None means the
    count was not available, and must never be coerced to 0.
    """

    model_config = ConfigDict(extra="forbid")

    match_scope: MatchScope
    age_band: Optional[str]
    mileage_band: Optional[str]
    total_tests: Optional[int]
    total_failures: Optional[int]


class ReportRisk(BaseModel):
    """The report's headline risk figure and its confidence."""

    model_config = ConfigDict(extra="forbid")

    failure_risk: float = Field(ge=0, le=1)
    confidence: ConfidenceLevel


class ComponentRiskItem(BaseModel):
    """A single component-level risk entry."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    risk: float = Field(ge=0, le=1)


class ReportComponents(BaseModel):
    """Component-level risk breakdown, if any is available."""

    model_config = ConfigDict(extra="forbid")

    available: bool
    items: Optional[List[ComponentRiskItem]] = None


class ReportRepairEstimate(BaseModel):
    """Indicative repair cost estimate, when one can be produced."""

    model_config = ConfigDict(extra="forbid")

    expected: int
    range_low: int
    range_high: int


class ReportPersistence(BaseModel):
    """Whether the report was saved and can be shared via a link."""

    model_config = ConfigDict(extra="forbid")

    saved: bool
    share_available: bool


class ReportResponse(BaseModel):
    """The v2 report: full response body for a created or fetched report."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = REPORT_CONTRACT_VERSION
    report_id: Optional[str] = None
    report_token: Optional[str] = None
    share_url: Optional[str] = None
    created_at: str
    expires_at: Optional[str] = None
    registration: str
    vehicle: ReportVehicle
    mot: ReportMot
    mileage: ReportMileage
    evidence: ReportEvidence
    risk: ReportRisk
    components: ReportComponents
    repair_estimate: Optional[ReportRepairEstimate] = None
    persistence: ReportPersistence
    prediction_source: PredictionSource
    vehicle_data_source: VehicleDataSource
    note: Optional[str] = None


class ErrorEnvelope(BaseModel):
    """Standard error body for the v2 report API."""

    model_config = ConfigDict(extra="forbid")

    error_code: ErrorCode
    message: str
    correlation_id: str
