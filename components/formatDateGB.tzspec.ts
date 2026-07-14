/**
 * formatDateGB timezone-regression matrix -- MUST run under Europe/London.
 *
 * This file is deliberately OUTSIDE the default vitest include glob in
 * vite.config.ts ('components/**\/*.test.{ts,tsx}'); it is named '*.tzspec.ts'
 * so `npm test` / `npx vitest run` never pick it up under an uncontrolled host
 * timezone. It runs as its own explicit ladder step:
 *
 *     TZ='Europe/London' npx vitest run --config vitest.tz.config.ts
 *
 * Why a pinned timezone is load-bearing: the DVSA "one day early" defect only
 * reproduces for a SUMMER/BST date parsed on a Europe/London host. Under
 * ECMAScript's Date Time String Format a zone-less date-TIME string
 * ('2025-07-02T00:00:00') parses as LOCAL time, so on a BST host the pre-fix
 * formatter rendered it '1 Jul 2025'. On a UTC runner (CI's default) the same
 * input parses as UTC and the pre-fix code passes -- so a UTC run is NOT
 * evidence. The guard test below verifies the timezone actually took effect
 * before any date assertion runs; if this file is ever run without
 * TZ=Europe/London it fails loudly rather than passing vacuously.
 *
 * A November date is GMT, not BST, and can never reproduce the bug; every
 * regression case here uses a July (BST) date.
 */
import { describe, it, expect } from 'vitest';
import { formatDateGB, lastRecordedMileageLine } from './ReportCopy';
import { fixtureKmConverted } from '../fixtures/reportResponses';

describe('formatDateGB under Europe/London (timezone-pinned regression matrix)', () => {
  it('guard: the pinned timezone took effect (a July date is BST, offset -60)', () => {
    // BST = UTC+1 => getTimezoneOffset() returns -60. If this fails, the file
    // was run without TZ=Europe/London and no assertion below is valid.
    expect(new Date('2025-07-02T12:00:00').getTimezoneOffset()).toBe(-60);
  });

  // Class (a): canonical date-only wire form (what the backend now emits).
  it('(a) canonical date-only summer date renders unchanged', () => {
    expect(formatDateGB('2025-07-02')).toBe('2 Jul 2025');
  });

  // Class (b): legacy zone-less midnight timestamp -- the pre-fix DVSA wire
  // form, and THE regression. Pre-fix on a BST host this rendered '1 Jul 2025'.
  it('(b) legacy zone-less summer midnight timestamp renders the SAME July date (regression)', () => {
    expect(formatDateGB('2025-07-02T00:00:00')).toBe('2 Jul 2025');
  });

  // Class (c): a winter (GMT) date, both forms, renders correctly.
  it('(c) winter date renders correctly in both canonical and legacy forms', () => {
    expect(formatDateGB('2025-01-15')).toBe('15 Jan 2025');
    expect(formatDateGB('2025-01-15T00:00:00')).toBe('15 Jan 2025');
  });

  // Class (d): legacy compat -- another zone-less summer midnight value.
  it('(d) legacy zone-less midnight value (compat path) renders correctly', () => {
    expect(formatDateGB('2026-07-01T00:00:00')).toBe('1 Jul 2026');
  });

  // Class (e): explicit-Z and explicit-offset timestamps pass through untouched.
  it('(e) explicit-Z timestamps render by their UTC instant', () => {
    expect(formatDateGB('2025-07-02T23:30:00Z')).toBe('2 Jul 2025');
    expect(formatDateGB('2025-07-02T00:00:00.000Z')).toBe('2 Jul 2025');
  });

  it('(e) explicit-offset timestamps are respected, not blindly normalized', () => {
    // '...+01:00' at midnight is 2025-07-01T23:00Z -> UTC display '1 Jul 2025'.
    expect(formatDateGB('2025-07-02T00:00:00+01:00')).toBe('1 Jul 2025');
    // '...-05:00' at noon is 2025-07-02T17:00Z -> UTC display '2 Jul 2025'.
    expect(formatDateGB('2025-07-02T12:00:00-05:00')).toBe('2 Jul 2025');
  });

  // Legacy-payload RENDER proof through the real report copy function: the km
  // fixture's observed_at is a zone-less July 'T00:00:00' (a persisted legacy
  // timestamp), and its full line -- date + km-conversion suffix -- must
  // render deterministically. Pre-fix on a BST host this dated '30 Jun 2026'.
  it('legacy T00:00:00 observed_at renders the correct dated last-recorded-mileage line end to end', () => {
    expect(lastRecordedMileageLine(fixtureKmConverted)).toBe(
      'Last recorded MOT mileage: 111,847 miles on 1 Jul 2026 — converted from 180,000 km'
    );
  });
});
