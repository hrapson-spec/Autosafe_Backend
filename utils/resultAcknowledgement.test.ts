import { describe, expect, it } from 'vitest';
import type { MatchScope, ReportV2, ResultKind } from '../types';
import {
  fixtureAnomalyMissing,
  fixtureExactHigh,
  fixtureKmConverted,
  fixtureLegacyEstimated2_0,
  fixtureModelAverageLow,
  fixtureObservedHighMileage,
  fixturePopulationDefault,
  fixtureUnavailableDegraded,
  fixtureVehiclePrediction,
} from '../fixtures/reportResponses';
import { classifyResult } from './resultAcknowledgement';

function withRisk(base: ReportV2, failure_risk: unknown): ReportV2 {
  return { ...base, risk: { ...base.risk, failure_risk: failure_risk as number } };
}

function withCounts(base: ReportV2, total_tests: unknown, total_failures: unknown): ReportV2 {
  return {
    ...base,
    evidence: { ...base.evidence, total_tests: total_tests as number | null, total_failures: total_failures as number | null },
  };
}

function withKindScope(base: ReportV2, result_kind: ResultKind, match_scope: MatchScope): ReportV2 {
  return { ...base, result_kind, evidence: { ...base.evidence, match_scope } };
}

describe('classifyResult: MEASUREMENT.md table rows', () => {
  it('row 1: valid vehicle_prediction / model_prediction => prediction, supported, no sample_nonzero key', () => {
    const c = classifyResult(fixtureVehiclePrediction);
    expect(c).toEqual({
      render_delivered: true,
      supported_result: true,
      outcome_group: 'prediction',
      rate_valid: true,
      scope_visible: true,
      result_kind: 'vehicle_prediction',
      match_scope: 'model_prediction',
    });
    expect('sample_nonzero' in c).toBe(false);
  });

  it('row 2: valid comparison / exact_band => exact_comparison, supported', () => {
    expect(classifyResult(fixtureExactHigh)).toEqual({
      render_delivered: true,
      supported_result: true,
      outcome_group: 'exact_comparison',
      rate_valid: true,
      sample_nonzero: true,
      scope_visible: true,
      result_kind: 'comparison',
      match_scope: 'exact_band',
    });
  });

  it.each([
    ['age_band_only', fixtureLegacyEstimated2_0],
    ['model_average', fixtureModelAverageLow],
  ] as const)('row 3: comparison / %s => broader_supported_comparison, supported', (scope, fixture) => {
    const c = classifyResult(fixture);
    expect(c.match_scope).toBe(scope);
    expect(c.outcome_group).toBe('broader_supported_comparison');
    expect(c.render_delivered).toBe(true);
    expect(c.supported_result).toBe(true);
    expect(c.sample_nonzero).toBe(true);
  });

  it('row 4: population_default => dataset_reference, delivered, NOT supported', () => {
    const c = classifyResult(fixturePopulationDefault);
    expect(c.outcome_group).toBe('dataset_reference');
    expect(c.render_delivered).toBe(true);
    expect(c.supported_result).toBe(false);
    expect(c.rate_valid).toBe(true);
    // total_tests is honestly null for the reference; null is not a nonzero sample.
    expect(c.sample_nonzero).toBe(false);
  });

  it('row 5: unavailable scope (real vehicle data) => unavailable group, NOT supported', () => {
    const c = classifyResult({ ...fixtureUnavailableDegraded, vehicle_data_source: 'dvsa' });
    expect(c.outcome_group).toBe('unavailable');
    expect(c.render_delivered).toBe(true);
    expect(c.supported_result).toBe(false);
    expect(c.match_scope).toBe('unavailable');
  });

  it('row 6: invalid contract => error, not delivered, not supported', () => {
    const c = classifyResult(withKindScope(fixtureVehiclePrediction, 'comparison', 'model_prediction'));
    expect(c.render_delivered).toBe(false);
    expect(c.supported_result).toBe(false);
    expect(c.outcome_group).toBe('error');
  });

  it.each([
    ['fixtureExactHigh', fixtureExactHigh],
    ['fixtureObservedHighMileage', fixtureObservedHighMileage],
    ['fixtureKmConverted', fixtureKmConverted],
    ['fixtureAnomalyMissing', fixtureAnomalyMissing],
  ])('other valid exact/age fixtures stay supported: %s', (_n, fixture) => {
    expect(classifyResult(fixture).supported_result).toBe(true);
  });
});

