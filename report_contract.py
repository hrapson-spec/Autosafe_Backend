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
``PredictionSource.DATASET_REFERENCE`` — rather than substituting a
plausible-looking value in its place. A fully degraded report (nothing
known about the vehicle) must still validate against this contract.

This is a pure data module: pydantic and the standard library only. No
I/O, no web-framework imports, no database imports.
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPORT_CONTRACT_VERSION = "2.0"
REPORT_TTL_DAYS = 90
# Exact aggregate of the checked-in primary comparison artifact
# (prod_data_clean.csv.gz): 254,145 cohort rows. The public reference rate and
# dataset-size claims must be updated together when that artifact changes;
# scripts/claim_sweep.py verifies these totals directly from the gzip CSV.
DATASET_TOTAL_TESTS = 148_509_908
DATASET_TOTAL_FAILURES = 39_969_903
# Date the checked-in aggregate artifact last changed in repository history
# (git log -- prod_data_clean.csv.gz). This is an artifact revision date, not
# a claim about the underlying source records' coverage period.
DATASET_ARTIFACT_REVISION = "2026-01-29"
POPULATION_DEFAULT_FAILURE_RISK = DATASET_TOTAL_FAILURES / DATASET_TOTAL_TESTS
SHARE_URL_PATH = "/app/report/{token}"


class MileageSource(str, Enum):
    """Provenance of the mileage value used to produce a report.

    USER_ENTERED and ESTIMATED are RETAINED but write-deprecated as of
    Release 1 ("Truthful Population Report"): already-persisted 2.0
    payloads carrying either value must keep replaying through
    ``ReportResponse.model_validate`` (report_routes.py's idempotency
    replay and GET-by-token paths both do this), but
    ``report_service.resolve_mileage`` can now only ever produce
    OBSERVED_MOT (a real recorded MOT odometer reading) or MISSING
    (honestly absent). Do not use USER_ENTERED or ESTIMATED in any new
    write path.
    """

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
    """Exact source of the report's displayed risk figure."""

    POSTGRES = "postgres"
    SQLITE = "sqlite"
    DATASET_REFERENCE = "dataset_reference"
    # Retained for backwards compatibility with already-saved payloads.
    # New degraded reports display the checked-in dataset reference and
    # therefore use DATASET_REFERENCE; MatchScope records why they degraded.
    UNAVAILABLE = "unavailable"


class VehicleDataSource(str, Enum):
    """Which backing store served the report's vehicle details."""

    DVSA = "dvsa"
    DEMO = "demo"


class OdometerStatus(str, Enum):
    """Whether report_service.resolve_odometer found a displayable,
    trustworthy MOT odometer reading."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class OdometerUnavailableReason(str, Enum):
    """Why no odometer reading is being shown -- always explicit, never
    silently absent."""

    NO_READING = "no_reading"                       # no test carries a displayable reading
    ROLLBACK = "rollback"                            # negative movement vs adjacent prior reading
    IMPLAUSIBLE_INCREASE = "implausible_increase"     # > 50,000 annualised miles/yr
    UNKNOWN_UNIT = "unknown_unit"                     # unit absent or not 'mi'/'km'


class LookupPredictionSource(str, Enum):
    """/api/risk's honest source of the displayed rate (RiskLookupResponse)."""

    POPULATION_EXACT = "population_exact"
    POPULATION_BROAD = "population_broad"
    POPULATION_GLOBAL = "population_global"
    UNAVAILABLE = "unavailable"


class CohortMatchLevel(str, Enum):
    """How closely a LookupCohort matches the requested vehicle."""

    EXACT_BAND = "exact_band"
    AGE_BAND_ONLY = "age_band_only"
    MODEL_AVERAGE = "model_average"
    DATASET = "dataset"


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
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    # FLAGGED ADDITIVE CHANGE (report_routes.py Wave-3 HTTP layer): the v2
    # routes reject any undeclared query-string parameter via a FastAPI
    # dependency shared by both routes. 400 is the correct status for a
    # malformed request, but none of the existing codes describe *why* --
    # this one does, without overloading INVALID_REGISTRATION (which is
    # specifically about the registration field) or INTERNAL_ERROR (which
    # is specifically about the server, not the request). See
    # tests/test_report_contract.py::TestEnumValuesExact.test_error_code_values
    # for the matching one-line update to this contract's exhaustiveness
    # expectation.
    UNDECLARED_PARAMETER = "undeclared_parameter"


