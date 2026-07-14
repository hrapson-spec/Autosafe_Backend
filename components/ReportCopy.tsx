/**
 * ReportCopy — the report-narrative truth matrix.
 *
 * Pure functions that turn a typed `ReportV2` report into user-facing copy.
 * This is the single source of truth for how report evidence gets worded:
 * risk percentages, mileage phrases, scope disclosures, sample-size clauses,
 * confidence caveats, the composed narrative, component-section copy, the
 * last-recorded-mileage statement, the population-average badge, the
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
 *    value (buildMileagePhrase, mileageHeaderValue), and never a dated
 *    last-recorded-mileage line (lastRecordedMileageLine returns null for a
 *    missing or undated reading rather than an undated claim)
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

export function hasVehicleComparison(report: ReportV2): boolean {
  return ['exact_band', 'age_band_only', 'model_average'].includes(report.evidence.match_scope);
}

export function failureRateLabel(report: ReportV2): string {
  return hasVehicleComparison(report)
    ? 'Comparable-vehicle MOT failure rate'
    : 'Dataset-wide reference MOT failure rate';
}

/**
 * Fixed badge shown next to every displayed rate, regardless of match
 * scope: every served rate is a population/cohort lookup, never a
 * per-vehicle prediction, so this badge and its tooltip apply uniformly —
 * unlike failureRateLabel, which varies its wording by match scope, this
 * is one constant pair for every report.
 */
export function populationBadge(): { label: string; title: string } {
  return {
    label: 'Population average',
    title: 'A recorded failure rate for a group of similar vehicles — not a prediction for this vehicle.',
  };
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

/** Keep the dataset-wide reference numerically tied to its primary aggregate.
 * Very-low confidence there means "not vehicle matched", not that the
 * aggregate itself should be shifted to a nearest-five display. */
export function reportRateDisplay(report: ReportV2): RiskPercentDisplay {
  if (!hasVehicleComparison(report)) {
    const value = Math.round(report.risk.failure_risk * 100);
    return { value, approximate: false, text: `${value}%` };
  }
  return riskPercentDisplay(report.risk.failure_risk, report.risk.confidence);
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
      return `This comparison uses ${make} ${model} records in the matched age and mileage bands.`;
    case 'age_band_only': {
      // Two distinct reasons mileage went unused: genuinely sparse
      // mileage-matched data (the original case) vs. no reliable recorded
      // mileage at all (missing or anomalous). Same sentence shape either
      // way; only the reason clause differs.
      const reason = report.mileage.source === 'missing'
        ? 'no reliable recorded mileage was available'
        : "there wasn't enough mileage-matched data";
      return `This comparison uses ${make} ${model} records in the matched age band; mileage was not used because ${reason}.`;
    }
    case 'model_average':
      return `This comparison uses all ${make} ${model} records in the dataset, across all age and mileage bands.`;
    case 'population_default':
      return `We don't have enough ${make} ${model} group data, so this is the dataset-wide reference rate.`;
    case 'unavailable':
      return `Our comparison data is temporarily unavailable, so this is the dataset-wide reference rate — not a ${make} ${model} result.`;
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
  return `Based on ${total.toLocaleString('en-GB')} recorded MOT ${total === 1 ? 'test' : 'tests'} in that comparison group.`;
}

/**
 * Same sample-size fact as buildSampleSizeClause, but for UI slots (badges,
 * chips) that must always render something rather than being omitted.
 */
export function sampleSizeBadge(report: ReportV2): string {
  const total = report.evidence.total_tests;
  if (total === null || total <= 0) {
    return hasVehicleComparison(report)
      ? 'Sample size unavailable'
      : 'Vehicle-matched sample unavailable';
  }
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
      return `using a mileage band selected from the ${n} miles you entered`;
    case 'observed_mot': {
      let phrase = `using a mileage band selected from ${n} miles recorded at its MOT${observed_at ? ` on ${formatDateGB(observed_at)}` : ''}`;
      if (anomaly) phrase += ' (an inconsistent newer reading was ignored)';
      if (unit_converted) phrase += ' (converted from kilometres)';
      return phrase;
    }
    case 'estimated':
      return `using a mileage band selected from an estimated ${n} miles for a vehicle of this age`;
    default:
      return assertUnreachable(source, 'mileage.source');
  }
}

