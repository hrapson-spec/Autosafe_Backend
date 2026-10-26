import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { parse } from 'yaml';
import { BOOKING_TYPES } from '../lib/importDraft';
import { DECISION_OPTIONS } from '../components/DecisionPanel';
import { FEEDBACK_REASON_LABEL } from '../components/FeedbackControl';
import { BAND_LABEL } from '../lib/estimate';
import { demoBookings, demoBriefingFor } from '../mocks/fixtures';
import briefingFixtures from '../mocks/briefingFixtures.json';
import { BRIEFING_RESULTS, BRIEFING_STATUSES, FEEDBACK_REASONS, FEEDBACK_VALUES, IMPORT_STATUSES, MAX_IMPORT_ROWS } from './types';

/**
 * Drift guard: the hand-written types, UI option lists, mock fixtures and client calls must match
 * garage/contract/openapi.yaml. Skipped (not failed) when the contract file is absent.
 */
const path = join(__dirname, '../../../contract/openapi.yaml');
const maybe = existsSync(path) ? describe : describe.skip;

type Schema = Record<string, any>;

/** A small structural validator for the subset of OpenAPI 3.0 the contract uses ($ref, type, enum, required, nullable, items, properties). */
function validate(schema: Schema, value: unknown, root: Schema, where = '$'): string[] {
  if (value === null && schema.nullable) return [];
  if (schema.$ref) {
    const target = (schema.$ref as string).replace(/^#\//, '').split('/').reduce((o: any, k) => o[k], root);
    return validate(target, value, root, where);
  }
  if (schema.allOf) return schema.allOf.flatMap((s: Schema) => validate(s, value, root, where));
  if (value === null) return [`${where}: null not allowed`];
  const errs: string[] = [];
  if (schema.enum && !schema.enum.includes(value)) errs.push(`${where}: ${JSON.stringify(value)} not in enum`);
  const t = schema.type;
  const isObj = typeof value === 'object' && !Array.isArray(value);
  if (t === 'object') {
    if (!isObj) return [`${where}: expected object`];
    for (const k of schema.required ?? []) if (!(k in (value as object))) errs.push(`${where}.${k}: required`);
    for (const [k, v] of Object.entries(value as object)) {
      const sub = schema.properties?.[k];
      if (!sub) {
        if (schema.additionalProperties === false || schema.properties) errs.push(`${where}.${k}: not in the contract`);
        continue;
      }
      errs.push(...validate(sub, v, root, `${where}.${k}`));
    }
  } else if (t === 'array') {
    if (!Array.isArray(value)) return [`${where}: expected array`];
    value.forEach((v, i) => errs.push(...validate(schema.items, v, root, `${where}[${i}]`)));
  } else if (t === 'string' && typeof value !== 'string') errs.push(`${where}: expected string`);
  else if (t === 'integer' && !Number.isInteger(value)) errs.push(`${where}: expected integer`);
  else if (t === 'boolean' && typeof value !== 'boolean') errs.push(`${where}: expected boolean`);
  return errs;
}

maybe('garage/contract/openapi.yaml', () => {
  const doc = parse(readFileSync(path, 'utf8')) as { info: { description: string }; paths: Record<string, any>; components: { schemas: Record<string, any> } };
  const schemas = doc.components.schemas;
  const clientSource = readFileSync(join(__dirname, 'client.ts'), 'utf8');

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

  // ---- the endpoints the client calls
  it('every endpoint the client calls exists in the contract with the method it uses', () => {
    const calls: Array<[string, string]> = [
      ['/auth/signup', 'post'], ['/auth/login-links', 'post'], ['/auth/logout', 'post'], ['/me', 'get'], ['/workshop', 'get'], ['/workshop', 'patch'],
      ['/usage', 'get'], ['/staff', 'get'], ['/staff', 'post'], ['/staff/{membership_id}', 'delete'], ['/imports/preview', 'post'],
      ['/imports/{import_id}/commit', 'post'], ['/imports/{import_id}', 'get'], ['/bookings', 'get'], ['/bookings/{booking_id}', 'get'],
      ['/bookings/{booking_id}/assessments', 'get'], ['/bookings/{booking_id}/assessments', 'post'], ['/bookings/{booking_id}/decisions', 'get'],
      ['/bookings/{booking_id}/decisions', 'post'], ['/bookings/{booking_id}/briefing', 'get'], ['/bookings/{booking_id}/feedback', 'put'],
    ];
    for (const [p, m] of calls) expect(doc.paths[p]?.[m], `${m} ${p}`).toBeTruthy();
    expect(clientSource).toContain("'GET', `/bookings/${id(bookingId)}/briefing`");
    expect(clientSource).toContain("'PUT', `/bookings/${id(bookingId)}/feedback`");
  });

  // ---- gap 8: one confirm page, the server's
  it('the SPA never consumes a sign-in link: only the server-rendered confirm page does', () => {
    expect(doc.paths['/auth/links/{token}'].get.responses['200'].content['text/html']).toBeTruthy();
    expect(clientSource).not.toMatch(/consume|\/auth\/links/);
    expect(readFileSync(join(__dirname, '../App.tsx'), 'utf8')).not.toMatch(/path="\/auth\/confirm"/);
    expect(Object.keys(doc.paths).some((p) => p.includes('confirm'))).toBe(false);
  });

  // ---- gap 3: demo data stays client-side
  it('demo data is not served by the API and says so; the client makes no demo call', () => {
    expect(doc.info.description).toContain('NOT served by this API');
    expect(Object.keys(doc.paths).filter((p) => /demo/i.test(p))).toEqual([]);
    expect(clientSource).not.toMatch(/demo/i);
    const demo = demoBriefingFor(demoBookings('2026-10-26')[0], '2026-10-26', 1);
    expect(validate({ $ref: '#/components/schemas/Briefing' }, demo, doc as any)).toEqual([]);   // fictional, but in the contract's shape
  });

  // ---- gaps 1, 2: briefing and feedback
  it('briefing and feedback enums match the UI', () => {
    expect([...BRIEFING_STATUSES]).toEqual(schemas.Briefing.properties.status.enum);
    expect([...BRIEFING_RESULTS]).toEqual(schemas.BriefingRecord.properties.result.enum);
    expect([...FEEDBACK_VALUES]).toEqual(schemas.FeedbackValue.enum);
    expect([...FEEDBACK_REASONS]).toEqual(schemas.FeedbackReason.enum);
    expect(Object.keys(FEEDBACK_REASON_LABEL)).toEqual(schemas.FeedbackReason.enum);              // every reason code has UI copy
    const body = doc.paths['/bookings/{booking_id}/feedback'].put.requestBody.content['application/json'].schema;
    expect(Object.keys(body.properties).sort()).toEqual(['briefing_version', 'reason_code', 'value']);
    expect(body.required).toEqual(['value']);
  });

  it('every one of the 50 real-shape briefing fixtures (the backend engine\'s output) validates against the Briefing schema', () => {
    const entries = Object.entries(briefingFixtures as Record<string, any>);
    expect(entries.length).toBe(52);                                      // 50 fixture vehicles + not_found + no_prior_test
    const bad: string[] = [];
    for (const [reg, b] of entries) {
      const full = { ...b, booking_id: '00000000-0000-4000-8000-000000000000', status: 'ready', reason: null, reason_text: null, assessment_id: '00000000-0000-4000-8000-000000000001', assessment_version: 1, feedback: null };
      bad.push(...validate({ $ref: '#/components/schemas/Briefing' }, full, doc as any, reg));
    }
    expect(bad).toEqual([]);
    // rule ids used by the fixtures are in the contract's enums
    const gapIds = new Set<string>(schemas.BriefingGapRuleId.enum);
    const qIds = new Set<string>(schemas.BriefingQuestionRuleId.enum);
    for (const b of Object.values(briefingFixtures as Record<string, any>)) {
      for (const g of b.evidence_gaps) expect(gapIds.has(g.rule_id), g.rule_id).toBe(true);
      for (const q of b.follow_up_questions) expect(qIds.has(q.rule_id), q.rule_id).toBe(true);
    }
  });

  it('the validator itself rejects a bad briefing (self-test)', () => {
    const good = (briefingFixtures as Record<string, any>).ZZ03FXT;
    const full = { ...good, booking_id: 'x', status: 'ready', reason: null, reason_text: null, assessment_id: 'y', assessment_version: 1, feedback: null };
    expect(validate({ $ref: '#/components/schemas/Briefing' }, full, doc as any)).toEqual([]);
    expect(validate({ $ref: '#/components/schemas/Briefing' }, { ...full, status: 'maybe' }, doc as any)).not.toEqual([]);
    expect(validate({ $ref: '#/components/schemas/Briefing' }, { ...full, score: 0.3 }, doc as any)).not.toEqual([]);
    const { summary: _s, ...noSummary } = full;
    expect(validate({ $ref: '#/components/schemas/Briefing' }, noSummary, doc as any)).not.toEqual([]);
  });

  // ---- gaps 4, 5: display policy and ranking
  it('display_policy and rank_key are on the assessment, order-only, and no score field exists anywhere', () => {
    expect(Object.keys(schemas.DisplayPolicy.properties).sort()).toEqual(['band_edges', 'band_labels', 'display_state', 'model_version', 'operational_qualified', 'policy_version', 'provisional', 'ranking_enabled', 'rounding_pp', 'suppression_reason']);
    expect(schemas.DisplayPolicy.properties.rounding_pp.enum).toEqual([5]);
    expect(Object.keys(schemas.DisplayPolicy.properties.band_labels.properties).sort()).toEqual(Object.keys(BAND_LABEL).sort());
    expect(schemas.Assessment.properties.display_policy).toBeTruthy();
    expect(schemas.Assessment.properties.rank_key.type).toBe('integer');
    const names = Object.values<any>(schemas).flatMap((s) => Object.keys(s.properties ?? {}));
    expect(names.filter((n) => /score|unrounded|probab|raw_percent/i.test(n))).toEqual([]);
    for (const b of demoBookings('2026-10-26')) {
      if (b.assessment) expect(validate({ $ref: '#/components/schemas/Assessment' }, b.assessment, doc as any)).toEqual([]);
    }
  });

  // ---- gap 6: appointment time
  it('appointment_time exists on bookings, import rows (request and preview) and the patch body', () => {
    expect(schemas.Booking.properties.appointment_time).toBeTruthy();
    expect(schemas.ImportRow.properties.time).toBeTruthy();
    expect(schemas.PreviewRow.properties.appointment_time).toBeTruthy();
    expect(doc.paths['/bookings/{booking_id}'].patch.requestBody.content['application/json'].schema.properties.appointment_time).toBeTruthy();
  });

  // ---- gap 7: asynchronous commit
  it('import commit is asynchronous: 202 with progress, a record that carries N of M', () => {
    expect(Object.keys(doc.paths['/imports/{import_id}/commit'].post.responses)).toEqual(expect.arrayContaining(['200', '202', '404', '410']));
    expect([...IMPORT_STATUSES]).toEqual(schemas.ImportRecord.properties.status.enum);
    expect(schemas.ImportProgress.required.sort()).toEqual(['processed', 'total']);
    expect(schemas.ImportResult).toBeUndefined();
  });
});
