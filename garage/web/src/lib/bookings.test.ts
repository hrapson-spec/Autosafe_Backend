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
  it('ties on both key and rounded percentage fall back to date then registration', () => {
    const [a, b] = demoBookings('2026-10-26').filter((x) => x.registration === 'DEMO02' || x.registration === 'DEMO01');
    a.assessment = { ...a.assessment!, result: { percent_rounded: 30, band: 'medium' }, rank_key: 5 };
    b.assessment = { ...b.assessment!, result: { percent_rounded: 30, band: 'medium' }, rank_key: 5 };
    expect(groupBookings([b, a]).ranked.map((x) => x.registration)).toEqual(['DEMO01', 'DEMO02']);
  });
  it('the service\'s rank_key orders equal percentages (and wins over date/registration)', () => {
    const [a, b, c] = demoBookings('2026-10-26').filter((x) => ['DEMO01', 'DEMO02', 'DEMO04'].includes(x.registration));
    const same = { percent_rounded: 25, band: 'medium' as const };
    a.assessment = { ...a.assessment!, result: same, rank_key: 100 };
    b.assessment = { ...b.assessment!, result: same, rank_key: 300 };
    c.assessment = { ...c.assessment!, result: same, rank_key: 200 };
    expect(groupBookings([a, b, c]).ranked.map((x) => x.registration)).toEqual(['DEMO02', 'DEMO04', 'DEMO01']);
  });
  it('ranking follows rank_key even where the service\'s key and its rounded percentage disagree (the key is the order)', () => {
    const [a, b] = demoBookings('2026-10-26').filter((x) => x.registration === 'DEMO01' || x.registration === 'DEMO02');
    a.assessment = { ...a.assessment!, result: { percent_rounded: 45, band: 'high' }, rank_key: 1 };
    b.assessment = { ...b.assessment!, result: { percent_rounded: 30, band: 'medium' }, rank_key: 2 };
    expect(groupBookings([a, b]).ranked.map((x) => x.registration)).toEqual(['DEMO02', 'DEMO01']);
  });
  it('without keys it falls back to the percentage, then date, appointment time and registration', () => {
    const [a, b, c] = demoBookings('2026-10-26').filter((x) => ['DEMO01', 'DEMO02', 'DEMO12'].includes(x.registration));
    for (const x of [a, b, c]) x.assessment = { ...x.assessment!, result: { percent_rounded: 30, band: 'medium' }, rank_key: null };
    a.appointment_time = '15:00'; b.appointment_time = '08:30'; c.appointment_time = null;
    c.appointment_date = a.appointment_date = b.appointment_date;
    expect(groupBookings([c, a, b]).ranked.map((x) => x.appointment_time)).toEqual(['08:30', '15:00', null]);
  });
  it('a booking with no assessment yet is shown as not ranked', () => {
    const b = { ...demoBookings('2026-10-26')[0], assessment: null };
    expect(groupBookings([b]).notRanked).toHaveLength(1);
  });
});


it('does not rank a numerical estimate when ranking permission is false or missing', () => {
  const source = demoBookings('2026-10-26').find(b => b.assessment?.state === 'available')!;
  for (const flag of [false, undefined]) {
    const b = { ...source, assessment: { ...source.assessment!, display_policy: { ...source.assessment!.display_policy!, ranking_enabled: flag } } };
    expect(groupBookings([b]).ranked).toEqual([]);
    expect(groupBookings([b]).notRanked).toEqual([b]);
  }
});