/**
 * Mileage value for header/summary slots. Same missing-source rule as
 * buildMileagePhrase: never "0 miles", never "— miles" — the slot is
 * omitted (null) instead.
 *
 * observed_mot is always qualified "(last recorded MOT mileage)" — this is
 * the most recent odometer reading on file, which may be months old, and
 * must never be presented (or misread) as the vehicle's *current* mileage.
 */
export function mileageHeaderValue(report: ReportV2): string | null {
  const { effective_value, source } = report.mileage;
  if (effective_value === null) return null;
  const n = effective_value.toLocaleString();

  switch (source) {
    case 'missing':
      return null;
    case 'user_entered':
      return `${n} miles`;
    case 'observed_mot':
      // Explicit 'en-GB' here (matching buildSampleSizeClause / sampleSizeBadge
      // elsewhere in this file), computed separately from the shared `n`
      // above so the user_entered/estimated branches stay byte-identical.
      return `${effective_value.toLocaleString('en-GB')} miles (last recorded MOT mileage)`;
    case 'estimated':
      return `~${n} miles (estimated)`;
    default:
      return assertUnreachable(source, 'mileage.source');
  }
}

/**
 * Standalone "as of" mileage statement for a dedicated header/detail slot —
 * distinct from mileageHeaderValue's compact inline value (which this
 * complements, not replaces): names the MOT test date the effective
 * mileage came from, and discloses the pre-conversion km figure when a
 * unit conversion happened.
 *
 * Null whenever there is no dateable MOT-observed reading to report: a
 * `missing` mileage source, or (defensively) any source lacking an
 * `observed_at` date — never an undated claim. In practice this also
 * excludes user_entered/estimated mileage, since neither ever carries an
 * observed_at date (ReportMileageV2.observed_at is populated only for the
 * observed-MOT reading path).
 */
export function lastRecordedMileageLine(report: ReportV2): string | null {
  const { effective_value, source, observed_at, unit_converted, original_value } = report.mileage;
  if (source === 'missing') return null;
  if (!observed_at) return null;
  if (effective_value === null) return null;

  const n = effective_value.toLocaleString('en-GB');
  let line = `Last recorded MOT mileage: ${n} miles on ${formatDateGB(observed_at)}`;
  if (unit_converted && original_value != null) {
    line += ` — converted from ${original_value.toLocaleString('en-GB')} km`;
  }
  return line;
}

/**
 * Mileage provenance for comparison scopes that did not use mileage. This
 * keeps observed/user/estimated context visible without implying that it
 * narrowed an age-only, model-wide, or dataset-wide rate.
 */
function buildUnmatchedMileageDisclosure(report: ReportV2): string | null {
  const { effective_value, source, observed_at, anomaly, unit_converted } = report.mileage;
  if (source === 'missing') {
    // New-state copy (Release 1): the backend now rejects an anomalous
    // reading outright (source becomes 'missing') rather than silently
    // substituting an inconsistent one — see the observed_mot anomaly
    // suffix below, kept only for legacy payloads that still show a
    // substituted reading. A genuinely absent reading (anomaly: false)
    // still has nothing to disclose here.
    return anomaly
      ? "Recorded mileage was not used: the vehicle's MOT mileage history is inconsistent, so no reading could be trusted."
      : null;
  }
  if (effective_value === null) return null;
  const n = effective_value.toLocaleString();
  let context: string;

  switch (source) {
    case 'user_entered':
      context = `${n} miles entered by the user`;
      break;
    case 'observed_mot':
      context = `${n} miles recorded at its MOT${observed_at ? ` on ${formatDateGB(observed_at)}` : ''}`;
      // Legacy rendering only: current backend logic never substitutes an
      // inconsistent reading silently (an anomalous reading now makes
      // source 'missing', handled above) — this branch stays reachable so
      // an old persisted payload recorded before that change still renders
      // honestly.
      if (anomaly) context += ' (an inconsistent newer reading was ignored)';
      if (unit_converted) context += ' (converted from kilometres)';
      break;
    case 'estimated':
      context = `an estimated ${n} miles for a vehicle of this age`;
      break;
    default:
      return assertUnreachable(source, 'mileage.source');
  }

  const scope = report.evidence.match_scope;
  const reason = scope === 'age_band_only'
    ? 'the comparison only matched the age band'
    : scope === 'model_average'
      ? 'the comparison is model-wide'
      : 'the displayed rate is the dataset-wide reference';
  return `Mileage context: ${context}. This mileage was not used because ${reason}.`;
}

