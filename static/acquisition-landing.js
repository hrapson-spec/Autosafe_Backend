/* AutoSafe first-party landing measurement for public server-rendered pages
 * (OA-005, DECISIONS.md D-005). OFF: ENABLED below is false and this file
 * does nothing. It flips to true only at the enable gate described in
 * docs/acquisition/COLLECTOR.md.
 *
 * When enabled, on a public page it:
 *  - derives a source group from the ORIGIN of document.referrer only (the
 *    referrer path and query are never read or sent), except that a paid-click
 *    marker in this page's own URL (gclid, gbraid, wbraid, utm_medium of
 *    cpc/ppc/paid) makes it paid_search; the marker and its value are only
 *    tested, never sent, stored or copied into links;
 *  - does nothing at all unless this load is a fresh navigation (Navigation
 *    Timing type 'navigate'); a reload or back/forward is not a landing. If
 *    the API is unavailable it proceeds (documented fail-open);
 *  - mints a random landing id (memory only);
 *  - sends one landing_observed event to the same-origin collector with an
 *    allowlisted page family (never the path);
 *  - appends #al=<landing id>&src=<source group> (the URL FRAGMENT, which is
 *    never sent in a request or a Referer) to the site's own links to the app
 *    (links to /app, and CTA links marked data-acq-cta) so the app can attribute
 *    a displayed result to this landing. A link that already has a fragment is
 *    left alone. The first inline script in the app's index.html consumes and
 *    removes the fragment before any third-party script can run.
 *
 * No cookies, localStorage, sessionStorage or IndexedDB. Sends nothing if
 * navigator.globalPrivacyControl is true. Never runs on report routes.
 */
