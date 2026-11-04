import type { Assessment, Band, DisplayPolicy } from '../api/types';

/**
 * The service decides the rounded percentage, the band and how they were produced (`display_policy`).
 * This module only labels what it is given. BAND_LABEL is the fallback when an assessment carries no policy.
 */
export const BAND_LABEL: Record<Band, string> = { low: 'Low', medium: 'Medium', high: 'High' };

export const ESTIMATE_WORDING = 'estimated chance of failing or needing rectification during the MOT';

export interface ShownEstimate { percent: number; band: Band; bandLabel: string }

export function qualifiedBands(a: Assessment | null | undefined): string | null {
  const p = a?.display_policy;
  return a?.state === 'available' && a.result && p?.display_state === 'bands' && p.operational_qualified === true
    && p.provisional === false && p.model_version && p.policy_version && !p.suppression_reason
    ? p.band_labels[a.result.band] : null;
}

export function shownEstimate(a: Assessment | null | undefined): ShownEstimate | null {
  if (!a || a.state !== 'available' || !a.result) return null;
  const p = a.display_policy;
  if (p?.display_state !== 'simulated' && !(p?.display_state === 'percentage' && p.operational_qualified === true && p.provisional === false && p.model_version && p.policy_version && !p.suppression_reason)) return null;
  if (a.result.percent_rounded == null) return null;
  const label = a.display_policy?.band_labels?.[a.result.band] ?? BAND_LABEL[a.result.band];
  return { percent: a.result.percent_rounded, band: a.result.band, bandLabel: label };
}

/** "Rounded to the nearest 5 percentage points by the service." (the step comes from the service). */
export function roundingWording(p: DisplayPolicy | null | undefined): string {
  return p ? `Rounded to the nearest ${p.rounding_pp} percentage points by the service.` : 'Rounded by the service.';
}

export interface BandLegendRow { band: Band; label: string; range: string }

/** Band ranges as the service defined them; null when the assessment carries no policy. */
export function bandLegend(p: DisplayPolicy | null | undefined): BandLegendRow[] | null {
  if (!p || !(p.display_state === 'simulated' || (p.display_state === 'percentage' && p.operational_qualified === true && p.provisional === false && p.model_version && p.policy_version && !p.suppression_reason))) return null;
  const [lo, hi] = p.band_edges;
  return [
    { band: 'low', label: p.band_labels.low, range: `under ${lo}%` },
    { band: 'medium', label: p.band_labels.medium, range: `${lo}% to under ${hi}%` },
    { band: 'high', label: p.band_labels.high, range: `${hi}% or more` },
  ];
}

/** Shown beside every free-text notes input and in onboarding (Henri, 2026-10-25). */
export const NOTES_HINT = "Don't include customer names, phone numbers or other customer details.";

/** Shown on the briefing: during the pilot the estimate is for workshop staff only. */
export const STAFF_ONLY_NOTE = 'For workshop staff only. Not a defect finding.';
