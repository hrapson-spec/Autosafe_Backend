import React from 'react';
import type { Assessment, AssessmentState } from '../api/types';
import { Chip } from './ui';

const LABEL: Record<AssessmentState, string> = {
  pending: 'Pending',
  available: 'Available',
  unavailable: 'Unavailable',
  unsupported: 'Unsupported',
  failed: 'Failed',
};
const TONE: Record<AssessmentState, 'neutral' | 'ok' | 'warn'> = {
  pending: 'neutral', available: 'ok', unavailable: 'warn', unsupported: 'neutral', failed: 'warn',
};
const FALLBACK_REASON: Record<AssessmentState, string | null> = {
  pending: 'Assessment in progress.',
  available: null,
  unavailable: 'No estimate could be produced.',
  unsupported: 'This booking is outside the supported population.',
  failed: 'The assessment did not complete.',
};

/** State label plus the API's reason text (falls back to a generic line only when the API sends none). */
export const AssessmentStatus: React.FC<{ assessment: Assessment | null | undefined; hideAvailable?: boolean }> = ({ assessment, hideAvailable }) => {
  const s: AssessmentState = assessment?.state ?? 'pending';
  if (s === 'available' && hideAvailable) return null;
  const reason = assessment?.reason_text ?? FALLBACK_REASON[s];
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <span><span className="sr-only">Assessment state: </span><Chip tone={TONE[s]}>{LABEL[s]}</Chip></span>
      {reason && <span className="text-sm text-slate-700">{reason}</span>}
    </span>
  );
};
