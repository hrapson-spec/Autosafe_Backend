/**
 * Runtime validation for the v2 report API's wire types (types.ts's
 * ReportV2 / ApiErrorEnvelope). Hand-rolled rather than a schema library:
 * these are the only two shapes this client needs to trust before handing
 * them to the UI, and the null/optional discipline they enforce (see
 * types.ts's module comment) is specific enough that a generic schema
 * validator would just be reproducing this file with more ceremony.
 *
 * Every guard here is structural and defensive: unknown extra keys on the
 * input are tolerated (this client only cares that the fields it reads
 * exist and are honestly typed), but a required field that is missing,
 * wrongly typed, or an unrecognised enum value fails the check. A field
 * typed `T | null` must be present as either `null` or a valid `T` --
 * `undefined` (key missing, or explicitly set to undefined) is NOT
 * accepted for those; only the handful of fields types.ts marks with `?`
 * (mileage.observed_at, mileage.original_value, mileage.original_unit,
 * components.items, repair_estimate) tolerate an absent key --
 * mileage.original_value/original_unit are additive (Release 1): old
 * persisted 2.0 payloads recorded before those fields existed must still
 * validate. See reportApi.ts / reportValidation.test.ts for how this
 * distinction is exercised.
 */
import type {
  ApiErrorCode,
  ApiErrorEnvelope,
  CohortEvidenceV2,
  CohortMatchLevelV2,
  ComponentRiskItemV2,
  ConfidenceLevel,
  LookupPredictionSourceV2,
  MatchScope,
  MileageSource,
  OdometerReadingV2,
  OdometerStatusV2,
  OdometerUnavailableReasonV2,
  PredictionSource,
  ResultKind,
  ReportComponentsV2,
  ReportEvidenceV2,
  ReportMileageV2,
  ReportMotV2,
  ReportPersistenceV2,
  ReportRepairEstimateV2,
  ReportRiskV2,
  ReportV2,
  ReportVehicleV2,
  RiskLookupV2,
  VehicleDataSource,
} from '../types';

// ----------------------------------------------------------------------------
// Enum membership -- exported so callers (and tests) that need to render or
// iterate over the valid values don't have to re-derive them from types.ts's
// string-literal unions.
// ----------------------------------------------------------------------------

export const VALID_MATCH_SCOPES: readonly MatchScope[] = [
  'exact_band',
  'age_band_only',
  'model_average',
  'population_default',
  'unavailable',
];

export const VALID_MILEAGE_SOURCES: readonly MileageSource[] = [
  'user_entered',
  'observed_mot',
  'estimated',
  'missing',
];

export const VALID_CONFIDENCE_LEVELS: readonly ConfidenceLevel[] = ['High', 'Medium', 'Low', 'Very Low'];

export const VALID_RESULT_KINDS: readonly ResultKind[] = ['comparison', 'vehicle_prediction'];

export const VALID_PREDICTION_SOURCES: readonly PredictionSource[] = [
  'postgres',
  'sqlite',
  'dataset_reference',
  'model_v55',
  'unavailable',
];

export const VALID_VEHICLE_DATA_SOURCES: readonly VehicleDataSource[] = ['dvsa', 'demo'];

export const VALID_API_ERROR_CODES: readonly ApiErrorCode[] = [
  'invalid_registration',
  'vehicle_not_found',
  'dvsa_unavailable',
  'rate_limited',
  'internal_error',
  'report_not_found',
  'report_expired',
  'storage_unavailable',
  'idempotency_conflict',
  'undeclared_parameter',
];

export const VALID_ODOMETER_STATUSES: readonly OdometerStatusV2[] = ['available', 'unavailable'];

export const VALID_ODOMETER_UNAVAILABLE_REASONS: readonly OdometerUnavailableReasonV2[] = [
  'no_reading',
  'rollback',
  'implausible_increase',
  'unknown_unit',
];

