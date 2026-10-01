/**
 * Landing attribution for the SPA (OA-005, DECISIONS.md D-005). Collection
 * is OFF (ACQUISITION_COLLECTOR_ENABLED=false): in that state this module's
 * only effect is hygiene, removing `al`/`src` from the address bar if a
 * visitor arrives with them.
 *
 * Two entry paths:
 *
 * 1. Handoff from a public page. The site's own CTA links to the app append
 *    `?al=<landing_id>&src=<source_group>` (static/acquisition-landing.js).
 *    The SPA validates both against the exact shapes we mint, holds them in
 *    memory and removes them with `history.replaceState` straight away. They
 *    are never stored, and never sent to a third party: Umami's filter
 *    reduces URLs to the path, the global Referrer-Policy is
 *    strict-origin-when-cross-origin, and the strip below runs from
 *    index.tsx before React renders, so before the first Umami page view
 *    (utils/analytics.ts trackPageView, fired from an App effect).
 *    The public page already emitted landing_observed; the SPA does not.
 *
 * 2. Direct SPA landing on `/` or `/app` (no handoff). The SPA mints a
 *    landing_id, derives source_group from the `document.referrer` ORIGIN
 *    only (or `paid_search` if the URL carries a paid-click marker, D-006),
 *    and emits landing_observed with an allowlisted page_family.
 *
 * A reload (or back/forward, prerender) is not a landing (D-006): the event
 * is emitted only when Navigation Timing says the load was a `navigate`. If
 * that API is unavailable the landing IS emitted (fail open: no storage
 * exists to dedupe with, and an unobservable reload is rarer than a
 * missing-API browser losing every landing). Reading the type is not storage.
 *
 * Report routes (`/app/report/*`) and every other SPA route are not
 * landings. A reload, new tab or typed URL starts a new, unattributed
 * session by design (no storage; accepted coverage limitation).
 */
import {
  ACQUISITION_COLLECTOR_ENABLED,
  emitAcquisitionEvent,
  globalPrivacyControlSet,
  randomId,
  setAcquisitionContext,
  type PageFamily,
  type SourceGroup,
} from './acquisitionEvents';

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** Source groups a public page may hand to the app. */
const HANDOFF_SOURCE_GROUPS: ReadonlySet<string> = new Set<SourceGroup>([
  'google_organic',
  'other_search',
  'direct',
  'referral',
  'unknown',
  'internal',
  'paid_search',
]);

/** Search engines other than Google (hostname, with or without `www.`). */
const OTHER_SEARCH_HOSTS = [
  /^bing\.com$/,
  /^duckduckgo\.com$/,
  /^([a-z]{2}\.)?search\.yahoo\.com$/,
  /^yahoo\.com$/,
  /^ecosia\.org$/,
  /^search\.brave\.com$/,
  /^startpage\.com$/,
  /^qwant\.com$/,
  /^yandex\.[a-z.]+$/,
  /^baidu\.com$/,
  /^search\.aol\.com$/,
  /^ask\.com$/,
  /^search\.naver\.com$/,
];
/** google.com, google.co.uk, google.de, google.com.au ... (not mail.google.com, docs.google.com). */
const GOOGLE_SEARCH_HOST = /^google\.(com|co\.[a-z]{2}|[a-z]{2,3})(\.[a-z]{2})?$/;

function bareHost(hostname: string): string {
  return hostname.toLowerCase().replace(/^www\./, '');
}

/**
 * Map a `document.referrer` to a source group using the ORIGIN only. The
 * referrer's path and query are never inspected or kept.
 */
export function classifyReferrer(referrer: string, ownHostname: string): SourceGroup {
  if (!referrer) return 'direct';
  let host: string;
  try {
    host = bareHost(new URL(referrer).hostname);
  } catch {
    return 'unknown';
  }
  if (!host) return 'unknown';
  if (host === bareHost(ownHostname)) return 'internal';
  if (GOOGLE_SEARCH_HOST.test(host)) return 'google_organic';
  if (OTHER_SEARCH_HOSTS.some((re) => re.test(host))) return 'other_search';
  return 'referral';
}

