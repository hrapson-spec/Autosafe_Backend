// Legacy pre-v2 types (CarSelection, CarReport, Fault, RepairCostEstimate,
// VehicleLookupResponse, BackendRiskResponse, MockCarMake/Model, InputMode)
// were removed in RC1: the v2 report contract (ReportV2 below) replaced them
// and nothing references them. Their adapter fabricated evidence defaults
// (0 tests, stringified-year "bands", a 50,000-mile placeholder) — do not
// reintroduce that pattern.

export interface RegistrationQuery {
  registration: string;
  postcode: string;
}

// Lead Capture Types
//
// year: number | null (not just `number`) because it is sourced from
// ReportV2.vehicle.year, which is honestly null when the vehicle's
// manufacture year is unknown (see report_contract.ReportVehicle) -- main.py's
// VehicleInfo.year is Optional[int] = None server-side, so an explicit null is
// accepted the same as an absent key; no need to fabricate or omit it.
// mileage / mileage_source: additive, nullable-tweak fields for the same
// reason -- ReportV2.mileage.effective_value is `number | null` (omitted from
// the wire payload entirely when null, never sent as 0 or a fabricated
// default), and mileage_source records its provenance. Both stay optional so
// any other producer of GarageLeadVehicle in the codebase keeps compiling.
export interface GarageLeadVehicle {
  make: string;
  model: string;
  year: number | null;
  mileage?: number;
  mileage_source?: MileageSource;
}

export interface GarageLeadRiskData {
  failure_risk: number;
  match_scope: MatchScope;
  reliability_score?: number;
  top_risks: string[];
}

export interface GarageLeadSubmission {
  email: string;
  postcode: string;
  name?: string;
  phone?: string;
  lead_type: 'garage';
  services_requested: string[];
  description?: string;
  urgency?: string;
  consent_given: boolean;
  vehicle: GarageLeadVehicle;
  risk_data: GarageLeadRiskData;
  experiment_variant?: string;
}

export interface GarageLeadResponse {
  success: boolean;
  lead_id: string;
  message: string;
}

export interface MotReminderSubmission {
  email: string;
  registration: string;
  postcode?: string;
  vehicle_make?: string;
  vehicle_model?: string;
  vehicle_year?: number;
  mot_expiry_date?: string;
  failure_risk?: number;
  match_scope?: MatchScope;
  experiment_variant?: string;
}

export interface MotReminderResponse {
  success: boolean;
  already_subscribed?: boolean;
  message: string;
}

export interface ReportEmailSubmission {
  email: string;
  registration: string;
  postcode?: string;
  vehicle_make?: string;
  vehicle_model?: string;
  vehicle_year?: number;
  failure_risk: number;
  match_scope?: MatchScope;
  common_faults: { component: string; risk_level: string }[];
  repair_cost_min?: number;
  repair_cost_max?: number;
  mot_expiry_date?: string;
  days_until_mot_expiry?: number;
  experiment_variant?: string;
}

export interface PublicStats {
  total_checks: number;
  checks_this_month: number;
  mot_records: string;
}

// Recommendation Engine Types
export type PrimaryAction = 'GET_QUOTES' | 'PRE_MOT_CHECK' | 'BOOK_MOT' | 'SET_REMINDER' | 'FIND_GARAGE';
export type CtaVariant = 'primary' | 'secondary' | 'tertiary';
export type MotivatorCardType = 'COST_ESTIMATE' | 'MOT_COUNTDOWN' | 'REMINDER_PITCH';

export interface RecommendationInput {
  failureRisk: number;                // 0-1
  hasVehicleComparison: boolean;
  repairCostEstimate?: { cost_min: number; cost_max: number; display: string };
  motExpired?: boolean;
  daysUntilMotExpiry?: number;        // undefined = unknown
  motExpiryDate?: string;
  highRiskFaultCount: number;
  make: string;
  model: string;
}

