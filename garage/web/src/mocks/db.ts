/**
 * In-memory mock of the garage API (contract 1.0.0-draft.1). Pure logic; handlers.ts wraps it in MSW.
 * In the browser it can persist to localStorage so reloads keep the session (mock only).
 */
import type {
  Assessment, Booking, BookingType, BriefingExtras, Decision, DecisionType, FeedbackValue, ImportCounts, ImportPreview,
  ImportResult, ImportRow, Me, Member, PreviewRow, Role, Usage, Workshop,
} from '../api/types';
import { MAX_IMPORT_ROWS } from '../api/types';
import { parseBookingType } from '../lib/importDraft';
import { parseFlexibleDate, todayLondon } from '../lib/dates';
import { isPlausibleRegistration, normaliseRegistration } from '../lib/registration';
import { DEMO_SPECS, bandFor, briefingExtrasFor, demoBookings, makeAssessment, roundTo5 } from './fixtures';

/** Mock-only invite code. Real codes are issued by AutoSafe and checked by the server. */
export const VALID_INVITE_CODE = 'PILOT-0001';

export class MockHttpError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

interface User { id: string; email: string }
interface Membership { id: string; user_id: string; role: Role; created_at: string }
interface StoredPreview { rows: PreviewRow[]; counts: ImportCounts; expires_at: string; accepted: Array<{ reg: string; date: string; type: BookingType }>; result: ImportResult | null }

interface State {
  seq: number;
  users: User[];
  memberships: Membership[];
  workshop: { id: string; name: string } | null;
  mailbox: Record<string, string>; // token -> email
  sessionUserId: string | null;
  bookings: Booking[];
  assessments: Record<string, Assessment[]>; // newest first
  pollCount: Record<string, number>;
  decisions: Record<string, Decision[]>;
  feedback: Record<string, FeedbackValue>;
  previews: Record<string, StoredPreview>;
  assessmentsUsed: number;
}

const STORAGE_KEY = 'garage_mock_state_v2';

