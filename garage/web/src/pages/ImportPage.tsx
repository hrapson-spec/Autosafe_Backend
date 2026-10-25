import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import type { BookingType, ImportPreview, ImportResult } from '../api/types';
import { MAX_IMPORT_ROWS } from '../api/types';
import { ImportPreviewTable } from '../components/ImportPreviewTable';
import { PageHeading } from '../components/Layout';
import { Alert, Button, Card, Chip, SelectField, TextField } from '../components/ui';
import { parseImportCsv } from '../lib/csv';
import { todayLondon } from '../lib/dates';
import {
  BOOKING_TYPES, BOOKING_TYPE_LABEL, checkDraft, emptyRow, rowsFromPaste, toImportRows, type DraftRow,
} from '../lib/importDraft';

type Mode = 'paste' | 'csv' | 'manual';

const MODES: Array<{ id: Mode; label: string }> = [
  { id: 'paste', label: 'Paste a list' },
  { id: 'csv', label: 'Upload a CSV' },
  { id: 'manual', label: 'Type them in' },
];

interface Snapshot { rows: DraftRow[]; preview: ImportPreview }

export const ImportPage: React.FC<{ previewDebounceMs?: number }> = ({ previewDebounceMs = 300 }) => {
  const today = useMemo(() => todayLondon(), []);
  const [mode, setMode] = useState<Mode>('paste');
  const [date, setDate] = useState(today);
  const [type, setType] = useState<BookingType>('mot');
  const [pasted, setPasted] = useState('');
  const [rows, setRows] = useState<DraftRow[] | null>(null);
  const [ignoredCols, setIgnoredCols] = useState<string[]>([]);
  const [inputError, setInputError] = useState<string | null>(null);
  const [snap, setSnap] = useState<{ key: string; preview: ImportPreview } | null>(null);
  const [checking, setChecking] = useState(false);
  const [phase, setPhase] = useState<'edit' | 'importing' | 'done'>('edit');
  const [result, setResult] = useState<ImportResult | null>(null);
  const [done, setDone] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rowsKey = useMemo(() => (rows ? JSON.stringify(toImportRows(rows)) : ''), [rows]);
  const current = snap && snap.key === rowsKey ? snap.preview : null;
  const checks = useMemo(() => (rows ? checkDraft(rows, current?.rows ?? null) : new Map()), [rows, current]);
  const tooMany = (rows?.length ?? 0) > MAX_IMPORT_ROWS;
  const ready = rows ? rows.filter((r) => checks.get(r.key)?.kind === 'ok').length : 0;
  const flagged = (rows?.length ?? 0) - ready;

  // The service's preview is authoritative (duplicates against existing bookings, row validation).
  useEffect(() => {
    if (!rows || rows.length === 0 || tooMany || phase !== 'edit') return;
    let cancelled = false;
    setChecking(true);
    const t = setTimeout(async () => {
      try {
        const preview = await api.previewImport(toImportRows(rows));
        if (!cancelled) { setSnap({ key: rowsKey, preview }); setError(null); }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : 'Could not check these rows with the service.');
      } finally {
        if (!cancelled) setChecking(false);
      }
    }, previewDebounceMs);
    return () => { cancelled = true; clearTimeout(t); };
  }, [rowsKey, tooMany, phase, previewDebounceMs]);

  const startFromPaste = () => {
    setInputError(null);
    setIgnoredCols([]);
    const r = rowsFromPaste(pasted, date, type);
    if (r.length === 0) { setInputError('Paste at least one registration.'); return; }
    setRows(r);
  };

  const onFile = async (file: File | undefined) => {
    setInputError(null);
    if (!file) return;
    if (file.size > 1_000_000) { setInputError('That file is too large. Keep it under 1 MB and 200 rows.'); return; }
    const parsed = parseImportCsv(await file.text(), { date, type });
    if (parsed.error) { setInputError(parsed.error); return; }
    setIgnoredCols(parsed.ignoredColumns);
    setRows(parsed.rows);
  };

  const reset = () => {
    setRows(null); setSnap(null); setPhase('edit'); setResult(null); setDone(null); setError(null);
  };

  const commit = async () => {
    if (!rows || !current) return;
    setError(null);
    setPhase('importing');
    try {
      const res = await api.commitImport(current.import_id);
      setResult(res);
      setDone({ rows, preview: current });
      setPhase('done');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The import failed. You can try again.');
      setPhase('edit');
    }
  };

  // ------------------------------------------------------------------ done
  if (phase === 'done' && result && done) {
    const imported = result.created_booking_ids.length;
    return (
      <div className="space-y-4">
        <PageHeading>Import finished</PageHeading>
        <Alert kind="success">
          <span data-testid="import-summary">Imported {imported} of {result.counts.total} rows.</span>{' '}
          {result.already_committed && 'This import had already been committed; nothing was added twice. '}
          {result.counts.total - imported} not imported (listed below). Assessments run in the background and appear in the list.
        </Alert>
        <Card aria-labelledby="res-h">
          <h2 id="res-h" className="text-lg font-bold">Row by row</h2>
          <ul className="mt-2 divide-y divide-slate-200" aria-label="Import results">
            {done.preview.rows.map((p, i) => {
              const reason = p.errors.map((e) => e.message).join('; ');
              const label = p.status === 'new' ? 'Imported' : p.status === 'invalid' ? 'Rejected' : 'Skipped, duplicate';
              return (
                <li key={p.row_number} className="flex flex-wrap items-center gap-3 py-2" data-testid="result-row" data-outcome={p.status === 'new' ? 'imported' : p.status}>
                  <span className="w-16 text-sm text-slate-600">Row {p.row_number}</span>
                  <span className="font-semibold">{p.registration ?? done.rows[i]?.registration ?? '(blank)'}</span>
                  <Chip tone={p.status === 'new' ? 'ok' : 'warn'}>{label}</Chip>
                  {reason && <span className="text-sm text-slate-700">{reason}</span>}
                  {p.status === 'duplicate_existing' && <span className="text-sm text-slate-700">Already booked.</span>}
                  {p.status === 'duplicate_in_batch' && <span className="text-sm text-slate-700">Repeats row {p.duplicate_of_row}.</span>}
                </li>
              );
            })}
          </ul>
        </Card>
        <div className="flex flex-wrap gap-3">
          <Link to="/app/bookings" className="inline-flex min-h-11 items-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">View bookings</Link>
          <Button variant="secondary" onClick={reset}>Import more</Button>
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------- importing
  if (phase === 'importing') {
    const n = current?.counts.new ?? 0;
    return (
      <div className="space-y-4">
        <PageHeading>Importing</PageHeading>
        <Card>
          {/* The commit is a single request, so progress is indeterminate (no value attribute). */}
          <label htmlFor="imp-progress" className="block font-semibold">Importing {n} {n === 1 ? 'row' : 'rows'}…</label>
          <progress id="imp-progress" className="mt-2 h-4 w-full" aria-label={`Importing ${n} rows`} />
          <p role="status" className="sr-only">Importing {n} rows. Please wait.</p>
        </Card>
      </div>
    );
  }

  // ------------------------------------------------------------------ edit
  return (
    <div className="space-y-4">
      <PageHeading>Import bookings</PageHeading>
      <p className="max-w-2xl text-slate-700">
        Add up to {MAX_IMPORT_ROWS} bookings by registration. No customer names, emails or phone numbers are needed or accepted.
      </p>
      {error && <Alert kind="error">{error}</Alert>}

      {!rows ? (
        <Card>
          <div role="radiogroup" aria-label="How do you want to add bookings?" className="flex flex-wrap gap-2">
            {MODES.map((m) => (
              <label key={m.id} className={`inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border px-4 py-2 text-sm font-semibold has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 ${mode === m.id ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-400 bg-white'}`}>
                <input type="radio" name="import-mode" className="sr-only" checked={mode === m.id} onChange={() => { setMode(m.id); setInputError(null); }} />
                {m.label}
              </label>
            ))}
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <TextField label={mode === 'paste' ? 'Date for every row' : 'Default date'} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            <SelectField label={mode === 'paste' ? 'Type for every row' : 'Default type'} value={type} onChange={(e) => setType(e.target.value as BookingType)}>
              {BOOKING_TYPES.map((t) => <option key={t} value={t}>{BOOKING_TYPE_LABEL[t]}</option>)}
            </SelectField>
          </div>

          {mode === 'paste' && (
            <div className="mt-4">
              <label htmlFor="paste" className="block text-sm font-semibold">Registrations</label>
              <p className="text-sm text-slate-600">One per line. Commas also work.</p>
              <textarea id="paste" rows={8} value={pasted} onChange={(e) => setPasted(e.target.value)} className="mt-1 block w-full rounded-lg border border-slate-400 bg-white px-3 py-2 font-mono text-base" />
              <Button className="mt-3" onClick={startFromPaste}>Preview</Button>
            </div>
          )}
          {mode === 'csv' && (
            <div className="mt-4">
              <TextField
                label="CSV file" type="file" accept=".csv,text/csv"
                hint="Columns used: registration (required), date, type. Any other column is ignored and never uploaded."
                onChange={(e) => void onFile(e.target.files?.[0])}
              />
            </div>
          )}
          {mode === 'manual' && (
            <div className="mt-4"><Button onClick={() => { setInputError(null); setIgnoredCols([]); setRows([emptyRow(date, type)]); }}>Start entering rows</Button></div>
          )}
          {inputError && <Alert kind="error" className="mt-4">{inputError}</Alert>}
        </Card>
      ) : (
        <Card aria-labelledby="prev-h">
          <h2 id="prev-h" className="text-lg font-bold">Check and edit</h2>
          {ignoredCols.length > 0 && <Alert className="mt-2">Ignored and not uploaded: {ignoredCols.join(', ')}.</Alert>}
          <p className="mt-2 text-sm text-slate-700" aria-live="polite" data-testid="preview-counts">
            {rows.length} rows: {ready} ready, {flagged} flagged. Flagged rows are skipped unless you fix them.
            {checking && ' Checking with the service…'}
          </p>
          {tooMany && <Alert kind="error" className="mt-2">There are {rows.length} rows. The limit is {MAX_IMPORT_ROWS} per import: remove {rows.length - MAX_IMPORT_ROWS} or split the list.</Alert>}
          <div className="mt-3">
            <ImportPreviewTable rows={rows} checks={checks} onChange={setRows} defaults={{ date, type }} />
          </div>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button onClick={commit} disabled={!current || current.counts.new === 0 || tooMany || checking}>
              Import {current?.counts.new ?? ready} {(current?.counts.new ?? ready) === 1 ? 'row' : 'rows'}
            </Button>
            <Button variant="secondary" onClick={reset}>Start again</Button>
          </div>
        </Card>
      )}
    </div>
  );
};