ERROR_CODE_STATUS: Dict[ErrorCode, int] = {
    ErrorCode.INVALID_REGISTRATION: 400,
    ErrorCode.VEHICLE_NOT_FOUND: 404,
    ErrorCode.DVSA_UNAVAILABLE: 503,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.REPORT_NOT_FOUND: 404,
    ErrorCode.REPORT_EXPIRED: 410,
    ErrorCode.STORAGE_UNAVAILABLE: 503,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.UNDECLARED_PARAMETER: 400,
}


class ReportCreateRequest(BaseModel):
    """Inbound request to create (or idempotently fetch) a report."""

    model_config = ConfigDict(extra="forbid")

    # min_length=1 (not 2) is deliberate: a 1-char value must reach the
    # route's VRN regex so it yields the typed 400 invalid_registration
    # envelope rather than pydantic's generic 422 (staging check 4h).
    registration: str = Field(min_length=1, max_length=12)
    postcode: Optional[str] = Field(default=None, max_length=10)
    idempotency_key: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


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

    effective_value: Optional[int] = Field(ge=0)
    source: MileageSource
    observed_at: Optional[str] = None
    unit_converted: bool = False
    anomaly: bool = False
    # Additive (Release 1): the verbatim DVSA reading behind effective_value,
    # before any km->mi conversion. Optional with a None default so old
    # persisted 2.0 payloads (recorded before these fields existed) still
    # validate under this model unchanged.
    original_value: Optional[int] = Field(default=None, ge=0)
    original_unit: Optional[str] = None

    @model_validator(mode="after")
    def validate_source_value_consistency(self):
        if self.source == MileageSource.MISSING and self.effective_value is not None:
            raise ValueError("missing mileage cannot have an effective value")
        if self.source != MileageSource.MISSING and self.effective_value is None:
            raise ValueError("known mileage source requires an effective value")
        # original_unit is None-tolerant here (not just != 'km' -> reject):
        # an old payload persisted before original_value/original_unit
        # existed has unit_converted possibly True with original_unit
        # absent entirely (defaulted to None), and that must still replay
        # per the additive-optional contract guarantee. None means
        # "unknown", not "known and not km" -- only an explicitly wrong
        # unit is rejected.
        if self.unit_converted and self.original_unit is not None and self.original_unit != 'km':
            raise ValueError("unit-converted mileage must record a km original unit")
        return self


class OdometerReading(BaseModel):
    """A single resolved odometer reading, produced by
    report_service.resolve_odometer, with honest availability.

    AVAILABLE requires every one of value_miles/recorded_at/original_value/
    original_unit/source to be present (source pinned to OBSERVED_MOT --
    this model is only ever produced from a real DVSA-recorded reading)
    and unavailable_reason absent. UNAVAILABLE requires the mirror image:
    all five detail fields None and unavailable_reason present. There is
    no partial state -- either every reading detail is known, or none is
    invented in its place.
    """

    model_config = ConfigDict(extra="forbid")

    value_miles: Optional[int] = Field(ge=0)
    recorded_at: Optional[str]
    original_value: Optional[int] = Field(ge=0)
    original_unit: Optional[str]
    source: Optional[MileageSource]
    status: OdometerStatus
    unavailable_reason: Optional[OdometerUnavailableReason]

    @model_validator(mode="after")
    def validate_status_consistency(self):
        detail_fields = (self.value_miles, self.recorded_at, self.original_value, self.original_unit, self.source)
        if self.status == OdometerStatus.AVAILABLE:
            if any(field is None for field in detail_fields):
                raise ValueError(
                    "available odometer reading requires value, date, original reading, and source"
                )
            if self.source != MileageSource.OBSERVED_MOT:
                raise ValueError("available odometer reading must be sourced from an observed MOT reading")
            if self.unavailable_reason is not None:
                raise ValueError("available odometer reading cannot carry an unavailable_reason")
        else:
            if any(field is not None for field in detail_fields):
                raise ValueError("unavailable odometer reading cannot carry reading detail")
            if self.unavailable_reason is None:
                raise ValueError("unavailable odometer reading requires an unavailable_reason")
        return self


