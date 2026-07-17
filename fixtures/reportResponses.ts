/**
 * Hand-authored ReportV2 / ApiErrorEnvelope fixtures: one per notable
 * evidence-ladder matrix cell, plus one ApiErrorEnvelope example per
 * ApiErrorCode. Every ReportV2 export here is internally consistent with
 * report_service.py's real evidence-ladder and mileage-resolution logic
 * (not just structurally valid) -- reportValidation.test.ts asserts every
 * one of them passes isReportV2.
 *
 * Vehicle identities use the TESTMAKE/TESTMODEL convention the backend's
 * own test fixtures already use (tests/report_test_helpers.py), plus one
 * obviously-fake VRM (ZZ99ZZZ) for the fully-degraded demo case, so nothing
 * here can be mistaken for a real vehicle or registration.
 */
import type { ApiErrorCode, ApiErrorEnvelope, ReportV2 } from '../types';

// ----------------------------------------------------------------------------
// 1. exact_band / observed_mot / High confidence -- the "everything worked"
//    happy path: real MOT history, a precise evidence match, a full
//    component breakdown, a repair estimate, and a saved + shareable report.
// ----------------------------------------------------------------------------
export const fixtureExactHigh: ReportV2 = {
  contract_version: '2.0',
  result_kind: 'comparison',
  report_id: 'rpt_4f8a21c6b9d3',
  report_token: '9c7f2b1a4e6d8035c2f19a7b6e4d3c81',
  share_url: 'https://www.autosafe.one/app/report/9c7f2b1a4e6d8035c2f19a7b6e4d3c81',
  created_at: '2026-05-01T09:12:00Z',
  expires_at: '2026-07-30T09:12:00Z',
  registration: 'AB12CDE',
  vehicle: {
    make: 'TESTMAKE',
    model: 'TESTMODEL',
    year: 2021,
    fuel_type: 'PETROL',
    colour: 'BLUE',
  },
  mot: {
    expiry_date: '2027-04-18T00:00:00',
    last_test_date: '2026-04-18T00:00:00',
    last_result: 'PASSED',
  },
  mileage: {
    effective_value: 62411,
    source: 'observed_mot',
    observed_at: '2026-04-18T00:00:00',
    unit_converted: false,
    anomaly: false,
  },
  evidence: {
    match_scope: 'exact_band',
    age_band: '3-5',
    mileage_band: '60k-100k',
    total_tests: 1842,
    total_failures: 221,
  },
  risk: {
    failure_risk: 0.12,
    confidence: 'High',
  },
  components: {
    available: true,
    items: [
      { key: 'brakes', label: 'Brakes', risk: 0.18 },
      { key: 'tyres', label: 'Tyres', risk: 0.09 },
      { key: 'suspension', label: 'Suspension', risk: 0.07 },
    ],
  },
  repair_estimate: {
    expected: 320,
    range_low: 180,
    range_high: 460,
  },
  persistence: {
    saved: true,
    share_available: true,
  },
  prediction_source: 'postgres',
  vehicle_data_source: 'dvsa',
  note: null,
};

export const fixtureVehiclePrediction: ReportV2 = {
  ...fixtureExactHigh,
  result_kind: 'vehicle_prediction',
  prediction_source: 'model_v55',
  // A prediction is a per-vehicle model output, not a cohort match: it
  // claims the model_prediction scope and carries no bands or sample counts.
  evidence: {
    match_scope: 'model_prediction',
    age_band: null,
    mileage_band: null,
    total_tests: null,
    total_failures: null,
  },
};

