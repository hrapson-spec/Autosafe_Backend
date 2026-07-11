import { describe, it, expect } from 'vitest';
import {
  isApiErrorEnvelope,
  isReportV2,
  VALID_MATCH_SCOPES,
  VALID_MILEAGE_SOURCES,
  VALID_CONFIDENCE_LEVELS,
  VALID_PREDICTION_SOURCES,
  VALID_VEHICLE_DATA_SOURCES,
  VALID_API_ERROR_CODES,
} from './reportValidation';
import {
  fixtureExactHigh,
  fixtureAgeOnlyMedium,
  fixtureModelAverageLow,
  fixturePopulationDefault,
  fixtureUnavailableDegraded,
  fixtureErrorEnvelopes,
} from '../fixtures/reportResponses';
import type { ReportV2 } from '../types';

const ALL_FIXTURES: Array<[string, ReportV2]> = [
  ['fixtureExactHigh', fixtureExactHigh],
  ['fixtureAgeOnlyMedium', fixtureAgeOnlyMedium],
  ['fixtureModelAverageLow', fixtureModelAverageLow],
  ['fixturePopulationDefault', fixturePopulationDefault],
  ['fixtureUnavailableDegraded', fixtureUnavailableDegraded],
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
    expect(VALID_PREDICTION_SOURCES).toEqual(['postgres', 'sqlite', 'unavailable']);
    expect(VALID_VEHICLE_DATA_SOURCES).toEqual(['dvsa', 'demo']);
    expect(VALID_API_ERROR_CODES).toHaveLength(9);
    expect(VALID_API_ERROR_CODES).toContain('undeclared_parameter');
  });
});
