import React from 'react';
import { Link } from 'react-router-dom';
import type { Assessment, Booking, BriefingExtras, Decision, DecisionType, FeedbackValue, SourceRecord } from '../api/types';
import { evidenceGaps } from '../lib/briefing';
import { formatDate, formatDay, formatTimestamp } from '../lib/dates';
import { BOOKING_TYPE_LABEL } from '../lib/importDraft';
import { BAND_LABEL, STAFF_ONLY_NOTE, shownEstimate } from '../lib/estimate';
import { AssessmentStatus } from './AssessmentStatus';
import { DecisionPanel } from './DecisionPanel';
import { EstimateDisplay } from './EstimateDisplay';
import { FeedbackControl } from './FeedbackControl';
import { PageHeading } from './Layout';
import { Alert, Button, Card, Chip } from './ui';

const RESULT_TEXT: Record<SourceRecord['test_result'], string> = {
  PASSED: 'Passed', FAILED: 'Failed', PRS: 'Passed after repair on the day', ABANDONED: 'Abandoned', ABORTED: 'Aborted',
};
const DEFECT_TEXT: Record<string, string> = {
  ADVISORY: 'Advisory', MINOR: 'Minor', MAJOR: 'Major', DANGEROUS: 'Dangerous', PRS: 'Repaired on the day', FAIL: 'Failure item', USER_ENTERED: 'Entered by tester',
};