describe('classifyResult: headline-rate validity', () => {
  it.each([
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
    ['-Infinity', Number.NEGATIVE_INFINITY],
    ['above 1', 1.0001],
    ['negative', -0.0001],
    ['null', null],
    ['undefined', undefined],
    ['string', '0.2'],
  ])('%s risk is never supported and never delivered', (_n, bad) => {
    for (const base of [fixtureExactHigh, fixtureVehiclePrediction, fixtureLegacyEstimated2_0, fixturePopulationDefault]) {
      const c = classifyResult(withRisk(base, bad));
      expect(c.rate_valid).toBe(false);
      expect(c.supported_result).toBe(false);
      expect(c.render_delivered).toBe(false);
      expect(c.outcome_group).toBe('error');
    }
  });

  it('accepts the closed interval endpoints 0 and 1', () => {
    expect(classifyResult(withRisk(fixtureExactHigh, 0)).supported_result).toBe(true);
    expect(classifyResult(withRisk(fixtureExactHigh, 1)).supported_result).toBe(true);
    expect(classifyResult(withRisk(fixtureVehiclePrediction, 0)).supported_result).toBe(true);
  });

  it('a missing risk block does not throw', () => {
    const broken = { ...fixtureExactHigh, risk: undefined } as unknown as ReportV2;
    expect(classifyResult(broken).supported_result).toBe(false);
  });
});

describe('classifyResult: comparison sample rules', () => {
  it.each([
    ['0', 0, null],
    ['null', null, null],
    ['non-integer', 10.5, null],
    ['negative', -5, null],
    ['NaN', Number.NaN, null],
    ['string', '100', null],
    ['failures above tests', 100, 101],
    ['negative failures', 100, -1],
    ['non-integer failures', 100, 1.5],
    ['failures without tests', null, 3],
  ])('exact_band with total_tests %s / failures is not supported', (_n, tests, failures) => {
    const c = classifyResult(withCounts(fixtureExactHigh, tests, failures));
    expect(c.supported_result).toBe(false);
    expect(c.render_delivered).toBe(false);
    expect(c.outcome_group).toBe('error');
    expect(c.sample_nonzero).toBe(false);
  });

  it('accepts null failure count and failures equal to tests', () => {
    expect(classifyResult(withCounts(fixtureExactHigh, 50, null)).supported_result).toBe(true);
    expect(classifyResult(withCounts(fixtureExactHigh, 50, 50)).supported_result).toBe(true);
    expect(classifyResult(withCounts(fixtureExactHigh, 50, 0)).supported_result).toBe(true);
  });

  it('reference and unavailable scopes are never supported even when they carry a valid sample and rate', () => {
    for (const base of [fixturePopulationDefault, fixtureUnavailableDegraded]) {
      const c = classifyResult(withCounts(base, 1000, 100));
      expect(c.sample_nonzero).toBe(true);
      expect(c.rate_valid).toBe(true);
      expect(c.render_delivered).toBe(true);
      expect(c.supported_result).toBe(false);
    }
  });

  it('sample_nonzero reports false for a reference scope with zero tests but the reference is still never supported', () => {
    const c = classifyResult(withCounts(fixturePopulationDefault, 0, null));
    expect(c.supported_result).toBe(false);
    expect(c.sample_nonzero).toBe(false);
  });

  it('a prediction ignores sample counts entirely and carrying cohort counts is contract-invalid', () => {
    const c = classifyResult(withCounts(fixtureVehiclePrediction, 10, 2));
    expect('sample_nonzero' in c).toBe(false);
    expect(c.supported_result).toBe(false);
    expect(c.outcome_group).toBe('error');
  });
});

describe('classifyResult: contract-invalid combinations are never supported', () => {
  const kinds: ResultKind[] = ['comparison', 'vehicle_prediction'];
  const scopes: MatchScope[] = ['exact_band', 'age_band_only', 'model_average', 'population_default', 'unavailable', 'model_prediction'];
  it('exhaustive kind x scope matrix: only the contract-consistent pairs deliver', () => {
    for (const kind of kinds) {
      for (const scope of scopes) {
        const predKind = kind === 'vehicle_prediction';
        const predScope = scope === 'model_prediction';
        // Use a valid comparison base and consistent counts/source for the pair under test.
        const base = predScope ? fixtureVehiclePrediction : fixtureExactHigh;
        const report: ReportV2 = {
          ...withKindScope(base, kind, scope),
          prediction_source: predKind ? 'model_v55' : 'postgres',
          evidence: {
            ...base.evidence,
            match_scope: scope,
            total_tests: predScope ? null : base.evidence.total_tests ?? 100,
            total_failures: predScope ? null : base.evidence.total_failures ?? 10,
          },
        };
        const c = classifyResult(report);
        if (predKind !== predScope) {
          expect(c.render_delivered, `${kind}/${scope}`).toBe(false);
          expect(c.supported_result, `${kind}/${scope}`).toBe(false);
          expect(c.outcome_group, `${kind}/${scope}`).toBe('error');
        } else {
          expect(c.render_delivered, `${kind}/${scope}`).toBe(true);
        }
      }
    }
  });

  it('model_v55 prediction_source on a comparison, or another source on a prediction, is invalid', () => {
    expect(classifyResult({ ...fixtureExactHigh, prediction_source: 'model_v55' }).outcome_group).toBe('error');
    expect(classifyResult({ ...fixtureVehiclePrediction, prediction_source: 'postgres' }).outcome_group).toBe('error');
  });

  it('unknown enum values are errors with null typed fields', () => {
    const bad = { ...fixtureExactHigh, result_kind: 'broad_fallback' } as unknown as ReportV2;
    const c = classifyResult(bad);
    expect(c.result_kind).toBeNull();
    expect(c.supported_result).toBe(false);
    expect(c.outcome_group).toBe('error');
    const badScope = { ...fixtureExactHigh, evidence: { ...fixtureExactHigh.evidence, match_scope: 'nope' } } as unknown as ReportV2;
    expect(classifyResult(badScope).match_scope).toBeNull();
    expect(classifyResult(badScope).supported_result).toBe(false);
  });

  it('null / undefined / non-object reports are errors, not exceptions', () => {
    for (const v of [null, undefined, 'x' as unknown as ReportV2]) {
      const c = classifyResult(v);
      expect(c.supported_result).toBe(false);
      expect(c.render_delivered).toBe(false);
    }
  });
});