export interface Recommendation {
  primaryAction: PrimaryAction;
  ctaText: string;
  recommendationHeadline: string;
  supportingLine: string;
  ctaVariant: CtaVariant;
  trustMicrocopy: string;
  secondaryAction: PrimaryAction | null;
  secondaryCtaText: string | null;
  secondaryVariant: CtaVariant;
  motivatorCardType: MotivatorCardType;
  motivatorHeadline: string;
  motivatorSupportingLine: string;
  failureRiskPercent: number;
  scoreLabel: string;
}

// ============================================================================
// Report v2 contract (services/reportApi.ts, services/reportValidation.ts)
//
// Mirrors report_contract.py -- the backend's versioned source of truth for
// the v2 report request/response schema -- field-for-field: same names,
// same enum wire values, same null/optional discipline. A field the backend
// can legitimately send as null (e.g. evidence.total_tests when the
// evidence count itself is unknown) stays `T | null` here; it must never be
// narrowed to a non-null type and coerced at the UI boundary. See
// report_contract.py's module docstring for the full "no fabricated
// defaults" rationale this mirrors.
//
// Plain string-literal unions, not enums: this project targets erasable
// syntax (no runtime enum object), and a union is structurally identical to
// the corresponding Python str Enum's set of wire values.
// ============================================================================

/** How closely the served evidence matches this specific vehicle. Ordered
 * from most to least specific; mirrors report_contract.MatchScope. */
export type MatchScope =
  | 'exact_band'
  | 'age_band_only'
  | 'model_average'
  | 'population_default'
  | 'unavailable';

/** Provenance of the mileage value used to produce a report. Mirrors
 * report_contract.MileageSource. */
export type MileageSource = 'user_entered' | 'observed_mot' | 'estimated' | 'missing';

/** Sample-size confidence classification. Mirrors
 * report_contract.ConfidenceLevel (values match confidence.classify_confidence
 * exactly, including the space in 'Very Low'). */
export type ConfidenceLevel = 'High' | 'Medium' | 'Low' | 'Very Low';

/** Semantic meaning of the report's displayed risk figure. Mirrors
 * report_contract.ResultKind. */
export type ResultKind = 'comparison' | 'vehicle_prediction';

/** Exact source of the report's displayed risk figure. Mirrors
 * report_contract.PredictionSource. */
export type PredictionSource = 'postgres' | 'sqlite' | 'dataset_reference' | 'model_v55' | 'unavailable';

/** Which backing store served the report's vehicle details. Mirrors
 * report_contract.VehicleDataSource. */
export type VehicleDataSource = 'dvsa' | 'demo';

/** Whether report_service.resolve_odometer found a displayable, trustworthy
 * MOT odometer reading. Mirrors report_contract.OdometerStatus. */
export type OdometerStatusV2 = 'available' | 'unavailable';

/** Why no odometer reading is being shown -- always explicit, never
 * silently absent. Mirrors report_contract.OdometerUnavailableReason. */
export type OdometerUnavailableReasonV2 =
  | 'no_reading'
  | 'rollback'
  | 'implausible_increase'
  | 'unknown_unit';

/** /api/risk's honest source of its displayed rate. A distinct union from
 * PredictionSource above: that one records which backing store served the
 * v2 report response; this one records how closely /api/risk's legacy
 * population-comparison ladder could match the requested vehicle. Mirrors
 * report_contract.LookupPredictionSource. */
export type LookupPredictionSourceV2 =
  | 'population_exact'
  | 'population_broad'
  | 'population_global'
  | 'unavailable';

/** How closely a CohortEvidenceV2 matches the requested vehicle. Mirrors
 * report_contract.CohortMatchLevel. */
export type CohortMatchLevelV2 = 'exact_band' | 'age_band_only' | 'model_average' | 'dataset';

/** Machine-readable error codes for the v2 report API. Mirrors
 * report_contract.ErrorCode.
 *
 * 'undeclared_parameter' landed in the committed report_contract.py mid-wave
 * (added alongside report_routes.py's shared undeclared-query-parameter
 * guard, ERROR_CODE_STATUS[UNDECLARED_PARAMETER] = 400; confirmed present in
 * both report_contract.py's ErrorCode and
 * tests/test_report_contract.py::TestEnumValuesExact.test_error_code_values'
 * 9-member set as of this file's last edit) -- this union, errorMessages.ts,
 * and fixtures/reportResponses.ts all already carry it as a full member, so
 * no follow-up is needed here.
 */
