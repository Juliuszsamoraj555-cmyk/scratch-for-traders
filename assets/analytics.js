/* ============================================================
   ANALYTICS + GOOGLE ADS CONVERSION TRACKING (2026-09-03)
   ============================================================
   Loaded by every page on the site. Three jobs:

     1. EU consent (Consent Mode v2) before anything is measured.
     2. GA4 + Google Ads gtag, once configured below.
     3. One helper, apTrack(), that records an event BOTH to this app's
        own analytics_events table (see /api/analytics/event in main.py)
        and to GA4 - so product analysis and ad measurement never drift
        apart by being instrumented in two different places.

   ------------------------------------------------------------
   >>> PASTE THE IDs HERE. Nothing else in the codebase needs editing. <<<
   ------------------------------------------------------------
   Where each one comes from is in docs/ANALYTICS.md. Until they're
   filled in, this file is INERT: no external script is loaded, no
   consent banner is shown, and apTrack() still records to our own
   backend exactly as before. That's deliberate - the site must not
   start showing a cookie banner for tags that don't exist yet.

   Deliberate exception to this project's "nothing loads from a CDN at
   runtime" rule (see assets/vendor/ for Blockly and Tailwind): Google's
   tag has to come from Google's own domain to work at all. It's the one
   third-party script on the site, it's consent-gated, and it's the price
   of measuring paid traffic.
   ============================================================ */