describe('classifyResult: scope_visible', () => {
  it('requires make and model for vehicle-matched cohort scopes (their label names the cohort)', () => {
    const noMake = { ...fixtureExactHigh, vehicle: { ...fixtureExactHigh.vehicle, make: '  ' } };
    const c = classifyResult(noMake);
    expect(c.scope_visible).toBe(false);
    expect(c.supported_result).toBe(false);
    expect(c.render_delivered).toBe(true);
  });

  it('a prediction does not depend on make/model for its scope label', () => {
    const noMake = { ...fixtureVehiclePrediction, vehicle: { ...fixtureVehiclePrediction.vehicle, make: '' } };
    expect(classifyResult(noMake).scope_visible).toBe(true);
  });
});

describe('classifyResult: no numeric leakage', () => {
  it('the classification carries no risk value or sample count', () => {
    for (const fixture of [fixtureExactHigh, fixtureVehiclePrediction, fixtureModelAverageLow]) {
      const json = JSON.stringify(classifyResult(fixture));
      expect(json).not.toContain(String(fixture.risk.failure_risk));
      if (fixture.evidence.total_tests !== null) expect(json).not.toContain(String(fixture.evidence.total_tests));
      for (const value of Object.values(classifyResult(fixture))) {
        expect(typeof value === 'boolean' || typeof value === 'string' || value === null).toBe(true);
      }
    }
  });
});

describe('classifyResult: demo data (DECISIONS.md D-004)', () => {
  const asDemo = (r: ReportV2): ReportV2 => ({ ...r, vehicle_data_source: 'demo' });
  const asReal = (r: ReportV2): ReportV2 => ({ ...r, vehicle_data_source: 'dvsa' });

  it.each([
    ['exact_band', fixtureExactHigh],
    ['age_band_only', fixtureLegacyEstimated2_0],
    ['model_average', fixtureModelAverageLow],
    ['population_default', fixturePopulationDefault],
    ['unavailable', fixtureUnavailableDegraded],
    ['model_prediction (demo prediction)', fixtureVehiclePrediction],
  ])('demo x %s => delivered, never supported, group demo', (_n, fixture) => {
    const demo = classifyResult(asDemo(fixture));
    expect(demo.render_delivered).toBe(true);
    expect(demo.supported_result).toBe(false);
    expect(demo.outcome_group).toBe('demo');
    expect(demo.rate_valid).toBe(true);
    // Typed scope/kind are still reported alongside the group.
    expect(demo.match_scope).toBe(fixture.evidence.match_scope);
    expect(demo.result_kind).toBe(fixture.result_kind);
    // sample_nonzero stays omitted for predictions and present for comparisons.
    expect('sample_nonzero' in demo).toBe(fixture.result_kind === 'comparison');
  });

  it('the same states from real data keep their normal groups (demo is the only difference)', () => {
    expect(classifyResult(asReal(fixtureExactHigh)).outcome_group).toBe('exact_comparison');
    expect(classifyResult(asReal(fixtureExactHigh)).supported_result).toBe(true);
    expect(classifyResult(asReal(fixtureVehiclePrediction)).outcome_group).toBe('prediction');
    expect(classifyResult(asReal(fixtureUnavailableDegraded)).outcome_group).toBe('unavailable');
  });

  it('a demo report that is also contract-invalid stays error / not delivered', () => {
    const c = classifyResult(asDemo(withRisk(fixtureExactHigh, Number.NaN)));
    expect(c.outcome_group).toBe('error');
    expect(c.render_delivered).toBe(false);
    expect(c.supported_result).toBe(false);
    expect(classifyResult(asDemo(withKindScope(fixtureVehiclePrediction, 'comparison', 'model_prediction'))).outcome_group).toBe('error');
  });

  it('demo is never supported across the whole kind x scope matrix, even with valid samples', () => {
    for (const fixture of [fixtureExactHigh, fixtureVehiclePrediction, fixturePopulationDefault, fixtureModelAverageLow]) {
      expect(classifyResult(asDemo(withRisk(fixture, 0.5))).supported_result).toBe(false);
    }
  });
});
