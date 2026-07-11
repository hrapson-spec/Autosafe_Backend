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

/** Exact source of the report's displayed risk figure. Mirrors
 * report_contract.PredictionSource. */
export type PredictionSource = 'postgres' | 'sqlite' | 'dataset_reference' | 'unavailable';

/** Which backing store served the report's vehicle details. Mirrors
 * report_contract.VehicleDataSource. */
export type VehicleDataSource = 'dvsa' | 'demo';

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