export type ApiErrorCode =
  | 'invalid_registration'
  | 'vehicle_not_found'
  | 'dvsa_unavailable'
  | 'rate_limited'
  | 'internal_error'
  | 'report_not_found'
  | 'report_expired'
  | 'storage_unavailable'
  | 'idempotency_conflict'
  | 'undeclared_parameter';

/** Vehicle identity fields shown on a report. Mirrors report_contract.ReportVehicle. */
export interface ReportVehicleV2 {
  make: string;
  model: string;
  year: number | null;
  fuel_type: string | null;
  colour: string | null;
}

/** Most recent known MOT status for the vehicle. Mirrors report_contract.ReportMot. */
export interface ReportMotV2 {
  expiry_date: string | null;
  last_test_date: string | null;
  last_result: string | null;
}

/** Mileage figure actually used to produce the report, with provenance.
 * Mirrors report_contract.ReportMileage. */
export interface ReportMileageV2 {
  effective_value: number | null;
  source: MileageSource;
  observed_at?: string | null;
  unit_converted: boolean;
  anomaly: boolean;
  /** Additive (Release 1): the verbatim DVSA reading behind
   * effective_value, before any km->mi conversion. Optional because old
   * persisted 2.0 payloads (recorded before these fields existed) lack
   * them entirely -- absent, not a fabricated 0 or a copy of
   * effective_value. Mirrors report_contract.ReportMileage.original_value. */
  original_value?: number | null;
  /** Paired with original_value; when unit_converted is true and this is
   * present, it must be 'km' (report_contract.ReportMileage enforces this
   * on write). Mirrors report_contract.ReportMileage.original_unit. */
  original_unit?: string | null;
}

/** A single resolved odometer reading, produced by
 * report_service.resolve_odometer, with honest availability. AVAILABLE
 * requires every one of value_miles/recorded_at/original_value/
 * original_unit/source to be present (source pinned to the observed-MOT
 * mileage source -- this shape is only ever produced from a real
 * DVSA-recorded reading) and unavailable_reason absent; UNAVAILABLE
 * requires the mirror image: every detail field null and
 * unavailable_reason present. There is no partial state -- either every
 * reading detail is known, or none is invented in its place. Mirrors
 * report_contract.OdometerReading. */
export interface OdometerReadingV2 {
  value_miles: number | null;
  recorded_at: string | null;
  original_value: number | null;
  original_unit: string | null;
  source: MileageSource | null;
  status: OdometerStatusV2;
  unavailable_reason: OdometerUnavailableReasonV2 | null;
}

/** Evidence backing the report's risk figure. total_tests / total_failures
 * are null-means-unknown: null means the count was not available, and must
 * never be coerced to 0. Mirrors report_contract.ReportEvidence. */
export interface ReportEvidenceV2 {
  match_scope: MatchScope;
  age_band: string | null;
  mileage_band: string | null;
  total_tests: number | null;
  total_failures: number | null;
}

/** The comparison cohort backing a RiskLookupV2's displayed rate, when one
 * is available. total_tests is required and always positive -- unlike
 * ReportEvidenceV2's null-means-unknown total_tests, a CohortEvidenceV2
 * only ever exists to represent real evidence; the fully-unavailable case
 * is RiskLookupV2.cohort === null, not a cohort with a zero/null count.
 * Mirrors report_contract.LookupCohort. */
export interface CohortEvidenceV2 {
  match_level: CohortMatchLevelV2;
  age_band: string | null;
  mileage_band: string | null;
  total_tests: number;
  total_failures: number | null;
}

/** The report's headline risk figure and its confidence. Mirrors
 * report_contract.ReportRisk. */
export interface ReportRiskV2 {
  failure_risk: number;
  confidence: ConfidenceLevel;
}

/** A single component-level risk entry. Mirrors report_contract.ComponentRiskItem. */
export interface ComponentRiskItemV2 {
  key: string;
  label: string;
  risk: number;
}

