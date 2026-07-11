/**
 * Route-interception mocks for every `/api/*` call the app makes, derived
 * from fixtures/reportResponses.ts. This suite runs with no backend process
 * at all (see e2e/README.md) -- every one of these must be registered
 * before the request that would trigger it (page.route handlers only
 * affect requests made after they're registered), and every response body
 * is either a real ReportV2 fixture or a real ApiErrorEnvelope fixture, so
 * the frontend's own runtime validation (services/reportValidation.ts)
 * exercises the exact same shapes it would against a real backend.
 */
import type { Page } from '@playwright/test';
import type { ApiErrorEnvelope, PublicStats, ReportV2 } from '../../types';

/**
 * Default /api/stats fixture. checks_this_month is kept under 1000
 * deliberately, mirroring App.test.tsx's DEFAULT_STATS: toLocaleString()
 * inserts a thousands separator whose exact character depends on
 * locale/ICU data, which text assertions in these specs don't want to
 * depend on.
 */
export const DEFAULT_STATS: PublicStats = {
  total_checks: 15321,
  checks_this_month: 481,
  mot_records: '142M+',
};

type ReportOrError = ReportV2 | ApiErrorEnvelope;

function isErrorEnvelope(body: ReportOrError): body is ApiErrorEnvelope {
  return typeof (body as ApiErrorEnvelope).error_code === 'string';
}

/** Mocks GET /api/stats, fulfilling immediately with `stats`. */
export async function mockStats(page: Page, stats: PublicStats = DEFAULT_STATS): Promise<void> {
  await page.route('**/api/stats', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stats) });
  });
}

/**
 * Mocks GET /api/stats behind a manually-released gate, for exercising the
 * "stats resolves while the user is mid-type" remount regression
 * (App.tsx's `getPublicStats().then(setStats)` re-renders HomePage;
 * HeroForm must not lose whatever the user has already typed when that
 * happens). The route registered here overrides any earlier mockStats()
 * registration for the same page -- Playwright runs the most-recently
 * registered matching handler first -- so callers may call mockStats() in
 * a shared beforeEach and gateStats() again inside one specific test.
 *
 * Returns a release() function; the pending request is not fulfilled until
 * it's called.
 */
export async function gateStats(page: Page, stats: PublicStats = DEFAULT_STATS): Promise<() => void> {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/stats', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await gate;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stats) });
  });
  return () => release();
}

/** Mocks POST /api/v2/reports (report creation) with either a ReportV2 or
 * an ApiErrorEnvelope body. `status` defaults sensibly from the body shape
 * (200 for a report, 404 for an envelope) but every call site in this
 * suite passes it explicitly since the real status varies by error code
 * (see report_contract.ERROR_CODE_STATUS). */
export async function mockCreateReport(
  page: Page,
  body: ReportOrError,
  status: number = isErrorEnvelope(body) ? 404 : 200
): Promise<void> {
  await page.route('**/api/v2/reports', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

/** Mocks GET /api/v2/reports/<token> (report fetch by share token) with
 * either a ReportV2 or an ApiErrorEnvelope body. */
export async function mockGetReport(
  page: Page,
  token: string,
  body: ReportOrError,
  status: number = isErrorEnvelope(body) ? 404 : 200
): Promise<void> {
  await page.route(`**/api/v2/reports/${encodeURIComponent(token)}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

/**
 * Aborts GET /api/v2/reports/<token> outright -- the class of failure
 * (offline, DNS, CORS-block: anything that never reached a server) that
 * services/reportApi.ts's safeFetch() turns into a `network_error`
 * ReportApiError, distinct from any envelope-carrying HTTP error response.
 */
export async function abortGetReport(page: Page, token: string): Promise<void> {
  await page.route(`**/api/v2/reports/${encodeURIComponent(token)}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await route.abort('failed');
  });
}
