import { describe, it, expect } from 'vitest';
import {
  isApiErrorEnvelope,
  isReportV2,
  isReportMileageV2,
  isOdometerReadingV2,
  isCohortEvidenceV2,
  isRiskLookupV2,
  VALID_MATCH_SCOPES,
  VALID_MILEAGE_SOURCES,
  VALID_CONFIDENCE_LEVELS,
  VALID_PREDICTION_SOURCES,
  VALID_VEHICLE_DATA_SOURCES,
  VALID_API_ERROR_CODES,
  VALID_ODOMETER_STATUSES,
  VALID_ODOMETER_UNAVAILABLE_REASONS,
  VALID_LOOKUP_PREDICTION_SOURCES,
  VALID_COHORT_MATCH_LEVELS,
} from './reportValidation';
import {
  fixtureExactHigh,
  fixtureLegacyEstimated2_0,
  fixtureModelAverageLow,
  fixturePopulationDefault,
  fixtureUnavailableDegraded,
  fixtureObservedHighMileage,
  fixtureAnomalyMissing,
  fixtureKmConverted,
  fixtureErrorEnvelopes,
} from '../fixtures/reportResponses';
import type { ReportV2 } from '../types';

const ALL_FIXTURES: Array<[string, ReportV2]> = [
  ['fixtureExactHigh', fixtureExactHigh],
  ['fixtureLegacyEstimated2_0', fixtureLegacyEstimated2_0],
  ['fixtureModelAverageLow', fixtureModelAverageLow],
  ['fixturePopulationDefault', fixturePopulationDefault],
  ['fixtureUnavailableDegraded', fixtureUnavailableDegraded],
  ['fixtureObservedHighMileage', fixtureObservedHighMileage],
  ['fixtureAnomalyMissing', fixtureAnomalyMissing],
  ['fixtureKmConverted', fixtureKmConverted],
];