const fresh = (): State => ({
  seq: 0, users: [], memberships: [], workshop: null, mailbox: {}, sessionUserId: null, bookings: [], assessments: {},
  pollCount: {}, decisions: {}, feedback: {}, previews: {}, assessmentsUsed: 0,
});

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export class MockDb {
  s: State = fresh();
  persist = false;
  /** Fetches of a pending booking before its assessment resolves. */
  pendingPolls = 2;
  /** When false, the PROPOSED briefing and feedback endpoints answer 404, like the real contract today. */
  supportsExtras = true;
  /** Page size ceiling for GET /bookings (the real default is 50). */
  defaultLimit = 50;

  constructor(opts: { persist?: boolean } = {}) {
    this.persist = !!opts.persist;
    if (this.persist) this.load();
  }

  reset() {
    this.s = fresh();
    this.supportsExtras = true;
    this.save();
  }

  private load() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) this.s = { ...fresh(), ...(JSON.parse(raw) as State) };
    } catch { /* ignore */ }
  }

  private save() {
    if (!this.persist) return;
    try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(this.s)); } catch { /* ignore */ }
  }

  private id(prefix: string) {
    this.s.seq += 1;
    return `${prefix}-${this.s.seq}`;
  }

  private now() { return new Date().toISOString(); }

  /** Mock-only: the "email" a test can read to follow the sign-in link. */
  lastTokenFor(email: string): string | null {
    const e = email.trim().toLowerCase();
    const toks = Object.entries(this.s.mailbox).filter(([, v]) => v === e);
    return toks.length ? toks[toks.length - 1][0] : null;
  }

  // ---- auth -----------------------------------------------------------
  signup(p: { invite_code: string; workshop_name: string; email: string }) {
    const email = (p.email ?? '').trim().toLowerCase();
    if (!EMAIL_RE.test(email)) throw new MockHttpError(422, 'validation_error', 'Enter a valid email address.');
    const name = (p.workshop_name ?? '').trim();
    if (name.length < 1 || name.length > 120) throw new MockHttpError(422, 'validation_error', 'Workshop name must be 1 to 120 characters.');
    if ((p.invite_code ?? '').trim().toUpperCase() !== VALID_INVITE_CODE) throw new MockHttpError(403, 'invalid_invite', 'That invite code is not valid.');
    if (this.s.workshop) throw new MockHttpError(403, 'invalid_invite', 'That invite code has already been used.');
    const user: User = { id: this.id('u'), email };
    this.s.users.push(user);
    this.s.workshop = { id: this.id('w'), name };
    this.s.memberships.push({ id: this.id('m'), user_id: user.id, role: 'owner', created_at: this.now() });
    this.issueToken(email);
    this.save();
    return { status: 'accepted' as const };
  }

  requestLoginLink(email: string) {
    const e = (email ?? '').trim().toLowerCase();
    if (!EMAIL_RE.test(e)) throw new MockHttpError(422, 'validation_error', 'Enter a valid email address.');
    if (this.s.users.some((u) => u.email === e)) this.issueToken(e);
    this.save();
    return { status: 'accepted' as const }; // same answer whether or not the address has an account
  }

  private issueToken(email: string) {
    const token = `tok-${this.id('t')}-${'x'.repeat(20)}`;
    this.s.mailbox[token] = email;
  }

  consume(token: string): Me {
    const email = this.s.mailbox[token];
    if (!email) throw new MockHttpError(410, 'link_invalid', 'This sign-in link is invalid or has expired. Request a new one.');
    delete this.s.mailbox[token];
    const user = this.s.users.find((u) => u.email === email)!;
    this.s.sessionUserId = user.id;
    this.save();
    return this.me();
  }

  private requireUser(): { user: User; membership: Membership } {
    const user = this.s.users.find((x) => x.id === this.s.sessionUserId);
    const membership = user && this.s.memberships.find((m) => m.user_id === user.id);
    if (!user || !membership || !this.s.workshop) throw new MockHttpError(401, 'unauthenticated', 'Sign in to continue.');
    return { user, membership };
  }

  private requireOwner() {
    const { membership } = this.requireUser();
    if (membership.role !== 'owner') throw new MockHttpError(403, 'forbidden', 'Only the owner can do this.');
  }

  me(): Me {
    const { user, membership } = this.requireUser();
    const w = this.s.workshop!;
    return {
      user: { id: user.id, email: user.email },
      workshop: { id: w.id, name: w.name },
      role: membership.role,
      membership_id: membership.id,
      memberships: [{ workshop_id: w.id, workshop_name: w.name, role: membership.role }],
    };
  }

  logout() {
    this.requireUser();
    this.s.sessionUserId = null;
    this.save();
  }

  // ---- workshop and team --------------------------------------------
  usage(): Usage {
    this.requireUser();
    return { date: todayLondon(), assessments_used: this.s.assessmentsUsed, assessments_limit: 50, assessments_remaining: 50 - this.s.assessmentsUsed };
  }

  workshop(): Workshop {
    this.requireUser();
    return { id: this.s.workshop!.id, name: this.s.workshop!.name, usage: this.usage() };
  }

  renameWorkshop(name: string): Workshop {
    this.requireOwner();
    const n = (name ?? '').trim();
    if (n.length < 1 || n.length > 120) throw new MockHttpError(422, 'validation_error', 'Workshop name must be 1 to 120 characters.');
    this.s.workshop!.name = n;
    this.save();
    return this.workshop();
  }

  private member(m: Membership): Member {
    const u = this.s.users.find((x) => x.id === m.user_id)!;
    return { membership_id: m.id, email: u.email, role: m.role, status: 'active', created_at: m.created_at };
  }

  staff() {
    this.requireUser();
    return { items: this.s.memberships.map((m) => this.member(m)) };
  }

  addStaff(email: string): Member {
    this.requireOwner();
    const e = (email ?? '').trim().toLowerCase();
    if (!EMAIL_RE.test(e)) throw new MockHttpError(422, 'validation_error', 'Enter a valid email address.');
    if (this.s.users.some((u) => u.email === e)) throw new MockHttpError(409, 'already_member', 'That person is already a member.');
    const user: User = { id: this.id('u'), email: e };
    this.s.users.push(user);
    const m: Membership = { id: this.id('m'), user_id: user.id, role: 'staff', created_at: this.now() };
    this.s.memberships.push(m);
    this.issueToken(e);
    this.save();
    return this.member(m);
  }

  removeStaff(membershipId: string) {
    this.requireOwner();
    const m = this.s.memberships.find((x) => x.id === membershipId);
    if (!m) throw new MockHttpError(404, 'not_found', 'No such member.');
    if (m.role === 'owner') throw new MockHttpError(409, 'cannot_remove_owner', 'The owner cannot be removed.');
    this.s.memberships = this.s.memberships.filter((x) => x.id !== membershipId);
    this.s.users = this.s.users.filter((x) => x.id !== m.user_id);
    this.save();
  }

  // ---- imports --------------------------------------------------------
  previewImport(req: { format: string; rows?: ImportRow[] }): ImportPreview {
    this.requireUser();
    const rows = req.rows ?? [];
    if (rows.length > MAX_IMPORT_ROWS) throw new MockHttpError(422, 'too_many_rows', `At most ${MAX_IMPORT_ROWS} rows per import.`);
    const out: PreviewRow[] = [];
    const accepted: StoredPreview['accepted'] = [];
    const seen = new Map<string, number>();
    const counts: ImportCounts = { total: rows.length, new: 0, duplicate_existing: 0, duplicate_in_batch: 0, invalid: 0 };
    rows.forEach((r, i) => {
      const reg = normaliseRegistration(r.registration ?? '');
      const date = parseFlexibleDate(r.date ?? '');
      const type = parseBookingType(r.type ?? '');
      const errors = [] as PreviewRow['errors'];
      if (!reg) errors.push({ code: 'missing_registration', field: 'registration', message: 'Registration is missing.' });
      else if (!isPlausibleRegistration(reg)) errors.push({ code: 'invalid_registration', field: 'registration', message: 'Registration is not valid.' });
      if (!r.date) errors.push({ code: 'missing_date', field: 'date', message: 'Date is missing.' });
      else if (!date) errors.push({ code: 'invalid_date', field: 'date', message: 'Date is not valid.' });
      if (!r.type) errors.push({ code: 'missing_type', field: 'type', message: 'Booking type is missing.' });
      else if (!type) errors.push({ code: 'invalid_type', field: 'type', message: 'Booking type is not recognised.' });
      const row: PreviewRow = { row_number: i + 1, status: 'new', registration: reg || null, appointment_date: date, booking_type: type || null, existing_booking_id: null, duplicate_of_row: null, errors, warnings: [] };
      if (errors.length) { row.status = 'invalid'; counts.invalid++; }
      else {
        const k = `${reg}|${date}|${type}`;
        const existing = this.s.bookings.find((b) => b.registration === reg && b.appointment_date === date && b.booking_type === type);
        if (existing) { row.status = 'duplicate_existing'; row.existing_booking_id = existing.id; counts.duplicate_existing++; }
        else if (seen.has(k)) { row.status = 'duplicate_in_batch'; row.duplicate_of_row = seen.get(k)!; counts.duplicate_in_batch++; }
        else { seen.set(k, i + 1); counts.new++; accepted.push({ reg, date: date!, type: type as BookingType }); }
      }
      out.push(row);
    });
    const import_id = this.id('imp');
    const expires_at = new Date(Date.now() + 3600_000).toISOString();
    this.s.previews[import_id] = { rows: out, counts, expires_at, accepted, result: null };
    this.save();
    return { import_id, status: 'previewed', counts, rows: out, expires_at };
  }

  commitImport(importId: string): ImportResult {
    this.requireUser();
    const p = this.s.previews[importId];
    if (!p) throw new MockHttpError(404, 'not_found', 'No such import.');
    if (p.result) return { ...p.result, already_committed: true };
    const today = todayLondon();
    const ids: string[] = [];
    for (const a of p.accepted) {
      const bid = this.id('b');
      const supported = a.type === 'mot' || a.type === 'service_mot';
      this.s.bookings.push({
        id: bid, registration: a.reg, appointment_date: a.date, original_appointment_date: a.date, booking_type: a.type,
        status: 'active', source: 'import', rescheduled: false, created_at: this.now(), assessment: null, decision: null,
      });
      const first = supported
        ? makeAssessment(bid, 'pending', { reason: 'upstream_retrying', reasonText: 'Fetching recorded MOT history.', today })
        : makeAssessment(bid, 'unsupported', { reason: a.type === 'retest' ? 'retest_booking' : 'service_only', reasonText: a.type === 'retest' ? 'Retests are not assessed.' : 'Service-only bookings are not assessed.', today });
      this.s.assessments[bid] = [first];
      ids.push(bid);
    }
    p.result = { import_id: importId, status: 'committed', already_committed: false, counts: p.counts, created_booking_ids: ids, committed_at: this.now() };
    this.save();
    return p.result;
  }

  // ---- bookings -------------------------------------------------------
  private latest(b: Booking): Assessment | null {
    return (this.s.assessments[b.id] ?? [])[0] ?? null;
  }

  private resolvePending(b: Booking) {
    const cur = this.latest(b);
    if (!cur || cur.state !== 'pending') return;
    const polls = (this.s.pollCount[b.id] ?? 0) + 1;
    this.s.pollCount[b.id] = polls;
    if (polls < this.pendingPolls) return;
    const today = todayLondon();
    const version = cur.version;
    const trigger = cur.trigger;
    const spec = DEMO_SPECS.find((x) => x.reg === b.registration && b.id.startsWith('demo'));
    let next: Assessment;
    const reg = b.registration;
    if (spec) {
      const state = spec.state === 'pending' ? 'available' : spec.state;
      next = makeAssessment(b.id, state, { percent: roundTo5(spec.p ?? 0.36), reason: spec.reason, reasonText: spec.text, today, version, trigger });
    } else if (reg.startsWith('FAIL')) next = makeAssessment(b.id, 'failed', { reason: 'provider_error', reasonText: 'The assessment did not complete. Refresh to try again.', today, version, trigger });
    else if (reg.startsWith('UNAV')) next = makeAssessment(b.id, 'unavailable', { reason: 'upstream_unavailable', reasonText: 'The recorded MOT history could not be retrieved. Try refreshing the assessment.', today, version, trigger });
    else if (reg.startsWith('NOPR')) next = makeAssessment(b.id, 'unsupported', { reason: 'no_prior_mot', reasonText: 'No earlier MOT test is recorded for this vehicle, so no estimate is made.', today, version, trigger });
    else next = makeAssessment(b.id, 'available', { percent: roundTo5((5 + (hash(reg) % 50)) / 100), today, version, trigger });
    this.s.assessments[b.id][0] = next;
    if (next.state === 'available') this.s.assessmentsUsed += 1;
  }

  private view(b: Booking): Booking {
    const d = (this.s.decisions[b.id] ?? [])[0] ?? null;
    return { ...b, assessment: this.latest(b), decision: d };
  }

  bookings(q: { from?: string; to?: string; limit?: number; cursor?: string | null }) {
    this.requireUser();
    const from = q.from ?? '0000-01-01';
    const to = q.to ?? '9999-12-31';
    const all = this.s.bookings
      .filter((b) => b.appointment_date >= from && b.appointment_date <= to)
      .sort((a, b) => a.appointment_date.localeCompare(b.appointment_date) || a.id.localeCompare(b.id));
    const limit = q.limit ?? this.defaultLimit;
    const start = q.cursor ? Number(q.cursor) : 0;
    const page = all.slice(start, start + limit);
    page.forEach((b) => this.resolvePending(b));
    this.save();
    const nextStart = start + limit;
    return { items: page.map((b) => this.view(b)), next_cursor: nextStart < all.length ? String(nextStart) : null };
  }

  private find(id: string): Booking {
    this.requireUser();
    const b = this.s.bookings.find((x) => x.id === id);
    if (!b) throw new MockHttpError(404, 'not_found', 'No such booking.');
    return b;
  }

  booking(id: string): Booking {
    const b = this.find(id);
    this.resolvePending(b);
    this.save();
    return this.view(b);
  }

  assessments(id: string) {
    const b = this.find(id);
    return { items: this.s.assessments[b.id] ?? [] };
  }

  refreshAssessment(id: string): Assessment {
    const b = this.find(id);
    if (b.status === 'cancelled') throw new MockHttpError(409, 'booking_cancelled', 'This booking is cancelled.');
    const cur = this.latest(b);
    if (cur?.state === 'pending') throw new MockHttpError(409, 'assessment_pending', 'An assessment is already running.');
    if (b.booking_type === 'retest' || b.booking_type === 'service') throw new MockHttpError(409, 'unsupported', 'This booking type is not assessed.');
    if (this.s.assessmentsUsed >= 50) throw new MockHttpError(429, 'fair_use_limit', 'The daily assessment limit has been reached.');
    const next = makeAssessment(b.id, 'pending', { reason: 'upstream_retrying', reasonText: 'Fetching recorded MOT history.', today: todayLondon(), version: (cur?.version ?? 0) + 1, trigger: 'refresh' });
    this.s.assessments[b.id].unshift(next);
    this.s.pollCount[b.id] = 0;
    this.save();
    return next;
  }

  decisions(id: string) {
    const b = this.find(id);
    return { items: this.s.decisions[b.id] ?? [] };
  }

  decide(id: string, type: DecisionType, note?: string): Decision {
    const b = this.find(id);
    const types: DecisionType[] = ['check_repair_history', 'offer_inspection', 'adjust_booking_plan', 'proceed_as_booked'];
    if (!types.includes(type)) throw new MockHttpError(422, 'validation_error', 'Choose one of the four options.');
    if ((note ?? '').length > 500) throw new MockHttpError(422, 'validation_error', 'Keep the note to 500 characters.');
    const { user, membership } = this.requireUser();
    const prev = (this.s.decisions[b.id] ?? [])[0];
    const d: Decision = {
      id: this.id('d'), booking_id: b.id, decision_type: type, note: note ?? null,
      actor: { membership_id: membership.id, email: user.email, role: membership.role },
      assessment_id: this.latest(b)?.id ?? null, supersedes_id: prev?.id ?? null, created_at: this.now(),
    };
    (this.s.decisions[b.id] ??= []).unshift(d);
    this.save();
    return d;
  }

  /** Mock-only seeding (tests and screenshots): the fictional demo bookings, stored as real bookings. */
  seedDemoBookings() {
    this.requireUser();
    for (const b of demoBookings(todayLondon())) {
      const { assessment, ...rest } = b;
      this.s.bookings.push({ ...rest, assessment: null });
      this.s.assessments[b.id] = assessment ? [assessment] : [];
    }
    this.save();
  }

  // ---- PROPOSED endpoints (not in the contract) ----------------------
  briefingExtras(id: string): BriefingExtras {
    const b = this.find(id);
    if (!this.supportsExtras) throw new MockHttpError(404, 'not_found', 'Not found.');
    return briefingExtrasFor(this.view(b), todayLondon(), hash(b.registration) % 7);
  }

  setFeedback(id: string, value: FeedbackValue) {
    const b = this.find(id);
    if (!this.supportsExtras) throw new MockHttpError(404, 'not_found', 'Not found.');
    if (value !== 'helpful' && value !== 'not_helpful') throw new MockHttpError(422, 'validation_error', 'Invalid feedback value.');
    this.s.feedback[b.id] = value;
    this.save();
    return { value };
  }
}

export { bandFor };
