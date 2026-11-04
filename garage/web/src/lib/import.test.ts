import { describe, expect, it } from 'vitest';
import { parseCsv, parseImportCsv } from './csv';
import { checkDraft, emptyRow, parseBookingType, rowsFromPaste, toImportRows } from './importDraft';
import { MAX_IMPORT_ROWS } from '../api/types';

const D = { date: '2026-10-27', type: 'mot' as const };

describe('pasted list', () => {
  it('applies the shared date and type, normalises, and splits on lines and commas', () => {
    const rows = rowsFromPaste('ab12 cde\n  xy99zzz, q1\r\n\n', D.date, 'retest');
    expect(rows.map((r) => r.registration)).toEqual(['AB12CDE', 'XY99ZZZ', 'Q1']);
    expect(rows.every((r) => r.date === D.date && r.type === 'retest')).toBe(true);
  });
  it('flags invalid and duplicate rows; the first occurrence stays valid', () => {
    const rows = rowsFromPaste('AB12CDE\nab12 cde\n!!\nTOOLONG123', D.date, D.type);
    const v = checkDraft(rows, null);
    expect(v.get(rows[0].key)?.kind).toBe('ok');
    expect(v.get(rows[1].key)?.kind).toBe('duplicate');
    expect(v.get(rows[2].key)?.kind).toBe('invalid');
    expect(v.get(rows[3].key)?.kind).toBe('invalid');
  });
  it('same registration on a different date or type is not a duplicate', () => {
    const a = rowsFromPaste('AB12CDE', '2026-10-27', 'mot')[0];
    const b = rowsFromPaste('AB12CDE', '2026-10-28', 'mot')[0];
    const c = rowsFromPaste('AB12CDE', '2026-10-27', 'retest')[0];
    const v = checkDraft([a, b, c], null);
    expect([b, c].map((r) => v.get(r.key)?.kind)).toEqual(['ok', 'ok']);
  });
  it('merges the service preview on top of the local checks', () => {
    const rows = rowsFromPaste('AB12CDE\nXY99ZZZ', D.date, D.type);
    const server = [
      { row_number: 1, status: 'duplicate_existing' as const, errors: [], warnings: [] },
      { row_number: 2, status: 'invalid' as const, errors: [{ code: 'date_out_of_range', message: 'Date is too far away.' }], warnings: [] },
    ];
    const v = checkDraft(rows, server);
    expect(v.get(rows[0].key)?.kind).toBe('duplicate');
    expect(v.get(rows[1].key)).toEqual({ kind: 'invalid', messages: ['Date is too far away.'] });
  });
  it('sends only the contract fields: registration, date, type', () => {
    const rows = rowsFromPaste('ab12 cde', D.date, 'service_mot');
    expect(toImportRows(rows)).toEqual([{ registration: 'AB12CDE', date: D.date, type: 'service_mot' }]);
  });
});

describe('booking type words', () => {
  it('maps common spellings to the four contract types', () => {
    expect(parseBookingType('MOT')).toBe('mot');
    expect(parseBookingType('MOT retest')).toBe('retest');
    expect(parseBookingType('Service only')).toBe('service');
    expect(parseBookingType('Service + MOT')).toBe('service_mot');
    expect(parseBookingType('valet')).toBe('');
  });
});

describe('csv', () => {
  it('parses quoted fields, doubled quotes and CRLF', () => {
    expect(parseCsv('a,"b,c","d ""e"""\r\n1,2,3\r\n')).toEqual([['a', 'b,c', 'd "e"'], ['1', '2', '3']]);
  });
  it('maps known columns (incl. contract aliases), accepts DD/MM/YYYY and type words, defaults the rest', () => {
    const r = parseImportCsv('vrm,appointment_date,booking_type\nAB12CDE,28/10/2026,Retest\nXY99ZZZ,,\n', D);
    expect(r.error).toBeNull();
    expect(r.rows[0]).toMatchObject({ registration: 'AB12CDE', date: '2026-10-28', type: 'retest' });
    expect(r.rows[1]).toMatchObject({ registration: 'XY99ZZZ', date: D.date, type: 'mot' });
  });
  it('drops customer-style columns entirely: reported as ignored, never kept', () => {
    const r = parseImportCsv('registration,customer name,email,phone\nAB12CDE,Jane Doe,j@x.com,07000\n', D);
    expect(r.ignoredColumns).toEqual(['customer name', 'email', 'phone']);
    expect(JSON.stringify(r.rows)).not.toMatch(/Jane|j@x|07000/);
  });
  it('treats a headerless file as registrations and reports extra columns as ignored', () => {
    const r = parseImportCsv('AB12CDE,Jane Doe\nXY99ZZZ,Bob\n', D);
    expect(r.rows.map((x) => x.registration)).toEqual(['AB12CDE', 'XY99ZZZ']);
    expect(JSON.stringify(r.rows)).not.toMatch(/Jane|Bob/);
    expect(r.ignoredColumns.length).toBe(1);
  });
  it('marks unknown type words and bad dates invalid instead of guessing', () => {
    const r = parseImportCsv('registration,date,type\nAB12CDE,31/02/2026,mot\nXY99ZZZ,2026-10-28,valet\n', D);
    const v = checkDraft(r.rows, null);
    expect(v.get(r.rows[0].key)?.messages.join()).toMatch(/real date/);
    expect(v.get(r.rows[1].key)?.messages.join()).toMatch(/booking type/);
  });
  it('reports an empty file', () => {
    expect(parseImportCsv('', D).error).toMatch(/empty/i);
  });
});

describe('limits', () => {
  it('the row limit is 200', () => expect(MAX_IMPORT_ROWS).toBe(200));
  it('manual rows start blank and invalid', () => {
    const r = emptyRow(D.date);
    expect(checkDraft([r], null).get(r.key)?.kind).toBe('invalid');
  });
});