describe('isReportV2', () => {
  for (const [name, fixture] of ALL_FIXTURES) {
    it(`accepts ${name}`, () => {
      expect(isReportV2(fixture)).toBe(true);
    });
  }

  it('rejects a non-object (null, array, primitive)', () => {
    expect(isReportV2(null)).toBe(false);
    expect(isReportV2(undefined)).toBe(false);
    expect(isReportV2('a string')).toBe(false);
    expect(isReportV2([])).toBe(false);
  });

  it('rejects total_tests given as a string instead of a number', () => {
    const mutated = {
      ...fixtureExactHigh,
      evidence: { ...fixtureExactHigh.evidence, total_tests: '5' },
    };
    expect(isReportV2(mutated)).toBe(false);
  });

  it('rejects a report missing the evidence object entirely', () => {
    const { evidence: _omittedEvidence, ...withoutEvidence } = fixtureExactHigh;
    expect(isReportV2(withoutEvidence)).toBe(false);
  });

  it('rejects an unrecognised match_scope enum value', () => {
    const mutated = {
      ...fixtureExactHigh,
      evidence: { ...fixtureExactHigh.evidence, match_scope: 'bogus_scope' },
    };
    expect(isReportV2(mutated)).toBe(false);
  });

  it('rejects other unrecognised enum values (mileage source, confidence, prediction source, vehicle data source)', () => {
    expect(
      isReportV2({
        ...fixtureExactHigh,
        mileage: { ...fixtureExactHigh.mileage, source: 'bogus_source' },
      })
    ).toBe(false);
    expect(
      isReportV2({
        ...fixtureExactHigh,
        risk: { ...fixtureExactHigh.risk, confidence: 'Extremely High' },
      })
    ).toBe(false);
    expect(isReportV2({ ...fixtureExactHigh, prediction_source: 'mysql' })).toBe(false);
    expect(isReportV2({ ...fixtureExactHigh, vehicle_data_source: 'mock' })).toBe(false);
  });

  it('null/undefined discipline: effective_value accepts null but rejects undefined', () => {
    const withNull = {
      ...fixturePopulationDefault,
      mileage: { ...fixturePopulationDefault.mileage, effective_value: null },
    };
    expect(isReportV2(withNull)).toBe(true);

    const withUndefined = {
      ...fixturePopulationDefault,
      mileage: { ...fixturePopulationDefault.mileage, effective_value: undefined },
    };
    expect(isReportV2(withUndefined)).toBe(false);
  });

  it('requires mileage value and source to agree', () => {
    expect(isReportV2({
      ...fixturePopulationDefault,
      mileage: { ...fixturePopulationDefault.mileage, effective_value: 50000 },
    })).toBe(false);
    expect(isReportV2({
      ...fixtureExactHigh,
      mileage: { ...fixtureExactHigh.mileage, effective_value: null },
    })).toBe(false);
  });

  it('requires the exact supported contract version', () => {
    expect(isReportV2({ ...fixtureExactHigh, contract_version: '2.1' })).toBe(false);
  });

  it('null/undefined discipline: total_tests accepts null but rejects undefined', () => {
    const withNull = {
      ...fixturePopulationDefault,
      evidence: { ...fixturePopulationDefault.evidence, total_tests: null },
    };
    expect(isReportV2(withNull)).toBe(true);

    const withUndefined = {
      ...fixturePopulationDefault,
      evidence: { ...fixturePopulationDefault.evidence, total_tests: undefined },
    };
    expect(isReportV2(withUndefined)).toBe(false);
  });

  it('tolerates unknown extra keys on input (forward compatibility)', () => {
    const withExtra = { ...fixtureExactHigh, some_future_field: 'unrecognised but harmless' };
    expect(isReportV2(withExtra)).toBe(true);
  });

  it('rejects probability values outside 0..1', () => {
    expect(
      isReportV2({
        ...fixtureExactHigh,
        risk: { ...fixtureExactHigh.risk, failure_risk: 1.01 },
      })
    ).toBe(false);
    expect(
      isReportV2({
        ...fixtureExactHigh,
        components: {
          available: true,
          items: [{ key: 'brakes', label: 'Brakes', risk: -0.01 }],
        },
      })
    ).toBe(false);
  });

  it('rejects impossible evidence counts', () => {
    for (const evidence of [
      { ...fixtureExactHigh.evidence, total_tests: -1 },
      { ...fixtureExactHigh.evidence, total_tests: 0, total_failures: null },
      { ...fixtureExactHigh.evidence, total_tests: 1.5 },
      { ...fixtureExactHigh.evidence, total_tests: 10, total_failures: 11 },
      { ...fixtureExactHigh.evidence, total_tests: null, total_failures: 1 },
    ]) {
      expect(isReportV2({ ...fixtureExactHigh, evidence })).toBe(false);
    }
  });

  it('requires evidence bands to describe only the matched scope', () => {
    expect(isReportV2({
      ...fixtureExactHigh,
      evidence: { ...fixtureExactHigh.evidence, mileage_band: null },
    })).toBe(false);
    expect(isReportV2({
      ...fixtureLegacyEstimated2_0,
      evidence: { ...fixtureLegacyEstimated2_0.evidence, mileage_band: '30k-60k' },
    })).toBe(false);
    expect(isReportV2({
      ...fixtureModelAverageLow,
      evidence: { ...fixtureModelAverageLow.evidence, age_band: '11-15' },
    })).toBe(false);
    expect(isReportV2({
      ...fixtureUnavailableDegraded,
      evidence: { ...fixtureUnavailableDegraded.evidence, age_band: 'Unknown' },
    })).toBe(false);
  });

  it('requires component availability to agree with component items', () => {
    expect(
      isReportV2({
        ...fixtureExactHigh,
        components: { available: false, items: fixtureExactHigh.components.items },
      })
    ).toBe(false);
    expect(
      isReportV2({
        ...fixtureExactHigh,
        components: { available: true, items: null },
      })
    ).toBe(false);
  });

  it('requires a shareable persistence state to have all bearer-link fields', () => {
    expect(
      isReportV2({
        ...fixtureExactHigh,
        report_token: null,
      })
    ).toBe(false);
    expect(
      isReportV2({
        ...fixtureUnavailableDegraded,
        persistence: { saved: false, share_available: true },
      })
    ).toBe(false);
  });

  it('rejects an expiry on an unsaved report with no durable identity', () => {
    expect(
      isReportV2({
        ...fixtureUnavailableDegraded,
        expires_at: '2026-10-09T00:00:00Z',
      })
    ).toBe(false);
  });

  it('rejects inverted or negative repair ranges', () => {
    expect(
      isReportV2({
        ...fixtureExactHigh,
        repair_estimate: { expected: 100, range_low: 200, range_high: 50 },
      })
    ).toBe(false);
  });

  it('rejects a repair estimate when component evidence is unavailable', () => {
    expect(
      isReportV2({
        ...fixtureUnavailableDegraded,
        repair_estimate: { expected: 200, range_low: 100, range_high: 300 },
      })
    ).toBe(false);
  });

  it('accepts the optional repair_estimate / components.items / mileage.observed_at when the key is entirely absent', () => {
    const { repair_estimate: _omittedRepair, ...withoutRepairEstimate } = fixtureExactHigh;
    expect(isReportV2(withoutRepairEstimate)).toBe(true);
  });
});

