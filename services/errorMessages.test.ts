import { describe, it, expect } from 'vitest';
import { mapErrorToMessage, ReportApiError } from './errorMessages';
import type { ApiErrorCode } from '../types';

type Code = ApiErrorCode | 'network_error' | 'invalid_response';

// The exact copy table this module must produce, transcribed independently
// from the product-copy spec (not imported from errorMessages.ts) so this
// test actually pins the wording rather than just checking it's non-empty.
const EXPECTED_MESSAGES: Record<Code, string> = {
  invalid_registration: "That doesn't look like a valid UK registration — please check and try again.",
  vehicle_not_found:
    "We couldn't find that registration in the MOT database. Double-check the plate — brand-new vehicles may not appear yet.",
  dvsa_unavailable:
    'The official vehicle-data service is temporarily unavailable. Your details are fine — please try again in a few minutes.',
  rate_limited: 'Too many checks in a short time — please wait a minute and try again.',
  report_not_found: "That report link isn't valid — it may be incomplete.",
  report_expired: 'This report link has expired. Run a fresh check to get an up-to-date result.',
  storage_unavailable: 'Saved reports are temporarily unavailable — please try again shortly.',
  internal_error: 'Something went wrong on our side — please try again.',
  undeclared_parameter: 'Something went wrong on our side — please try again.',
  network_error: "We couldn't reach AutoSafe — check your connection and try again.",
  invalid_response: 'We received an unexpected response — please try again.',
};

const ALL_CODES = Object.keys(EXPECTED_MESSAGES) as Code[];

// internal_error and undeclared_parameter intentionally share the generic
// fallback copy -- excluded from the "every message is distinguishable"
// check below, but still covered by the exact-text and non-empty checks.
const SHARED_FALLBACK_CODES = new Set<Code>(['internal_error', 'undeclared_parameter']);

describe('mapErrorToMessage', () => {
  it('returns nonempty copy for every ApiErrorCode plus network_error and invalid_response', () => {
    for (const code of ALL_CODES) {
      const message = mapErrorToMessage(code);
      expect(typeof message).toBe('string');
      expect(message.length).toBeGreaterThan(0);
    }
  });

  it('matches the exact product copy for every code (table-driven)', () => {
    for (const code of ALL_CODES) {
      expect(mapErrorToMessage(code), `code: ${code}`).toBe(EXPECTED_MESSAGES[code]);
    }
  });

  it('gives every code distinct-enough copy, except the two that intentionally share fallback text', () => {
    const seenBy = new Map<string, Code>();
    for (const code of ALL_CODES) {
      if (SHARED_FALLBACK_CODES.has(code)) continue;
      const message = mapErrorToMessage(code);
      const collidesWith = seenBy.get(message);
      expect(collidesWith, `"${code}" collides with "${collidesWith}" on: ${message}`).toBeUndefined();
      seenBy.set(message, code);
    }
  });

  it('falls back to the generic internal_error copy for a code it does not recognise', () => {
    expect(mapErrorToMessage('totally_unrecognised_code')).toBe(EXPECTED_MESSAGES.internal_error);
    expect(mapErrorToMessage('')).toBe(EXPECTED_MESSAGES.internal_error);
  });
});

describe('ReportApiError', () => {
  it('is a real Error subclass carrying code/status/correlationId', () => {
    const err = new ReportApiError('rate_limited', mapErrorToMessage('rate_limited'), 429, 'corr-1');
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ReportApiError);
    expect(err.code).toBe('rate_limited');
    expect(err.status).toBe(429);
    expect(err.correlationId).toBe('corr-1');
    expect(err.message).toBe(EXPECTED_MESSAGES.rate_limited);
  });

  it('defaults status and correlationId to null when omitted (e.g. a network_error)', () => {
    const err = new ReportApiError('network_error', mapErrorToMessage('network_error'));
    expect(err.status).toBeNull();
    expect(err.correlationId).toBeNull();
  });
});
