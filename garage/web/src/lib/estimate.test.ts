import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { demoBookings } from '../mocks/fixtures';
import { bandLegend, qualifiedBands, roundingWording, shownEstimate } from './estimate';

const withResult = (percent: number, band: 'low' | 'medium' | 'high') => {
  const b = demoBookings('2026-10-26')[0];
  return { ...b.assessment!, state: 'available' as const, result: { percent_rounded: percent, band } };
};

describe('shownEstimate (R7: the service decides, the client displays)', () => {
  it('passes the percentage and band through untouched, with no client-side rounding or re-banding', () => {
    expect(shownEstimate(withResult(47, 'low'))).toEqual({ percent: 47, band: 'low', bandLabel: 'Low' });
    expect(shownEstimate(withResult(90, 'medium'))?.band).toBe('medium');
  });
  it('shows nothing unless the state is available and a result is present', () => {
    const a = withResult(45, 'high');
    expect(shownEstimate({ ...a, state: 'pending' })).toBeNull();
    expect(shownEstimate({ ...a, state: 'unavailable' })).toBeNull();
    expect(shownEstimate({ ...a, result: null })).toBeNull();
    expect(shownEstimate(null)).toBeNull();
  });
});

describe('display_policy (supplied by the service)', () => {
  it('labels come from the policy; the table is only a fallback', () => {
    const a = withResult(25, 'medium');
    expect(shownEstimate({ ...a, display_policy: { ...a.display_policy!, band_labels: { low: 'L', medium: 'Moderate', high: 'H' } } })?.bandLabel).toBe('Moderate');
    expect(shownEstimate({ ...a, display_policy: null })).toBeNull();
    expect(shownEstimate({ ...a, display_policy: { ...a.display_policy!, display_state: 'unavailable' } })).toBeNull();
  });
  it('the rounding step and band ranges are worded from the policy, not hard-coded', () => {
    const p = withResult(25, 'medium').display_policy!;
    expect(roundingWording(p)).toBe('Rounded to the nearest 5 percentage points by the service.');
    expect(roundingWording({ ...p, rounding_pp: 10 })).toBe('Rounded to the nearest 10 percentage points by the service.');
    expect(roundingWording(null)).toBe('Rounded by the service.');
    expect(bandLegend({ ...p, band_edges: [15, 40] })?.map((r) => r.range)).toEqual(['under 15%', '15% to under 40%', '40% or more']);
    expect(bandLegend(null)).toBeNull();
  });
});

describe('no client-side thresholds or rounding', () => {
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((f) => {
      const p = join(dir, f);
      if (statSync(p).isDirectory()) return f === 'mocks' ? [] : walk(p);
      return /\.(ts|tsx)$/.test(f) && !/\.test\./.test(f) && f !== 'testUtils.tsx' ? [p] : [];
    });
  const files = walk(join(__dirname, '..'));
  it('production sources outside mocks have no rounding or band-edge arithmetic', () => {
    const offenders = files.filter((p) => /Math\.(round|floor|ceil)|toFixed|lower_bound|rounding_step|>=\s*35|>=\s*20/.test(readFileSync(p, 'utf8')));
    expect(offenders).toEqual([]);
  });
  it('the demo page is the only production file that imports mock fixtures', () => {
    const importers = files.filter((p) => /from '\.\.?\/(\.\.\/)?mocks\/fixtures'/.test(readFileSync(p, 'utf8'))).map((p) => p.split('/src/')[1]);
    expect(importers).toEqual(['pages/DemoPage.tsx']);
  });
});


it('qualified bands-only policy hides percentages and shadow hides all operational output', () => {
  const a = withResult(25, 'medium');
  const bands = { ...a, display_policy: { ...a.display_policy!, display_state: 'bands' as const, provisional: false, operational_qualified: true, model_version: 'test-qualified', suppression_reason: null } };
  expect(shownEstimate(bands)).toBeNull();
  expect(qualifiedBands(bands)).toBe('Medium');
  expect(qualifiedBands({ ...bands, display_policy: { ...bands.display_policy, provisional: true } })).toBeNull();
  expect(shownEstimate({ ...a, display_policy: { ...a.display_policy!, display_state: 'shadow' } })).toBeNull();
});