describe('isApiErrorEnvelope', () => {
  for (const code of VALID_API_ERROR_CODES) {
    it(`accepts the ${code} fixture envelope`, () => {
      expect(isApiErrorEnvelope(fixtureErrorEnvelopes[code])).toBe(true);
    });
  }

  it('rejects a non-object (null, array, primitive)', () => {
    expect(isApiErrorEnvelope(null)).toBe(false);
    expect(isApiErrorEnvelope(undefined)).toBe(false);
    expect(isApiErrorEnvelope('nope')).toBe(false);
  });

  it('rejects an envelope with an error_code outside the known set', () => {
    expect(isApiErrorEnvelope({ error_code: 'not_a_real_code', message: 'x', correlation_id: 'y' })).toBe(
      false
    );
  });

  it('rejects an envelope missing message', () => {
    const { message: _omittedMessage, ...rest } = fixtureErrorEnvelopes.rate_limited;
    expect(isApiErrorEnvelope(rest)).toBe(false);
  });

  it('rejects an envelope missing correlation_id', () => {
    const { correlation_id: _omittedCorrelationId, ...rest } = fixtureErrorEnvelopes.rate_limited;
    expect(isApiErrorEnvelope(rest)).toBe(false);
  });

  it('rejects a legacy-style {detail} body (not an envelope at all)', () => {
    expect(isApiErrorEnvelope({ detail: 'Internal Server Error' })).toBe(false);
  });
});

describe('enum membership consts', () => {
  it('are non-empty and cover exactly the types.ts unions', () => {
    expect(VALID_MATCH_SCOPES).toEqual([
      'exact_band',
      'age_band_only',
      'model_average',
      'population_default',
      'unavailable',
    ]);
    expect(VALID_MILEAGE_SOURCES).toEqual(['user_entered', 'observed_mot', 'estimated', 'missing']);
    expect(VALID_CONFIDENCE_LEVELS).toEqual(['High', 'Medium', 'Low', 'Very Low']);
    expect(VALID_PREDICTION_SOURCES).toEqual([
      'postgres',
      'sqlite',
      'dataset_reference',
      'unavailable',
    ]);
    expect(VALID_VEHICLE_DATA_SOURCES).toEqual(['dvsa', 'demo']);
    expect(VALID_API_ERROR_CODES).toHaveLength(10);
    expect(VALID_API_ERROR_CODES).toContain('undeclared_parameter');
  });

  it('cover exactly the new odometer/cohort/lookup unions, byte-for-byte against report_contract.py', () => {
    expect(VALID_ODOMETER_STATUSES).toEqual(['available', 'unavailable']);
    expect(VALID_ODOMETER_UNAVAILABLE_REASONS).toEqual([
      'no_reading',
      'rollback',
      'implausible_increase',
      'unknown_unit',
    ]);
    expect(VALID_LOOKUP_PREDICTION_SOURCES).toEqual([
      'population_exact',
      'population_broad',
      'population_global',
      'unavailable',
    ]);
    expect(VALID_COHORT_MATCH_LEVELS).toEqual([
      'exact_band',
      'age_band_only',
      'model_average',
      'dataset',
    ]);
  });
});

