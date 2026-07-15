import { describe, expect, it } from 'vitest';
import { getRecommendation } from './recommendation';
import type { RecommendationInput } from '../types';

function input(overrides: Partial<RecommendationInput> = {}): RecommendationInput {
  return {
    failureRisk: 0.52,
    highRiskFaultCount: 3,
    hasVehicleComparison: true,
    make: 'FORD',
    model: 'FIESTA',
    ...overrides,
  };
}

describe('getRecommendation product truth', () => {
  it('frames a high rate as comparable-vehicle evidence, not this car diagnosis', () => {
    const result = getRecommendation(input());
    const copy = `${result.scoreLabel} ${result.recommendationHeadline} ${result.supportingLine}`;

    expect(copy).toContain('Comparable-vehicle MOT failure rate');
    expect(copy).toContain('Similar FORD FIESTA vehicles');
    expect(copy).toContain('not a diagnosis');
    expect(copy).not.toMatch(/your FORD FIESTA has a .*chance of failing/i);
    expect(copy).not.toMatch(/we found .*high-risk area/i);
  });

  it('does not call a low comparison rate proof that this car is healthy', () => {
    const result = getRecommendation(input({ failureRisk: 0.12, daysUntilMotExpiry: 120 }));
    const copy = `${result.recommendationHeadline} ${result.supportingLine}`;

    expect(copy).toContain("doesn't confirm this car's condition");
    expect(copy).not.toMatch(/looks healthy|in good shape|no urgent action/i);
  });

  it('labels repair costs as comparable and non-diagnostic', () => {
    const result = getRecommendation(input({
      repairCostEstimate: { cost_min: 100, cost_max: 400, display: '' },
    }));

    expect(result.motivatorHeadline).toContain('Comparable-vehicle cost range');
    expect(result.motivatorSupportingLine).toContain('comparable vehicles');
    expect(result.motivatorSupportingLine).toContain('not a quote or diagnosis');
    expect(result.motivatorSupportingLine).not.toContain('common faults for your');
  });

  it('does not use a dataset-wide reference rate to infer action for this vehicle', () => {
    const result = getRecommendation(input({
      failureRisk: 0.52,
      hasVehicleComparison: false,
      daysUntilMotExpiry: 120,
    }));
    const copy = `${result.scoreLabel} ${result.recommendationHeadline} ${result.supportingLine}`;

    expect(result.scoreLabel).toBe('Dataset-wide reference MOT failure rate');
    expect(copy).toContain('No vehicle-matched comparison is available');
    expect(copy).toContain('does not describe this car');
    expect(result.primaryAction).toBe('SET_REMINDER');
    expect(copy).not.toContain('Similar FORD FIESTA vehicles');
  });
});