class ReportEvidence(BaseModel):
    """Evidence backing the report's risk figure.

    total_tests / total_failures are null-means-unknown: None means the
    count was not available, and must never be coerced to 0.
    """

    model_config = ConfigDict(extra="forbid")

    match_scope: MatchScope
    age_band: Optional[str]
    mileage_band: Optional[str]
    total_tests: Optional[int] = Field(ge=0)
    total_failures: Optional[int] = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self):
        if self.total_tests is None and self.total_failures is not None:
            raise ValueError("failure count requires a known test count")
        if self.total_tests is not None:
            if self.total_tests == 0:
                raise ValueError("zero tests are not evidence")
            if self.total_failures is not None and self.total_failures > self.total_tests:
                raise ValueError("failures cannot exceed tests")
        if (
            self.match_scope
            in {MatchScope.EXACT_BAND, MatchScope.AGE_BAND_ONLY, MatchScope.MODEL_AVERAGE}
            and self.total_tests is None
        ):
            raise ValueError("matched evidence requires sample counts")

        if self.match_scope == MatchScope.EXACT_BAND:
            if self.age_band is None or self.mileage_band is None:
                raise ValueError("exact-band evidence requires both matched bands")
        elif self.match_scope == MatchScope.AGE_BAND_ONLY:
            if self.age_band is None or self.mileage_band is not None:
                raise ValueError("age-only evidence requires only the matched age band")
        elif self.age_band is not None or self.mileage_band is not None:
            raise ValueError("broader or unavailable evidence cannot claim matched bands")
        return self


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

    @model_validator(mode="after")
    def validate_availability(self):
        if self.available and not self.items:
            raise ValueError("available component evidence requires items")
        if not self.available and self.items:
            raise ValueError("unavailable component evidence cannot contain items")
        return self


class ReportRepairEstimate(BaseModel):
    """Indicative repair cost estimate, when one can be produced."""

    model_config = ConfigDict(extra="forbid")

    expected: int = Field(ge=0)
    range_low: int = Field(ge=0)
    range_high: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self):
        if not self.range_low <= self.expected <= self.range_high:
            raise ValueError("repair estimate must satisfy low <= expected <= high")
        return self


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

    @model_validator(mode="after")
    def validate_report_consistency(self):
        if self.contract_version != REPORT_CONTRACT_VERSION:
            raise ValueError("unsupported report contract version")

        if self.repair_estimate is not None and not self.components.available:
            raise ValueError("repair estimate requires supported component evidence")

        if not self.persistence.saved and any(
            value is not None
            for value in (self.report_id, self.report_token, self.share_url, self.expires_at)
        ):
            raise ValueError("unsaved report cannot have durable report identity")
        if self.persistence.share_available:
            if not self.persistence.saved:
                raise ValueError("shareable report must be saved")
            if not all((self.report_id, self.report_token, self.share_url, self.expires_at)):
                raise ValueError("shareable report requires id, token, URL, and expiry")
            expected_suffix = SHARE_URL_PATH.format(token=self.report_token)
            if not self.share_url.endswith(expected_suffix):
                raise ValueError("share URL does not match report token")
        elif self.report_token is not None or self.share_url is not None:
            raise ValueError("non-shareable report cannot expose share credentials")
        return self


class LookupCohort(BaseModel):
    """The comparison cohort backing a /api/risk lookup's displayed rate,
    when one is available.

    total_tests uses ``gt=0`` (not ReportEvidence's null-means-unknown
    ``ge=0`` pattern) because a LookupCohort, when present at all, always
    represents real evidence -- the fully-unavailable case is
    ``RiskLookupResponse.cohort is None``, not a LookupCohort with a null
    count. Zero tests are not evidence, mirroring ReportEvidence's own
    rule (report_contract.py's ``validate_counts``).
    """

    model_config = ConfigDict(extra="forbid")

    match_level: CohortMatchLevel
    age_band: Optional[str]
    mileage_band: Optional[str]
    total_tests: int = Field(gt=0)
    total_failures: Optional[int] = Field(ge=0)