// ----------------------------------------------------------------------------
// Release 1 additive tolerance: old persisted 2.0 payloads (recorded before
// original_value/original_unit existed) must still pass. See
// fixtures/reportResponses.ts's fixtureLegacyEstimated2_0 -- its whole
// purpose is proving this in both directions: an old-shaped payload is
// accepted, and a payload that DOES carry the new fields is held to the
// same honesty rules as the backend's validate_source_value_consistency.
// ----------------------------------------------------------------------------
describe('isReportMileageV2 additive original_value/original_unit tolerance', () => {
  it('accepts an old persisted-payload mileage object with no original_value/original_unit keys at all', () => {
    expect('original_value' in fixtureLegacyEstimated2_0.mileage).toBe(false);
    expect('original_unit' in fixtureLegacyEstimated2_0.mileage).toBe(false);
    expect(isReportMileageV2(fixtureLegacyEstimated2_0.mileage)).toBe(true);
  });

  it('accepts a fresh payload that does carry a valid original_value/original_unit pair', () => {
    expect(isReportMileageV2(fixtureKmConverted.mileage)).toBe(true);
  });

  it('rejects a junk original_unit when unit_converted is true', () => {
    const junk = {
      ...fixtureExactHigh.mileage,
      unit_converted: true,
      original_value: 100000,
      original_unit: 'furlongs',
    };
    expect(isReportMileageV2(junk)).toBe(false);
  });

  it('tolerates original_unit being explicitly null even when unit_converted is true (honest-unknown, not a fabricated km)', () => {
    const legacyShaped = {
      ...fixtureExactHigh.mileage,
      unit_converted: true,
      original_value: null,
      original_unit: null,
    };
    expect(isReportMileageV2(legacyShaped)).toBe(true);
  });

  it('rejects a negative original_value', () => {
    const junk = { ...fixtureExactHigh.mileage, original_value: -1 };
    expect(isReportMileageV2(junk)).toBe(false);
  });
});

describe('isOdometerReadingV2', () => {
  const validAvailable = {
    value_miles: 62411,
    recorded_at: '2026-04-18T00:00:00',
    original_value: 62411,
    original_unit: 'mi',
    source: 'observed_mot',
    status: 'available',
    unavailable_reason: null,
  };

  const validUnavailable = {
    value_miles: null,
    recorded_at: null,
    original_value: null,
    original_unit: null,
    source: null,
    status: 'unavailable',
    unavailable_reason: 'no_reading',
  };

  it('accepts a valid available reading', () => {
    expect(isOdometerReadingV2(validAvailable)).toBe(true);
  });

  it('accepts a valid unavailable reading for each unavailable_reason', () => {
    for (const reason of VALID_ODOMETER_UNAVAILABLE_REASONS) {
      expect(isOdometerReadingV2({ ...validUnavailable, unavailable_reason: reason })).toBe(true);
    }
  });

  it('rejects a non-object (null, array, primitive)', () => {
    expect(isOdometerReadingV2(null)).toBe(false);
    expect(isOdometerReadingV2(undefined)).toBe(false);
    expect(isOdometerReadingV2('a string')).toBe(false);
  });

  it('rejects an available reading missing one of the required detail fields (fabrication guard)', () => {
    expect(isOdometerReadingV2({ ...validAvailable, recorded_at: null })).toBe(false);
    expect(isOdometerReadingV2({ ...validAvailable, original_value: null })).toBe(false);
    expect(isOdometerReadingV2({ ...validAvailable, original_unit: null })).toBe(false);
    expect(isOdometerReadingV2({ ...validAvailable, value_miles: null })).toBe(false);
  });

  it('rejects an available reading sourced from anything other than an observed MOT reading', () => {
    expect(isOdometerReadingV2({ ...validAvailable, source: 'estimated' })).toBe(false);
  });

  it('rejects an available reading that also carries an unavailable_reason', () => {
    expect(isOdometerReadingV2({ ...validAvailable, unavailable_reason: 'no_reading' })).toBe(false);
  });

  it('rejects an unavailable reading that fabricates a detail field instead of leaving it null', () => {
    expect(isOdometerReadingV2({ ...validUnavailable, value_miles: 62411 })).toBe(false);
    expect(isOdometerReadingV2({ ...validUnavailable, source: 'observed_mot' })).toBe(false);
  });

  it('rejects an unavailable reading missing unavailable_reason', () => {
    expect(isOdometerReadingV2({ ...validUnavailable, unavailable_reason: null })).toBe(false);
  });

  it('rejects an unrecognised status or unavailable_reason enum value', () => {
    expect(isOdometerReadingV2({ ...validAvailable, status: 'pending' })).toBe(false);
    expect(isOdometerReadingV2({ ...validUnavailable, unavailable_reason: 'odometer_replaced' })).toBe(false);
  });
});

