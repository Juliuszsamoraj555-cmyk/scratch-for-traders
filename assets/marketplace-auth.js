/* ============================================================
   MARKETPLACE AUTH (2026-08-31) - Supabase Auth email+password login,
   now required before buying a paid marketplace strategy. Previously
   anonymous/device-based (see assets/marketplace-data.js's git history) -
   changed per explicit product decision: device-only ownership doesn't
   survive clearing cookies or switching browsers/devices, which is a real
   problem for something a trader actually paid for. Same account model
   the 30-day pass already uses (see index_1.html), for the same reason.

   Talks directly to Supabase Auth's REST API - this app's own backend
   (supabase_auth.py) only ever verifies the resulting access token, never
   sees an email or password. Session (access + refresh token) lives in
   localStorage under the SAME key index_1.html's own copy of this logic
   uses (AUTH_SESSION_KEY below) - localStorage is shared per-origin, so
   logging in from the builder also logs you in here, and vice versa, with
   no extra work.

   This is a second, deliberately separate copy of index_1.html's AUTH
   section rather than a shared module - matches this project's existing
   tolerance for duplicating small, self-contained blocks across a
   handful of consumers (see marketplace-data.js's own MARKETPLACE_API_BASE
   comment for the same reasoning) rather than risking a refactor of
   index_1.html, a large, already-live, revenue-critical file, for this.
   If a third page ever needs it beyond strategy-of-the-week.html /
   strategy-detail.html / my-purchases.html, that's the point to actually
   factor it into one shared file.
   ============================================================ */

const SUPABASE_URL = 'https://qlwaysrcmxoqesplcrfc.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFsd2F5c3JjbXhvcWVzcGxjcmZjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODY3MDU1NzYsImV4cCI6MjEwMjI4MTU3Nn0.bvn1AJXF6Xn9AMa4G4Wg2eDyJol5oeDpF-bbBha0jv0';
const AUTH_SESSION_KEY = 'algoPuzzle.auth.v1';

function getAuthSession() {
  try {
    const raw = localStorage.getItem(AUTH_SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}
function saveAuthSession(session) {
  const withExpiry = { ...session, expires_at_ms: Date.now() + (session.expires_in || 3600) * 1000 };
  localStorage.setItem(AUTH_SESSION_KEY, JSON.stringify(withExpiry));
  return withExpiry;
}
function clearAuthSession() {
  localStorage.removeItem(AUTH_SESSION_KEY);
}

async function supabaseAuthFetch(path, body) {
  const res = await fetch(`${SUPABASE_URL}/auth/v1${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', apikey: SUPABASE_ANON_KEY },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(json.error_description || json.msg || 'Authentication request failed.');
  }
  return json;
}

async function authSignUp(email, password) {
  const redirectTo = window.location.origin + window.location.pathname;
  const json = await supabaseAuthFetch(`/signup?redirect_to=${encodeURIComponent(redirectTo)}`, { email, password });
  if (!json.access_token) {
    return { needsConfirmation: true };
  }
  return saveAuthSession(json);
}

async function authSignIn(email, password) {
  const json = await supabaseAuthFetch('/token?grant_type=password', { email, password });
  return saveAuthSession(json);
}

// Deliberately points at index_1.html, NOT window.location.pathname (this
// page) - index_1.html is the one page that actually handles a Supabase
// recovery-link landing (#access_token=...&type=recovery, see that
// file's handlePasswordRecoveryLanding()); duplicating that whole
// overlay+handler here just for "forgot password" would be a lot of code
// for a rarely-used path. A trader who requests a reset from here still
// gets a working link, it just resolves on the builder instead of
// wherever they started.
async function authRequestPasswordReset(email) {
  const redirectTo = window.location.origin + '/index_1.html';
  await supabaseAuthFetch(`/recover?redirect_to=${encodeURIComponent(redirectTo)}`, { email });
}

async function authSignOut() {
  const session = getAuthSession();
  clearAuthSession();
  if (session && session.access_token) {
    fetch(`${SUPABASE_URL}/auth/v1/logout`, {
      method: 'POST',
      headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${session.access_token}` },
    }).catch(() => {});
  }
}

// Returns a valid access token, refreshing first if expired/about to be
// (60s buffer), or null if never logged in / refresh itself fails (stale
// session also cleared then, so the UI doesn't keep claiming logged-in).
async function getValidAccessToken() {
  const session = getAuthSession();
  if (!session) return null;
  if (session.expires_at_ms - Date.now() > 60_000) return session.access_token;

  try {
    const refreshed = await supabaseAuthFetch('/token?grant_type=refresh_token', {
      refresh_token: session.refresh_token,
    });
    return saveAuthSession(refreshed).access_token;
  } catch (_) {
    clearAuthSession();
    return null;
  }
}

function getAuthEmail() {
  const session = getAuthSession();
  return (session && session.user && session.user.email) || null;
}