// ----------------------------------------------------------------------------
// 2. age_band_only / estimated mileage / Medium confidence -- no MOT history
//    at all for this specific vehicle (mot.* and mileage.observed_at are all
//    null), so mileage falls back to the age*8000mi/yr estimate and the
//    evidence ladder only matches on age band -- report_service.py's
//    NOTE_AGE_BAND_ONLY copy, reused verbatim.
//
//    Renamed from fixtureAgeOnlyMedium (Release 1): source: 'estimated' is
//    RETAINED but write-deprecated as of report_contract.MileageSource --
//    report_service.resolve_mileage can no longer produce it, but an
//    already-persisted 2.0 payload carrying it (from before
//    original_value/original_unit existed, and lacking both keys entirely,
//    exactly as this fixture's mileage object still does below) must keep
//    replaying through the report guards unchanged. That is this fixture's
//    job now: prove the additive original_value/original_unit tolerance in
//    both directions. Payload is byte-identical to the old
//    fixtureAgeOnlyMedium -- only the name and this comment changed.
// ----------------------------------------------------------------------------
export const fixtureLegacyEstimated2_0: ReportV2 = {
  contract_version: '2.0',
  result_kind: 'comparison',
  report_id: 'rpt_1a2b3c4d5e6f',
  report_token: '3d9e7c5b1a4f6082d4c7b9a1e6f3d8c2',
  share_url: 'https://www.autosafe.one/app/report/3d9e7c5b1a4f6082d4c7b9a1e6f3d8c2',
  created_at: '2026-06-10T11:40:00Z',
  expires_at: '2026-09-08T11:40:00Z',
  registration: 'CD34EFG',
  vehicle: {
    make: 'TESTMAKE',
    model: 'AGEMODEL',
    year: 2019,
    fuel_type: 'DIESEL',
    colour: 'GREY',
  },
  mot: {
    expiry_date: null,
    last_test_date: null,
    last_result: null,
  },
  mileage: {
    effective_value: 56000,
    source: 'estimated',
    observed_at: null,
    unit_converted: false,
    anomaly: false,
  },
  evidence: {
    match_scope: 'age_band_only',
    age_band: '6-10',
    mileage_band: null,
    total_tests: 340,
    total_failures: 65,
  },
  risk: {
    failure_risk: 0.19,
    confidence: 'Medium',
  },
  components: {
    available: true,
    items: [
      { key: 'visibility', label: 'Visibility', risk: 0.11 },
      { key: 'lamps', label: 'Lamps & Electrical', risk: 0.06 },
    ],
  },
  repair_estimate: {
    expected: 210,
    range_low: 120,
    range_high: 300,
  },
  persistence: {
    saved: true,
    share_available: true,
  },
  prediction_source: 'sqlite',
  vehicle_data_source: 'dvsa',
  note: "Exact mileage band unavailable for this vehicle's age group — showing the closest match for vehicles of a similar age.",
};

// ----------------------------------------------------------------------------
// 3. model_average / observed_mot with a rejected (anomalous) reading -- the
//    latest MOT test's odometer reading was implausible (a km->mi unit
//    conversion plus a jump report_service.py's resolve_mileage flags as
//    impossible), so the *previous* test's reading is shown instead, dated
//    earlier than mot.last_test_date -- mot.* always reflects the newest
//    test regardless of which test the mileage anomaly check trusted.
//    Neither the exact band nor the age band had data, so this falls all
//    the way to the model-wide average.
// ----------------------------------------------------------------------------
export const fixtureModelAverageLow: ReportV2 = {
  contract_version: '2.0',
  result_kind: 'comparison',
  report_id: 'rpt_7b6a5c4d3e2f',
  report_token: 'a1c3e5f7b9d2846013c5e7a9f1b3d5c7',
  share_url: 'https://www.autosafe.one/app/report/a1c3e5f7b9d2846013c5e7a9f1b3d5c7',
  created_at: '2026-06-25T15:05:00Z',
  expires_at: '2026-09-23T15:05:00Z',
  registration: 'EF56HIJ',
  vehicle: {
    make: 'TESTMAKE',
    model: 'RAREMODEL',
    year: 2013,
    fuel_type: 'PETROL',
    colour: 'RED',
  },
  mot: {
    expiry_date: '2027-06-01T00:00:00',
    last_test_date: '2026-06-01T00:00:00',
    last_result: 'PASSED',
  },
  mileage: {
    effective_value: 121000,
    source: 'observed_mot',
    observed_at: '2026-01-15T00:00:00',
    unit_converted: true,
    anomaly: true,
  },
  evidence: {
    match_scope: 'model_average',
    age_band: null,
    mileage_band: null,
    total_tests: 47,
    total_failures: 17,
  },
  risk: {
    failure_risk: 0.36,
    confidence: 'Low',
  },
  components: {
    available: true,
    items: [
      { key: 'brakes', label: 'Brakes', risk: 0.22 },
      { key: 'suspension', label: 'Suspension', risk: 0.15 },
      { key: 'steering', label: 'Steering', risk: 0.08 },
      { key: 'body', label: 'Body & Chassis', risk: 0.12 },
    ],
  },
  repair_estimate: {
    expected: 480,
    range_low: 260,
    range_high: 720,
  },
  persistence: {
    saved: true,
    share_available: true,
  },
  prediction_source: 'postgres',
  vehicle_data_source: 'dvsa',
  note: 'Exact age and mileage match not found — showing the overall average for this make and model.',
};

