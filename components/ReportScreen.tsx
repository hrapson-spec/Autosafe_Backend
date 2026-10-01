/**
 * Shareable report route: /app/report/:token.
 *
 * Two ways to end up with a report to show:
 *  1. Freshly created, persisted -- App.tsx navigated here with a real
 *     report_token; this component fetches it via getReport(token).
 *  2. Freshly created, persistence-degraded (report_token is null) --
 *     App.tsx navigated to the literal 'unsaved' token and carried the
 *     report itself via location.state.inlineReport. That report is
 *     rendered directly; getReport is never called for it, and it is never
 *     reachable by a real URL a second time (persistence.share_available is
 *     already false on the report itself, so nothing here implies a link
 *     that doesn't exist).
 *
 * A visit to /app/report/unsaved with no location.state (e.g. a bookmark or
 * a page refresh -- location.state doesn't survive either) falls through to
 * getReport('unsaved'), which the backend honestly reports as not found;
 * that's the correct outcome, not a bug to special-case around.
 *
 * No vehicle data is ever rendered outside the success path -- loading and
 * error states are both structurally incapable of it (see
 * ReportUnavailable.tsx).
 */
import React, { useEffect, useRef, useState, lazy, Suspense } from 'react';
import { useParams, useLocation, useNavigate } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import type { ReportV2 } from '../types';
import { getReport } from '../services/reportApi';
import { ReportApiError } from '../services/errorMessages';
import { reportRateDisplay } from './ReportCopy';
import { trackReportView } from '../utils/analytics';
import ReportUnavailable, { type ReportUnavailableReason } from './ReportUnavailable';
import ResultErrorBoundary, { ResultChunkLoadError } from './ResultErrorBoundary';
import { classifyResult } from '../utils/resultAcknowledgement';
import {
  claimCompletion,
  emitAcquisitionEvent,
  randomId,
  readOperationId,
  type EntryMode,
  type PersistenceMode,
  type RenderFailedStage,
} from '../utils/acquisitionEvents';

// A rejected chunk is re-thrown as a typed error so ResultErrorBoundary can
// tell a load failure from a render throw without reading any message.
const ReportDashboard = lazy(() =>
  import('./ReportDashboard').catch(() => {
    throw new ResultChunkLoadError();
  })
);

// Kept in sync by comment with App.tsx's handleCarCheck, which is the only
// place this literal is produced.
const UNSAVED_TOKEN = 'unsaved';

interface ReportScreenLocationState {
  postcode?: string;
  inlineReport?: ReportV2;
  /** Random per-check id minted in App.tsx; absent on restored/shared links. */
  operationId?: unknown;
}

function reasonForError(err: unknown): ReportUnavailableReason {
  if (err instanceof ReportApiError) {
    if (err.code === 'report_not_found') return 'not_found';
    if (err.code === 'report_expired') return 'expired';
    // network_error, storage_unavailable, invalid_response, rate_limited,
    // internal_error, dvsa_unavailable, invalid_registration,
    // undeclared_parameter, vehicle_not_found -- none of these are
    // report-specific, so they all collapse to the generic "error" state.
    return 'error';
  }
  return 'error';
}

/** report_viewed fires once per successfully-obtained report, from
 * whichever path produced it (fetched or inline). */
function fireReportViewed(report: ReportV2): void {
  const { value } = reportRateDisplay(report);
  trackReportView(report.vehicle.make, report.vehicle.model, value);
}

