import type { Assessment, BriefingExtras } from '../api/types';

export interface Gap { code: string; text: string }

/**
 * Deterministic evidence-gap templates (R8: no LLM). `extras === null` means the
 * service did not provide dated source records / follow-up questions (the
 * proposed briefing endpoint is not in the contract yet).
 */
export function evidenceGaps(a: Assessment | null | undefined, extras: BriefingExtras | null): Gap[] {
  const gaps: Gap[] = [];
  if (!a || a.state !== 'available') {
    gaps.push({ code: 'no_estimate', text: a?.reason_text ?? (a?.state === 'pending' || !a ? 'The assessment has not finished.' : 'No estimate is available for this booking.') });
  } else {
    gaps.push({ code: 'history_only', text: 'The estimate is built from recorded MOT history only. Nothing has inspected the vehicle.' });
    gaps.push({ code: 'mileage_since_last_test', text: 'Mileage since the last recorded test is not known.' });
    if (a.evidence?.prior_test_count === 0) gaps.push({ code: 'no_prior_tests', text: 'No earlier tests are recorded.' });
  }
  if (extras === null) {
    gaps.push({ code: 'records_not_provided', text: 'Individual test records, recorded advisories and follow-up questions are not available from the service yet.' });
  }
  return gaps;
}