/** Component-level risk breakdown, if any is available. Mirrors
 * report_contract.ReportComponents. */
export interface ReportComponentsV2 {
  available: boolean;
  items?: ComponentRiskItemV2[] | null;
}

/** Indicative repair cost estimate, when one can be produced. Mirrors
 * report_contract.ReportRepairEstimate. */
export interface ReportRepairEstimateV2 {
  expected: number;
  range_low: number;
  range_high: number;
}

/** Whether the report was saved and can be shared via a link. Mirrors
 * report_contract.ReportPersistence. */
export interface ReportPersistenceV2 {
  saved: boolean;
  share_available: boolean;
}

/** The v2 report: full response body for a created or fetched report.
 * Mirrors report_contract.ReportResponse. */
export interface ReportV2 {
  contract_version: string;
  result_kind: ResultKind;
  report_id: string | null;
  report_token: string | null;
  share_url: string | null;
  created_at: string;
  expires_at: string | null;
  registration: string;
  vehicle: ReportVehicleV2;
  mot: ReportMotV2;
  mileage: ReportMileageV2;
  evidence: ReportEvidenceV2;
  risk: ReportRiskV2;
  components: ReportComponentsV2;
  repair_estimate?: ReportRepairEstimateV2 | null;
  persistence: ReportPersistenceV2;
  prediction_source: PredictionSource;
  vehicle_data_source: VehicleDataSource;
  note: string | null;
}

/** Standard error body for the v2 report API. Mirrors report_contract.ErrorEnvelope. */
export interface ApiErrorEnvelope {
  error_code: ApiErrorCode;
  message: string;
  correlation_id: string;
}

// ============================================================================
// /api/risk lookup contract (services/reportValidation.ts)
//
// RiskLookupV2 mirrors report_contract.RiskLookupResponse: it is a
// different endpoint's contract from ReportV2 above, not a v2 report field.
// /api/risk's legacy 15-key surface (vehicle/year/mileage/failure_risk/
// confidence_level/the seven risk_* components/repair_cost_estimate) is
// preserved unchanged, plus the additive truth fields (prediction_source/
// cohort/note) describing exactly how the displayed rate was produced.
//
// Modelled as a discriminated union on prediction_source, not one
// interface with optional fields: 'unavailable' genuinely cannot carry a
// rate, cohort, or confidence/component detail, so a separate variant
// makes that impossible state unrepresentable instead of merely
// discouraged by a comment.
// ============================================================================

/** RiskLookupV2 when a rate was actually produced -- prediction_source
 * pins which tier of the population comparison ladder supplied it. */
export interface RiskLookupAvailableV2 {
  prediction_source: 'population_exact' | 'population_broad' | 'population_global';
  failure_risk: number;
  cohort: CohortEvidenceV2;
  vehicle: string;
  year: number;
  mileage: number | null;
  confidence_level: string | null;
  risk_brakes: number | null;
  risk_suspension: number | null;
  risk_tyres: number | null;
  risk_steering: number | null;
  risk_visibility: number | null;
  risk_lamps: number | null;
  risk_body: number | null;
  repair_cost_estimate: Record<string, unknown> | null;
  note: string | null;
}

/** RiskLookupV2 when no rate could be produced at all -- every risk/cohort/
 * confidence field is honestly null rather than a fabricated default.
 * repair_cost_estimate is included (always null here) because real backend
 * responses carry the key as null rather than omitting it -- this lets
 * components read it type-safely without an `in` check. */
export interface RiskLookupUnavailableV2 {
  prediction_source: 'unavailable';
  failure_risk: null;
  cohort: null;
  vehicle: string;
  year: number;
  mileage: number | null;
  confidence_level: null;
  risk_brakes: null;
  risk_suspension: null;
  risk_tyres: null;
  risk_steering: null;
  risk_visibility: null;
  risk_lamps: null;
  risk_body: null;
  repair_cost_estimate: null;
  note: string | null;
}

/** /api/risk's response shape. Mirrors report_contract.RiskLookupResponse. */
export type RiskLookupV2 = RiskLookupAvailableV2 | RiskLookupUnavailableV2;
