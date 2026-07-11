/**
 * ReportCopy — the report-narrative truth matrix.
 *
 * Pure functions that turn a typed `ReportV2` report into user-facing copy.
 * This is the single source of truth for how report evidence gets worded:
 * risk percentages, mileage phrases, scope disclosures, sample-size clauses,
 * confidence caveats, the composed narrative, component-section copy, the
 * WhatsApp share message, and the demo-data banner. No other module should
 * hand-roll any of these strings.
 *
 * AutoSafe's served product is a make/model/age/mileage-band population
 * lookup, not a per-vehicle model (see root CLAUDE.md). scripts/claim_sweep.py
 * statically scans this file (components/*.tsx) for banned capability
 * claims — every function here is written to read honestly: honest about
 * scope, honest about sample size, and never fabricating a number or a
 * mileage phrase the evidence doesn't support.
 *
 * No fabrication, enforced here:
 *  - a null or zero sample size never renders as a number (buildSampleSizeClause,
 *    sampleSizeBadge)
 *  - a `missing` mileage source never produces a mileage phrase or header
 *    value (buildMileagePhrase, mileageHeaderValue)
 *  - nothing here claims vehicle-specific modelling — copy is always phrased
 *    in terms of "vehicles like this" / "similar vehicles"
 *
 * These are pure functions: no I/O, no randomness, no mutation of the
 * `report` argument. Zero JSX by design — this module is copy logic, not
 * rendering.
 */
import type { ReportV2, MatchScope } from '../types';

/** The report's headline confidence classification (mirrors report.risk.confidence). */
export type Confidence = ReportV2['risk']['confidence'];

export interface RiskPercentDisplay {
  /** Rounded percentage to display (nearest 5 when approximate). */
  value: number;
  /** True when the value has been coarsened (Very Low confidence). */
  approximate: boolean;
  /** Ready-to-render text: "23%" or "roughly 25%". */
  text: string;
}

export interface ComponentsSectionCopy {
  /** Whether the components section should render at all. */
  show: boolean;
  /** Section caption, shown only when there are components to list. */
  caption: string | null;
  /** Empty-state copy, shown only when the section is visible but has nothing to list. */
  emptyStateText: string | null;
}

/**
 * Exhaustiveness guard for switches over the report's literal-union fields.
 * If the backend contract ever grows a new enum member without a matching
 * case here, this fails at compile time (TS: argument not assignable to
 * `never`) rather than silently falling through to unreviewed copy.
 */
function assertUnreachable(value: never, context: string): never {
  throw new Error(`ReportCopy: unhandled ${context} value: ${JSON.stringify(value)}`);
}

/**
 * The single risk-number derivation for the whole UI. Normally a plain
 * rounded percentage; under Very Low confidence the evidence is thin enough
 * that a precise-looking number would overstate what we know, so it's
 * coarsened to the nearest 5 and marked `approximate` / prefixed "roughly".
 */
export function riskPercentDisplay(failureRisk: number, confidence: Confidence): RiskPercentDisplay {
  if (confidence === 'Very Low') {
    const value = Math.round((failureRisk * 100) / 5) * 5;
    return { value, approximate: true, text: `roughly ${value}%` };
  }
  const value = Math.round(failureRisk * 100);
  return { value, approximate: false, text: `${value}%` };
}

/**
 * States, in plain language, exactly how closely the served evidence
 * matches this vehicle. This is the honesty backbone of the report: it is
 * derived for every match_scope, never hidden behind a narrower-looking
 * claim.
 */
export function buildScopeDisclosure(report: ReportV2): string {
  const { make, model } = report.vehicle;
  const scope: MatchScope = report.evidence.match_scope;

  switch (scope) {
    case 'exact_band':
      return `This result is based on ${make} ${model} vehicles of a similar age and mileage.`;
    case 'age_band_only':
      return `This result is based on ${make} ${model} vehicles of a similar age — we didn't have enough mileage-matched data to narrow it further.`;
    case 'model_average':
      return `This result is based on all ${make} ${model} vehicles in our data, across all ages and mileages.`;
    case 'population_default':
      return `We don't have enough ${make} ${model} data yet, so this is the average across all vehicles we've checked.`;
    case 'unavailable':
      return `Our comparison data is temporarily unavailable, so this is the overall average across all vehicles — not a result for this ${make} ${model}.`;
    default:
      return assertUnreachable(scope, 'evidence.match_scope');
  }
}

