import type { Assessment, Band } from '../api/types';

/**
 * The service decides the rounded percentage and the band (contract: "The
 * client must not compute bands"). This module only labels what it is given.
 */
export const BAND_LABEL: Record<Band, string> = { low: 'Low', medium: 'Medium', high: 'High' };

export const ESTIMATE_WORDING = 'estimated chance of failing or needing rectification during the MOT';

export interface ShownEstimate { percent: number; band: Band; bandLabel: string }

export function shownEstimate(a: Assessment | null | undefined): ShownEstimate | null {
  if (!a || a.state !== 'available' || !a.result) return null;
  return { percent: a.result.percent_rounded, band: a.result.band, bandLabel: BAND_LABEL[a.result.band] };
}

/** Shown beside every free-text notes input and in onboarding (Henri, 2026-10-25). */
export const NOTES_HINT = "Don't include customer names, phone numbers or other customer details.";

/** Shown on the briefing: during the pilot the estimate is for workshop staff only. */
export const STAFF_ONLY_NOTE = 'For workshop staff only. Not a defect finding.';
