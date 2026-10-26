import type { BookingType } from '../api/types';
import { newKey, parseBookingType, type DraftRow } from './importDraft';
import { parseFlexibleDate, parseTime } from './dates';
import { normaliseRegistration } from './registration';

/** Minimal RFC 4180 parser (quoted fields, doubled quotes, CRLF/LF). */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  const s = text.replace(/^﻿/, '');
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inQuotes) {
      if (c === '"') {
        if (s[i + 1] === '"') {
          field += '"';
          i++;
        } else inQuotes = false;
      } else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ',') {
      row.push(field);
      field = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && s[i + 1] === '\n') i++;
      row.push(field);
      field = '';
      if (row.some((f) => f.trim() !== '')) rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field);
  if (row.some((f) => f.trim() !== '')) rows.push(row);
  return rows;
}

type Column = 'registration' | 'date' | 'type' | 'time';

const HEADER_ALIASES: Record<string, Column> = {
  registration: 'registration',
  reg: 'registration',
  vrm: 'registration',
  'vehicle registration': 'registration',
  date: 'date',
  appointment_date: 'date',
  'appointment date': 'date',
  'booking date': 'date',
  type: 'type',
  booking_type: 'type',
  'test type': 'type',
  'booking type': 'type',
  time: 'time',
  appointment_time: 'time',
  'appointment time': 'time',
  'booking time': 'time',
  slot: 'time',
};

export interface CsvParseResult {
  rows: DraftRow[];
  /** Header names that were not recognised and were therefore dropped, never kept or uploaded. */
  ignoredColumns: string[];
  error: string | null;
}

const MAX_KEPT = 1000;

export function parseImportCsv(text: string, defaults: { date: string; type: BookingType }): CsvParseResult {
  const table = parseCsv(text);
  if (table.length === 0) return { rows: [], ignoredColumns: [], error: 'The file is empty.' };
  const head = table[0].map((h) => h.trim().toLowerCase());
  const mapped = head.map((h) => HEADER_ALIASES[h]);
  const hasHeader = mapped.includes('registration');
  let body = table;
  const colIndex: Partial<Record<Column, number>> = {};
  let ignored: string[] = [];
  if (hasHeader) {
    body = table.slice(1);
    mapped.forEach((c, i) => {
      if (c && colIndex[c] === undefined) colIndex[c] = i;
    });
    ignored = table[0].map((h, i) => ({ h: h.trim(), c: mapped[i] })).filter((x) => !x.c && x.h !== '').map((x) => x.h);
  } else {
    colIndex.registration = 0;
    if (table[0].length > 1) ignored = [`${table[0].length - 1} further column(s)`];
  }
  const rows: DraftRow[] = body.slice(0, MAX_KEPT).map((cells) => {
    const get = (c: Column) => (colIndex[c] === undefined ? '' : (cells[colIndex[c] as number] ?? '').trim());
    const rawDate = get('date');
    const rawType = get('type');
    const rawTime = get('time');
    return {
      key: newKey(),
      registration: normaliseRegistration(get('registration')),
      date: rawDate === '' ? defaults.date : (parseFlexibleDate(rawDate) ?? rawDate),
      type: rawType === '' ? defaults.type : parseBookingType(rawType),
      time: rawTime === '' ? '' : (parseTime(rawTime) ?? rawTime),
    };
  });
  return { rows, ignoredColumns: ignored, error: rows.length === 0 ? 'No data rows found.' : null };
}
