import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { parse } from 'yaml';
import { BOOKING_TYPES } from '../lib/importDraft';
import { DECISION_OPTIONS } from '../components/DecisionPanel';
import { BAND_LABEL } from '../lib/estimate';
import { MAX_IMPORT_ROWS } from './types';

/**
 * Drift guard: the hand-written types and UI option lists must match
 * garage/contract/openapi.yaml. Skipped (not failed) when the contract file is absent.
 */
const path = join(__dirname, '../../../contract/openapi.yaml');
const maybe = existsSync(path) ? describe : describe.skip;

maybe('garage/contract/openapi.yaml', () => {
  const doc = parse(readFileSync(path, 'utf8')) as { paths: Record<string, unknown>; components: { schemas: Record<string, any> } };
  const schemas = doc.components.schemas;

  it('booking types match', () => expect([...BOOKING_TYPES].sort()).toEqual([...schemas.BookingType.enum].sort()));
  it('decision types match the four UI options', () => expect(DECISION_OPTIONS.map((o) => o.id).sort()).toEqual([...schemas.DecisionType.enum].sort()));
  it('bands match', () => expect(Object.keys(BAND_LABEL).sort()).toEqual([...schemas.AssessmentResult.properties.band.enum].sort()));
  it('assessment states are the five the UI renders', () =>
    expect([...schemas.AssessmentState.enum].sort()).toEqual(['available', 'failed', 'pending', 'unavailable', 'unsupported']));
  it('the percentage is a multiple of 5 chosen by the service', () => {
    expect(schemas.AssessmentResult.properties.percent_rounded.multipleOf).toBe(5);
  });
  it('the contract has no customer name, email or phone property on any schema', () => {
    const bad: string[] = [];
    for (const [name, s] of Object.entries<any>(schemas)) {
      for (const prop of Object.keys(s.properties ?? {})) {
        if (/(customer|keeper|driver).*(name|email|phone)|^phone$|^mobile$|^telephone$/i.test(prop)) bad.push(`${name}.${prop}`);
      }
    }
    expect(bad).toEqual([]);
  });
  it('the import limit is 200 and the preview route exists', () => {
    expect(MAX_IMPORT_ROWS).toBe(200);
    expect(doc.paths['/imports/preview']).toBeTruthy();
    expect(doc.paths['/imports/{import_id}/commit']).toBeTruthy();
  });
  it('every endpoint the client calls exists in the contract, and the proposed ones do not', () => {
    for (const p of ['/auth/signup', '/auth/login-links', '/auth/links/{token}/consume', '/auth/logout', '/me', '/workshop', '/usage', '/staff', '/staff/{membership_id}',
      '/bookings', '/bookings/{booking_id}', '/bookings/{booking_id}/assessments', '/bookings/{booking_id}/decisions']) {
      expect(doc.paths[p], p).toBeTruthy();
    }
    expect(doc.paths['/bookings/{booking_id}/briefing']).toBeUndefined();
    expect(doc.paths['/bookings/{booking_id}/feedback']).toBeUndefined();
  });
});
