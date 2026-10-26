import React from 'react';
import type { BookingType } from '../api/types';
import { BOOKING_TYPES, BOOKING_TYPE_LABEL, emptyRow, type DraftRow, type RowCheck } from '../lib/importDraft';
import { Button, Chip } from './ui';

const cell = 'block min-h-11 w-full min-w-[9.5rem] rounded-lg border border-slate-400 bg-white px-2 py-1 text-base';

export const ImportPreviewTable: React.FC<{
  rows: DraftRow[];
  checks: Map<string, RowCheck>;
  onChange: (rows: DraftRow[]) => void;
  defaults: { date: string; type: BookingType };
}> = ({ rows, checks, onChange, defaults }) => {
  const update = (key: string, patch: Partial<DraftRow>) => onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  return (
    <div className="overflow-x-auto" role="region" aria-label="Import preview, scrolls sideways on small screens" tabIndex={0}>
      <table className="w-full min-w-[50rem] text-left text-sm">
        <caption className="sr-only">Rows to import. Edit any cell. Rows with a problem are marked and will be skipped.</caption>
        <thead>
          <tr className="border-b border-slate-300">
            <th scope="col" className="p-2">Row</th>
            <th scope="col" className="p-2">Registration</th>
            <th scope="col" className="p-2">Date</th>
            <th scope="col" className="p-2">Time (optional)</th>
            <th scope="col" className="p-2">Type</th>
            <th scope="col" className="p-2">Check</th>
            <th scope="col" className="p-2"><span className="sr-only">Remove</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const c = checks.get(r.key) ?? { kind: 'ok', messages: [] };
            const bad = c.kind !== 'ok';
            return (
              <tr key={r.key} className={`border-b border-slate-200 align-top ${bad ? 'bg-red-50' : ''}`} data-testid="preview-row" data-flagged={bad ? 'true' : 'false'}>
                <th scope="row" className="p-2 font-semibold">{i + 1}</th>
                <td className="p-2"><input aria-label={`Registration, row ${i + 1}`} className={cell} value={r.registration} autoCapitalize="characters" onChange={(e) => update(r.key, { registration: e.target.value.toUpperCase() })} /></td>
                <td className="p-2"><input aria-label={`Date, row ${i + 1}`} type="date" className={cell} value={r.date} onChange={(e) => update(r.key, { date: e.target.value })} /></td>
                <td className="p-2"><input aria-label={`Time, row ${i + 1}`} inputMode="numeric" placeholder="09:30" className={cell} value={r.time} onChange={(e) => update(r.key, { time: e.target.value })} /></td>
                <td className="p-2">
                  <select aria-label={`Type, row ${i + 1}`} className={cell} value={r.type} onChange={(e) => update(r.key, { type: e.target.value as BookingType | '' })}>
                    <option value="">Choose…</option>
                    {BOOKING_TYPES.map((t) => <option key={t} value={t}>{BOOKING_TYPE_LABEL[t]}</option>)}
                  </select>
                </td>
                <td className="p-2">
                  {bad ? (
                    <ul className="space-y-1">
                      {c.messages.map((m) => (
                        <li key={m}><Chip tone="warn">{c.kind === 'duplicate' ? 'Duplicate' : 'Invalid'}</Chip> <span className="text-red-900">{m}</span></li>
                      ))}
                    </ul>
                  ) : <Chip tone="ok">OK</Chip>}
                </td>
                <td className="p-2"><Button variant="ghost" aria-label={`Remove row ${i + 1}`} onClick={() => onChange(rows.filter((x) => x.key !== r.key))}>Remove</Button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="p-2"><Button variant="secondary" onClick={() => onChange([...rows, emptyRow(defaults.date, defaults.type)])}>Add a row</Button></div>
    </div>
  );
};
