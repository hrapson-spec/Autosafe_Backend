import type { Assessment, Briefing } from '../api/types';

export interface Gap { code: string; text: string }

/**
 * Evidence gaps about the ESTIMATE, written by the client (fixed wording, R8: no LLM). Gaps about the
 * recorded history (no test in 15 months, missing odometer, short history, ...) come from the service's
 * briefing rules and are shown after these, unchanged.
 */
export function estimateGaps(a: Assessment | null | undefined): Gap[] {
  if (!a || a.state !== 'available') {
    return [{ code: 'no_estimate', text: a?.reason_text ?? (a?.state === 'pending' || !a ? 'The assessment has not finished.' : 'No estimate is available for this booking.') }];
  }
  return [
    { code: 'history_only', text: 'The estimate is built from recorded MOT history only. Nothing has inspected the vehicle.' },
    { code: 'mileage_since_last_test', text: 'Mileage since the last recorded test is not known.' },
  ];
}

export function evidenceGaps(a: Assessment | null | undefined, briefing: Briefing | null): Gap[] {
  const gaps = estimateGaps(a);
  const add = (g: Gap) => { if (!gaps.some((x) => x.text === g.text)) gaps.push(g); };      // never list a sentence twice
  if (briefing === null) {
    add({ code: 'briefing_missing', text: 'The briefing could not be loaded, so no individual test records or follow-up questions are shown.' });
  } else if (briefing.status !== 'ready') {
    add({ code: 'briefing_not_ready', text: briefing.reason_text ?? 'The briefing is not ready yet: the recorded history has not been fetched.' });
  } else {
    briefing.evidence_gaps.forEach((g) => add({ code: g.rule_id, text: g.text }));
  }
  return gaps;
}
