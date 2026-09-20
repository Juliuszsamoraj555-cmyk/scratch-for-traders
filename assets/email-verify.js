/* ============================================================
   EMAIL VERIFICATION AT PURCHASE TIME (2026-09-19).

   Supabase's "Confirm email" setting is off, so signing up and the free
   exports never ask for an email round trip. Before an account's first
   payment, though, it proves it controls its address once: this modal asks
   Supabase to email a one-time code, the trader types it in, and our
   backend (POST /api/account/verify-email) checks it with Supabase and
   records the result. Every checkout endpoint answers 403
   `email_not_verified` until then; callers catch that, run
   apVerifyEmail(), and retry the same checkout.

   One file shared by the builder and the marketplace pages instead of a
   copy in each (see the note in marketplace-auth.js on why the login code
   is duplicated: that was about not touching the big live builder file,
   this is new code with three callers).

   Uses these globals from the host page, at call time: getValidAccessToken,
   getAuthSession, SUPABASE_URL, SUPABASE_ANON_KEY (all defined by
   index_1.html and by marketplace-auth.js).

   Plain CSS with an `ev-` prefix, no Tailwind: the precompiled build
   silently drops classes it has not seen.
   ============================================================ */
(function () {
  'use strict';

  const CSS = `
    .ev-overlay { position: fixed; inset: 0; z-index: 110; display: flex; align-items: center; justify-content: center;
      padding: 16px; background: rgba(0,0,0,0.6); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
    .ev-overlay[hidden] { display: none; }
    .ev-card { width: 100%; max-width: 400px; box-sizing: border-box; background: #111827; border: 1px solid #374151;
      border-radius: 8px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.6); padding: 24px; color: #d1d5db; font-size: 14px; line-height: 1.5; }
    .ev-title { margin: 0 0 4px 0; font-size: 18px; font-weight: 600; color: #f3f4f6; }
    .ev-text { margin: 0 0 16px 0; color: #9ca3af; }
    .ev-text b { color: #f3f4f6; font-weight: 600; overflow-wrap: anywhere; }
    .ev-error { margin: 0 0 12px 0; padding: 10px 12px; border-radius: 6px; background: #450a0a; border: 1px solid #dc2626;
      color: #fecaca; font-weight: 500; font-size: 14px; }
    .ev-error[hidden] { display: none; }
    .ev-label { display: block; margin-bottom: 4px; font-size: 12px; color: #9ca3af; }
    .ev-input { width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 6px; background: #1f2937;
      border: 1px solid #374151; color: #f3f4f6; font-size: 20px; letter-spacing: 0.3em; text-align: center; font-family: inherit; }
    .ev-input:focus { outline: 2px solid #10b981; outline-offset: 1px; }
    .ev-btn { display: block; width: 100%; margin-top: 16px; padding: 10px 16px; border: 0; border-radius: 6px; cursor: pointer;
      background: #10b981; color: #111827; font-weight: 600; font-size: 14px; font-family: inherit; }
    .ev-btn:hover { background: #34d399; }
    .ev-btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .ev-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; font-size: 12px; }
    .ev-link { border: 0; background: transparent; padding: 0; cursor: pointer; color: #9ca3af; font: inherit;
      text-decoration: underline; text-underline-offset: 3px; }
    .ev-link:hover { color: #f3f4f6; }
    .ev-link:disabled { cursor: default; text-decoration: none; color: #6b7280; }
    .ev-cancel { margin-top: 14px; }
  `;

  const RESEND_SECONDS = 60;

  function track(type, meta) {
    try { if (typeof window.apTrack === 'function') window.apTrack(type, meta); } catch (_) {}
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function currentEmail() {
    try {
      const s = getAuthSession();
      return (s && s.user && s.user.email) || '';
    } catch (_) { return ''; }
  }

  /* Asks Supabase to email a code. Same endpoint the sign-in-by-code flow
     uses; create_user:false so it can never create an account. */
  async function requestCode(email) {
    const res = await fetch(`${SUPABASE_URL}/auth/v1/otp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', apikey: SUPABASE_ANON_KEY },
      body: JSON.stringify({ email, create_user: false }),
    });
    if (res.ok) return;
    const json = await res.json().catch(() => ({}));
    if (res.status === 429) {
      throw new Error('A code was just sent. Wait a minute before asking for another one.');
    }
    throw new Error(json.error_description || json.msg || 'Could not send the code. Try again in a moment.');
  }

  async function submitCode(apiBase, code) {
    const token = await getValidAccessToken();
    if (!token) throw new Error('Your session expired. Log in again and retry.');
    const res = await fetch(`${apiBase}/api/account/verify-email`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ code }),
    });
    if (res.ok) return;
    const json = await res.json().catch(() => ({}));
    const d = json.detail;
    throw new Error((d && d.message) || (typeof d === 'string' ? d : '') || 'Could not check the code. Try again.');
  }

  /* Resolves true once the address is verified, false if the trader backs out. */
  window.apVerifyEmail = function (apiBase) {
    return new Promise((resolve) => {
      if (!document.getElementById('ev-style')) {
        const s = document.createElement('style');
        s.id = 'ev-style';
        s.textContent = CSS;
        document.head.appendChild(s);
      }
      const email = currentEmail();

      const overlay = el('div', 'ev-overlay');
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.setAttribute('aria-label', 'Confirm your email');
      const card = el('div', 'ev-card');
      overlay.appendChild(card);
      document.body.appendChild(overlay);
      track('verify_email_shown', {});

      let resendTimer = null;
      let finished = false;

      function finish(ok) {
        if (finished) return;
        finished = true;
        clearInterval(resendTimer);
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        if (ok) track('verify_email_completed', {});
        resolve(ok);
      }
      function onKey(e) { if (e.key === 'Escape') finish(false); }
      document.addEventListener('keydown', onKey);
      overlay.addEventListener('click', (e) => { if (e.target === overlay) finish(false); });

      function errorBox() {
        const box = el('p', 'ev-error');
        box.hidden = true;
        box.setAttribute('role', 'alert');
        return box;
      }
      function showError(box, msg) { box.textContent = msg; box.hidden = false; }

      function cancelButton() {
        const b = el('button', 'ev-link ev-cancel', 'Cancel');
        b.type = 'button';
        b.addEventListener('click', () => finish(false));
        return b;
      }

      /* ---- step 1: explain and send ---- */
      function renderIntro() {
        clearInterval(resendTimer);
        card.replaceChildren();
        card.appendChild(el('h2', 'ev-title', 'Confirm your email'));
        const p = el('p', 'ev-text');
        p.append('Before your first purchase we check that ');
        p.appendChild(el('b', '', email || 'your address'));
        p.append(' is yours, so receipts and password resets reach you. We will email you a short code.');
        card.appendChild(p);
        const err = errorBox();
        card.appendChild(err);
        const send = el('button', 'ev-btn', 'Send code');
        send.type = 'button';
        send.addEventListener('click', async () => {
          send.disabled = true;
          err.hidden = true;
          try {
            await requestCode(email);
            renderCode(true);
          } catch (e) {
            showError(err, e.message);
            send.disabled = false;
          }
        });
        card.appendChild(send);
        card.appendChild(cancelButton());
        send.focus();
      }

      /* ---- step 2: enter the code ---- */
      function renderCode(justSent) {
        card.replaceChildren();
        card.appendChild(el('h2', 'ev-title', 'Enter your code'));
        const p = el('p', 'ev-text');
        p.append(justSent ? 'We sent a code to ' : 'Code sent to ');
        p.appendChild(el('b', '', email));
        p.append('. It can take a minute to arrive, and it is worth checking spam.');
        card.appendChild(p);
        const err = errorBox();
        card.appendChild(err);

        const label = el('label', 'ev-label', 'Code from the email');
        label.setAttribute('for', 'ev-code');
        const input = el('input', 'ev-input');
        input.id = 'ev-code';
        input.type = 'text';
        input.inputMode = 'numeric';
        input.autocomplete = 'one-time-code';
        input.maxLength = 10;
        input.setAttribute('aria-label', 'Code from the email');
        card.appendChild(label);
        card.appendChild(input);

        const confirm = el('button', 'ev-btn', 'Confirm and continue');
        confirm.type = 'button';
        async function submit() {
          const code = input.value.replace(/\s+/g, '');
          if (!/^\d{4,10}$/.test(code)) { showError(err, 'Enter the digits from the email.'); return; }
          confirm.disabled = true;
          err.hidden = true;
          const original = confirm.textContent;
          confirm.textContent = 'Checking...';
          try {
            await submitCode(apiBase, code);
            finish(true);
          } catch (e) {
            showError(err, e.message);
            confirm.disabled = false;
            confirm.textContent = original;
            input.select();
          }
        }
        confirm.addEventListener('click', submit);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
        card.appendChild(confirm);

        const row = el('div', 'ev-row');
        const resend = el('button', 'ev-link', '');
        resend.type = 'button';
        row.appendChild(resend);
        row.appendChild(cancelButton());
        row.lastChild.classList.remove('ev-cancel');
        card.appendChild(row);

        let left = RESEND_SECONDS;
        function tick() {
          if (left > 0) {
            resend.disabled = true;
            resend.textContent = `Send a new code in ${left}s`;
            left -= 1;
          } else {
            clearInterval(resendTimer);
            resend.disabled = false;
            resend.textContent = 'Send a new code';
          }
        }
        clearInterval(resendTimer);
        tick();
        resendTimer = setInterval(tick, 1000);
        resend.addEventListener('click', async () => {
          resend.disabled = true;
          err.hidden = true;
          try {
            await requestCode(email);
            left = RESEND_SECONDS;
            tick();
            resendTimer = setInterval(tick, 1000);
            showError(err, 'A new code is on its way.');
            err.style.background = '#052e21'; err.style.borderColor = '#059669'; err.style.color = '#a7f3d0';
          } catch (e) {
            err.style.background = ''; err.style.borderColor = ''; err.style.color = '';
            showError(err, e.message);
            resend.disabled = false;
            resend.textContent = 'Send a new code';
          }
        });
        input.focus();
      }

      renderIntro();
    });
  };
})();