/**
 * Sample-size sentence for the narrative. Null-means-unknown is preserved:
 * a null or zero total_tests renders nothing (caller omits) rather than
 * "Based on 0 MOT tests...".
 */
export function buildSampleSizeClause(report: ReportV2): string | null {
  const total = report.evidence.total_tests;
  if (total === null || total <= 0) return null;
  return `Based on ${total.toLocaleString('en-GB')} MOT tests of similar vehicles.`;
}

/**
 * Same sample-size fact as buildSampleSizeClause, but for UI slots (badges,
 * chips) that must always render something rather than being omitted.
 */
export function sampleSizeBadge(report: ReportV2): string {
  const total = report.evidence.total_tests;
  if (total === null || total <= 0) return 'Sample size unavailable';
  return `${total.toLocaleString('en-GB')} tests`;
}

/**
 * The mileage clause used inside the narrative sentence. Returns null when
 * the mileage source is `missing` (or, defensively, when there's no numeric
 * value to report even for a non-missing source) — no other function may
 * substitute a mileage phrase in that case.
 */
export function buildMileagePhrase(report: ReportV2): string | null {
  const { effective_value, source, observed_at, anomaly, unit_converted } = report.mileage;
  if (effective_value === null) return null;
  const n = effective_value.toLocaleString();

  switch (source) {
    case 'missing':
      return null;
    case 'user_entered':
      return `based on the ${n} miles you entered`;
    case 'observed_mot': {
      let phrase = `based on ${n} miles recorded at its MOT${observed_at ? ` on ${formatDateGB(observed_at)}` : ''}`;
      if (anomaly) phrase += ' (an inconsistent newer reading was ignored)';
      if (unit_converted) phrase += ' (converted from kilometres)';
      return phrase;
    }
    case 'estimated':
      return `based on an estimated ${n} miles for a vehicle of this age`;
    default:
      return assertUnreachable(source, 'mileage.source');
  }
}

/**
 * Mileage value for header/summary slots. Same missing-source rule as
 * buildMileagePhrase: never "0 miles", never "— miles" — the slot is
 * omitted (null) instead.
 */
export function mileageHeaderValue(report: ReportV2): string | null {
  const { effective_value, source } = report.mileage;
  if (effective_value === null) return null;
  const n = effective_value.toLocaleString();

  switch (source) {
    case 'missing':
      return null;
    case 'user_entered':
    case 'observed_mot':
      return `${n} miles`;
    case 'estimated':
      return `~${n} miles (estimated)`;
    default:
      return assertUnreachable(source, 'mileage.source');
  }
}

/**
 * Caveat sentence for thin-evidence confidence levels. High/Medium
 * confidence needs no caveat; Low and Very Low get progressively more
 * direct language about how much weight to put on the figure.
 */
export function buildConfidenceCaveat(report: ReportV2): string | null {
  const confidence = report.risk.confidence;
  switch (confidence) {
    case 'High':
    case 'Medium':
      return null;
    case 'Low':
      return 'This estimate is based on limited data — treat it as a guide rather than a precise figure.';
    case 'Very Low':
      return 'Very limited data is available here — treat this as a rough indication only.';
    default:
      return assertUnreachable(confidence, 'risk.confidence');
  }
}

