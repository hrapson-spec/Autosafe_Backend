import React from 'react';
import { addDays, dateRange, formatDay } from '../lib/dates';
import { Button } from './ui';

export interface Window { start: string; days: number }

export const DateNav: React.FC<{ window: Window; today: string; onChange: (w: Window) => void }> = ({ window: w, today, onChange }) => {
  const last = addDays(w.start, w.days - 1);
  const label = w.days === 1 ? formatDay(w.start) : `${formatDay(w.start)} to ${formatDay(last)}`;
  const isDefault = w.start === today && w.days === 7;
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Choose dates">
      <Button variant="secondary" onClick={() => onChange({ ...w, start: addDays(w.start, -7) })}>Previous week</Button>
      <Button variant="secondary" onClick={() => onChange({ ...w, start: addDays(w.start, -1) })}>Previous day</Button>
      <p className="px-2 text-base font-semibold" aria-live="polite" data-testid="range-label">{label}</p>
      <Button variant="secondary" onClick={() => onChange({ ...w, start: addDays(w.start, 1) })}>Next day</Button>
      <Button variant="secondary" onClick={() => onChange({ ...w, start: addDays(w.start, 7) })}>Next week</Button>
      <Button variant="ghost" disabled={isDefault} onClick={() => onChange({ start: today, days: 7 })}>Next 7 days</Button>
      <Button variant="ghost" disabled={w.days === 1} onClick={() => onChange({ start: w.start, days: 1 })}>Single day</Button>
    </div>
  );
};

export { dateRange };