export const VALID_LOOKUP_PREDICTION_SOURCES: readonly LookupPredictionSourceV2[] = [
  'population_exact',
  'population_broad',
  'population_global',
  'unavailable',
];

export const VALID_COHORT_MATCH_LEVELS: readonly CohortMatchLevelV2[] = [
  'exact_band',
  'age_band_only',
  'model_average',
  'dataset',
];

// ----------------------------------------------------------------------------
// Primitive guards
// ----------------------------------------------------------------------------

function isPlainObject(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

function isString(x: unknown): x is string {
  return typeof x === 'string';
}

function isNumber(x: unknown): x is number {
  return typeof x === 'number' && Number.isFinite(x);
}

function isNonNegativeInteger(x: unknown): x is number {
  return isNumber(x) && Number.isInteger(x) && x >= 0;
}

function isProbability(x: unknown): x is number {
  return isNumber(x) && x >= 0 && x <= 1;
}

function isBoolean(x: unknown): x is boolean {
  return typeof x === 'boolean';
}

function isOneOf<T extends string>(x: unknown, valid: readonly T[]): x is T {
  return isString(x) && (valid as readonly string[]).includes(x);
}

/** For a REQUIRED field typed `T | null`: the key must be present on the
 * wire, but its value may honestly be null. `undefined` (key missing, or
 * explicitly set to undefined) fails -- that is a distinct, invalid state
 * from an honest null. */
function isNullable<T>(x: unknown, guard: (v: unknown) => v is T): x is T | null {
  return x === null || guard(x);
}

/** For an OPTIONAL field typed `T | null` with a `?` in its interface: the
 * key may be entirely absent (undefined), explicitly null, or a valid T. */
function isOptionalNullable<T>(x: unknown, guard: (v: unknown) => v is T): x is T | null | undefined {
  return x === undefined || isNullable(x, guard);
}

function isComponentRiskItemV2(x: unknown): x is ComponentRiskItemV2 {
  if (!isPlainObject(x)) return false;
  return isString(x.key) && isString(x.label) && isProbability(x.risk);
}

function isComponentRiskItemArray(x: unknown): x is ComponentRiskItemV2[] {
  return Array.isArray(x) && x.every(isComponentRiskItemV2);
}

// ----------------------------------------------------------------------------
// Nested-shape guards
// ----------------------------------------------------------------------------

function isReportVehicleV2(x: unknown): x is ReportVehicleV2 {
  if (!isPlainObject(x)) return false;
  return (
    isString(x.make) &&
    isString(x.model) &&
    isNullable(x.year, isNumber) &&
    isNullable(x.fuel_type, isString) &&
    isNullable(x.colour, isString)
  );
}

function isReportMotV2(x: unknown): x is ReportMotV2 {
  if (!isPlainObject(x)) return false;
  return (
    isNullable(x.expiry_date, isString) &&
    isNullable(x.last_test_date, isString) &&
    isNullable(x.last_result, isString)
  );
}

/** Structural guard for ReportMileageV2. original_value/original_unit are
 * additive (Release 1) and OPTIONAL on the wire -- isOptionalNullable
 * accepts the key being entirely absent, not just null, so an old
 * persisted 2.0 payload (recorded before these fields existed) still
 * passes unchanged. Mirrors
 * report_contract.ReportMileage.validate_source_value_consistency exactly,
 * including its None-tolerance: the km-only check on original_unit only
 * fires when unit_converted is true AND original_unit is an actual
 * (wrong) string -- an absent or explicitly-null original_unit is treated
 * as "unknown", not "known and not km". */
export function isReportMileageV2(x: unknown): x is ReportMileageV2 {
  if (!isPlainObject(x)) return false;
  const structurallyValid = (
    isNullable(x.effective_value, isNonNegativeInteger) &&
    isOneOf(x.source, VALID_MILEAGE_SOURCES) &&
    isOptionalNullable(x.observed_at, isString) &&
    isBoolean(x.unit_converted) &&
    isBoolean(x.anomaly) &&
    isOptionalNullable(x.original_value, isNonNegativeInteger) &&
    isOptionalNullable(x.original_unit, isString)
  );
  if (!structurallyValid) return false;
  const valueSourceConsistent = x.source === 'missing' ? x.effective_value === null : x.effective_value !== null;
  if (!valueSourceConsistent) return false;
  if (x.unit_converted === true && typeof x.original_unit === 'string' && x.original_unit !== 'km') {
    return false;
  }
  return true;
}

/** Structural guard for a single resolved odometer reading. AVAILABLE
 * requires every one of value_miles/recorded_at/original_value/
 * original_unit/source to be non-null (source pinned to the observed-MOT
 * mileage source) and unavailable_reason absent; UNAVAILABLE requires the
 * mirror image: every detail field null and unavailable_reason present.
 * There is no partial state -- a payload with e.g. status: 'unavailable'
 * but a non-null value_miles is rejected outright, never trusted as a
 * partial reading. Mirrors
 * report_contract.OdometerReading.validate_status_consistency. */
export function isOdometerReadingV2(x: unknown): x is OdometerReadingV2 {
  if (!isPlainObject(x)) return false;
  const structurallyValid = (
    isNullable(x.value_miles, isNonNegativeInteger) &&
    isNullable(x.recorded_at, isString) &&
    isNullable(x.original_value, isNonNegativeInteger) &&
    isNullable(x.original_unit, isString) &&
    isNullable(x.source, (v): v is MileageSource => isOneOf(v, VALID_MILEAGE_SOURCES)) &&
    isOneOf(x.status, VALID_ODOMETER_STATUSES) &&
    isNullable(x.unavailable_reason, (v): v is OdometerUnavailableReasonV2 =>
      isOneOf(v, VALID_ODOMETER_UNAVAILABLE_REASONS)
    )
  );
  if (!structurallyValid) return false;

  const detailFields = [x.value_miles, x.recorded_at, x.original_value, x.original_unit, x.source];
  if (x.status === 'available') {
    if (detailFields.some((field) => field === null)) return false;
    if (x.source !== 'observed_mot') return false;
    if (x.unavailable_reason !== null) return false;
  } else {
    if (detailFields.some((field) => field !== null)) return false;
    if (x.unavailable_reason === null) return false;
  }
  return true;
}

function isReportEvidenceV2(x: unknown): x is ReportEvidenceV2 {
  if (!isPlainObject(x)) return false;
  const structurallyValid = (
    isOneOf(x.match_scope, VALID_MATCH_SCOPES) &&
    isNullable(x.age_band, isString) &&
    isNullable(x.mileage_band, isString) &&
    isNullable(x.total_tests, isNonNegativeInteger) &&
    isNullable(x.total_failures, isNonNegativeInteger)
  );
  if (!structurallyValid) return false;
  if (x.total_tests === null && x.total_failures !== null) return false;
  if (x.total_tests === 0) return false;
  if (
    ['exact_band', 'age_band_only', 'model_average'].includes(x.match_scope as string) &&
    x.total_tests === null
  ) return false;
  if (
    typeof x.total_tests === 'number' &&
    typeof x.total_failures === 'number' &&
    x.total_failures > x.total_tests
  ) {
    return false;
  }
  if (x.match_scope === 'exact_band') {
    if (x.age_band === null || x.mileage_band === null) return false;
  } else if (x.match_scope === 'age_band_only') {
    if (x.age_band === null || x.mileage_band !== null) return false;
  } else if (x.age_band !== null || x.mileage_band !== null) {
    return false;
  }
  return true;
}

/** Structural guard for the comparison cohort backing a RiskLookupV2's
 * displayed rate. total_tests is REQUIRED and must be a positive integer --
 * unlike ReportEvidenceV2.total_tests, a CohortEvidenceV2 only ever exists
 * to represent real evidence (the fully-unavailable case is
 * RiskLookupV2.cohort === null, not a cohort with a zero/null count).
 * Mirrors report_contract.LookupCohort (which, unlike ReportEvidence, has
 * no age_band/mileage_band-vs-match_level cross validation of its own --
 * that check is layered on by isRiskLookupV2, keyed to prediction_source,
 * exactly where report_contract.RiskLookupResponse.validate_source_shape
 * puts it). */
export function isCohortEvidenceV2(x: unknown): x is CohortEvidenceV2 {
  if (!isPlainObject(x)) return false;
  return (
    isOneOf(x.match_level, VALID_COHORT_MATCH_LEVELS) &&
    isNullable(x.age_band, isString) &&
    isNullable(x.mileage_band, isString) &&
    isNonNegativeInteger(x.total_tests) &&
    x.total_tests > 0 &&
    isNullable(x.total_failures, isNonNegativeInteger)
  );
}

function isReportRiskV2(x: unknown): x is ReportRiskV2 {
  if (!isPlainObject(x)) return false;
  return isProbability(x.failure_risk) && isOneOf(x.confidence, VALID_CONFIDENCE_LEVELS);
}

function isReportComponentsV2(x: unknown): x is ReportComponentsV2 {
  if (!isPlainObject(x)) return false;
  if (!isBoolean(x.available) || !isOptionalNullable(x.items, isComponentRiskItemArray)) return false;
  if (x.available) return Array.isArray(x.items) && x.items.length > 0;
  return x.items === undefined || x.items === null || (Array.isArray(x.items) && x.items.length === 0);
}

function isReportRepairEstimateV2(x: unknown): x is ReportRepairEstimateV2 {
  if (!isPlainObject(x)) return false;
  return (
    isNonNegativeInteger(x.expected) &&
    isNonNegativeInteger(x.range_low) &&
    isNonNegativeInteger(x.range_high) &&
    x.range_low <= x.expected &&
    x.expected <= x.range_high
  );
}

function isReportPersistenceV2(x: unknown): x is ReportPersistenceV2 {
  if (!isPlainObject(x)) return false;
  return isBoolean(x.saved) && isBoolean(x.share_available);
}

// ----------------------------------------------------------------------------
// Top-level guards
// ----------------------------------------------------------------------------

/** Structural guard for the full v2 report body. Required fields must be
 * present with an honestly-typed value (null is a legitimate value for the
 * many fields typed `T | null`; undefined is not). Unknown extra keys are
 * tolerated. */
export function isReportV2(x: unknown): x is ReportV2 {
  if (!isPlainObject(x)) return false;
  const structurallyValid = (
    x.contract_version === '2.0' &&
    isOneOf(x.result_kind, VALID_RESULT_KINDS) &&
    isNullable(x.report_id, isString) &&
    isNullable(x.report_token, isString) &&
    isNullable(x.share_url, isString) &&
    isString(x.created_at) &&
    isNullable(x.expires_at, isString) &&
    isString(x.registration) &&
    isReportVehicleV2(x.vehicle) &&
    isReportMotV2(x.mot) &&
    isReportMileageV2(x.mileage) &&
    isReportEvidenceV2(x.evidence) &&
    isReportRiskV2(x.risk) &&
    isReportComponentsV2(x.components) &&
    isOptionalNullable(x.repair_estimate, isReportRepairEstimateV2) &&
    isReportPersistenceV2(x.persistence) &&
    isOneOf(x.prediction_source, VALID_PREDICTION_SOURCES) &&
    isOneOf(x.vehicle_data_source, VALID_VEHICLE_DATA_SOURCES) &&
    isNullable(x.note, isString)
  );
  if (!structurallyValid) return false;

  if (x.result_kind === 'vehicle_prediction') {
    if (x.prediction_source !== 'model_v55') return false;
  } else if (x.prediction_source === 'model_v55') {
    return false;
  }

  // Repeat this guard to give TypeScript a direct narrowing point after the
  // aliased compound condition above.
  if (!isReportComponentsV2(x.components)) return false;
  if (x.repair_estimate !== undefined && x.repair_estimate !== null && !x.components.available) {
    return false;
  }
  if (!isReportPersistenceV2(x.persistence)) return false;
  const { saved, share_available: shareAvailable } = x.persistence;
  if (
    !saved &&
    (x.report_id !== null || x.report_token !== null || x.share_url !== null || x.expires_at !== null)
  ) return false;
  if (shareAvailable) {
    return (
      saved &&
      typeof x.report_id === 'string' &&
      typeof x.report_token === 'string' &&
      typeof x.share_url === 'string' &&
      typeof x.expires_at === 'string' &&
      x.share_url.endsWith(`/app/report/${x.report_token}`)
    );
  }
  return x.report_token === null && x.share_url === null;
}

/** Structural guard for /api/risk's response shape. Discriminates on
 * prediction_source: 'unavailable' requires failure_risk/cohort/
 * confidence_level and all seven component risks to be null (no rate was
 * produced -- nothing is fabricated in their place); the three
 * population_* tiers require a real failure_risk in [0,1] and a cohort
 * satisfying isCohortEvidenceV2, with the cohort's match_level pinned to
 * the specific tier that produced it: population_exact -> exact_band with
 * both matched bands known, population_broad -> age_band_only |
 * model_average, population_global -> dataset. No consumer should read a
 * raw /api/risk response except through this guard. Mirrors
 * report_contract.RiskLookupResponse.validate_source_shape. */
export function isRiskLookupV2(x: unknown): x is RiskLookupV2 {
  if (!isPlainObject(x)) return false;
  if (!isOneOf(x.prediction_source, VALID_LOOKUP_PREDICTION_SOURCES)) return false;

  const structurallyValid = (
    isString(x.vehicle) &&
    isNumber(x.year) &&
    isNullable(x.mileage, isNonNegativeInteger) &&
    isNullable(x.note, isString) &&
    isNullable(x.confidence_level, isString) &&
    isNullable(x.risk_brakes, isNumber) &&
    isNullable(x.risk_suspension, isNumber) &&
    isNullable(x.risk_tyres, isNumber) &&
    isNullable(x.risk_steering, isNumber) &&
    isNullable(x.risk_visibility, isNumber) &&
    isNullable(x.risk_lamps, isNumber) &&
    isNullable(x.risk_body, isNumber)
  );
  if (!structurallyValid) return false;

  if (x.prediction_source === 'unavailable') {
    return (
      x.failure_risk === null &&
      x.cohort === null &&
      x.confidence_level === null &&
      x.risk_brakes === null &&
      x.risk_suspension === null &&
      x.risk_tyres === null &&
      x.risk_steering === null &&
      x.risk_visibility === null &&
      x.risk_lamps === null &&
      x.risk_body === null
    );
  }

  // Available tiers: population_exact | population_broad | population_global.
  if (!isProbability(x.failure_risk)) return false;
  if (!isNullable(x.repair_cost_estimate, isPlainObject)) return false;
  if (!isCohortEvidenceV2(x.cohort)) return false;

  if (x.prediction_source === 'population_global') {
    if (x.cohort.match_level !== 'dataset') return false;
  } else if (x.prediction_source === 'population_exact') {
    if (x.cohort.match_level !== 'exact_band') return false;
    if (x.cohort.age_band === null || x.cohort.mileage_band === null) return false;
  } else {
    // population_broad
    if (x.cohort.match_level !== 'age_band_only' && x.cohort.match_level !== 'model_average') return false;
  }
  return true;
}

/** Structural guard for the v2 API's error envelope. error_code is checked
 * against the exact set of codes this client knows how to map to copy
 * (VALID_API_ERROR_CODES) -- see errorMessages.ts for what happens to a
 * code outside that set. */
export function isApiErrorEnvelope(x: unknown): x is ApiErrorEnvelope {
  if (!isPlainObject(x)) return false;
  return isOneOf(x.error_code, VALID_API_ERROR_CODES) && isString(x.message) && isString(x.correlation_id);
}
