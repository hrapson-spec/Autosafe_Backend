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
 * (mileage.observed_at, components.items, repair_estimate) tolerate an
 * absent key. See reportApi.ts / reportValidation.test.ts for how this
 * distinction is exercised.
 */
import type {
  ApiErrorCode,
  ApiErrorEnvelope,
  ComponentRiskItemV2,
  ConfidenceLevel,
  MatchScope,
  MileageSource,
  PredictionSource,
  ReportComponentsV2,
  ReportEvidenceV2,
  ReportMileageV2,
  ReportMotV2,
  ReportPersistenceV2,
  ReportRepairEstimateV2,
  ReportRiskV2,
  ReportV2,
  ReportVehicleV2,
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

export const VALID_PREDICTION_SOURCES: readonly PredictionSource[] = [
  'postgres',
  'sqlite',
  'dataset_reference',
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

function isReportMileageV2(x: unknown): x is ReportMileageV2 {
  if (!isPlainObject(x)) return false;
  const structurallyValid = (
    isNullable(x.effective_value, isNonNegativeInteger) &&
    isOneOf(x.source, VALID_MILEAGE_SOURCES) &&
    isOptionalNullable(x.observed_at, isString) &&
    isBoolean(x.unit_converted) &&
    isBoolean(x.anomaly)
  );
  if (!structurallyValid) return false;
  return x.source === 'missing' ? x.effective_value === null : x.effective_value !== null;
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

/** Structural guard for the v2 API's error envelope. error_code is checked
 * against the exact set of codes this client knows how to map to copy
 * (VALID_API_ERROR_CODES) -- see errorMessages.ts for what happens to a
 * code outside that set. */
export function isApiErrorEnvelope(x: unknown): x is ApiErrorEnvelope {
  if (!isPlainObject(x)) return false;
  return isOneOf(x.error_code, VALID_API_ERROR_CODES) && isString(x.message) && isString(x.correlation_id);
}
