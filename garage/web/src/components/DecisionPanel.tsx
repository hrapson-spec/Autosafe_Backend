import React, { useState } from 'react';
import type { Decision, DecisionType } from '../api/types';
import { formatTimestamp } from '../lib/dates';
import { NOTES_HINT } from '../lib/estimate';
import { Alert, Button, Card } from './ui';

export const DECISION_OPTIONS: Array<{ id: DecisionType; label: string; help: string }> = [
  { id: 'check_repair_history', label: 'Check repair history', help: 'Ask about work done since the last recorded test.' },
  { id: 'offer_inspection', label: 'Offer an inspection', help: 'Offer a pre-test inspection of the areas in the questions.' },
  { id: 'adjust_booking_plan', label: 'Adjust the booking plan', help: 'Change the slot, bay time or order of work.' },
  { id: 'proceed_as_booked', label: 'Proceed as booked', help: 'No change to the plan.' },
];

export const DECISION_LABEL: Record<DecisionType, string> = Object.fromEntries(
  DECISION_OPTIONS.map((o) => [o.id, o.label]),
) as Record<DecisionType, string>;

export const NOTE_MAX = 500;

export const DecisionPanel: React.FC<{
  decisions: Decision[];
  onDecide: (type: DecisionType, note: string) => Promise<void>;
}> = ({ decisions, onDecide }) => {
  const [option, setOption] = useState<DecisionType | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!option) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await onDecide(option, note.trim());
      setOption(null);
      setNote('');
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the decision.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card aria-labelledby="decision-h">
      <h2 id="decision-h" className="text-lg font-bold">Your decision</h2>
      <form onSubmit={submit} className="mt-3 space-y-4">
        <fieldset>
          <legend className="text-sm font-semibold">What will you do with this booking?</legend>
          <div className="mt-2 space-y-2">
            {DECISION_OPTIONS.map((o) => (
              <label key={o.id} className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-slate-300 p-3 has-[:checked]:border-slate-900 has-[:checked]:bg-slate-50">
                <input type="radio" name="decision" value={o.id} checked={option === o.id} onChange={() => setOption(o.id)} className="mt-1 h-4 w-4" />
                <span><span className="block font-semibold">{o.label}</span><span className="block text-sm text-slate-600">{o.help}</span></span>
              </label>
            ))}
          </div>
        </fieldset>
        <div>
          <label htmlFor="decision-note" className="block text-sm font-semibold">Short note (optional)</label>
          <p id="decision-note-hint" className="text-sm text-slate-600">{NOTES_HINT} {note.length}/{NOTE_MAX}</p>
          <textarea
            id="decision-note" aria-describedby="decision-note-hint" rows={2} maxLength={NOTE_MAX}
            value={note} onChange={(e) => setNote(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-slate-400 bg-white px-3 py-2 text-base"
          />
        </div>
        {error && <Alert kind="error">{error}</Alert>}
        {saved && <Alert kind="success">Decision saved.</Alert>}
        <Button type="submit" disabled={!option || busy}>{busy ? 'Saving…' : 'Save decision'}</Button>
      </form>

      <h3 className="mt-6 text-base font-bold">Decision history</h3>
      {decisions.length === 0 ? (
        <p className="mt-1 text-sm text-slate-700">No decisions recorded yet.</p>
      ) : (
        <ul className="mt-2 divide-y divide-slate-200" aria-label="Decision history">
          {decisions.map((d) => (
            <li key={d.id} className="py-2 text-sm" data-testid="decision-item">
              <p className="font-semibold">{DECISION_LABEL[d.decision_type]}</p>
              {d.note && <p className="text-slate-800">{d.note}</p>}
              <p className="text-slate-600">By {d.actor.email} on {formatTimestamp(d.created_at)}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};