// ----------------------------------------------------------------------------
// 4. population_default -- vehicle identity partially known (DVSA found the
//    plate: make/model/fuel/colour) but manufacture year is missing, so
//    mileage cannot even be estimated (resolve_mileage's ESTIMATED rung
//    requires a known vehicle year) and falls all the way to 'missing'. The
//    evidence store WAS reached but had literally no rows for this make/model.
//    The displayed rate's source is therefore the checked-in dataset reference;
//    match_scope preserves the distinct "reached but empty" reason --
//    total_tests/total_failures are honestly null, never coerced to 0.
// ----------------------------------------------------------------------------
export const fixturePopulationDefault: ReportV2 = {
  contract_version: '2.0',
  result_kind: 'comparison',
  report_id: 'rpt_2e4c6a8b0d1f',
  report_token: 'f3b1d5a7c9e2408162a4c6e8f0b2d4a6',
  share_url: 'https://www.autosafe.one/app/report/f3b1d5a7c9e2408162a4c6e8f0b2d4a6',
  created_at: '2026-02-14T08:30:00Z',
  expires_at: '2026-05-15T08:30:00Z',
  registration: 'GH78JKL',
  vehicle: {
    make: 'TESTMAKE',
    model: 'OBSCUREMODEL',
    year: null,
    fuel_type: 'PETROL',
    colour: 'SILVER',
  },
  mot: {
    expiry_date: null,
    last_test_date: null,
    last_result: null,
  },
  mileage: {
    effective_value: null,
    source: 'missing',
    observed_at: null,
    unit_converted: false,
    anomaly: false,
  },
  evidence: {
    match_scope: 'population_default',
    age_band: null,
    mileage_band: null,
    total_tests: null,
    total_failures: null,
  },
  risk: {
    failure_risk: 39_969_903 / 148_509_908,
    confidence: 'Very Low',
  },
  components: {
    available: false,
    items: null,
  },
  repair_estimate: null,
  persistence: {
    saved: true,
    share_available: true,
  },
  prediction_source: 'dataset_reference',
  vehicle_data_source: 'dvsa',
  note: 'No data available for this make and model — showing the dataset-wide reference across the recorded tests.',
};

