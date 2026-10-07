/**
 * Terminal, non-success state for the /app/report/:token route: the token
 * was expired, not found, or the report couldn't be loaded for some other
 * reason (network/storage/unexpected). Structurally incapable of rendering
 * vehicle data -- its only prop is `reason` -- so a caller can never
 * accidentally leak report contents into an error state.
 */
import React, { useEffect, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { AlertCircle } from './Icons';
import { Logo } from './Logo';
import { Button } from './ui';

export type ReportUnavailableReason = 'expired' | 'not_found' | 'error';

interface ReportUnavailableProps {
  reason: ReportUnavailableReason;
  /**
   * Optional post-commit hook, called once per mount with the fixed reason
   * enum only (never data). Absent for the error-boundary fallback, which is
   * a render failure, not a delivered unavailable view.
   */
  onShown?: (reason: ReportUnavailableReason) => void;
}

const COPY: Record<ReportUnavailableReason, { heading: string; body: string }> = {
  expired: {
    heading: 'This report link has expired',
    body: 'Reports are kept for a limited time. Run a fresh check to get an up-to-date result.',
  },
  not_found: {
    heading: "That report link isn't valid",
    body: 'The link may be incomplete or mistyped.',
  },
  error: {
    heading: "We couldn't load this report",
    body: 'Please check your connection and try again.',
  },
};

const ReportUnavailable: React.FC<ReportUnavailableProps> = ({ reason, onShown }) => {
  const navigate = useNavigate();
  const { heading, body } = COPY[reason];
  const onShownRef = useRef(onShown);
  useEffect(() => {
    onShownRef.current = onShown;
  });

  // Declared after the ref-sync effect above so it always reads the latest callback.
  useEffect(() => {
    onShownRef.current?.(reason);
  }, [reason]);

  return (
    <div className="min-h-screen flex flex-col font-sans text-slate-900 bg-[#F0F0F0]">
      <nav className="w-full bg-transparent pt-8 pb-4" aria-label="Main navigation">
        <div className="max-w-4xl mx-auto px-4 flex items-center justify-between">
          <Link to="/app" className="flex items-center gap-3 group">
            <Logo className="text-slate-900 w-8 h-8" />
            <span className="font-serif font-bold text-2xl text-slate-900">AutoSafe</span>
          </Link>
        </div>
      </nav>

      <main className="grow flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-md bg-white rounded-2xl shadow-xs p-8 md:p-10 text-center space-y-6">
          <div className="mx-auto w-12 h-12 rounded-full bg-red-50 flex items-center justify-center">
            <AlertCircle className="w-6 h-6 text-red-500" aria-hidden="true" />
          </div>
          <div className="space-y-2">
            <h1 className="font-serif text-2xl font-medium text-slate-900">{heading}</h1>
            <p className="text-slate-600 leading-relaxed">{body}</p>
          </div>
          <Button variant="primary" size="lg" fullWidth onClick={() => navigate('/app')}>
            Check a vehicle
          </Button>
        </div>
      </main>
    </div>
  );
};

export default ReportUnavailable;