/**
 * Caveat sentence for thin-evidence confidence levels. High/Medium
 * confidence needs no caveat; Low and Very Low get progressively more
 * direct language about how much weight to put on the figure.
 */
export function buildConfidenceCaveat(report: ReportV2): string | null {
  if (!hasVehicleComparison(report)) {
    return 'No vehicle-matched evidence is available — treat this dataset reference as general context only.';
  }
  const confidence = report.risk.confidence;
  switch (confidence) {
    case 'High':
    case 'Medium':
      return null;
    case 'Low':
      return 'This rate comes from limited data — treat it as broad context rather than a precise vehicle-level figure.';
    case 'Very Low':
      return 'Very limited data is available here — treat this as broad context only.';
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
  const { vehicle } = report;
  const riskText = reportRateDisplay(report).text;
  const scope = report.evidence.match_scope;
  let sentence1: string;

  switch (scope) {
    case 'exact_band': {
      const mileagePhrase = buildMileagePhrase(report);
      sentence1 = `The matched ${vehicle.make} ${vehicle.model} age-and-mileage group has a recorded MOT failure rate of ${riskText}${mileagePhrase ? `, ${mileagePhrase}` : ''}.`;
      break;
    }
    case 'age_band_only':
      sentence1 = `The matched ${vehicle.make} ${vehicle.model} age group has a recorded MOT failure rate of ${riskText}.`;
      break;
    case 'model_average':
      sentence1 = `Across all ${vehicle.make} ${vehicle.model} records in the comparison data, the recorded MOT failure rate is ${riskText}.`;
      break;
    case 'population_default':
    case 'unavailable':
      sentence1 = `The dataset-wide reference MOT failure rate shown here is ${riskText}; it is not a vehicle-matched result.`;
      break;
    default:
      return assertUnreachable(scope, 'evidence.match_scope');
  }

  const unmatchedMileageDisclosure = scope === 'exact_band'
    ? null
    : buildUnmatchedMileageDisclosure(report);

  const sampleSizeClause = buildSampleSizeClause(report);
  const scopeDisclosure = buildScopeDisclosure(report);
  const confidenceCaveat = buildConfidenceCaveat(report);

  return [sentence1, unmatchedMileageDisclosure, sampleSizeClause, scopeDisclosure, confidenceCaveat]
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
      caption: 'Indicative estimates: components most often linked to MOT failure among similar vehicles — not a diagnosis of this vehicle.',
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
  if (!report.persistence.share_available || report.share_url === null) return null;
  const { vehicle } = report;
  const riskText = reportRateDisplay(report).text;
  if (!hasVehicleComparison(report)) {
    return `This report shows a dataset-wide reference MOT failure rate of ${riskText}, not a vehicle-matched result. View it: ${report.share_url}`;
  }
  const yearPrefix = vehicle.year ? `${vehicle.year} ` : '';
  return `Comparable ${yearPrefix}${vehicle.make} ${vehicle.model} vehicles have a recorded MOT failure rate of ${riskText}. View the report: ${report.share_url}`;
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
