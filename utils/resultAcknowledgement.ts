/**
 * Displayed-result classification (OA-004, MEASUREMENT.md "Testable result
 * rules").
 *
 * `classifyResult(report)` is a pure function: it inspects a report that the
 * UI is about to show (or has just committed) and returns bounded validity
 * flags and a typed outcome group. It never returns, and callers must never
 * transmit, the risk figure or any sample count; those are inspected here
 * and discarded. Nothing here performs I/O.
 *
 * What the flags mean (client-reported, see EVENT_SCHEMA_v1.md):
 *  - render_delivered: the designated final view is structurally able to
 *    display this report -- contract invariants and the headline-rate bound
 *    hold. A report that fails them is classified `error` and is never
 *    counted as delivered. (The caller additionally only asks for this
 *    classification from the committed final view, never from a spinner or
 *    an error fallback.)
 *  - supported_result: the primary-metric numerator rule: a validated
 *    vehicle_prediction/model_prediction, or a validated comparison with
 *    exact_band / age_band_only / model_average, finite rate in [0, 1],
 *    nonzero sample (comparison only) and visible scope. population_default
 *    and unavailable are never supported.
 *  - rate_valid / sample_nonzero / scope_visible: the individual checks.
 *    sample_nonzero is OMITTED (key absent) for model_prediction: a
 *    prediction carries no cohort counts.
 *
 * These flags are an acknowledgement that the app believes it showed a valid
 * result. They prove neither accuracy nor model qualification.
 */
import type { MatchScope, ReportV2, ResultKind } from '../types';
import { buildScopeDisclosure } from '../components/ReportCopy';

export type OutcomeGroup =
  | 'prediction'
  | 'exact_comparison'
  | 'broader_supported_comparison'
  | 'dataset_reference'
  | 'unavailable'
  | 'error';

export interface ResultClassification {
  render_delivered: boolean;
  supported_result: boolean;
  outcome_group: OutcomeGroup;
  rate_valid: boolean;
  /** Omitted entirely for the model_prediction scope / vehicle_prediction kind. */
  sample_nonzero?: boolean;
  scope_visible: boolean;
  /** null when the report carries an unrecognised result_kind. */
  result_kind: ResultKind | null;
  /** null when the report carries an unrecognised match_scope. */
  match_scope: MatchScope | null;
}

const RESULT_KINDS: readonly ResultKind[] = ['comparison', 'vehicle_prediction'];
const MATCH_SCOPES: readonly MatchScope[] = [
  'exact_band',
  'age_band_only',
  'model_average',
  'population_default',
  'unavailable',
  'model_prediction',
];
/** Scopes that name a vehicle-matched cohort and therefore must carry counts. */
const MATCHED_SCOPES: readonly MatchScope[] = ['exact_band', 'age_band_only', 'model_average'];

function isResultKind(value: unknown): value is ResultKind {
  return typeof value === 'string' && (RESULT_KINDS as readonly string[]).includes(value);
}

function isMatchScope(value: unknown): value is MatchScope {
  return typeof value === 'string' && (MATCH_SCOPES as readonly string[]).includes(value);
}

function isNonNegInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

/** Headline risk must be non-null, finite and within [0, 1]. */
function rateIsValid(report: ReportV2): boolean {
  const risk = (report as { risk?: { failure_risk?: unknown } }).risk;
  const value = risk?.failure_risk;
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
}

/**
 * Count invariants mirrored from report_contract.ReportEvidence.validate_counts.
 * Returns false when the evidence block is internally inconsistent.
 */
function countsConsistent(report: ReportV2, scope: MatchScope): boolean {
  const evidence = report.evidence;
  const total = evidence.total_tests;
  const failures = evidence.total_failures;
  if (total !== null && total !== undefined && !(isNonNegInt(total) && total > 0)) return false;
  if (failures !== null && failures !== undefined) {
    if (!isNonNegInt(failures)) return false;
    if (total === null || total === undefined) return false;
    if (failures > total) return false;
  }
  if (scope === 'model_prediction') {
    // A model prediction carries no cohort counts.
    return (total ?? null) === null && (failures ?? null) === null;
  }
  if ((MATCHED_SCOPES as readonly string[]).includes(scope)) {
    return (total ?? null) !== null;
  }
  return true;
}

/**
 * comparison sample rule: integer total_tests > 0 and any available failure
 * count within [0, total_tests].
 */
function sampleIsNonzero(report: ReportV2): boolean {
  const total = report.evidence.total_tests;
  const failures = report.evidence.total_failures;
  if (typeof total !== 'number' || !Number.isInteger(total) || total <= 0) return false;
  if (failures === null || failures === undefined) return true;
  return typeof failures === 'number' && Number.isInteger(failures) && failures >= 0 && failures <= total;
}

