/* AutoSafe optional analytics consent gate for standalone static pages. */
(function () {
  'use strict';

  var params = new URLSearchParams(window.location.search);
  var changed = false;
  ['reg', 'registration', 'vrm', 'postcode'].forEach(function (key) {
    if (params.has(key)) {
      params.delete(key);
      changed = true;
    }
  });
  if (changed) {
    var cleanQuery = params.toString();
    history.replaceState(
      null,
      '',
      window.location.pathname + (cleanQuery ? '?' + cleanQuery : '') + window.location.hash
    );
  }

  var analyticsAllowed = !/^\/app\/report\//.test(window.location.pathname);
  window.autosafeAnalyticsAllowed = analyticsAllowed;
  window.dataLayer = window.dataLayer || [];
  window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
  window.gtag('js', new Date());
  window.gtag('consent', 'default', {
    ad_storage: 'denied',
    ad_user_data: 'denied',
    ad_personalization: 'denied',
    analytics_storage: 'denied'
  });

  var loaded = false;
  function loadGtag() {
    if (!analyticsAllowed || loaded) return;
    loaded = true;
    window.gtag('consent', 'update', {
      ad_storage: 'granted',
      ad_user_data: 'granted',
      ad_personalization: 'denied',
      analytics_storage: 'granted'
    });
    var script = document.createElement('script');
    script.async = true;
    script.src = 'https://www.googletagmanager.com/gtag/js?id=AW-17896487388';
    document.head.appendChild(script);
    window.gtag('config', 'AW-17896487388', { send_page_view: false });
  }

  var choice = null;
  try { choice = localStorage.getItem('autosafe_consent'); } catch (_) {}
  if (choice === 'accepted') {
    loadGtag();
    return;
  }
  if (choice === 'declined') return;

  function showConsent() {
    if (document.getElementById('consent-banner')) return;
    var banner = document.createElement('div');
    banner.id = 'consent-banner';
    banner.setAttribute('role', 'dialog');
    banner.setAttribute('aria-label', 'Cookie consent');
    banner.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#f1f5f9;padding:14px 16px;z-index:9999;display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:center;font:14px/1.4 system-ui,sans-serif';
    banner.innerHTML = '<span>We use optional cookies to measure advertising conversions. None are set unless you accept. <a href="/privacy" style="color:#93c5fd">Privacy notice</a></span>'
      + '<button id="consent-accept" style="background:#2563eb;color:#fff;border:0;border-radius:6px;padding:8px 14px;cursor:pointer">Accept</button>'
      + '<button id="consent-decline" style="background:transparent;color:#cbd5e1;border:1px solid #475569;border-radius:6px;padding:8px 14px;cursor:pointer">Decline</button>';
    document.body.appendChild(banner);
    document.getElementById('consent-accept').onclick = function () {
      try { localStorage.setItem('autosafe_consent', 'accepted'); } catch (_) {}
      loadGtag();
      banner.remove();
    };
    document.getElementById('consent-decline').onclick = function () {
      try { localStorage.setItem('autosafe_consent', 'declined'); } catch (_) {}
      banner.remove();
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', showConsent);
  } else {
    showConsent();
  }
})();