describe('isCohortEvidenceV2', () => {
  const validCohort = {
    match_level: 'exact_band',
    age_band: '3-5',
    mileage_band: '60k-100k',
    total_tests: 1842,
    total_failures: 221,
  };

  it('accepts a valid cohort', () => {
    expect(isCohortEvidenceV2(validCohort)).toBe(true);
  });

  it('accepts a valid cohort with total_failures null (failure count itself unknown)', () => {
    expect(isCohortEvidenceV2({ ...validCohort, total_failures: null })).toBe(true);
  });

  it('rejects total_tests: 0 (zero tests are not evidence)', () => {
    expect(isCohortEvidenceV2({ ...validCohort, total_tests: 0 })).toBe(false);
  });

  it('rejects a negative total_tests', () => {
    expect(isCohortEvidenceV2({ ...validCohort, total_tests: -5 })).toBe(false);
  });

  it('rejects a non-integer total_tests', () => {
    expect(isCohortEvidenceV2({ ...validCohort, total_tests: 12.5 })).toBe(false);
  });

  it('rejects an unrecognised match_level enum value', () => {
    expect(isCohortEvidenceV2({ ...validCohort, match_level: 'bogus_level' })).toBe(false);
  });

  it('rejects a non-object (null, array, primitive)', () => {
    expect(isCohortEvidenceV2(null)).toBe(false);
    expect(isCohortEvidenceV2(undefined)).toBe(false);
    expect(isCohortEvidenceV2([])).toBe(false);
  });
});

