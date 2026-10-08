/* Optional first-party paid measurement v1. Explicit opt-in, 30 minutes.
 * No raw URL, click ID, vehicle input or report token leaves this script. */
(function () {
  'use strict';
  var ENABLED = false; // Activate only after exact-release acceptance.
  var KEY = 'autosafe_paid_measurement_v1';
  var GROUPS = ['discover_owner', 'discover_buyer', 'search_owner', 'search_buyer'];
  var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  var pending = null, timer;
  function clear() {
    if (timer) clearTimeout(timer);
    try { sessionStorage.removeItem(KEY); } catch (_) {}
  }
  function allowed() {
    if (!ENABLED || navigator.globalPrivacyControl === true || !window.autosafeMeasurement || window.autosafeMeasurement.isOff()) {
      clear(); pending = null; return false;
    }
    try { return localStorage.getItem('autosafe_paid_consent_v1') === 'accepted'; }
    catch (_) { clear(); return false; }
  }
  function minute() { return Math.floor(Date.now() / 60000); }
  function family(path) {
    if (/^\/app\/report\//.test(path)) return 'app';
    if (path === '/') return 'home';
    if (path === '/app') return 'app';
    if (/^\/guides\//.test(path)) return 'guide';
    if (/^\/mot-check\/compare\//.test(path)) return 'comparison';
    if (/^\/mot-check\/problems\//.test(path)) return 'problem_hub';
    if (/^\/mot-check\/[^/]+\/[^/]+\/?$/.test(path)) return 'model';
    if (/^\/mot-check\/[^/]+\/?$/.test(path)) return 'make';
    if (/^\/will-my-car-pass-mot\/?$/.test(path)) return 'pillar';
    if (/^\/mot-check\/?$/.test(path)) return 'other_public';
    return null;
  }
  function getContext() {
    if (!allowed()) return null;
    try {
      var c = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      if (!c || !UUID.test(c.landingId) || GROUPS.indexOf(c.pilotGroup) < 0
          || !Number.isInteger(c.windowStartMinute) || c.windowStartMinute > minute()
          || minute() >= c.windowStartMinute + 30) { clear(); return null; }
      if (timer) clearTimeout(timer);
      timer = setTimeout(clear, (c.windowStartMinute + 30) * 60000 - Date.now());
      return c;
    } catch (_) { clear(); return null; }
  }
  function emit(input) {
    try {
      var c = getContext();
      if (!c) return;
      var body = JSON.stringify(Object.assign({}, input, {
        schema_version: 2, metric_version: 'paid-journey-30m-v1', consent_granted: true,
        event_id: crypto.randomUUID(), session_id: c.landingId, landing_id: c.landingId,
        window_start_minute: c.windowStartMinute, pilot_group: c.pilotGroup,
        source_group: 'paid_search', page_family: family(location.pathname) || 'app'
      }));
      if (body.length > 2048) return;
      var started = Date.now();
      function send(attempt) {
        // Withdrawal/expiry/new arrival cancels any queued retry.
        var current = getContext();
        if (!current || current.landingId !== c.landingId) return;
        function retry() { if (!attempt && Date.now() - started < 9000) setTimeout(function () { send(1); }, 1000); }
        try { fetch('/api/acquisition/paid-events', { method:'POST', headers:{'Content-Type':'application/json'},
          body:body, keepalive:true, credentials:'omit', referrerPolicy:'no-referrer', cache:'no-store', mode:'same-origin'
        }).then(function(r) { if (r.status >= 500) retry(); }, retry); } catch (_) { retry(); }
      }
      send(0);
    } catch (_) { /* Measurement never interrupts the product. */ }
  }
  var lastPage = null;
  function pageView() {
    var c = getContext(), path = location.pathname;
    if (!c || !family(path)) return;
    // In-memory path only; token/query/hash never transmitted or persisted.
    var key = c.landingId + ':' + path;
    if (lastPage === key) return;
    lastPage = key;
    emit({event:'page_viewed', observation_state:'observed'});
  }
  function begin() {
    if (!pending || !allowed()) return;
    if (minute() >= pending.windowStartMinute + 30) { pending = null; return; }
    try {
      pending.landingId = crypto.randomUUID();
      sessionStorage.setItem(KEY, JSON.stringify(pending));
      pending = null;
      emit({event:'landing_observed', observation_state:'observed'});
      pageView();
    } catch (_) { clear(); }
  }
  function prepareArrival() {
    var params = new URLSearchParams(location.search);
    var group = params.get('utm_campaign');
    var f = family(location.pathname);
    if (GROUPS.indexOf(group) >= 0 && params.get('utm_source') === 'google'
        && /^(cpc|paid|paid_social)$/.test(params.get('utm_medium') || '')
        && f && !/^\/app\/report\//.test(location.pathname)) {
      var nav;
      try { nav = performance.getEntriesByType('navigation')[0].type; } catch (_) { return; }
      if (nav === 'navigate') {
        clear(); pending = {windowStartMinute:minute(), pilotGroup:group}; begin();
      } else { pageView(); }
    } else {
      // Only same-site navigation may inherit a consented paid journey.
      try { if (document.referrer && new URL(document.referrer).origin === location.origin) pageView(); else clear(); }
      catch (_) { clear(); }
    }
  }
  window.autosafePaidMeasurement = {
    getContext:getContext, emit:emit, pageView:pageView,
    setConsent:function (accept) {
      try { localStorage.setItem('autosafe_paid_consent_v1', accept ? 'accepted' : 'declined'); } catch (_) {}
      if (accept) begin(); else { pending = null; clear(); }
    }
  };
  prepareArrival();
  function control() {
    if (!pending && !getContext()) return;
    if (document.getElementById('paid-measurement-control')) return;
    var bar = document.createElement('aside'); bar.id='paid-measurement-control';
    bar.setAttribute('aria-label','Optional advertising measurement');
    bar.style.cssText='padding:12px 16px;background:#e2e8f0;color:#1e293b;font:14px/1.5 system-ui;text-align:center';
    var label = document.createElement('span');
    label.textContent='Allow optional measurement of this ad visit, page views and check results for 30 minutes? The site works with measurement off. ';
    var link = document.createElement('a'); link.href='/privacy#paid-measurement'; link.textContent='Privacy details';
    var yes = document.createElement('button'), no = document.createElement('button');
    yes.type=no.type='button'; yes.textContent='Allow measurement'; no.textContent='Keep measurement off';
    function refresh() {
      var enabled = allowed(); yes.textContent=enabled ? 'Measurement allowed' : 'Allow measurement';
      yes.disabled=enabled || navigator.globalPrivacyControl === true;
      no.textContent=enabled ? 'Withdraw permission' : 'Keep measurement off';
    }
    yes.onclick=function(){window.autosafePaidMeasurement.setConsent(true);refresh();};
    no.onclick=function(){window.autosafePaidMeasurement.setConsent(false);refresh();};
    yes.style.cssText=no.style.cssText='margin:8px;padding:8px 12px;min-height:44px;cursor:pointer';
    bar.append(label,link,yes,no); document.body.prepend(bar); refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',control); else control();
  window.addEventListener('storage',function(){getContext();});
  document.addEventListener('click',function(){getContext();});
})();