/**
 * Whether the scope/limitation label the UI shows for this state is actually
 * present.
 *
 * What the UI renders (components/ReportDashboard.tsx, ReportResult.tsx,
 * ReportCopy.tsx):
 *  - Always inline: ReportResult's heading/summary states the kind of result.
 *    A vehicle_prediction says "predicted chance"; a comparison says it
 *    "isn't a prediction" and names either "<make> <model> comparison" (a
 *    vehicle-matched scope) or "dataset-wide reference comparison".
 *  - Always in the DOM: ReportDashboard renders
 *    `buildScopeDisclosure(report)` in the "How this result was calculated"
 *    disclosure for every report. That text is the only place that
 *    distinguishes exact_band / age_band_only / model_average, and it is
 *    inside a <details> that is collapsed by default.
 *
 * Rule: the scope label is "visible" when buildScopeDisclosure produces
 * non-empty text for this scope (it throws on an unrecognised scope, which
 * is treated as not visible), and, for the vehicle-matched cohort scopes
 * whose label and text interpolate the vehicle, make and model are present.
 * Because the UI's own copy function is the evidence, the flag tracks what
 * is rendered rather than restating the scope. NOTE: this counts the
 * collapsed disclosure as present. If product wants the scope visible
 * without interaction, change this one function (the default-collapsed
 * disclosure would then not qualify for age_band_only / model_average).
 */
function scopeLabelPresent(report: ReportV2, scope: MatchScope): boolean {
  try {
    const text = buildScopeDisclosure(report);
    if (typeof text !== 'string' || text.trim().length === 0) return false;
  } catch {
    return false;
  }
  if ((MATCHED_SCOPES as readonly string[]).includes(scope)) {
    const make = report.vehicle?.make;
    const model = report.vehicle?.model;
    if (typeof make !== 'string' || make.trim() === '') return false;
    if (typeof model !== 'string' || model.trim() === '') return false;
  }
  return true;
}

const ERROR_CLASSIFICATION: ResultClassification = {
  render_delivered: false,
  supported_result: false,
  outcome_group: 'error',
  rate_valid: false,
  scope_visible: false,
  result_kind: null,
  match_scope: null,
};

export function classifyResult(report: ReportV2 | null | undefined): ResultClassification {
  if (report === null || report === undefined || typeof report !== 'object') {
    return { ...ERROR_CLASSIFICATION };
  }
  const kind = isResultKind(report.result_kind) ? report.result_kind : null;
  const scope = isMatchScope(report.evidence?.match_scope) ? report.evidence.match_scope : null;
  const rate_valid = rateIsValid(report);
  const omitSample = scope === 'model_prediction' || kind === 'vehicle_prediction';

  const base = {
    rate_valid,
    result_kind: kind,
    match_scope: scope,
  };

  const failed = (scope_visible: boolean, sample?: boolean): ResultClassification => ({
    render_delivered: false,
    supported_result: false,
    outcome_group: 'error',
    ...base,
    ...(omitSample || sample === undefined ? {} : { sample_nonzero: sample }),
    scope_visible,
  });

  if (kind === null || scope === null) {
    return failed(false);
  }

  const sample = omitSample ? undefined : sampleIsNonzero(report);
  const scope_visible = scopeLabelPresent(report, scope);

  // Contract invariants (report_contract.ReportResponse.validate_report_consistency):
  // vehicle_prediction <=> model_prediction scope, and model_v55 source <=>
  // vehicle_prediction. A contradictory report is never delivered, never supported.
  const predictionKind = kind === 'vehicle_prediction';
  const predictionScope = scope === 'model_prediction';
  if (predictionKind !== predictionScope) return failed(scope_visible, sample);
  const source = report.prediction_source;
  if (source !== undefined && (source === 'model_v55') !== predictionKind) {
    return failed(scope_visible, sample);
  }
  if (!countsConsistent(report, scope)) return failed(scope_visible, sample);
  // Matched cohort evidence needs a real sample (also guaranteed by
  // countsConsistent, which requires non-null counts; this additionally
  // rejects any residual non-positive value).
  if ((MATCHED_SCOPES as readonly string[]).includes(scope) && sample !== true) {
    return failed(scope_visible, sample);
  }
  // A non-finite / out-of-range headline rate means the displayed number is
  // not a valid result.
  if (!rate_valid) return failed(scope_visible, sample);

  let outcome_group: OutcomeGroup;
  switch (scope) {
    case 'model_prediction':
      outcome_group = 'prediction';
      break;
    case 'exact_band':
      outcome_group = 'exact_comparison';
      break;
    case 'age_band_only':
    case 'model_average':
      outcome_group = 'broader_supported_comparison';
      break;
    case 'population_default':
      outcome_group = 'dataset_reference';
      break;
    case 'unavailable':
      outcome_group = 'unavailable';
      break;
  }

  const supportedGroup =
    outcome_group === 'prediction' ||
    outcome_group === 'exact_comparison' ||
    outcome_group === 'broader_supported_comparison';
  const supported_result = supportedGroup && rate_valid && scope_visible && (omitSample || sample === true);

  return {
    render_delivered: true,
    supported_result,
    outcome_group,
    rate_valid,
    ...(omitSample || sample === undefined ? {} : { sample_nonzero: sample }),
    scope_visible,
    result_kind: kind,
    match_scope: scope,
  };
}
