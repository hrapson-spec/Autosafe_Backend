import type { BookingType, ImportRow, PreviewRow } from '../api/types';
import { MAX_IMPORT_ROWS } from '../api/types';
import { isIsoDate, parseFlexibleDate, parseTime } from './dates';
import { isPlausibleRegistration, normaliseRegistration } from './registration';

/** An editable preview row. Fields are strings while being edited. */
export interface DraftRow {
  key: string;
  registration: string;
  date: string;
  type: BookingType | '';
  /** Optional appointment time as typed ("" = untimed). */
  time: string;
}

let counter = 0;
export function newKey(): string {
  counter += 1;
  return `r${counter}`;
}

export function emptyRow(date = '', type: BookingType | '' = 'mot'): DraftRow {
  return { key: newKey(), registration: '', date, type, time: '' };
}

export const BOOKING_TYPE_LABEL: Record<BookingType, string> = {
  mot: 'MOT',
  service: 'Service only',
  retest: 'Retest',
  service_mot: 'Service + MOT',
};
export const BOOKING_TYPES = Object.keys(BOOKING_TYPE_LABEL) as BookingType[];

const TYPE_ALIASES: Record<string, BookingType> = {
  mot: 'mot', 'initial mot': 'mot', initial: 'mot',
  retest: 'retest', 'mot retest': 'retest',
  service: 'service', 'service only': 'service', 'full service': 'service',
  service_mot: 'service_mot', 'service + mot': 'service_mot', 'service and mot': 'service_mot',
  'full service and mot': 'service_mot', 'service mot': 'service_mot',
};

export function parseBookingType(raw: string): BookingType | '' {
  return TYPE_ALIASES[raw.trim().toLowerCase().replace(/\s+/g, ' ')] ?? '';
}

/** Pasted list: one registration per line (commas, semicolons and tabs also split). Shared date and type. */
export function rowsFromPaste(text: string, date: string, type: BookingType): DraftRow[] {
  return text
    .split(/[\n\r,;\t]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((s) => ({ key: newKey(), registration: normaliseRegistration(s), date, type, time: '' }));
}

export type RowKind = 'ok' | 'invalid' | 'duplicate';
export interface RowCheck { kind: RowKind; messages: string[] }

/** Instant local checks; the service's preview is authoritative and is merged on top. */
export function checkDraft(rows: DraftRow[], server: PreviewRow[] | null): Map<string, RowCheck> {
  const out = new Map<string, RowCheck>();
  const seen = new Set<string>();
  rows.forEach((r, i) => {
    const messages: string[] = [];
    let kind: RowKind = 'ok';
    if (!isPlausibleRegistration(r.registration)) { kind = 'invalid'; messages.push('Registration looks wrong (2 to 8 letters or digits)'); }
    if (!isIsoDate(r.date)) { kind = 'invalid'; messages.push('Date must be a real date'); }
    if (r.type === '') { kind = 'invalid'; messages.push('Choose a booking type'); }
    if (r.time.trim() !== '' && parseTime(r.time) === null) { kind = 'invalid'; messages.push('Time must be like 09:30, or left blank'); }
    if (kind === 'ok') {
      const k = `${normaliseRegistration(r.registration)}|${r.date}|${r.type}`;
      if (seen.has(k)) { kind = 'duplicate'; messages.push('Duplicate of an earlier row (same registration, date and type)'); }
      seen.add(k);
    }
    const s = server?.[i];
    if (s && s.status !== 'new') {
      const extra = s.errors.map((e) => e.message);
      if (s.status === 'invalid' && kind === 'ok') { kind = 'invalid'; messages.push(...(extra.length ? extra : ['The service rejected this row'])); }
      if (s.status === 'duplicate_existing' && kind === 'ok') { kind = 'duplicate'; messages.push('Already booked (same registration, date and type)'); }
      if (s.status === 'duplicate_in_batch' && kind === 'ok') { kind = 'duplicate'; messages.push('Duplicate of an earlier row'); }
    }
    out.set(r.key, { kind, messages });
  });
  return out;
}

export function toImportRows(rows: DraftRow[]): ImportRow[] {
  return rows.map((r) => ({
    registration: normaliseRegistration(r.registration), date: r.date, type: r.type,
    ...(r.time.trim() !== '' ? { time: parseTime(r.time) ?? r.time.trim() } : {}),
  }));
}

export function tooManyRows(n: number): boolean {
  return n > MAX_IMPORT_ROWS;
}

export { parseFlexibleDate };
