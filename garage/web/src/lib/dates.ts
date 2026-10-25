/** Date helpers. All booking dates are calendar dates in Europe/London. */

const LONDON = 'Europe/London';

export function todayLondon(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: LONDON,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')}`;
}

export function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Inclusive list of ISO dates from `start` for `days` days. */
export function dateRange(start: string, days: number): string[] {
  return Array.from({ length: days }, (_, i) => addDays(start, i));
}

/** "Mon 27 Oct" */
export function formatDay(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  }).format(new Date(`${iso}T00:00:00Z`));
}

/** "27 Oct 2026" */
export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(`${iso}T00:00:00Z`));
}

/** "27 Oct 2026, 14:05" in Europe/London. */
export function formatTimestamp(isoTimestamp: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: LONDON,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(isoTimestamp));
}

/** Parses DD/MM/YYYY or YYYY-MM-DD; returns ISO or null. */
export function parseFlexibleDate(raw: string): string | null {
  const s = raw.trim();
  if (isIsoDate(s)) return s;
  const m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/.exec(s);
  if (!m) return null;
  const iso = `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`;
  return isIsoDate(iso) ? iso : null;
}
