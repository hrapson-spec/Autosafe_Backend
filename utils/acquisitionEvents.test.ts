import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import acquisitionEventsSource from './acquisitionEvents.ts?raw';
import acknowledgementSource from './resultAcknowledgement.ts?raw';
import boundarySource from '../components/ResultErrorBoundary.tsx?raw';
import indexSource from '../index.tsx?raw';
import {
  ACQUISITION_COLLECTOR_ENABLED,
  ACQUISITION_METRIC_VERSION,
  ACQUISITION_SCHEMA_VERSION,
  CHECK_ERROR_CATEGORIES,
  __resetAcquisitionStateForTests,
  __setAcquisitionSinkForTests,
  checkErrorCategory,
  claimCompletion,
  emitAcquisitionEvent,
  newOperationId,
  noopSink,
  randomId,
  readOperationId,
  type AcquisitionEvent,
  type AcquisitionEventInput,
} from './acquisitionEvents';
import { ReportApiError } from '../services/errorMessages';
import { fixtureExactHigh } from '../fixtures/reportResponses';
import { loadEventSchema, validates } from './acquisitionSchemaCheck.testutil';

const OP = '0b0e7a52-5d5c-4f55-9a52-3c6e1f1c8a10';

/** One representative input per event type, plus the optional-field variants. */
export const SAMPLE_INPUTS: AcquisitionEventInput[] = [
  { event: 'landing_observed', page_family: 'guide', source_group: 'google_organic', observation_state: 'observed' },
  { event: 'check_started', operation_id: OP, entry_mode: 'fresh_check' },
  { event: 'report_created', operation_id: OP, entry_mode: 'fresh_check', result_kind: 'comparison', match_scope: 'exact_band', persistence_mode: 'saved' },
  { event: 'report_created', operation_id: OP, entry_mode: 'fresh_check', result_kind: 'vehicle_prediction', match_scope: 'model_prediction', persistence_mode: 'inline_unsaved' },
  { event: 'result_rendered', operation_id: OP, entry_mode: 'fresh_check', persistence_mode: 'saved', render_delivered: true, supported_result: true, outcome_group: 'prediction', rate_valid: true, scope_visible: true, result_kind: 'vehicle_prediction', match_scope: 'model_prediction' },
  { event: 'result_rendered', operation_id: OP, entry_mode: 'fresh_check', persistence_mode: 'saved', render_delivered: true, supported_result: true, outcome_group: 'exact_comparison', rate_valid: true, sample_nonzero: true, scope_visible: true, result_kind: 'comparison', match_scope: 'exact_band' },
  { event: 'result_rendered', entry_mode: 'restored_link', persistence_mode: 'saved', render_delivered: true, supported_result: true, outcome_group: 'broader_supported_comparison', rate_valid: true, sample_nonzero: true, scope_visible: true, result_kind: 'comparison', match_scope: 'model_average' },
  { event: 'result_rendered', operation_id: OP, entry_mode: 'fresh_check', persistence_mode: 'inline_unsaved', render_delivered: true, supported_result: false, outcome_group: 'dataset_reference', rate_valid: true, sample_nonzero: false, scope_visible: true, result_kind: 'comparison', match_scope: 'population_default' },
  { event: 'result_rendered', operation_id: OP, entry_mode: 'fresh_check', persistence_mode: 'saved', render_delivered: true, supported_result: false, outcome_group: 'demo', rate_valid: true, sample_nonzero: true, scope_visible: true, result_kind: 'comparison', match_scope: 'exact_band' },
  { event: 'result_rendered', entry_mode: 'restored_link', persistence_mode: 'saved', render_delivered: true, supported_result: false, outcome_group: 'demo', rate_valid: true, scope_visible: true, result_kind: 'vehicle_prediction', match_scope: 'model_prediction' },
  { event: 'result_unavailable', entry_mode: 'restored_link', reason: 'not_found' },
  { event: 'result_unavailable', operation_id: OP, entry_mode: 'fresh_check', reason: 'unavailable' },
  { event: 'check_failed', operation_id: OP, entry_mode: 'fresh_check', error_category: 'rate_limited', stage: 'create_report' },
  { event: 'render_failed', operation_id: OP, entry_mode: 'fresh_check', stage: 'lazy_load' },
  { event: 'render_failed', entry_mode: 'restored_link', stage: 'render' },
  { event: 'landing_observed', page_family: 'home', source_group: 'internal', observation_state: 'observed' },
];

const FORBIDDEN_KEYS = [
  'failure_risk', 'risk', 'rate', 'total_tests', 'total_failures', 'sample', 'report_token', 'token', 'report_id',
  'registration', 'vrm', 'postcode', 'make', 'model', 'url', 'referrer', 'referer', 'message', 'error', 'error_message',
  'text', 'note', 'path_with_query', 'email', 'user_agent', 'session_id',
];