/**
 * Composes the full narrative paragraph: risk + mileage clause, sample
 * size, scope disclosure, confidence caveat — in that order, joined with
 * single spaces. This replaces the old age-band/mileage-band template
 * ("this 2015 old vehicle with 50000 miles..."); no band values are ever
 * interpolated here.
 *
 * Note/scope dedupe (flagged for the orchestrator): report_service.py
 * derives `note` as a pure function of `evidence.match_scope`
 * (`_NOTE_BY_SCOPE` / NOTE_POPULATION_DEFAULT / NOTE_UNAVAILABLE — see
 * report_service.py lines ~66-92, 569-592) — the two always carry the same
 * fact, just with different wording, and `note` is None whenever
 * match_scope is EXACT_BAND. report_service.py is also outside
 * claim_sweep's SURFACES list, so `note` strings are never swept for
 * banned claims the way this file is. For both reasons — guaranteed
 * redundancy AND unreviewed provenance — this function always prefers the
 * scope disclosure and drops `report.note` entirely, rather than render an
 * un-reviewed backend string. Revisit if `note` is ever repurposed to
 * carry information beyond its mapped scope.
 */
export function buildNarrative(report: ReportV2): string {
  const { vehicle, risk } = report;
  const riskText = riskPercentDisplay(risk.failure_risk, risk.confidence).text;
  const mileagePhrase = buildMileagePhrase(report);
  const yearPrefix = vehicle.year ? `${vehicle.year} ` : '';
  const mileageSuffix = mileagePhrase ? `, ${mileagePhrase}` : '';
  const sentence1 = `A ${yearPrefix}${vehicle.make} ${vehicle.model} like this has a ${riskText} chance of an MOT failure${mileageSuffix}.`;

  const sampleSizeClause = buildSampleSizeClause(report);
  const scopeDisclosure = buildScopeDisclosure(report);
  const confidenceCaveat = buildConfidenceCaveat(report);

  return [sentence1, sampleSizeClause, scopeDisclosure, confidenceCaveat]
    .filter((part): part is string => part !== null)
    .join(' ');
}

/**
 * Components-section visibility + copy matrix. `available: false` hides the
 * section (and the repair-estimate section alongside it, by the caller
 * gating on the same flag) entirely; when available, shows either the
 * caption (items present) or empty-state copy (no items) — never both.
 */
export function componentsSectionCopy(report: ReportV2): ComponentsSectionCopy {
  const { available, items } = report.components;
  if (!available) {
    return { show: false, caption: null, emptyStateText: null };
  }
  if (items && items.length > 0) {
    return {
      show: true,
      caption: 'Components most often linked to MOT failure for similar vehicles — not a diagnosis of this vehicle.',
      emptyStateText: null,
    };
  }
  return {
    show: true,
    caption: null,
    emptyStateText: 'No component stood out as higher-risk for similar vehicles.',
  };
}

/** Fixed caption for the repair-estimate section. */
export function repairEstimateCaption(): string {
  return 'Indicative repair-cost range for similar vehicles, not a quote.';
}

/**
 * WhatsApp share message. Null when there's no share link to send. Takes
 * only `report` — ReportV2 has no postcode field at all, so a postcode
 * cannot leak into a shared message via this function.
 */
export function buildWhatsAppMessage(report: ReportV2): string | null {
  if (report.share_url === null) return null;
  const { vehicle, risk } = report;
  const riskText = riskPercentDisplay(risk.failure_risk, risk.confidence).text;
  const yearPrefix = vehicle.year ? `${vehicle.year} ` : '';
  return `My ${yearPrefix}${vehicle.make} ${vehicle.model} has a ${riskText} MOT failure risk. Check yours free: ${report.share_url}`;
}

/** Banner shown when the report was produced from demo data, not a real lookup. */
export function demoBanner(report: ReportV2): string | null {
  return report.vehicle_data_source === 'demo' ? 'Demonstration data — not a real vehicle lookup.' : null;
}

/**
 * en-GB short date, e.g. "12 Mar 2025". Forces UTC interpretation so the
 * result is stable regardless of the host machine's timezone — robust to
 * both bare ISO dates ("2025-03-12") and full timestamps
 * ("2025-03-12T23:45:00.000Z").
 */
export function formatDateGB(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
}
