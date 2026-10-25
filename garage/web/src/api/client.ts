import type {
  ApiErrorBody, Assessment, Booking, BookingPage, BriefingExtras, Decision, DecisionType, FeedbackValue,
  ImportPreview, ImportResult, ImportRow, Me, Member, Usage, Workshop,
} from './types';

export const API_BASE: string = import.meta.env?.VITE_API_BASE ?? '/api/garage/v1';

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      credentials: 'include',
      headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'network', 'Could not reach the service. Check your connection and try again.');
  }
  if (!res.ok) {
    let code = 'error';
    let message = 'Something went wrong. Please try again.';
    try {
      const j = (await res.json()) as ApiErrorBody;
      code = j.error.code;
      message = j.error.message;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const id = encodeURIComponent;

export const api = {
  signup: (p: { invite_code: string; workshop_name: string; email: string }) => request<{ status: 'accepted' }>('POST', '/auth/signup', p),
  requestLoginLink: (email: string) => request<{ status: 'accepted' }>('POST', '/auth/login-links', { email }),
  consumeLink: (token: string) => request<Me>('POST', `/auth/links/${id(token)}/consume`, {}),
  logout: () => request<void>('POST', '/auth/logout'),
  me: () => request<Me>('GET', '/me'),

  workshop: () => request<Workshop>('GET', '/workshop'),
  renameWorkshop: (name: string) => request<Workshop>('PATCH', '/workshop', { name }),
  usage: () => request<Usage>('GET', '/usage'),

  staff: () => request<{ items: Member[] }>('GET', '/staff'),
  addStaff: (email: string) => request<Member>('POST', '/staff', { email }),
  removeStaff: (membershipId: string) => request<void>('DELETE', `/staff/${id(membershipId)}`),

  previewImport: (rows: ImportRow[]) => request<ImportPreview>('POST', '/imports/preview', { format: 'manual', rows }),
  commitImport: (importId: string) => request<ImportResult>('POST', `/imports/${id(importId)}/commit`),

  /** Follows next_cursor so a window with more than one page is complete. */
  async bookings(from: string, to: string): Promise<Booking[]> {
    const out: Booking[] = [];
    let cursor: string | null = null;
    for (let i = 0; i < 20; i++) {
      const q = new URLSearchParams({ from, to, limit: '200' });
      if (cursor) q.set('cursor', cursor);
      const page: BookingPage = await request<BookingPage>('GET', `/bookings?${q.toString()}`);
      out.push(...page.items);
      cursor = page.next_cursor;
      if (!cursor) break;
    }
    return out;
  },
  /** True when the workshop has any booking at all (used to decide whether to show onboarding). */
  hasBookings: async () => (await request<BookingPage>('GET', '/bookings?limit=1')).items.length > 0,
  booking: (bookingId: string) => request<Booking>('GET', `/bookings/${id(bookingId)}`),
  assessments: (bookingId: string) => request<{ items: Assessment[] }>('GET', `/bookings/${id(bookingId)}/assessments`),
  refreshAssessment: (bookingId: string) => request<Assessment>('POST', `/bookings/${id(bookingId)}/assessments`),
  decisions: (bookingId: string) => request<{ items: Decision[] }>('GET', `/bookings/${id(bookingId)}/decisions`),
  decide: (bookingId: string, decision_type: DecisionType, note: string) =>
    request<Decision>('POST', `/bookings/${id(bookingId)}/decisions`, note ? { decision_type, note } : { decision_type }),

  /** NOT IN CONTRACT (proposed). A 404 or 405 is treated as "not provided". */
  briefingExtras: (bookingId: string) => request<BriefingExtras>('GET', `/bookings/${id(bookingId)}/briefing`),
  /** NOT IN CONTRACT (proposed). */
  feedback: (bookingId: string, value: FeedbackValue) => request<{ value: FeedbackValue }>('PUT', `/bookings/${id(bookingId)}/feedback`, { value }),
};

export type Api = typeof api;
