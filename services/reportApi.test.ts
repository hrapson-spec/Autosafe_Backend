import { describe, it, expect, vi, afterEach } from 'vitest';
import { createReport, getReport } from './reportApi';
import { ReportApiError, mapErrorToMessage } from './errorMessages';
import { fixtureExactHigh, fixtureErrorEnvelopes } from '../fixtures/reportResponses';
import type { ApiErrorEnvelope } from '../types';

const API_BASE = import.meta.env.VITE_API_URL || '';
const FIXED_UUID = '11111111-1111-4111-8111-111111111111';

function mockResponse(status: number, body: unknown): Response {
  const ok = status >= 200 && status < 300;
  return {
    ok,
    status,
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('createReport', () => {
  it('POSTs registration + a deterministic idempotency_key, and omits postcode when empty', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => FIXED_UUID });
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh));
    vi.stubGlobal('fetch', fetchMock);

    await createReport('AB12CDE', '');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE}/api/v2/reports`);
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' });

    const sentBody = JSON.parse(init.body as string);
    expect(sentBody).toEqual({
      registration: 'AB12CDE',
      idempotency_key: FIXED_UUID,
    });
    expect('postcode' in sentBody).toBe(false);
    expect('mileage_user' in sentBody).toBe(false);
  });

  it('includes postcode in the body when truthy', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh));
    vi.stubGlobal('fetch', fetchMock);

    await createReport('AB12CDE', 'SW1A 1AA');

    const [, init] = fetchMock.mock.calls[0];
    const sentBody = JSON.parse(init.body as string);
    expect(sentBody.postcode).toBe('SW1A 1AA');
  });

  it('never sends a mileage_user key in the request body, regardless of arguments (the backend 422s any sender of it)', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh));
    vi.stubGlobal('fetch', fetchMock);

    await createReport('AB12CDE', 'SW1A 1AA', 'some-idempotency-key');

    const [, init] = fetchMock.mock.calls[0];
    const sentBody = JSON.parse(init.body as string);
    expect('mileage_user' in sentBody).toBe(false);
  });

  it('uses a caller-supplied idempotency key so a logical retry can reuse it', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh));
    vi.stubGlobal('fetch', fetchMock);

    await createReport('AB12CDE', 'SW1A 1AA', 'stable-retry-key');

    const [, init] = fetchMock.mock.calls[0];
    const sentBody = JSON.parse(init.body as string);
    expect(sentBody.idempotency_key).toBe('stable-retry-key');
  });

  it('never puts registration or postcode in the URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh));
    vi.stubGlobal('fetch', fetchMock);

    await createReport('AB12CDE', 'SW1A 1AA');

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE}/api/v2/reports`);
    expect(url).not.toMatch(/AB12CDE|SW1A/);
  });

  it('returns the exact parsed object on a valid 200 (object identity, not a rebuilt copy)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh)));

    const result = await createReport('AB12CDE', 'SW1A 1AA');

    expect(result).toBe(fixtureExactHigh);
  });

  it('throws invalid_response when a 200 body does not satisfy isReportV2', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(200, { not: 'a report' })));

    const error: ReportApiError = await createReport('AB12CDE', '').catch((e) => e);

    expect(error).toBeInstanceOf(ReportApiError);
    expect(error.code).toBe('invalid_response');
    expect(error.status).toBe(200);
  });

  it('maps a 400 invalid_registration envelope to a typed error with the mapped (not server) message', async () => {
    const envelope = fixtureErrorEnvelopes.invalid_registration;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(400, envelope)));

    const error: ReportApiError = await createReport('A', '').catch((e) => e);

    expect(error).toBeInstanceOf(ReportApiError);
    expect(error.code).toBe('invalid_registration');
    expect(error.status).toBe(400);
    expect(error.correlationId).toBe(envelope.correlation_id);
    expect(error.message).toBe(mapErrorToMessage('invalid_registration'));
    expect(error.message).not.toBe(envelope.message);
  });

  it('maps 429 / 503 / 404 envelopes to their typed errors (table-driven)', async () => {
    const cases: Array<[number, ApiErrorEnvelope]> = [
      [429, fixtureErrorEnvelopes.rate_limited],
      [503, fixtureErrorEnvelopes.dvsa_unavailable],
      [404, fixtureErrorEnvelopes.vehicle_not_found],
    ];

    for (const [status, envelope] of cases) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(status, envelope)));

      const error: ReportApiError = await createReport('AB12CDE', '').catch((e) => e);

      expect(error).toBeInstanceOf(ReportApiError);
      expect(error.code).toBe(envelope.error_code);
      expect(error.status).toBe(status);
      expect(error.correlationId).toBe(envelope.correlation_id);
      expect(error.message).toBe(mapErrorToMessage(envelope.error_code));
    }
  });

  it('treats a non-ok legacy {detail} body as invalid_response, preserving the HTTP status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse(500, { detail: 'Internal Server Error' }))
    );

    const error: ReportApiError = await createReport('AB12CDE', '').catch((e) => e);

    expect(error).toBeInstanceOf(ReportApiError);
    expect(error.code).toBe('invalid_response');
    expect(error.status).toBe(500);
    expect(error.correlationId).toBeNull();
  });

  it('wraps a fetch rejection (offline/DNS/CORS) as network_error with no status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    const error: ReportApiError = await createReport('AB12CDE', '').catch((e) => e);

    expect(error).toBeInstanceOf(ReportApiError);
    expect(error.code).toBe('network_error');
    expect(error.status).toBeNull();
    expect(error.correlationId).toBeNull();
    expect(error.message).toBe(mapErrorToMessage('network_error'));
  });
});

describe('getReport', () => {
  it('GETs exactly `${API_BASE}/api/v2/reports/<token>` for a plain token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh));
    vi.stubGlobal('fetch', fetchMock);

    await getReport('sometoken123');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE}/api/v2/reports/sometoken123`);
    expect(init.method).toBe('GET');
  });

  it('encodes a token containing reserved URL characters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh));
    vi.stubGlobal('fetch', fetchMock);

    const rawToken = 'abc 123/def?x=1';
    await getReport(rawToken);

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE}/api/v2/reports/${encodeURIComponent(rawToken)}`);
  });

  it('never leaks a registration or postcode into the GET URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh));
    vi.stubGlobal('fetch', fetchMock);

    await getReport('sometoken123');

    const [url] = fetchMock.mock.calls[0];
    expect(url).not.toMatch(/registration|postcode/i);
  });

  it('returns the exact parsed object on success', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(200, fixtureExactHigh)));

    const result = await getReport('sometoken123');

    expect(result).toBe(fixtureExactHigh);
  });

  it('maps a 410 report_expired envelope to its typed error', async () => {
    const envelope = fixtureErrorEnvelopes.report_expired;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(410, envelope)));

    const error: ReportApiError = await getReport('expiredtoken').catch((e) => e);

    expect(error).toBeInstanceOf(ReportApiError);
    expect(error.code).toBe('report_expired');
    expect(error.status).toBe(410);
    expect(error.correlationId).toBe(envelope.correlation_id);
  });

  it('wraps a fetch rejection as network_error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    const error: ReportApiError = await getReport('sometoken123').catch((e) => e);

    expect(error).toBeInstanceOf(ReportApiError);
    expect(error.code).toBe('network_error');
  });
});
