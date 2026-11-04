import React, { useState } from 'react';
import type { FeedbackReason, FeedbackValue } from '../api/types';
import { Button } from './ui';

/** Client copy for the contract's reason codes (FeedbackReason). */
export const FEEDBACK_REASON_LABEL: Record<FeedbackReason, string> = {
  inaccurate_records: 'A record looked wrong',
  not_relevant: 'Not relevant to this vehicle',
  too_long: 'Too much to read',
  missing_information: 'Something was missing',
  other: 'Something else',
};

export const FeedbackControl: React.FC<{
  value: FeedbackValue | null;
  reason?: FeedbackReason | null;
  onChange: (v: FeedbackValue, reason: FeedbackReason | null) => Promise<void>;
  disabled?: boolean;
}> = ({ value, reason = null, onChange, disabled }) => {
  const [current, setCurrent] = useState<FeedbackValue | null>(value);
  const [why, setWhy] = useState<FeedbackReason | null>(reason);
  const [error, setError] = useState<string | null>(null);
  const send = async (v: FeedbackValue, r: FeedbackReason | null) => {
    setError(null);
    try {
      await onChange(v, r);
      setCurrent(v);
      setWhy(r);
    } catch {
      setError('Could not save your feedback.');
    }
  };
  return (
    <div role="group" aria-labelledby="fb-q" className="flex flex-wrap items-center gap-3">
      <p id="fb-q" className="text-sm font-semibold">Was this briefing helpful? (optional)</p>
      <Button variant="secondary" aria-pressed={current === 'helpful'} disabled={disabled} onClick={() => send('helpful', null)}>Helpful</Button>
      <Button variant="secondary" aria-pressed={current === 'not_helpful'} disabled={disabled} onClick={() => send('not_helpful', why)}>Not helpful</Button>
      {current === 'not_helpful' && (
        <label className="flex items-center gap-2 text-sm">
          <span className="font-semibold">What was wrong? (optional)</span>
          <select
            className="min-h-11 rounded-lg border border-slate-400 bg-white px-2"
            value={why ?? ''} disabled={disabled}
            onChange={(e) => void send('not_helpful', (e.target.value || null) as FeedbackReason | null)}
          >
            <option value="">Choose…</option>
            {(Object.keys(FEEDBACK_REASON_LABEL) as FeedbackReason[]).map((k) => <option key={k} value={k}>{FEEDBACK_REASON_LABEL[k]}</option>)}
          </select>
        </label>
      )}
      {current && <span role="status" className="text-sm text-slate-700">Thanks, saved.</span>}
      {error && <span role="alert" className="text-sm text-red-800">{error}</span>}
    </div>
  );
};