(function () {
  'use strict';

  /* ---------- CONFIG - the only lines that need editing ---------- */

  // Google Analytics 4 measurement ID (algopuzzle.com web stream).
  const GA4_ID = 'G-8W3F8H6PLY';

  // Google Ads conversion ID, e.g. 'AW-123456789'. Shared by every
  // conversion action on the account.
  const ADS_ID = 'AW-18392802331';

  // The half AFTER the slash in each conversion snippet Google shows,
  // e.g. 'AbC-D_efG-h12_34-567'. One per conversion action.
  const ADS_LABELS = {
    signup: 'mjD7CMOK8u4cEJvIr8JE',
    export: 'AOdFCLus8u4cEJvIr8JE',
    purchase: 'k1ApCI6o_u4cEJvIr8JE',
  };

  /* ---------- end of config ---------- */

  const CONSENT_KEY = 'algoPuzzle.consent.v1';
  const hasGoogleTags = Boolean(GA4_ID || ADS_ID);

  /* ------------------------------------------------------------
     Backend event logging - unchanged behaviour, just centralised.
     Mirrors index_1.html's own hostname-based API_BASE detection (kept
     as a small local copy rather than imported, matching how
     marketplace-data.js already handles the same problem). Everything
     lives inside this IIFE on purpose: index_1.html and
     strategy-detail.html already declare a global `const API_BASE`, and
     a second top-level declaration of that name would be a SyntaxError
     that takes the whole page down.
     ------------------------------------------------------------ */
  const IS_LOCAL_DEV = ['localhost', '127.0.0.1', ''].includes(window.location.hostname)
    || window.location.protocol === 'file:';
  const IS_STAGING = window.location.hostname === 'algopuzzle-frontend-staging.onrender.com';
  const BACKEND = IS_LOCAL_DEV
    ? (window.location.origin.includes('null') || window.location.protocol === 'file:'
        ? 'http://127.0.0.1:8000'
        : window.location.origin.replace(/:\d+$/, ':8000'))
    : IS_STAGING
      ? 'https://algopuzzle-api-staging.onrender.com'
      : 'https://algopuzzle-api.onrender.com';

  // The auth session is written by index_1.html / marketplace-auth.js
  // under this key. Read directly rather than calling their helpers,
  // since this file loads on marketing pages where neither exists.
  function accessToken() {
    try {
      const raw = localStorage.getItem('algoPuzzle.auth.v1');
      if (!raw) return null;
      const s = JSON.parse(raw);
      // Not refreshed here on purpose - this is best-effort attribution,
      // and a stale token simply logs the event without a user_id rather
      // than blocking on a token refresh the visitor didn't ask for.
      return s && s.access_token ? s.access_token : null;
    } catch (_) {
      return null;
    }
  }

  function logToBackend(eventType, metadata) {
    try {
      const headers = { 'Content-Type': 'application/json' };
      const token = accessToken();
      if (token) headers.Authorization = 'Bearer ' + token;
      // keepalive so an event fired on a click that navigates away (a CTA
      // into the builder, a checkout redirect) still gets sent instead of
      // being killed mid-flight by the page unload.
      fetch(BACKEND + '/api/analytics/event', {
        method: 'POST',
        headers,
        credentials: 'include',
        keepalive: true,
        body: JSON.stringify({
          event_type: eventType,
          metadata: metadata || null,
          path: window.location.pathname,
        }),
      }).catch(function () {});
    } catch (_) {
      // Analytics is best-effort and must never surface to a trader.
    }
  }

  /* ------------------------------------------------------------
     CONSENT MODE v2. Defaults are DENIED and are set before the tag
     loads, which is what makes this lawful to run on EU traffic before
     the visitor has chosen: Google receives cookieless pings only, and
     upgrades to full measurement the moment consent is granted.
     ad_user_data / ad_personalization are the two v2 signals Google has
     required for EEA traffic since March 2024 - without them, Ads
     conversion tracking and remarketing degrade badly.
     ------------------------------------------------------------ */
  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = window.gtag || gtag;

  function storedConsent() {
    try { return localStorage.getItem(CONSENT_KEY); } catch (_) { return null; }
  }

  function applyConsent(granted) {
    gtag('consent', 'update', {
      ad_storage: granted ? 'granted' : 'denied',
      ad_user_data: granted ? 'granted' : 'denied',
      ad_personalization: granted ? 'granted' : 'denied',
      analytics_storage: granted ? 'granted' : 'denied',
    });
  }

  function loadGoogleTags() {
    gtag('consent', 'default', {
      ad_storage: 'denied',
      ad_user_data: 'denied',
      ad_personalization: 'denied',
      analytics_storage: 'denied',
      // Google's own documented grace period for the banner to resolve,
      // so an immediate bounce isn't recorded as an explicit refusal.
      wait_for_update: 500,
    });

    const prior = storedConsent();
    if (prior === 'granted') applyConsent(true);
    if (prior === 'denied') applyConsent(false);

    const s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(GA4_ID || ADS_ID);
    document.head.appendChild(s);

    gtag('js', new Date());
    if (GA4_ID) gtag('config', GA4_ID);
    // allow_enhanced_conversions is NOT enabled here - it would hash and
    // send the visitor's email to Google, which is a separate consent and
    // privacy-policy question, not something to switch on by default.
    if (ADS_ID) gtag('config', ADS_ID);
  }

  /* ------------------------------------------------------------
     Consent banner. Built in plain DOM with inline styles rather than
     Tailwind classes: this file is shared by every page, and a utility
     that isn't in the precompiled build would silently style nothing
     (see docs/DEPLOYMENT.md step 0 - that exact trap has bitten this
     project repeatedly).
     ------------------------------------------------------------ */
  function showConsentBanner() {
    const bar = document.createElement('div');
    bar.setAttribute('role', 'dialog');
    bar.setAttribute('aria-label', 'Cookie choices');
    bar.style.cssText = [
      'position:fixed', 'left:0', 'right:0', 'bottom:0', 'z-index:2147483000',
      'background:#0b1120', 'border-top:1px solid #1f2937',
      'padding:16px 20px', 'display:flex', 'flex-wrap:wrap',
      'align-items:center', 'justify-content:center', 'gap:14px',
      'font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif',
      'font-size:13px', 'color:#9ca3af', 'line-height:1.5',
    ].join(';');

    const text = document.createElement('span');
    text.style.cssText = 'max-width:56ch';
    text.textContent = 'We use cookies to measure how people find and use AlgoPuzzle. '
      + 'Decline and we only keep what the site needs to work.';

    const btnBase = 'border:0;cursor:pointer;font-weight:600;font-size:13px;'
      + 'padding:9px 18px;border-radius:6px;font-family:inherit';

    const decline = document.createElement('button');
    decline.textContent = 'Decline';
    decline.style.cssText = btnBase + ';background:#374151;color:#e5e7eb';

    const accept = document.createElement('button');
    accept.textContent = 'Accept';
    accept.style.cssText = btnBase + ';background:#10b981;color:#111827';

    function choose(granted) {
      try { localStorage.setItem(CONSENT_KEY, granted ? 'granted' : 'denied'); } catch (_) {}
      applyConsent(granted);
      bar.remove();
    }
    accept.addEventListener('click', function () { choose(true); });
    decline.addEventListener('click', function () { choose(false); });

    const actions = document.createElement('span');
    actions.style.cssText = 'display:flex;gap:10px;flex:none';
    actions.appendChild(decline);
    actions.appendChild(accept);

    bar.appendChild(text);
    bar.appendChild(actions);
    document.body.appendChild(bar);
  }

  /* ------------------------------------------------------------
     PUBLIC API
     ------------------------------------------------------------ */

  // Records an event to our own backend AND to GA4. event_type must be
  // in ALLOWED_ANALYTICS_EVENTS (main.py) or the backend drops it
  // silently by design - add it there rather than inventing names here.
  window.apTrack = function (eventType, metadata) {
    logToBackend(eventType, metadata);
    if (hasGoogleTags && typeof window.gtag === 'function') {
      try { window.gtag('event', eventType, metadata || {}); } catch (_) {}
    }
  };

  // Fires a Google Ads conversion. name is a key of ADS_LABELS.
  // params may carry { value, currency } for the purchase conversion.
  // No-op until ADS_ID and the matching label are filled in, so call
  // sites can be written now and start reporting the moment they are.
  window.apConversion = function (name, params) {
    if (!ADS_ID || !ADS_LABELS[name]) return;
    if (typeof window.gtag !== 'function') return;
    const payload = Object.assign(
      { send_to: ADS_ID + '/' + ADS_LABELS[name] },
      params || {}
    );
    try { window.gtag('event', 'conversion', payload); } catch (_) {}
  };

  /* ------------------------------------------------------------
     BOOT
     ------------------------------------------------------------ */
  if (hasGoogleTags) {
    loadGoogleTags();
    if (!storedConsent()) {
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', showConsentBanner);
      } else {
        showConsentBanner();
      }
    }
  }
})();