/* ------------------------------------------------------------
   AUTH MODAL - login / signup / forgot-password, three modes of one
   form. Requires the page to already contain the markup with these
   exact ids (copy it from strategy-of-the-week.html or
   strategy-detail.html rather than reinventing it - kept as plain HTML
   per-page, same reasoning as the purchase modal's own duplication,
   only the behavior is shared here). Call initMarketplaceAuthModal()
   once after that markup exists in the DOM.
   ------------------------------------------------------------ */
let _authMode = 'login';
let _authOnSuccess = null;
let _authEls = null;

function _setAuthMode(mode) {
  _authMode = mode;
  const e = _authEls;
  e.error.classList.add('hidden');
  if (mode === 'login') {
    e.title.textContent = 'Log in';
    // Downloading, not just buying: every generated file needs an account
    // as of 2026-09-03, the free strategy of the week included.
    e.subtitle.textContent = "Downloading or buying a strategy needs a free account - it's what lets it follow you to any device.";
    e.passwordRow.classList.remove('hidden');
    e.submitBtn.textContent = 'Log in';
    e.toggleModeBtn.textContent = 'Need an account? Sign up';
    e.forgotBtn.classList.remove('hidden');
  } else if (mode === 'signup') {
    e.title.textContent = 'Sign up';
    e.subtitle.textContent = 'Free to create. Everything you download or buy stays on your account.';
    e.passwordRow.classList.remove('hidden');
    e.submitBtn.textContent = 'Sign up';
    e.toggleModeBtn.textContent = 'Already have an account? Log in';
    e.forgotBtn.classList.remove('hidden');
  } else {
    e.title.textContent = 'Reset password';
    e.subtitle.textContent = "We'll email you a link to set a new password.";
    e.passwordRow.classList.add('hidden');
    e.submitBtn.textContent = 'Send reset link';
    e.toggleModeBtn.textContent = 'Back to log in';
    e.forgotBtn.classList.add('hidden');
  }
}

function showMarketplaceAuthOverlay(onSuccess) {
  _authOnSuccess = onSuccess || null;
  _setAuthMode('login');
  _authEls.email.value = '';
  _authEls.password.value = '';
  _authEls.overlay.classList.remove('hidden');
}
function hideMarketplaceAuthOverlay() {
  _authEls.overlay.classList.add('hidden');
}

function _showAuthError(message) {
  _authEls.errorText.textContent = message;
  _authEls.error.classList.remove('hidden');
}

function initMarketplaceAuthModal() {
  _authEls = {
    overlay: document.getElementById('authOverlay'),
    title: document.getElementById('authTitle'),
    subtitle: document.getElementById('authSubtitle'),
    error: document.getElementById('authError'),
    errorText: document.getElementById('authErrorText'),
    email: document.getElementById('authEmail'),
    password: document.getElementById('authPassword'),
    passwordRow: document.getElementById('authPasswordRow'),
    submitBtn: document.getElementById('authSubmitBtn'),
    toggleModeBtn: document.getElementById('authToggleModeBtn'),
    forgotBtn: document.getElementById('authForgotBtn'),
    cancelBtn: document.getElementById('authCancelBtn'),
  };
  const e = _authEls;

  e.cancelBtn.addEventListener('click', hideMarketplaceAuthOverlay);
  e.overlay.addEventListener('click', (ev) => { if (ev.target === e.overlay) hideMarketplaceAuthOverlay(); });
  e.toggleModeBtn.addEventListener('click', () => _setAuthMode(_authMode === 'login' ? 'signup' : 'login'));
  e.forgotBtn.addEventListener('click', () => _setAuthMode('reset'));

  e.submitBtn.addEventListener('click', async () => {
    const email = e.email.value.trim();
    const password = e.password.value;
    if (!email) { _showAuthError('Enter your email first.'); return; }
    if (_authMode !== 'reset' && !password) { _showAuthError('Enter your password first.'); return; }

    const originalLabel = e.submitBtn.textContent;
    e.submitBtn.disabled = true;
    e.submitBtn.textContent = 'Working…';
    e.error.classList.add('hidden');
    try {
      if (_authMode === 'login') {
        await authSignIn(email, password);
        hideMarketplaceAuthOverlay();
        if (_authOnSuccess) _authOnSuccess();
      } else if (_authMode === 'signup') {
        const result = await authSignUp(email, password);
        if (result.needsConfirmation) {
          hideMarketplaceAuthOverlay();
          alert('Account created - check your inbox for a confirmation link, then log in.');
        } else {
          hideMarketplaceAuthOverlay();
          if (_authOnSuccess) _authOnSuccess();
        }
      } else {
        await authRequestPasswordReset(email);
        _showAuthError(''); // clear, then show as a non-error confirmation below
        e.error.classList.add('hidden');
        alert('Check your email for a reset link.');
        _setAuthMode('login');
      }
    } catch (err) {
      _showAuthError(err.message);
    } finally {
      e.submitBtn.textContent = originalLabel;
      e.submitBtn.disabled = false;
    }
  });
}