// ----------------------------------------------------------------------------
// 5. unavailable -- the fully degraded end-to-end failure: DVSA vehicle
//    lookup itself fell back to a demo identity, and the evidence store
//    could not be reached at all. The displayed rate still comes from the
//    checked-in dataset reference; match_scope 'unavailable' preserves the
//    distinction from "reached but empty". Never persisted, so
//    report_id/report_token/share_url are all
//    honestly null rather than fabricated.
// ----------------------------------------------------------------------------
export const fixtureUnavailableDegraded: ReportV2 = {
  contract_version: '2.0',
  result_kind: 'comparison',
  report_id: null,
  report_token: null,
  share_url: null,
  created_at: '2026-03-20T17:55:00Z',
  expires_at: null,
  registration: 'ZZ99ZZZ',
  vehicle: {
    make: 'DEMO',
    model: 'VEHICLE',
    year: null,
    fuel_type: null,
    colour: null,
  },
  mot: {
    expiry_date: null,
    last_test_date: null,
    last_result: null,
  },
  mileage: {
    effective_value: null,
    source: 'missing',
    observed_at: null,
    unit_converted: false,
    anomaly: false,
  },
  evidence: {
    match_scope: 'unavailable',
    age_band: null,
    mileage_band: null,
    total_tests: null,
    total_failures: null,
  },
  risk: {
    failure_risk: 39_969_903 / 148_509_908,
    confidence: 'Very Low',
  },
  components: {
    available: false,
    items: null,
  },
  repair_estimate: null,
  persistence: {
    saved: false,
    share_available: false,
  },
  prediction_source: 'dataset_reference',
  vehicle_data_source: 'demo',
  // Deliberately NOT report_service.py's NOTE_UNAVAILABLE verbatim: that
  // copy specifically says "vehicle identity confirmed", which would
  // contradict vehicle_data_source: 'demo' here. This combination (demo
  // identity AND an unreachable evidence store) isn't produced by any
  // report_service.py code path today -- it's a plausible future route-layer
  // outcome (DVSA lookup itself failed too), so its note is honest about
  // both failures rather than reusing text that assumes only one did.
  note: 'Vehicle details and comparison data are temporarily unavailable — this is a demo report showing the checked-in dataset reference.',
};

// ----------------------------------------------------------------------------
// 6. exact_band / observed_mot, unconverted / High confidence -- a
//    high-mileage vehicle whose latest MOT recorded a plain miles reading
//    (no unit conversion needed), landing an exact age+mileage band match.
//    Release 1 fixture: demonstrates original_value/original_unit on a
//    freshly-authored payload where they simply mirror effective_value
//    unchanged (mirrors report_contract.EXACT_BAND's None note -- exact
//    matches carry no caveat).
// ----------------------------------------------------------------------------
const observedHighMileageToken = '5e7c9b1a3d6f8024b6d8a0c2e4f6b8d0';
export const fixtureObservedHighMileage: ReportV2 = {
  contract_version: '2.0',
  result_kind: 'comparison',
  report_id: 'rpt_9f1e3d5c7b0a',
  report_token: observedHighMileageToken,
  share_url: `https://www.autosafe.one/app/report/${observedHighMileageToken}`,
  created_at: '2026-07-10T09:00:00Z',
  expires_at: '2026-10-08T09:00:00Z',
  registration: 'IJ90KLM',
  vehicle: {
    make: 'TESTMAKE',
    model: 'HIGHMILEAGEMODEL',
    year: 2015,
    fuel_type: 'DIESEL',
    colour: 'BLACK',
  },
  mot: {
    expiry_date: '2026-11-02T00:00:00',
    last_test_date: '2025-11-02T00:00:00',
    last_result: 'PASSED',
  },
  mileage: {
    effective_value: 112406,
    source: 'observed_mot',
    observed_at: '2025-11-02',
    unit_converted: false,
    anomaly: false,
    original_value: 112406,
    original_unit: 'mi',
  },
  evidence: {
    match_scope: 'exact_band',
    age_band: '11-15',
    mileage_band: '100k+',
    total_tests: 1204,
    total_failures: 388,
  },
  risk: {
    failure_risk: 0.31,
    confidence: 'High',
  },
  components: {
    available: true,
    items: [
      { key: 'brakes', label: 'Brakes', risk: 0.28 },
      { key: 'suspension', label: 'Suspension', risk: 0.24 },
      { key: 'body', label: 'Body & Chassis', risk: 0.19 },
    ],
  },
  repair_estimate: {
    expected: 410,
    range_low: 240,
    range_high: 640,
  },
  persistence: {
    saved: true,
    share_available: true,
  },
  prediction_source: 'postgres',
  vehicle_data_source: 'dvsa',
  note: null,
};