class RiskLookupResponse(BaseModel):
    """/api/risk's response shape (T3 wires the route onto the v2
    Postgres-then-SQLite evidence ladder; this module defines and fully
    tests the model now).

    Preserves the legacy 15-key surface exactly -- vehicle/year/mileage/
    last_mot_date/last_mot_result/failure_risk/confidence_level/the seven
    risk_* components/repair_cost_estimate -- so existing legacy consumers
    keep working, while adding honest truth fields (prediction_source/
    cohort/note) describing exactly how the displayed rate was produced.
    last_mot_date and last_mot_result are pinned null: this lookup route
    never populated them even before this task, and it still never
    resolves DVSA history at all.

    year is Optional (R1-T4 fix): /api/risk's own route keeps year
    required (`Query(..., ge=1990)`), but build_lookup is also called by
    _fallback_prediction (main.py) with a possibly-unknown manufacture
    year, and must still be able to construct this response.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Legacy 15-key surface (unchanged shape/semantics). ---
    vehicle: str
    year: Optional[int]
    mileage: Optional[int]
    last_mot_date: None = None
    last_mot_result: None = None
    failure_risk: Optional[float] = Field(ge=0, le=1)
    confidence_level: Optional[str]
    risk_brakes: Optional[float]
    risk_suspension: Optional[float]
    risk_tyres: Optional[float]
    risk_steering: Optional[float]
    risk_visibility: Optional[float]
    risk_lamps: Optional[float]
    risk_body: Optional[float]
    repair_cost_estimate: Optional[Dict[str, Any]]

    # --- Additive truth fields. ---
    prediction_source: LookupPredictionSource
    cohort: Optional[LookupCohort]
    note: Optional[str]

    @model_validator(mode="after")
    def validate_source_shape(self):
        components = (
            self.risk_brakes, self.risk_suspension, self.risk_tyres, self.risk_steering,
            self.risk_visibility, self.risk_lamps, self.risk_body,
        )
        if self.prediction_source == LookupPredictionSource.UNAVAILABLE:
            if self.failure_risk is not None or self.cohort is not None or self.confidence_level is not None:
                raise ValueError("unavailable lookup cannot carry a rate, cohort, or confidence level")
            if any(component is not None for component in components):
                raise ValueError("unavailable lookup cannot carry component risks")
            if self.repair_cost_estimate is not None:
                raise ValueError("unavailable lookup cannot carry a repair cost estimate")
        elif self.prediction_source == LookupPredictionSource.POPULATION_GLOBAL:
            if self.cohort is None or self.cohort.match_level != CohortMatchLevel.DATASET:
                raise ValueError("population_global lookup requires a dataset-level cohort")
            if (
                self.cohort.total_tests != DATASET_TOTAL_TESTS
                or self.cohort.total_failures != DATASET_TOTAL_FAILURES
            ):
                raise ValueError("population_global lookup must pin the checked-in dataset totals")
        elif self.prediction_source == LookupPredictionSource.POPULATION_EXACT:
            if self.cohort is None or self.cohort.match_level != CohortMatchLevel.EXACT_BAND:
                raise ValueError("population_exact lookup requires an exact-band cohort")
            if self.cohort.age_band is None or self.cohort.mileage_band is None:
                raise ValueError("exact-band cohort requires both matched bands")
        elif self.prediction_source == LookupPredictionSource.POPULATION_BROAD:
            if self.cohort is None or self.cohort.match_level not in {
                CohortMatchLevel.AGE_BAND_ONLY, CohortMatchLevel.MODEL_AVERAGE,
            }:
                raise ValueError("population_broad lookup requires an age-band-only or model-average cohort")
        return self


class ErrorEnvelope(BaseModel):
    """Standard error body for the v2 report API."""

    model_config = ConfigDict(extra="forbid")

    error_code: ErrorCode
    message: str
    correlation_id: str