const ReportScreen: React.FC = () => {
  const { token } = useParams<{ token: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const state = (location.state ?? {}) as ReportScreenLocationState;
  const inlineReport = token === UNSAVED_TOKEN ? state.inlineReport : undefined;

  const [report, setReport] = useState<ReportV2 | null>(inlineReport ?? null);
  const [unavailableReason, setUnavailableReason] = useState<ReportUnavailableReason | null>(null);
  const [isLoading, setIsLoading] = useState(!inlineReport);
  const viewedRef = useRef<string | null>(null);

  // --- Acquisition measurement (OA-004; no data leaves the browser) --------
  // A report reached with a navigation-state operation id is the result of a
  // deliberate check in this tab; anything else is a restored/shared link and
  // never claims an original check_started.
  const operationId = readOperationId(state.operationId);
  const entryMode: EntryMode = operationId ? 'fresh_check' : 'restored_link';
  const persistenceMode: PersistenceMode = inlineReport ? 'inline_unsaved' : 'saved';
  const mountIdRef = useRef<string | null>(null);
  // Dedup key: the operation id, or a per-mount id for restored links.
  // In-memory only; it does not survive a reload.
  const completionKey = (): string => operationId ?? (mountIdRef.current ??= randomId());
  const entryFields = () => ({
    entry_mode: entryMode,
    ...(operationId ? { operation_id: operationId } : {}),
  });

  const handleRendered = () => {
    if (!report) return;
    const c = classifyResult(report);
    if (!c.render_delivered || c.outcome_group === 'error' || c.result_kind === null || c.match_scope === null) {
      // The final view mounted but the report fails the contract/numeric
      // checks: never a delivered result.
      handleRenderFailed('contract_invalid');
      return;
    }
    if (c.outcome_group === 'unavailable') {
      // Fully degraded display: reported as unavailable, not as a result.
      if (claimCompletion('result_unavailable', completionKey())) {
        emitAcquisitionEvent({ event: 'result_unavailable', ...entryFields(), reason: 'unavailable' });
      }
      return;
    }
    if (!claimCompletion('result_rendered', completionKey())) return;
    emitAcquisitionEvent({
      event: 'result_rendered',
      ...entryFields(),
      persistence_mode: persistenceMode,
      render_delivered: true,
      supported_result: c.supported_result,
      outcome_group: c.outcome_group,
      rate_valid: c.rate_valid,
      ...(c.sample_nonzero === undefined ? {} : { sample_nonzero: c.sample_nonzero }),
      scope_visible: c.scope_visible,
      result_kind: c.result_kind,
      match_scope: c.match_scope,
    });
  };

  const handleUnavailableShown = (reason: ReportUnavailableReason) => {
    if (!claimCompletion('result_unavailable', completionKey())) return;
    emitAcquisitionEvent({ event: 'result_unavailable', ...entryFields(), reason });
  };

  function handleRenderFailed(stage: RenderFailedStage): void {
    if (!claimCompletion('render_failed', completionKey())) return;
    emitAcquisitionEvent({ event: 'render_failed', ...entryFields(), stage });
  }

  useEffect(() => {
    if (inlineReport) {
      setReport(inlineReport);
      setUnavailableReason(null);
      setIsLoading(false);
      return;
    }

    if (!token) {
      setReport(null);
      setUnavailableReason('not_found');
      setIsLoading(false);
      return;
    }

    let cancelled = false;
    setIsLoading(true);
    setReport(null);
    setUnavailableReason(null);

    getReport(token)
      .then((r) => {
        if (cancelled) return;
        setReport(r);
        setIsLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setReport(null);
        setUnavailableReason(reasonForError(err));
        setIsLoading(false);
      });

    // Ignore-stale guard rather than AbortController: getReport() doesn't
    // expose a signal parameter, and this is enough to make sure a
    // fast token change never lets a slower, superseded fetch clobber
    // state that a newer one already set.
    return () => {
      cancelled = true;
    };
  }, [token, inlineReport]);

  // Fire report_viewed exactly once per distinct report shown, however it
  // was obtained.
  useEffect(() => {
    if (!report) return;
    const identity = report.report_token ?? `inline:${report.report_id ?? report.created_at}`;
    if (viewedRef.current === identity) return;
    viewedRef.current = identity;
    fireReportViewed(report);
  }, [report]);

  const handleReset = () => navigate('/app');

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F0F0F0]">
        <Helmet>
          <title>Checking your report… | AutoSafe</title>
        </Helmet>
        <div role="status" className="flex flex-col items-center gap-4">
          <div className="animate-spin h-8 w-8 border-2 border-slate-300 border-t-slate-900 rounded-full" aria-hidden="true" />
          <span className="text-slate-500 text-sm">Loading your report&hellip;</span>
        </div>
      </div>
    );
  }

  if (report) {
    return (
      <>
        <Helmet>
          <title>Your Vehicle Report | AutoSafe</title>
        </Helmet>
        <ResultErrorBoundary onRenderFailed={handleRenderFailed}>
        <Suspense
          fallback={
            <div className="flex justify-center py-20">
              <div className="animate-spin h-8 w-8 border-2 border-slate-300 border-t-slate-900 rounded-full" />
            </div>
          }
        >
          <ReportDashboard report={report} postcode={state.postcode} onReset={handleReset} onRendered={handleRendered} />
        </Suspense>
        </ResultErrorBoundary>
      </>
    );
  }

  return (
    <>
      <Helmet>
        <title>Report Unavailable | AutoSafe</title>
      </Helmet>
      <ReportUnavailable reason={unavailableReason ?? 'error'} onShown={handleUnavailableShown} />
    </>
  );
};

export default ReportScreen;
