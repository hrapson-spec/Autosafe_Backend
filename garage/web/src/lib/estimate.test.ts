import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { demoBookings } from '../mocks/fixtures';
import { shownEstimate } from './estimate';

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
