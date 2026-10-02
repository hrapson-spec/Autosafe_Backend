/* First-party service measurement v2. No identifiers in links. Disabled until
 * migration, privacy controls and synthetic production acceptance pass.
 * A fixed 30-minute sessionStorage context preserves the original arrival.
 * See docs/acquisition/COLLECTOR.md for coverage and purpose boundaries. */
(function () {
  'use strict';
  var ENABLED = false;
  var CONTEXT_KEY = 'autosafe_measurement_v2';
  var PREFERENCE_KEY = 'autosafe_measurement_choice';
  var ENDPOINT = '/api/acquisition/events';
  var WINDOW_MINUTES = 30;
  var volatileOff = false;
  var expiryTimer;
  var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  var SOURCES = ['google_organic','other_search','direct','referral','unknown','internal'];
  var PILOT = {
    '/guides/mot-cost': 'cost', '/guides/mot-checklist': 'checklist',
    '/mot-check/vauxhall/corsa': 'corsa', '/mot-check/citroen/c3': 'c3',
    '/mot-check/compare/renault-clio-vs-peugeot-208': 'clio208',
    '/mot-check/compare/volkswagen-polo-vs-ford-fiesta': 'polofiesta',
    '/mot-check/compare/toyota-yaris-vs-honda-jazz': 'yarisjazz'
  };
  function minute() { return Math.floor(Date.now() / 60000); }
  function gpc() { return navigator.globalPrivacyControl === true; }
  function clear() {
    if (expiryTimer) clearTimeout(expiryTimer);
    try { sessionStorage.removeItem(CONTEXT_KEY); } catch (_) {}
  }
  function objection() {
    if (gpc() || volatileOff) { clear(); return true; }
    try {
      var p = JSON.parse(localStorage.getItem(PREFERENCE_KEY) || 'null');
      if (p && p.off === true && p.until > Date.now()) { clear(); return true; }
      if (p) localStorage.removeItem(PREFERENCE_KEY);
      return false;
    } catch (_) { clear(); return true; } // unavailable storage: unobserved
  }
  function getContext() {
    if (!ENABLED || objection()) return null;
    try {
      var c = JSON.parse(sessionStorage.getItem(CONTEXT_KEY) || 'null');
      if (!c || !UUID.test(c.landingId) || !Number.isInteger(c.windowStartMinute)
          || c.windowStartMinute > minute() || minute() >= c.windowStartMinute + WINDOW_MINUTES
          || SOURCES.indexOf(c.sourceGroup) === -1
          || ['none','cost','checklist','corsa','c3','clio208','polofiesta','yarisjazz'].indexOf(c.pilotGroup) === -1) {
        clear(); return null;
      }
      if (expiryTimer) clearTimeout(expiryTimer);
      expiryTimer = setTimeout(function () { clear(); renderControl(); }, (c.windowStartMinute + WINDOW_MINUTES) * 60000 - Date.now());
      return c;
    } catch (_) { clear(); return null; }
  }
  function source() {
    var paid = false;
    new URLSearchParams(location.search).forEach(function (value, key) {
      key = key.toLowerCase();
      if (['gclid','gbraid','wbraid','msclkid'].indexOf(key) !== -1) paid = true;
      if (key === 'utm_medium' && /^(cpc|ppc|paid|paid_search|paidsearch|paid_social|display|cpm)$/i.test(value.trim())) paid = true;
    });
    if (paid) return 'paid_search';
    if (!document.referrer) return 'direct';
    try {
      var url = new URL(document.referrer);
      if (url.origin === location.origin) return 'internal';
      var h = url.hostname.toLowerCase().replace(/^www\./, '');
      if (/^google\.(com|co\.[a-z]{2}|[a-z]{2,3})(\.[a-z]{2})?$/.test(h)) return 'google_organic';
      if (/^(bing\.com|duckduckgo\.com|(?:[a-z]{2}\.)?search\.yahoo\.com|yahoo\.com|ecosia\.org|search\.brave\.com|startpage\.com|qwant\.com|yandex\.[a-z.]+|baidu\.com)$/.test(h)) return 'other_search';
      return 'referral';
    } catch (_) { return 'unknown'; }
  }
  function family(path) {
    if (path === '/') return 'home';
    if (path === '/app') return 'app';
    if (/^\/guides\/[^/]+$/.test(path)) return 'guide';
    if (path === '/will-my-car-pass-mot') return 'pillar';
    if (/^\/mot-check\/compare\//.test(path)) return 'comparison';
    if (/^\/mot-check\/problems\//.test(path)) return 'problem_hub';
    if (/^\/mot-check\/[^/]+\/[^/]+$/.test(path)) return 'model';
    if (/^\/mot-check\/[^/]+$/.test(path)) return 'make';
    if (path === '/mot-check') return 'other_public';
    return null; // reports, legal pages and unknown routes are not arrivals
  }
  function post(body, attempt, started) {
    if (!getContext()) return;
    function retry() {
      if (!attempt && Date.now() - started + 1000 <= 10000) {
        setTimeout(function () { post(body, 1, started); }, 1000);
      }
    }
    try {
      fetch(ENDPOINT, { method: 'POST', headers: {'Content-Type':'application/json'}, body: body,
        keepalive: true, credentials: 'omit', referrerPolicy: 'no-referrer', cache: 'no-store', mode: 'same-origin'
      }).then(function (r) { if (r.status >= 500) retry(); }, retry);
    } catch (_) { retry(); }
  }
  function begin() {
    if (!ENABLED || objection()) { clear(); return; }
    var nav;
    try { nav = performance.getEntriesByType('navigation')[0].type; } catch (_) { clear(); return; }
    if (nav !== 'navigate') { if (nav !== 'reload' && nav !== 'back_forward') clear(); return; }
    var path = location.pathname.replace(/\/+$/, '') || '/';
    var f = family(path), src = source(), old = getContext();
    if (src === 'paid_search') { clear(); return; } // no advertising-performance collection
    if (src === 'internal') { if (!old) clear(); return; }
    clear();
    if (!f || !crypto || typeof crypto.randomUUID !== 'function') return;
    var c = {landingId:crypto.randomUUID(), windowStartMinute:minute(), sourceGroup:src, pilotGroup:PILOT[path] || 'none'};
    try {
      sessionStorage.setItem(CONTEXT_KEY, JSON.stringify(c));
      if (!getContext()) return;
    } catch (_) { clear(); return; }
    post(JSON.stringify({schema_version:2, metric_version:'oa-journey-30m-v2', event_id:crypto.randomUUID(),
      session_id:c.landingId, landing_id:c.landingId, window_start_minute:c.windowStartMinute,
      pilot_group:c.pilotGroup, page_family:f, source_group:src, event:'landing_observed', observation_state:'observed'
    }), 0, Date.now());
  }
  function setOff(off) {
    volatileOff = off;
    clear();
    try {
      if (off) localStorage.setItem(PREFERENCE_KEY, JSON.stringify({off:true,until:Date.now()+90*86400000}));
      else localStorage.removeItem(PREFERENCE_KEY);
    } catch (_) {}
    // Switching on never retroactively creates an arrival. Next eligible
    // external/direct navigation can start a new observation.
    renderControl();
  }
  function renderControl() {
    if (!ENABLED) return;
    var bar = document.getElementById('autosafe-measurement-control');
    if (!bar) {
      bar = document.createElement('aside'); bar.id = 'autosafe-measurement-control';
      bar.setAttribute('aria-label','Website measurement');
      bar.style.cssText = 'padding:12px 16px;background:#f1f5f9;color:#334155;font:14px/1.5 system-ui;text-align:center';
      document.body.prepend(bar);
    }
    var off = objection();
    bar.replaceChildren();
    var text = document.createElement('span');
    text.textContent = off ? 'Website measurement is off. We remember this choice for 90 days in this browser. ' : 'We measure how these pages and the check tool work for up to 30 minutes. ';
    var link = document.createElement('a'); link.href='/privacy#website-measurement'; link.textContent='How it works';
    var button = document.createElement('button'); button.type='button';
    button.textContent = gpc() ? 'Measurement off (GPC)' : off ? 'Measurement off — turn on' : 'Turn measurement off';
    button.disabled=gpc(); button.style.cssText='margin:8px;padding:8px 12px;border:1px solid #64748b;border-radius:6px;cursor:pointer';
    button.onclick=function(){setOff(!off);};
    bar.append(text,link,button);
  }
  window.autosafeMeasurement = {getContext:getContext, isOff:objection, setOff:setOff};
  try { begin(); } catch (_) { clear(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',renderControl);
  else renderControl();
  window.addEventListener('pageshow',function(){getContext(); renderControl();});
  window.addEventListener('storage',function(){if (objection()) clear(); renderControl();});
})();
