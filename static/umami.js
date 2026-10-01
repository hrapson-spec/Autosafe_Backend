/* AutoSafe cookieless page-view analytics for standalone and SEO pages.
 *
 * Mirrors the SPA boundary in index.html: never loaded on bearer report
 * routes, automatic tracking off, one explicit page view per page load, and
 * every payload reduced to the URL path and the referrer origin.
 */
(function () {
  'use strict';

  if (/^\/app\/report\//.test(window.location.pathname)) return;

  window.autosafeUmamiBeforeSend = function (type, payload) {
    if (/^\/app\/report\//.test(window.location.pathname)) return false;
    payload.url = window.location.pathname;
    var referrer = '';
    try {
      var ref = payload.referrer ? new URL(payload.referrer) : null;
      if (ref && ref.origin !== window.location.origin) referrer = ref.origin + '/';
    } catch (_) {}
    payload.referrer = referrer;
    return payload;
  };

  var script = document.createElement('script');
  script.defer = true;
  script.src = 'https://umami-production-cb51.up.railway.app/script.js';
  script.setAttribute('data-website-id', '0dead4c2-42a2-456c-abc1-42f93e1a991b');
  script.setAttribute('data-domains', 'autosafe.one,www.autosafe.one');
  script.setAttribute('data-auto-track', 'false');
  script.setAttribute('data-before-send', 'autosafeUmamiBeforeSend');
  script.addEventListener('load', function () {
    try {
      if (window.umami && window.umami.track) window.umami.track();
    } catch (_) {}
  });
  document.head.appendChild(script);
})();