// ----------------------------------------------------------------------------
// 7. age_band_only / missing mileage with a rejected anomalous reading --
//    the vehicle's only odometer reading was flagged anomalous (e.g. a
//    rollback or implausible jump per report_contract.OdometerUnavailableReason)
//    and rejected outright, so there is no usable mileage at all
//    (source: 'missing', effective_value: null) even though anomaly: true
//    records that a reading was seen and distrusted, not simply absent.
//    original_value/original_unit are omitted entirely -- there is no
//    verbatim DVSA reading to report when the resolved mileage itself is
//    missing. Falls back to the age-band-only evidence match; note reuses
//    report_service.py's NOTE_AGE_BAND_ONLY verbatim, same as
//    fixtureLegacyEstimated2_0 -- the note is keyed to match_scope alone,
//    not to why the exact band wasn't reached.
// ----------------------------------------------------------------------------
const anomalyMissingToken = '7f9b1d3c5a8e2046c8e0b2d4f6a8c0e2';
export const fixtureAnomalyMissing: ReportV2 = {
  contract_version: '2.0',
  result_kind: 'comparison',
  report_id: 'rpt_3a5c7e9f1b2d',
  report_token: anomalyMissingToken,
  share_url: `https://www.autosafe.one/app/report/${anomalyMissingToken}`,
  created_at: '2026-07-12T13:20:00Z',
  expires_at: '2026-10-10T13:20:00Z',
  registration: 'KL12MNO',
  vehicle: {
    make: 'TESTMAKE',
    model: 'ANOMALYMODEL',
    year: 2017,
    fuel_type: 'PETROL',
    colour: 'WHITE',
  },
  mot: {
    expiry_date: '2027-06-20T00:00:00',
    last_test_date: '2026-06-20T00:00:00',
    last_result: 'PASSED',
  },
  mileage: {
    effective_value: null,
    source: 'missing',
    observed_at: null,
    unit_converted: false,
    anomaly: true,
  },
  evidence: {
    match_scope: 'age_band_only',
    age_band: '6-10',
    mileage_band: null,
    total_tests: 512,
    total_failures: 140,
  },
  risk: {
    failure_risk: 0.27,
    confidence: 'Medium',
  },
  components: {
    available: true,
    items: [
      { key: 'tyres', label: 'Tyres', risk: 0.14 },
      { key: 'lamps', label: 'Lamps & Electrical', risk: 0.08 },
    ],
  },
  repair_estimate: {
    expected: 180,
    range_low: 90,
    range_high: 260,
  },
  persistence: {
    saved: true,
    share_available: true,
  },
  prediction_source: 'sqlite',
  vehicle_data_source: 'dvsa',
  note: "Exact mileage band unavailable for this vehicle's age group — showing the closest match for vehicles of a similar age.",
};

