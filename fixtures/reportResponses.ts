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

// ----------------------------------------------------------------------------
// 2. age_band_only / estimated mileage / Medium confidence -- no MOT history
//    at all for this specific vehicle (mot.* and mileage.observed_at are all
//    null), so mileage falls back to the age*8000mi/yr estimate and the
//    evidence ladder only matches on age band -- report_service.py's
//    NOTE_AGE_BAND_ONLY copy, reused verbatim.
// ----------------------------------------------------------------------------
export const fixtureAgeOnlyMedium: ReportV2 = {
  contract_version: '2.0',
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
    mileage_band: '30k-60k',
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
    age_band: '11-15',
    mileage_band: '100k+',
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
//    evidence store WAS reached (prediction_source is postgres/sqlite, not
//    unavailable) but had literally no rows for this make/model --
//    total_tests/total_failures are honestly null, never coerced to 0.
// ----------------------------------------------------------------------------
export const fixturePopulationDefault: ReportV2 = {
  contract_version: '2.0',
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
    // Mirrors utils.get_age_band(None) == 'Unknown' (a real string, not
    // null -- report_service.py always calls get_age_band, even with a
    // None age) vs. report_service.py's explicit mileage_band=None
    // short-circuit when mileage.effective_value is None.
    match_scope: 'population_default',
    age_band: 'Unknown',
    mileage_band: null,
    total_tests: null,
    total_failures: null,
  },
  risk: {
    failure_risk: 0.28,
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
  prediction_source: 'sqlite',
  vehicle_data_source: 'dvsa',
  note: "No data available for this make and model — showing the UK average across all vehicles we've checked.",
};

// ----------------------------------------------------------------------------
// 5. unavailable -- the fully degraded end-to-end failure: DVSA vehicle
//    lookup itself fell back to a demo identity, and the evidence store
//    could not be reached at all (prediction_source unavailable, not just
//    "reached but empty" -- see population_default above for that distinct
//    case). Never persisted, so report_id/report_token/share_url are all
//    honestly null rather than fabricated.
// ----------------------------------------------------------------------------
export const fixtureUnavailableDegraded: ReportV2 = {
  contract_version: '2.0',
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
    age_band: 'Unknown',
    mileage_band: null,
    total_tests: null,
    total_failures: null,
  },
  risk: {
    failure_risk: 0.28,
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
  prediction_source: 'unavailable',
  vehicle_data_source: 'demo',
  // Deliberately NOT report_service.py's NOTE_UNAVAILABLE verbatim: that
  // copy specifically says "vehicle identity confirmed", which would
  // contradict vehicle_data_source: 'demo' here. This combination (demo
  // identity AND an unreachable evidence store) isn't produced by any
  // report_service.py code path today -- it's a plausible future route-layer
  // outcome (DVSA lookup itself failed too), so its note is honest about
  // both failures rather than reusing text that assumes only one did.
  note: 'Vehicle details and comparison data are temporarily unavailable — this is a demo report showing a UK average figure.',
};

// ----------------------------------------------------------------------------
// 6. One ApiErrorEnvelope example per ApiErrorCode. correlation_id values
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
  undeclared_parameter: {
    error_code: 'undeclared_parameter',
    message: 'The request included a field the API does not accept.',
    correlation_id: '3c4d5e6f1a2b',
  },
};
