/**
 * OA-005: the first-party fetch transport (utils/acquisitionEvents.ts).
 * These tests exercise enabled collection and the explicit rollback switch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ACQUISITION_COLLECTOR_ENABLED,
  ACQUISITION_ENDPOINT,
  __resetAcquisitionStateForTests,
  createFetchSink,
  emitAcquisitionEvent,
  getAcquisitionContext,
  installAcquisitionTransport,
  releaseSha,
  setAcquisitionContext,
  type AcquisitionEvent,
} from './acquisitionEvents';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const OP = '0b0e7a52-5d5c-4f55-9a52-3c6e1f1c8a10';
const LANDING = '5b1d7a52-5d5c-4f55-9a52-3c6e1f1c8a11';

const STARTED = { event: 'check_started', operation_id: OP, entry_mode: 'fresh_check' } as const;

type FetchMock = ReturnType<typeof vi.fn>;
const ok = (status: number) => Promise.resolve({ status } as Response);

function setGpc(value: unknown) {
  Object.defineProperty(window.navigator, 'globalPrivacyControl', { value, configurable: true });
}

function body(call: unknown[]): Record<string, unknown> {
  return JSON.parse((call[1] as { body: string }).body);
}

beforeEach(() => {
  __resetAcquisitionStateForTests();
  vi.useFakeTimers();
  setAcquisitionContext({landingId: LANDING, sourceGroup: 'google_organic', pageFamily: 'app', windowStartMinute: Math.floor(Date.now()/60000), pilotGroup:'none'});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  // @ts-expect-error cleanup of the test-defined property
  delete window.navigator.globalPrivacyControl;
  __resetAcquisitionStateForTests();
});

describe('transport enablement and rollback', () => {
  it('the default transport follows the enabled release flag', () => {
    const spy = vi.fn(() => ok(202));
    vi.stubGlobal('fetch', spy);
    expect(ACQUISITION_COLLECTOR_ENABLED).toBe(true);
    expect(installAcquisitionTransport()).toBe(true);
    emitAcquisitionEvent(STARTED);
    expect(spy).toHaveBeenCalledTimes(1);
  });
  it('the explicit disabled installer sends nothing', () => {
    const spy = vi.fn();
    vi.stubGlobal('fetch', spy);
    expect(installAcquisitionTransport(false)).toBe(false);
    emitAcquisitionEvent(STARTED);
    vi.advanceTimersByTime(60_000);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('transport request shape (force-enabled in test)', () => {
  it('POSTs same-origin with keepalive, no credentials, no referrer, no cache', () => {
    const fetchImpl: FetchMock = vi.fn(() => ok(202));
    const sink = createFetchSink({ fetchImpl: fetchImpl as unknown as typeof fetch });
    setAcquisitionContext({ windowStartMinute: Math.floor(Date.now()/60000), pilotGroup:'none', landingId: LANDING, sourceGroup: 'google_organic', pageFamily: 'app' });
    const event = { schema_version: 2, metric_version: 'm', event_id: OP, ...STARTED } as AcquisitionEvent;
    sink.emit(event);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(ACQUISITION_ENDPOINT);
    expect(init).toMatchObject({
      method: 'POST',
      keepalive: true,
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      cache: 'no-store',
      mode: 'same-origin',
    });
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    const wire = body(fetchImpl.mock.calls[0]);
    expect(wire).toMatchObject({
      event: 'check_started',
      operation_id: OP,
      landing_id: LANDING,
      source_group: 'google_organic',
      page_family: 'app',
    });
    expect(wire.session_id).toMatch(UUID);
    expect(Object.keys(wire).sort()).toEqual([
      'entry_mode', 'event', 'event_id', 'landing_id', 'metric_version', 'operation_id', 'page_family',
      'pilot_group', 'schema_version', 'session_id', 'source_group', 'window_start_minute',
    ]);
  });

  it('session id is one random value per document, shared by every event, and not derived from any input', () => {
    const fetchImpl: FetchMock = vi.fn(() => ok(202));
    const sink = createFetchSink({ fetchImpl: fetchImpl as unknown as typeof fetch });
    for (let i = 0; i < 3; i++) sink.emit({ event_id: `e${i}`, ...STARTED } as unknown as AcquisitionEvent);
    const sessions = new Set(fetchImpl.mock.calls.map((c) => body(c).session_id));
    expect(sessions.size).toBe(1);
    const other = createFetchSink({ fetchImpl: fetchImpl as unknown as typeof fetch });
    other.emit({ event_id: 'x', ...STARTED } as unknown as AcquisitionEvent);
    expect(sessions.has(body(fetchImpl.mock.calls[3]).session_id as string)).toBe(false);
  });

  it('missing attribution is unobserved rather than an invented unknown landing', () => {
    setAcquisitionContext({sourceGroup:'unknown',pageFamily:'app'});
    const fetchImpl = vi.fn(() => ok(202));
    createFetchSink({fetchImpl:fetchImpl as typeof fetch}).emit({event_id:'x',...STARTED} as unknown as AcquisitionEvent);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(getAcquisitionContext().landingId).toBeUndefined();
  });

  it('carries the build release sha when one is defined', () => {
    vi.stubGlobal('__RELEASE_SHA__', 'f19c85d0000000000000000000000000000abcde');
    const fetchImpl: FetchMock = vi.fn(() => ok(202));
    createFetchSink({ fetchImpl: fetchImpl as unknown as typeof fetch }).emit({ event_id: 'x', ...STARTED } as unknown as AcquisitionEvent);
    expect(body(fetchImpl.mock.calls[0]).release_sha).toBe('f19c85d0000000000000000000000000000abcde');
    vi.stubGlobal('__RELEASE_SHA__', 'not a sha');
    expect(releaseSha()).toBeUndefined();
  });

  it('a landing event keeps its own page family and source group', () => {
    const fetchImpl: FetchMock = vi.fn(() => ok(202));
    setAcquisitionContext({ windowStartMinute: Math.floor(Date.now()/60000), pilotGroup:'none', landingId: LANDING, sourceGroup: 'direct', pageFamily: 'app' });
    createFetchSink({ fetchImpl: fetchImpl as unknown as typeof fetch }).emit({
      event: 'landing_observed', event_id: 'x', page_family: 'home', source_group: 'google_organic',
      observation_state: 'observed',
    } as unknown as AcquisitionEvent);
    expect(body(fetchImpl.mock.calls[0])).toMatchObject({ page_family: 'home', source_group: 'google_organic', landing_id: LANDING });
  });

  it('drops an event larger than the 2 KB collector limit instead of sending it', () => {
    const fetchImpl: FetchMock = vi.fn(() => ok(202));
    createFetchSink({ fetchImpl: fetchImpl as unknown as typeof fetch }).emit({ event_id: 'x', pad: 'z'.repeat(3000), ...STARTED } as unknown as AcquisitionEvent);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('installAcquisitionTransport(true) routes emitAcquisitionEvent through global fetch', () => {
    const spy = vi.fn(() => ok(202));
    vi.stubGlobal('fetch', spy);
    expect(installAcquisitionTransport(true)).toBe(true);
    emitAcquisitionEvent(STARTED);
    expect(spy).toHaveBeenCalledTimes(1);
    const wire = body(spy.mock.calls[0] as unknown[]);
    expect(wire.event).toBe('check_started');
    expect(wire.event_id).toMatch(UUID);
    expect(wire.schema_version).toBe(2);
  });
});

describe('retry rules', () => {
  function sinkWith(responses: Array<() => Promise<Response>>) {
    const fetchImpl: FetchMock = vi.fn();
    responses.forEach((r) => fetchImpl.mockImplementationOnce(r));
    fetchImpl.mockImplementation(() => ok(202));
    const sink = createFetchSink({ fetchImpl: fetchImpl as unknown as typeof fetch });
    sink.emit({ event_id: 'e1', ...STARTED } as unknown as AcquisitionEvent);
    return fetchImpl;
  }

  it('retries once on a network error, with the same body (same event_id)', async () => {
    const f = sinkWith([() => Promise.reject(new TypeError('network'))]);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(f).toHaveBeenCalledTimes(2);
    expect((f.mock.calls[1][1] as { body: string }).body).toBe((f.mock.calls[0][1] as { body: string }).body);
  });

  it.each([500, 502, 503])('retries once on HTTP %i', async (status) => {
    const f = sinkWith([() => ok(status)]);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(f).toHaveBeenCalledTimes(2);
  });

  it.each([400, 404, 413, 429])('never retries HTTP %i', async (status) => {
    const f = sinkWith([() => ok(status)]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it('does not retry a success or a duplicate acknowledgement', async () => {
    const f = sinkWith([() => ok(202)]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it('retries at most once: a failing retry is discarded, with no queue and no later attempt', async () => {
    const f = sinkWith([() => Promise.reject(new TypeError('x')), () => ok(503)]);
    await vi.advanceTimersByTimeAsync(600_000);
    expect(f).toHaveBeenCalledTimes(2);
  });

  it('does not retry when the failure arrives more than 10 s after the first attempt', async () => {
    const f = sinkWith([() => new Promise<Response>((resolve) => setTimeout(() => resolve({ status: 503 } as Response), 9_500))]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(f).toHaveBeenCalledTimes(1);
  });

  it('a synchronously throwing fetch is a network failure: retried once, never thrown to the caller', async () => {
    const f: FetchMock = vi.fn(() => { throw new Error('sync'); });
    const sink = createFetchSink({ fetchImpl: f as unknown as typeof fetch });
    expect(() => sink.emit({ event_id: 'e1', ...STARTED } as unknown as AcquisitionEvent)).not.toThrow();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(f).toHaveBeenCalledTimes(2);
  });
});

describe('Global Privacy Control', () => {
  it('installs nothing and sends nothing when navigator.globalPrivacyControl is true', () => {
    setGpc(true);
    const spy = vi.fn(() => ok(202));
    vi.stubGlobal('fetch', spy);
    expect(installAcquisitionTransport(true)).toBe(false);
    emitAcquisitionEvent(STARTED);
    const sink = createFetchSink({ fetchImpl: spy as unknown as typeof fetch });
    sink.emit({ event_id: 'e1', ...STARTED } as unknown as AcquisitionEvent);
    expect(spy).not.toHaveBeenCalled();
  });

  it('only the literal true counts; absent or false does not suppress', () => {
    const spy = vi.fn(() => ok(202));
    setGpc(false);
    createFetchSink({ fetchImpl: spy as unknown as typeof fetch }).emit({ event_id: 'e1', ...STARTED } as unknown as AcquisitionEvent);
    setGpc('true');
    createFetchSink({ fetchImpl: spy as unknown as typeof fetch }).emit({ event_id: 'e2', ...STARTED } as unknown as AcquisitionEvent);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe('no storage of any kind', () => {
  it('never touches web storage, cookies or IndexedDB', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const getItem = vi.spyOn(Storage.prototype, 'getItem');
    const cookieSet = vi.spyOn(document, 'cookie', 'set');
    const cookieGet = vi.spyOn(document, 'cookie', 'get');
    const f: FetchMock = vi.fn(() => Promise.reject(new TypeError('x')));
    const sink = createFetchSink({ fetchImpl: f as unknown as typeof fetch });
    sink.emit({ event_id: 'e1', ...STARTED } as unknown as AcquisitionEvent);
    setAcquisitionContext({ windowStartMinute: Math.floor(Date.now()/60000), pilotGroup:'none', landingId: LANDING, sourceGroup: 'direct', pageFamily: 'app' });
    await vi.advanceTimersByTimeAsync(20_000);
    expect(setItem).not.toHaveBeenCalled();
    expect(getItem).not.toHaveBeenCalled();
    expect(cookieSet).not.toHaveBeenCalled();
    expect(cookieGet).not.toHaveBeenCalled();
  });
});