// ----------------------------------------------------------------------------
// 8. exact_band / observed_mot, km->mi converted / Medium confidence -- the
//    latest MOT recorded the odometer in kilometres (report_service.py's
//    MILES_PER_KM = 0.621371 conversion factor); 180000 km * 0.621371 =
//    111846.78, rounding to the effective_value shown here. original_value/
//    original_unit preserve the verbatim pre-conversion reading.
//    match_scope is exact_band, so -- same as fixtureObservedHighMileage --
//    the note is None (report_service.py's _NOTE_BY_SCOPE has no caveat for
//    an exact match; the note is keyed to match_scope, never to whether a
//    unit conversion happened).
// ----------------------------------------------------------------------------
const kmConvertedToken = 'b3d5f7a9c1e4062a4c6e8a0c2e4f6a80';
export const fixtureKmConverted: ReportV2 = {
  contract_version: '2.0',
  result_kind: 'comparison',
  report_id: 'rpt_6c8e0f2a4b7d',
  report_token: kmConvertedToken,
  share_url: `https://www.autosafe.one/app/report/${kmConvertedToken}`,
  created_at: '2026-07-05T09:00:00Z',
  expires_at: '2026-10-03T09:00:00Z',
  registration: 'MN34OPQ',
  vehicle: {
    make: 'TESTMAKE',
    model: 'IMPORTMODEL',
    year: 2016,
    fuel_type: 'DIESEL',
    colour: 'GREY',
  },
  mot: {
    expiry_date: '2027-07-01T00:00:00',
    last_test_date: '2026-07-01T00:00:00',
    last_result: 'PASSED',
  },
  mileage: {
    effective_value: 111847,
    source: 'observed_mot',
    observed_at: '2026-07-01T00:00:00',
    unit_converted: true,
    anomaly: false,
    original_value: 180000,
    original_unit: 'km',
  },
  evidence: {
    match_scope: 'exact_band',
    age_band: '6-10',
    mileage_band: '100k+',
    total_tests: 268,
    total_failures: 79,
  },
  risk: {
    failure_risk: 0.29,
    confidence: 'Medium',
  },
  components: {
    available: true,
    items: [
      { key: 'brakes', label: 'Brakes', risk: 0.2 },
      { key: 'steering', label: 'Steering', risk: 0.1 },
    ],
  },
  repair_estimate: {
    expected: 260,
    range_low: 140,
    range_high: 400,
  },
  persistence: {
    saved: true,
    share_available: true,
  },
  prediction_source: 'postgres',
  vehicle_data_source: 'dvsa',
  note: null,
};

// ----------------------------------------------------------------------------
// 9. One ApiErrorEnvelope example per ApiErrorCode. correlation_id values
//    mirror main.py's generate_correlation_id() shape (a 12-character
//    lowercase hex string) purely for realism -- none of these are derived
//    from a real request.
// ----------------------------------------------------------------------------
export const fixtureErrorEnvelopes: Record<ApiErrorCode, ApiErrorEnvelope> = {
  invalid_registration: {
    error_code: 'invalid_registration',
    message: 'Registration must be between 2 and 12 characters.',
    correlation_id: 'a1b2c3d4e5f6',
  },
  vehicle_not_found: {
    error_code: 'vehicle_not_found',
    message: 'No vehicle was found for that registration.',
    correlation_id: 'b2c3d4e5f6a1',
  },
  dvsa_unavailable: {
    error_code: 'dvsa_unavailable',
    message: 'The DVSA MOT history service did not respond in time.',
    correlation_id: 'c3d4e5f6a1b2',
  },
  rate_limited: {
    error_code: 'rate_limited',
    message: 'Too many requests from this client in the current window.',
    correlation_id: 'd4e5f6a1b2c3',
  },
  internal_error: {
    error_code: 'internal_error',
    message: 'An unexpected server error occurred.',
    correlation_id: 'e5f6a1b2c3d4',
  },
  report_not_found: {
    error_code: 'report_not_found',
    message: 'No report exists for that token.',
    correlation_id: 'f6a1b2c3d4e5',
  },
  report_expired: {
    error_code: 'report_expired',
    message: 'That report token has expired.',
    correlation_id: '1a2b3c4d5e6f',
  },
  storage_unavailable: {
    error_code: 'storage_unavailable',
    message: 'Report storage is temporarily unavailable.',
    correlation_id: '2b3c4d5e6f1a',
  },
  idempotency_conflict: {
    error_code: 'idempotency_conflict',
    message: 'That retry key was already used for a different report request.',
    correlation_id: '4d5e6f1a2b3c',
  },
  undeclared_parameter: {
    error_code: 'undeclared_parameter',
    message: 'The request included a field the API does not accept.',
    correlation_id: '3c4d5e6f1a2b',
  },
};