export const BriefingView: React.FC<{
  booking: Booking;
  decisions: Decision[];
  /** null = the service did not provide dated source records or follow-up questions. */
  extras: BriefingExtras | null;
  versions?: Assessment[];
  feedback?: FeedbackValue | null;
  backTo: string;
  onDecide: (type: DecisionType, note: string) => Promise<void>;
  onFeedback: (v: FeedbackValue) => Promise<void>;
  onRefresh?: () => Promise<void>;
  refreshing?: boolean;
  refreshError?: string | null;
  demo?: boolean;
}> = ({ booking: b, decisions, extras, versions = [], feedback = null, backTo, onDecide, onFeedback, onRefresh, refreshing, refreshError, demo }) => {
  const a = b.assessment;
  const gaps = evidenceGaps(a, extras);
  const canRefresh = !!onRefresh && b.status === 'active' && (b.booking_type === 'mot' || b.booking_type === 'service_mot');
  return (
    <div className="space-y-4">
      <p><Link to={backTo} className="text-sm font-semibold underline">Back to bookings</Link></p>
      <div>
        <PageHeading>Briefing for {b.registration}</PageHeading>
        <p className="mt-1 text-slate-700">
          {formatDay(b.appointment_date)} · {BOOKING_TYPE_LABEL[b.booking_type]}
          {b.status === 'cancelled' && <> · <Chip tone="warn">Cancelled</Chip></>}
          {demo && <> · <Chip tone="warn">Fictional demo</Chip></>}
        </p>
      </div>

      <Card aria-labelledby="estimate-h">
        <h2 id="estimate-h" className="text-lg font-bold">Estimate</h2>
        <p className="text-xs font-semibold text-slate-600" data-testid="staff-only">{STAFF_ONLY_NOTE}</p>
        {shownEstimate(a) && a ? (
          <div className="mt-2">
            <EstimateDisplay assessment={a} size="large" showWording />
            <p className="mt-3 text-sm text-slate-700">
              Rounded by the service. Built from recorded MOT tests strictly before {formatDate(a.input_cutoff_date)}
              {a.completed_at ? `; frozen at ${formatTimestamp(a.completed_at)}` : ''} (assessment version {a.version}).
              Not an inspection, a diagnosis or a guarantee.
            </p>
            {a.evidence && (
              <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]" data-testid="evidence-summary">
                {a.evidence.prior_test_count !== undefined && (<><dt className="font-semibold">Earlier tests used</dt><dd>{a.evidence.prior_test_count}</dd></>)}
                {a.evidence.latest_prior_test_date && (<><dt className="font-semibold">Most recent earlier test</dt><dd><time dateTime={a.evidence.latest_prior_test_date}>{formatDate(a.evidence.latest_prior_test_date)}</time></dd></>)}
                {a.evidence.history_fetched_at && (<><dt className="font-semibold">History fetched</dt><dd>{formatTimestamp(a.evidence.history_fetched_at)}</dd></>)}
              </dl>
            )}
          </div>
        ) : (
          <div className="mt-2"><AssessmentStatus assessment={a} /></div>
        )}
        {canRefresh && (
          <div className="mt-4">
            <Button variant="secondary" onClick={onRefresh} disabled={refreshing || a?.state === 'pending'}>
              {refreshing ? 'Refreshing…' : 'Refresh assessment'}
            </Button>
            <p className="mt-1 text-sm text-slate-600">Creates a new assessment version from tests before today. Earlier versions are kept. Counts toward the daily fair-use limit.</p>
            {refreshError && <Alert kind="error" className="mt-2">{refreshError}</Alert>}
          </div>
        )}
        {versions.length > 1 && (
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer font-semibold">Assessment versions ({versions.length})</summary>
            <ul className="mt-2 space-y-1" aria-label="Assessment versions">
              {versions.map((v) => (
                <li key={v.id}>
                  Version {v.version}: {v.state}
                  {v.result && <> · {v.result.percent_rounded}% {BAND_LABEL[v.result.band]}</>}
                  <span className="text-slate-600"> · tests before {formatDate(v.input_cutoff_date)}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </Card>

      <Card aria-labelledby="fu-h">
        <h2 id="fu-h" className="text-lg font-bold">Follow-up questions</h2>
        <p className="text-sm text-slate-600">Generated by fixed rules from the recorded history. Ask them at the vehicle.</p>
        {!extras || extras.follow_up_questions.length === 0 ? (
          <p className="mt-2 text-sm text-slate-700">No follow-up questions are available for this booking.</p>
        ) : (
          <ol className="mt-3 list-decimal space-y-2 pl-5" data-testid="follow-ups">
            {extras.follow_up_questions.map((q) => (
              <li key={q.id}>
                {q.text}
                {q.source_test_date && <span className="block text-sm text-slate-600">From the test on {formatDate(q.source_test_date)}</span>}
              </li>
            ))}
          </ol>
        )}
      </Card>

      <Card aria-labelledby="gaps-h">
        <h2 id="gaps-h" className="text-lg font-bold">Evidence gaps</h2>
        <ul className="mt-2 list-disc space-y-1 pl-5" data-testid="evidence-gaps">
          {gaps.map((g) => <li key={g.code}>{g.text}</li>)}
        </ul>
      </Card>

      <Card aria-labelledby="rec-h">
        <h2 id="rec-h" className="text-lg font-bold">Recorded MOT history</h2>
        <p className="text-sm text-slate-600">
          Dated source records. Advisories and defects below were recorded at the date shown. They are history, not a list of what is wrong now.
        </p>
        {!extras || extras.source_records.length === 0 ? (
          <p className="mt-2 text-sm text-slate-700">No individual test records are shown for this booking.</p>
        ) : (
          <ul className="mt-3 space-y-4" data-testid="source-records">
            {extras.source_records.map((r) => (
              <li key={r.test_date} className="rounded-lg border border-slate-200 p-3">
                <p className="font-semibold">
                  <time dateTime={r.test_date}>{formatDate(r.test_date)}</time> · {RESULT_TEXT[r.test_result]}
                  {r.odometer_value !== null && <span className="font-normal text-slate-700"> · {r.odometer_value.toLocaleString('en-GB')} {r.odometer_unit === 'km' ? 'km' : 'miles'}</span>}
                </p>
                {r.defects.length > 0 ? (
                  <>
                    <p className="mt-1 text-sm text-slate-700">Recorded at this test ({formatDate(r.test_date)}), historical:</p>
                    <ul className="mt-1 list-disc pl-5 text-sm">
                      {r.defects.map((d, i) => <li key={i}><span className="font-semibold">{DEFECT_TEXT[d.type]}:</span> {d.text}</li>)}
                    </ul>
                  </>
                ) : (
                  <p className="mt-1 text-sm text-slate-600">No advisories or defects recorded at this test.</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <DecisionPanel decisions={decisions} onDecide={onDecide} />

      <Card aria-label="Feedback"><FeedbackControl value={feedback} onChange={onFeedback} /></Card>
      <p className="text-sm text-slate-600">Template briefing. No customer name, email or phone number is held for any booking.</p>
    </div>
  );
};
