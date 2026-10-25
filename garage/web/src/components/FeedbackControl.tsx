import React, { useState } from 'react';
import type { FeedbackValue } from '../api/types';
import { Button } from './ui';

export const FeedbackControl: React.FC<{ value: FeedbackValue | null; onChange: (v: FeedbackValue) => Promise<void> }> = ({ value, onChange }) => {
  const [current, setCurrent] = useState<FeedbackValue | null>(value);
  const [error, setError] = useState<string | null>(null);
  const pick = async (v: FeedbackValue) => {
    setError(null);
    try {
      await onChange(v);
      setCurrent(v);
    } catch {
      setError('Could not save your feedback.');
    }
  };
  return (
    <div role="group" aria-labelledby="fb-q" className="flex flex-wrap items-center gap-3">
      <p id="fb-q" className="text-sm font-semibold">Was this briefing helpful? (optional)</p>
      <Button variant="secondary" aria-pressed={current === 'helpful'} onClick={() => pick('helpful')}>Helpful</Button>
      <Button variant="secondary" aria-pressed={current === 'not_helpful'} onClick={() => pick('not_helpful')}>Not helpful</Button>
      {current && <span role="status" className="text-sm text-slate-700">Thanks, saved.</span>}
      {error && <span role="alert" className="text-sm text-red-800">{error}</span>}
    </div>
  );
};