const PAID_PARAMS: ReadonlySet<string> = new Set(['gclid', 'gbraid', 'wbraid']);
const PAID_MEDIUMS: ReadonlySet<string> = new Set(['cpc', 'ppc', 'paid']);

/**
 * True if the landing URL query carries a paid-click marker (`gclid`,
 * `gbraid`, `wbraid`, or `utm_medium` of cpc/ppc/paid; keys and values
 * case-insensitive). Only presence/equality is tested: no value is returned,
 * stored or sent.
 */
export function hasPaidSearchMarker(search: string): boolean {
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search);
  } catch {
    return false;
  }
  for (const [key, value] of params) {
    const k = key.toLowerCase();
    if (PAID_PARAMS.has(k)) return true;
    if (k === 'utm_medium' && PAID_MEDIUMS.has(value.trim().toLowerCase())) return true;
  }
  return false;
}

/** Source group for a landing: a paid-click marker wins over the referrer origin (D-006). */
export function classifySource(referrer: string, ownHostname: string, search: string): SourceGroup {
  return hasPaidSearchMarker(search) ? 'paid_search' : classifyReferrer(referrer, ownHostname);
}

/**
 * True when this document load is a fresh navigation. Only `navigate` counts;
 * `reload`, `back_forward` and `prerender` do not. If Navigation Timing is
 * unavailable or empty, returns true (documented fail-open).
 */
export function isFreshNavigation(): boolean {
  try {
    const entries = performance.getEntriesByType('navigation') as PerformanceNavigationTiming[];
    const type = entries?.[0]?.type;
    return type === undefined ? true : type === 'navigate';
  } catch {
    return true;
  }
}

export interface Handoff {
  landingId: string;
  sourceGroup: SourceGroup;
}

/** Validate the `al`/`src` pair. Anything not exactly what we mint is ignored. */
export function parseHandoff(search: string): Handoff | null {
  const params = new URLSearchParams(search);
  const al = params.get('al');
  const src = params.get('src');
  if (al && src && UUID_PATTERN.test(al) && HANDOFF_SOURCE_GROUPS.has(src)) {
    return { landingId: al, sourceGroup: src as SourceGroup };
  }
  return null;
}

/** Remove `al` and `src` (only those) from the address bar, keeping the path, other params and hash. */
export function stripHandoffParams(): boolean {
  const params = new URLSearchParams(window.location.search);
  if (!params.has('al') && !params.has('src')) return false;
  params.delete('al');
  params.delete('src');
  const query = params.toString();
  window.history.replaceState(
    window.history.state,
    '',
    window.location.pathname + (query ? `?${query}` : '') + window.location.hash,
  );
  return true;
}

/** Page family for an SPA landing; null for every route that is not a landing (including report routes). */
export function spaLandingFamily(pathname: string): PageFamily | null {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
  if (path === '/') return 'home';
  if (path === '/app') return 'app';
  return null;
}

/**
 * Run once at startup, BEFORE React renders (index.tsx). Always strips the
 * handoff parameters; the rest happens only when collection is enabled and
 * Global Privacy Control is not set.
 */
export function initAcquisitionLanding(enabled: boolean = ACQUISITION_COLLECTOR_ENABLED): void {
  const handoff = parseHandoff(window.location.search);
  const search = window.location.search; // read before the strip; used only for the paid-click test
  stripHandoffParams();
  if (!enabled || globalPrivacyControlSet()) return;

  if (handoff) {
    setAcquisitionContext({ landingId: handoff.landingId, sourceGroup: handoff.sourceGroup, pageFamily: 'app' });
    return;
  }

  const family = spaLandingFamily(window.location.pathname);
  if (!family || !isFreshNavigation()) {
    setAcquisitionContext({ sourceGroup: 'unknown', pageFamily: 'app' });
    return;
  }
  const sourceGroup = classifySource(document.referrer, window.location.hostname, search);
  const landingId = randomId();
  // Later events happen on the app itself, so they carry page_family 'app';
  // only landing_observed carries the landing's family.
  setAcquisitionContext({ landingId, sourceGroup, pageFamily: 'app' });
  emitAcquisitionEvent({
    event: 'landing_observed',
    page_family: family,
    source_group: sourceGroup,
    observation_state: 'observed',
  });
}