describe('isRiskLookupV2', () => {
  const exactCohort = {
    match_level: 'exact_band',
    age_band: '3-5',
    mileage_band: '60k-100k',
    total_tests: 1842,
    total_failures: 221,
  };
  const ageOnlyCohort = {
    match_level: 'age_band_only',
    age_band: '6-10',
    mileage_band: null,
    total_tests: 340,
    total_failures: 65,
  };
  const modelAverageCohort = {
    match_level: 'model_average',
    age_band: null,
    mileage_band: null,
    total_tests: 47,
    total_failures: 17,
  };
  const datasetCohort = {
    match_level: 'dataset',
    age_band: null,
    mileage_band: null,
    total_tests: 148_509_908,
    total_failures: 39_969_903,
  };

  const validPopulationExact = {
    prediction_source: 'population_exact',
    failure_risk: 0.12,
    cohort: exactCohort,
    vehicle: 'TESTMAKE TESTMODEL',
    year: 2021,
    mileage: 62411,
    confidence_level: 'High',
    risk_brakes: 0.18,
    risk_suspension: 0.07,
    risk_tyres: 0.09,
    risk_steering: null,
    risk_visibility: null,
    risk_lamps: null,
    risk_body: null,
    repair_cost_estimate: null,
    note: null,
  };

  const validPopulationBroadAgeOnly = {
    ...validPopulationExact,
    prediction_source: 'population_broad',
    failure_risk: 0.19,
    cohort: ageOnlyCohort,
    confidence_level: 'Medium',
  };

  const validPopulationBroadModelAverage = {
    ...validPopulationExact,
    prediction_source: 'population_broad',
    failure_risk: 0.36,
    cohort: modelAverageCohort,
    confidence_level: 'Low',
  };

  const validPopulationGlobal = {
    ...validPopulationExact,
    prediction_source: 'population_global',
    failure_risk: 39_969_903 / 148_509_908,
    cohort: datasetCohort,
    confidence_level: 'Very Low',
    risk_brakes: null,
    risk_suspension: null,
    risk_tyres: null,
  };

  const validUnavailable = {
    prediction_source: 'unavailable',
    failure_risk: null,
    cohort: null,
    vehicle: 'DEMO VEHICLE',
    year: 2020,
    mileage: null,
    confidence_level: null,
    risk_brakes: null,
    risk_suspension: null,
    risk_tyres: null,
    risk_steering: null,
    risk_visibility: null,
    risk_lamps: null,
    risk_body: null,
    note: null,
  };

  it('accepts one valid object per available tier, and the unavailable variant', () => {
    expect(isRiskLookupV2(validPopulationExact)).toBe(true);
    expect(isRiskLookupV2(validPopulationBroadAgeOnly)).toBe(true);
    expect(isRiskLookupV2(validPopulationBroadModelAverage)).toBe(true);
    expect(isRiskLookupV2(validPopulationGlobal)).toBe(true);
    expect(isRiskLookupV2(validUnavailable)).toBe(true);
  });

  it('rejects a non-object (null, array, primitive)', () => {
    expect(isRiskLookupV2(null)).toBe(false);
    expect(isRiskLookupV2(undefined)).toBe(false);
    expect(isRiskLookupV2('nope')).toBe(false);
  });

  it('rejects an object missing prediction_source entirely (legacy 0.28-shaped junk object)', () => {
    const legacyJunk = {
      vehicle: 'TESTMAKE TESTMODEL',
      year: 2021,
      mileage: 62411,
      failure_risk: 0.28,
      confidence_level: 'High',
      risk_brakes: 0.18,
      risk_suspension: 0.07,
      risk_tyres: 0.09,
      risk_steering: null,
      risk_visibility: null,
      risk_lamps: null,
      risk_body: null,
      repair_cost_estimate: null,
    };
    expect(isRiskLookupV2(legacyJunk)).toBe(false);
  });

  it('rejects population_global with a non-dataset match_level', () => {
    expect(isRiskLookupV2({ ...validPopulationGlobal, cohort: exactCohort })).toBe(false);
  });

  it('rejects an available variant with failure_risk: null', () => {
    expect(isRiskLookupV2({ ...validPopulationExact, failure_risk: null })).toBe(false);
  });

  it('rejects a cohort with total_tests: 0', () => {
    expect(isRiskLookupV2({ ...validPopulationExact, cohort: { ...exactCohort, total_tests: 0 } })).toBe(false);
  });

  it('rejects population_exact with only one matched band known', () => {
    expect(
      isRiskLookupV2({ ...validPopulationExact, cohort: { ...exactCohort, mileage_band: null } })
    ).toBe(false);
    expect(
      isRiskLookupV2({ ...validPopulationExact, cohort: { ...exactCohort, age_band: null } })
    ).toBe(false);
  });

  it('rejects population_broad with a dataset-tier cohort', () => {
    expect(isRiskLookupV2({ ...validPopulationBroadAgeOnly, cohort: datasetCohort })).toBe(false);
  });

  it('rejects an unavailable variant that fabricates a failure_risk or cohort instead of leaving them null', () => {
    expect(isRiskLookupV2({ ...validUnavailable, failure_risk: 0.1 })).toBe(false);
    expect(isRiskLookupV2({ ...validUnavailable, cohort: exactCohort })).toBe(false);
  });

  it('rejects an unavailable variant that fabricates a confidence_level or component risk', () => {
    expect(isRiskLookupV2({ ...validUnavailable, confidence_level: 'High' })).toBe(false);
    expect(isRiskLookupV2({ ...validUnavailable, risk_brakes: 0.1 })).toBe(false);
  });

  it('rejects an unrecognised prediction_source enum value', () => {
    expect(isRiskLookupV2({ ...validPopulationExact, prediction_source: 'population_regional' })).toBe(false);
  });
});