let captured: AcquisitionEvent[] = [];

beforeEach(() => {
  __resetAcquisitionStateForTests();
  captured = [];
  __setAcquisitionSinkForTests({ emit: (e) => captured.push(e) });
});
afterEach(() => __resetAcquisitionStateForTests());

describe('acquisition events: enabled release keeps transport boundaries', () => {
  it('the release flag is enabled', () => {
    expect(ACQUISITION_COLLECTOR_ENABLED).toBe(true);
  });

  const stripComments = (source: string) =>
    source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

  it.each([
    ['utils/resultAcknowledgement.ts', acknowledgementSource],
    ['components/ResultErrorBoundary.tsx', boundarySource],
  ])('%s contains no network transport', (_name, source) => {
    // Strip comments so the documentation of the rule does not trip the rule.
    const code = stripComments(source);
    for (const token of ['fetch(', 'sendBeacon', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'new Image(', 'navigator.']) {
      expect(code, token).not.toContain(token);
    }
  });

  it('utils/acquisitionEvents.ts has exactly one transport (fetch) and no storage or alternative channel', () => {
    const code = stripComments(acquisitionEventsSource);
    expect(code.match(/fetch\(|doFetch\(/g)).toHaveLength(1);
    expect(code).toContain('doFetch(ACQUISITION_ENDPOINT');
    for (const token of [
      'sendBeacon', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'new Image(',
      'localStorage', 'sessionStorage', 'indexedDB', 'document.cookie', 'caches.',
    ]) {
      expect(code, token).not.toContain(token);
    }
  });

  it('the transport is installed only from index.tsx, through the flag-gated installer', () => {
    expect(indexSource).toContain('if (ACQUISITION_COLLECTOR_ENABLED) installAcquisitionTransport();');
    expect(acquisitionEventsSource).toContain('enabled: boolean = ACQUISITION_COLLECTOR_ENABLED');
    expect(acquisitionEventsSource).toContain('if (!enabled || globalPrivacyControlSet()) return false;');
  });

  it('the default sink drops events and emit never throws, even with a throwing sink', () => {
    __setAcquisitionSinkForTests(null);
    expect(() => emitAcquisitionEvent(SAMPLE_INPUTS[1])).not.toThrow();
    expect(noopSink.emit(undefined as unknown as AcquisitionEvent)).toBeUndefined();
    __setAcquisitionSinkForTests({ emit: () => { throw new Error('sink failed'); } });
    expect(() => emitAcquisitionEvent(SAMPLE_INPUTS[1])).not.toThrow();
  });

  it('does not expose the sink or any acquisition handle on window', () => {
    const keys = Object.getOwnPropertyNames(window).filter((k) => /acquisition|__setAcq/i.test(k));
    expect(keys).toEqual([]);
  });
});

describe('acquisition events: safe fields only', () => {
  it('every event type serialises to enums, booleans and random ids only', () => {
    const names = new Set(SAMPLE_INPUTS.map((i) => i.event));
    expect([...names].sort()).toEqual([
      'check_failed', 'check_started', 'landing_observed', 'render_failed', 'report_created',
      'result_rendered', 'result_unavailable',
    ]);
    for (const input of SAMPLE_INPUTS) emitAcquisitionEvent(input);
    expect(captured).toHaveLength(SAMPLE_INPUTS.length);

    const forbiddenValues = [
      fixtureExactHigh.report_token as string,
      fixtureExactHigh.registration,
      fixtureExactHigh.vehicle.make,
      fixtureExactHigh.vehicle.model,
      'SW1A',
      'http',
      '?',
    ];
    for (const event of captured) {
      const json = JSON.stringify(event);
      const parsed = JSON.parse(json) as Record<string, unknown>;
      for (const key of Object.keys(parsed)) {
        expect(FORBIDDEN_KEYS, `${event.event}.${key}`).not.toContain(key);
      }
      for (const [key, value] of Object.entries(parsed)) {
        expect(['string', 'boolean', 'number'], `${event.event}.${key}`).toContain(typeof value);
        if (typeof value === 'number') expect(key).toBe('schema_version');
      }
      for (const bad of forbiddenValues) expect(json, `${event.event} contains ${bad}`).not.toContain(bad);
      expect(parsed.schema_version).toBe(ACQUISITION_SCHEMA_VERSION);
      expect(parsed.metric_version).toBe(ACQUISITION_METRIC_VERSION);
    }
  });

  it('numeric values never appear: the only number is schema_version', () => {
    for (const input of SAMPLE_INPUTS) emitAcquisitionEvent(input);
    for (const event of captured) {
      const numeric = Object.entries(event).filter(([, v]) => typeof v === 'number').map(([k]) => k);
      expect(numeric).toEqual(['schema_version']);
    }
  });

  it('event ids are random, unique and uuid-shaped; operation ids are minted fresh each time', () => {
    for (let i = 0; i < 50; i += 1) emitAcquisitionEvent(SAMPLE_INPUTS[1]);
    const ids = new Set(captured.map((e) => e.event_id));
    expect(ids.size).toBe(50);
    for (const id of ids) expect(readOperationId(id)).toBe(id);
    const ops = new Set(Array.from({ length: 50 }, () => newOperationId()));
    expect(ops.size).toBe(50);
    expect(randomId()).not.toBe(randomId());
  });

  it('readOperationId rejects anything not shaped like a minted id (history.state is untrusted)', () => {
    for (const bad of [undefined, null, 5, {}, 'AB12CDE', fixtureExactHigh.report_token, 'x'.repeat(36), `${OP}extra`]) {
      expect(readOperationId(bad)).toBeUndefined();
    }
    expect(readOperationId(OP)).toBe(OP);
  });

  it('error categories map to a fixed enum and never read the message', () => {
    expect(checkErrorCategory(new ReportApiError('rate_limited', 'secret AB12CDE message'))).toBe('rate_limited');
    expect(checkErrorCategory(new ReportApiError('network_error', 'x'))).toBe('network_error');
    expect(checkErrorCategory(new Error('boom AB12CDE'))).toBe('unknown');
    expect(checkErrorCategory(null)).toBe('unknown');
    expect(checkErrorCategory({ code: 'AB12CDE' })).toBe('unknown');
    expect(CHECK_ERROR_CATEGORIES).toContain('unknown');
  });
});

describe('acquisition events: in-memory completion markers', () => {
  it('claims a (kind, key) once and distinguishes kinds and keys', () => {
    expect(claimCompletion('result_rendered', OP)).toBe(true);
    expect(claimCompletion('result_rendered', OP)).toBe(false);
    expect(claimCompletion('result_unavailable', OP)).toBe(true);
    expect(claimCompletion('result_rendered', 'other')).toBe(true);
  });
});

describe('event schema (docs/acquisition/event_schema_v2.json) agrees with the typed events', () => {
  const schema = loadEventSchema();

  it('every sample event built by the real emitter validates', () => {
    for (const input of SAMPLE_INPUTS) emitAcquisitionEvent(input);
    for (const event of captured) {
      expect(validates(schema, event), JSON.stringify(event)).toBe(true);
    }
  });

  it('rejects unknown fields, forbidden fields, bad enums and wrong shapes', () => {
    const base = captured.length ? captured[0] : (() => { emitAcquisitionEvent(SAMPLE_INPUTS[1]); return captured[0]; })();
    expect(validates(schema, base)).toBe(true);
    const mutate = (patch: Record<string, unknown>) => ({ ...base, ...patch });
    expect(validates(schema, mutate({ failure_risk: 0.2 }))).toBe(false);
    expect(validates(schema, mutate({ report_token: 'abc' }))).toBe(false);
    expect(validates(schema, mutate({ registration: 'AB12CDE' }))).toBe(false);
    expect(validates(schema, mutate({ entry_mode: 'restored_link' }))).toBe(false);
    expect(validates(schema, mutate({ operation_id: 'AB12CDE' }))).toBe(false);
    expect(validates(schema, mutate({ event_id: 'not-random' }))).toBe(false);
    expect(validates(schema, mutate({ event: 'check_finished' }))).toBe(false);
    expect(validates(schema, mutate({ schema_version: 1 }))).toBe(false);
    const { operation_id: _omit, ...withoutOp } = base as unknown as Record<string, unknown>;
    expect(validates(schema, withoutOp)).toBe(false);
  });

  const emitRaw = (input: Record<string, unknown>) => {
    emitAcquisitionEvent(input as unknown as AcquisitionEventInput);
    return captured[captured.length - 1];
  };
  const PRED = SAMPLE_INPUTS[4] as unknown as Record<string, unknown>;
  const EXACT = SAMPLE_INPUTS[5] as unknown as Record<string, unknown>;
  const REF = SAMPLE_INPUTS[7] as unknown as Record<string, unknown>;
  const ok = (input: Record<string, unknown>) => validates(schema, emitRaw(input));

  it('result_rendered combination rules', () => {
    // model_prediction must omit sample_nonzero and be prediction/vehicle_prediction
    expect(ok(PRED)).toBe(true);
    expect(ok({ ...PRED, sample_nonzero: true })).toBe(false);
    expect(ok({ ...PRED, result_kind: 'comparison' })).toBe(false);
    expect(ok({ ...PRED, supported_result: false })).toBe(false);
    // comparison groups need sample_nonzero and the right scope
    expect(ok(EXACT)).toBe(true);
    const { sample_nonzero: _s, ...noSample } = EXACT;
    expect(ok(noSample)).toBe(false);
    expect(ok({ ...EXACT, match_scope: 'age_band_only' })).toBe(false);
    expect(ok({ ...EXACT, match_scope: 'model_prediction' })).toBe(false);
    // dataset_reference is never supported
    expect(ok(REF)).toBe(true);
    expect(ok({ ...REF, supported_result: true })).toBe(false);
    // supported requires a valid rate and visible scope
    expect(ok({ ...EXACT, rate_valid: false })).toBe(false);
    expect(ok({ ...EXACT, scope_visible: false })).toBe(false);
    expect(ok({ ...EXACT, scope_visible: false, supported_result: false })).toBe(true);
    // render_delivered is always true on this event; error/unavailable groups are other events
    expect(ok({ ...EXACT, render_delivered: false })).toBe(false);
    expect(ok({ ...EXACT, outcome_group: 'unavailable' })).toBe(false);
    expect(ok({ ...EXACT, outcome_group: 'error' })).toBe(false);
    // demo (D-004): never supported; model_prediction demo omits sample_nonzero; comparison demo carries it
    const DEMO_CMP = SAMPLE_INPUTS[8] as unknown as Record<string, unknown>;
    const DEMO_PRED = SAMPLE_INPUTS[9] as unknown as Record<string, unknown>;
    expect(ok(DEMO_CMP)).toBe(true);
    expect(ok(DEMO_PRED)).toBe(true);
    expect(ok({ ...DEMO_CMP, supported_result: true })).toBe(false);
    expect(ok({ ...DEMO_PRED, supported_result: true })).toBe(false);
    expect(ok({ ...DEMO_PRED, sample_nonzero: true })).toBe(false);
    const { sample_nonzero: _d, ...demoNoSample } = DEMO_CMP;
    expect(ok(demoNoSample)).toBe(false);
    // demo cannot ride on a non-demo scope pairing it breaks (dataset_reference needs population_default)
    expect(ok({ ...DEMO_CMP, outcome_group: 'dataset_reference' })).toBe(false);
    // the unavailable scope is never a result_rendered (D-004 precedence: it is result_unavailable)
    expect(ok({ ...DEMO_CMP, match_scope: 'unavailable' })).toBe(false);
    expect(ok({ ...EXACT, match_scope: 'unavailable' })).toBe(false);
    // there is no broad_fallback result kind
    expect(ok({ ...EXACT, result_kind: 'broad_fallback' })).toBe(false);
  });

  it('report_created cannot pair vehicle_prediction with a comparison scope', () => {
    emitAcquisitionEvent({ ...(SAMPLE_INPUTS[2] as object), result_kind: 'vehicle_prediction' } as AcquisitionEventInput);
    expect(validates(schema, captured[captured.length - 1])).toBe(false);
    emitAcquisitionEvent({ ...(SAMPLE_INPUTS[2] as object), match_scope: 'model_prediction' } as AcquisitionEventInput);
    expect(validates(schema, captured[captured.length - 1])).toBe(false);
  });

  it('landing_observed carries an allowlisted page family, never a path', () => {
    emitAcquisitionEvent({ ...(SAMPLE_INPUTS[0] as object), page_family: '/app/report/9c7f2b1a' } as unknown as AcquisitionEventInput);
    expect(validates(schema, captured[captured.length - 1])).toBe(false);
    emitAcquisitionEvent({ ...(SAMPLE_INPUTS[0] as object), page_family: '/app?reg=AB12CDE' } as unknown as AcquisitionEventInput);
    expect(validates(schema, captured[captured.length - 1])).toBe(false);
    emitAcquisitionEvent({ ...(SAMPLE_INPUTS[0] as object), landing_path: '/guides/mot-cost' } as unknown as AcquisitionEventInput);
    expect(validates(schema, captured[captured.length - 1])).toBe(false);
    emitAcquisitionEvent({ ...(SAMPLE_INPUTS[0] as object) } as AcquisitionEventInput);
    expect(validates(schema, captured[captured.length - 1])).toBe(true);
  });

  it('the schema enum sets equal the TypeScript enum sets for error categories', () => {
    const checkFailed = (schema.oneOf as Array<Record<string, any>>).find((s) => s.properties.event.const === 'check_failed')!;
    expect([...checkFailed.properties.error_category.enum].sort()).toEqual([...CHECK_ERROR_CATEGORIES].sort());
  });
});