(function () {
  'use strict';

  var ENABLED = false;
  if (!ENABLED) return;

  if (navigator.globalPrivacyControl === true) return;
  if (/^\/app\/report(\/|$)/.test(window.location.pathname)) return;

  var ENDPOINT = '/api/acquisition/events';
  var METRIC_VERSION = 'oa-metric-v1-draft';

  function randomId() {
    var c = window.crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    if (c && typeof c.getRandomValues === 'function') {
      var b = new Uint8Array(16);
      c.getRandomValues(b);
      b[6] = (b[6] & 0x0f) | 0x40;
      b[8] = (b[8] & 0x3f) | 0x80;
      var h = '';
      for (var i = 0; i < 16; i++) h += (b[i] < 16 ? '0' : '') + b[i].toString(16);
      return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
    }
    return null; // no secure randomness: measure nothing rather than guess an id
  }

  function bareHost(host) {
    return String(host).toLowerCase().replace(/^www\./, '');
  }

  var GOOGLE_HOST = /^google\.(com|co\.[a-z]{2}|[a-z]{2,3})(\.[a-z]{2})?$/;
  var OTHER_SEARCH = [
    /^bing\.com$/, /^duckduckgo\.com$/, /^([a-z]{2}\.)?search\.yahoo\.com$/, /^yahoo\.com$/,
    /^ecosia\.org$/, /^search\.brave\.com$/, /^startpage\.com$/, /^qwant\.com$/,
    /^yandex\.[a-z.]+$/, /^baidu\.com$/, /^search\.aol\.com$/, /^ask\.com$/, /^search\.naver\.com$/
  ];

  /* Origin only. Same rules as utils/acquisitionLanding.ts (kept in step by tests). */
  function classifyReferrer(referrer, ownHost) {
    if (!referrer) return 'direct';
    var host;
    try { host = bareHost(new URL(referrer).hostname); } catch (e) { return 'unknown'; }
    if (!host) return 'unknown';
    if (host === bareHost(ownHost)) return 'internal';
    if (GOOGLE_HOST.test(host)) return 'google_organic';
    for (var i = 0; i < OTHER_SEARCH.length; i++) {
      if (OTHER_SEARCH[i].test(host)) return 'other_search';
    }
    return 'referral';
  }

  /* Allowlisted families; the path itself is never sent. */
  function pageFamily(pathname) {
    var p = pathname.toLowerCase().replace(/\/+$/, '') || '/';
    if (p === '/') return 'home';
    if (p === '/app') return 'app';
    if (/^\/(static\/|app\/)?guides\/[^/]+$/.test(p)) return 'guide';
    if (p === '/will-my-car-pass-mot') return 'pillar';
    var m = p.match(/^\/mot-check(?:\/(.*))?$/);
    if (m) {
      var segs = m[1] ? m[1].split('/') : [];
      if (segs.length === 0) return 'other_public';
      if (segs[0] === 'compare') return 'comparison';
      if (segs[0] === 'problems') return 'problem_hub';
      if (segs.length === 1) return 'make';
      return 'model';
    }
    return 'other_public';
  }

  /* Reload / back_forward / prerender are not landings (D-006). No Navigation
   * Timing support: fail open and treat the load as a landing. */
  function isFreshNavigation() {
    try {
      var entries = window.performance.getEntriesByType('navigation');
      var type = entries && entries[0] ? entries[0].type : undefined;
      return type === undefined ? true : type === 'navigate';
    } catch (e) {
      return true;
    }
  }
  if (!isFreshNavigation()) return;

  /* Paid-click marker in this page's own URL. Presence/equality test only. */
  function hasPaidMarker(search) {
    var params;
    try { params = new URLSearchParams(search); } catch (e) { return false; }
    var found = false;
    params.forEach(function (value, key) {
      var k = key.toLowerCase();
      if (k === 'gclid' || k === 'gbraid' || k === 'wbraid') found = true;
      if (k === 'utm_medium') {
        var v = String(value).trim().toLowerCase();
        if (v === 'cpc' || v === 'ppc' || v === 'paid') found = true;
      }
    });
    return found;
  }

  var landingId = randomId();
  var sessionId = randomId();
  if (!landingId || !sessionId) return;

  var source = hasPaidMarker(window.location.search)
    ? 'paid_search'
    : classifyReferrer(document.referrer, window.location.hostname);
  var family = pageFamily(window.location.pathname);

  function post(body, attempt, startedAt) {
    function retry() {
      if (attempt === 0 && new Date().getTime() - startedAt + 1000 <= 10000) {
        setTimeout(function () { post(body, 1, startedAt); }, 1000);
      }
    }
    try {
      window.fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
        keepalive: true,
        credentials: 'omit',
        referrerPolicy: 'no-referrer',
        cache: 'no-store',
        mode: 'same-origin'
      }).then(function (res) { if (res.status >= 500) retry(); }, retry);
    } catch (e) {
      retry();
    }
  }

  var eventId = randomId();
  if (eventId) {
    post(JSON.stringify({
      schema_version: 1,
      metric_version: METRIC_VERSION,
      event_id: eventId,
      session_id: sessionId,
      landing_id: landingId,
      page_family: family,
      source_group: source,
      event: 'landing_observed',
      observation_state: 'observed'
    }), 0, new Date().getTime());
  }

  var fragment = '#al=' + landingId + '&src=' + source;
  /* For the registration form on SEO pages, which navigates to /app itself. */
  window.autosafeLandingHandoff = fragment;

  function isAppCta(anchor) {
    var href = anchor.getAttribute('href');
    if (!href) return false;
    var url;
    try { url = new URL(href, window.location.href); } catch (e) { return false; }
    if (url.origin !== window.location.origin) return false;
    if (url.hash) return false; /* never alter a link that already has a fragment */
    var path = url.pathname.replace(/\/+$/, '') || '/';
    if (path === '/app') return true;
    return anchor.hasAttribute('data-acq-cta') && path === '/';
  }

  function rewriteLinks() {
    var anchors = document.querySelectorAll('a[href]');
    for (var i = 0; i < anchors.length; i++) {
      if (!isAppCta(anchors[i])) continue;
      var url = new URL(anchors[i].getAttribute('href'), window.location.href);
      anchors[i].setAttribute('href', url.pathname + url.search + fragment);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', rewriteLinks);
  } else {
    rewriteLinks();
  }
})();
