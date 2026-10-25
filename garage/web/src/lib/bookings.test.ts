import { describe, expect, it } from 'vitest';
import { demoBookings } from '../mocks/fixtures';
import { groupBookings } from './bookings';

describe('groupBookings', () => {
  const g = groupBookings(demoBookings('2026-10-26'));
  it('ranks only available MOT / service+MOT bookings by the rounded percentage, highest first', () => {
    expect(g.ranked.map((b) => b.registration)).toEqual(['DEMO01', 'DEMO02', 'DEMO04', 'DEMO12', 'DEMO03']);
  });
  it('keeps retests, service-only, unsupported and cancelled apart and unranked', () => {
    expect(g.retests.map((b) => b.registration)).toEqual(['DEMO09']);
    expect(g.serviceOnly.map((b) => b.registration)).toEqual(['DEMO10']);
    expect(g.unsupported.map((b) => b.registration).sort()).toEqual(['DEMO07', 'DEMO11']);
    expect(g.cancelled.map((b) => b.registration)).toEqual(['DEMO13']);
    expect(g.notRanked.map((b) => b.assessment?.state).sort()).toEqual(['failed', 'pending', 'unavailable']);
  });
  it('puts every booking in exactly one group', () => {
    const n = Object.values(g).reduce((a, x) => a + x.length, 0);
    expect(n).toBe(13);
  });
  it('never ranks a retest, service-only or cancelled booking even if it carries a result', () => {
    for (const reg of ['DEMO09', 'DEMO10', 'DEMO13']) {
      const b = demoBookings('2026-10-26').find((x) => x.registration === reg)!;
      b.assessment = { ...b.assessment!, state: 'available', result: { percent_rounded: 95, band: 'high' } };
      expect(groupBookings([b]).ranked).toHaveLength(0);
    }
  });
  it('ties on the rounded percentage fall back to date then registration', () => {
    const [a, b] = demoBookings('2026-10-26').filter((x) => x.registration === 'DEMO02' || x.registration === 'DEMO01');
    a.assessment = { ...a.assessment!, result: { percent_rounded: 30, band: 'medium' } };
    b.assessment = { ...b.assessment!, result: { percent_rounded: 30, band: 'medium' } };
    expect(groupBookings([b, a]).ranked.map((x) => x.registration)).toEqual(['DEMO01', 'DEMO02']);
  });
  it('a booking with no assessment yet is shown as not ranked', () => {
    const b = { ...demoBookings('2026-10-26')[0], assessment: null };
    expect(groupBookings([b]).notRanked).toHaveLength(1);
  });
});
