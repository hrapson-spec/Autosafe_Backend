import React from 'react';
import type { Assessment } from '../api/types';
import { ESTIMATE_WORDING, shownEstimate } from '../lib/estimate';
import { Chip } from './ui';

/** Shows the percentage and band exactly as the service returned them (R7). */
export const EstimateDisplay: React.FC<{ assessment: Assessment | null | undefined; size?: 'compact' | 'large'; showWording?: boolean }> = ({
  assessment, size = 'compact', showWording = false,
}) => {
  const e = shownEstimate(assessment);
  if (!e) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-2" data-testid="estimate">
      <span className={size === 'large' ? 'text-5xl font-bold tabular-nums' : 'text-xl font-bold tabular-nums'}>{e.percent}%</span>
      <Chip tone={e.band}>{e.bandLabel}</Chip>
      {showWording && <span className="basis-full text-base font-normal text-slate-700">{ESTIMATE_WORDING}</span>}
    </span>
  );
};
