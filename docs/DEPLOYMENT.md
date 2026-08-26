# Deployment guide — Render + Supabase + Stripe

This walks through taking the app from "runs on my machine" to live and
billing real money. Everything code-related is already built (see
`billing.py`, `db.py`, `device_identity.py`, `settings.py`,
`supabase/schema.sql`, `render.yaml`) — this is the checklist of accounts
and dashboard clicks that only you can do (account creation, payment
details, domain purchase are outside what Claude Code can act on).

Do these roughly in order — each step unblocks the next.

## 0. Rebuild the CSS after touching any Tailwind class

`index.html`, `index_1.html`, and `blog/*.html` link a **precompiled**
stylesheet (`assets/vendor/tailwind/tailwind-compiled.css`) instead of
loading Tailwind's Play CDN JIT script at runtime - the JIT script works
but is explicitly flagged by Tailwind's own docs as unsuitable for
production (it recompiles every class in-browser on every page load,
which is a real, if modest, Core Web Vitals cost - and Core Web Vitals
are a real Google ranking factor).

The tradeoff: the compiled file only contains whatever classes existed
in the HTML **at the moment it was built**. If you (or a future Claude
Code session) add a new Tailwind utility class anywhere and forget this
step, the class will do nothing - no error, it just silently won't be
styled. Whenever you add/change Tailwind classes:

```bash
npm install        # first time only
npm run build:css
```

(Needs Node.js - if it's not installed, grab it from
[nodejs.org](https://nodejs.org).) Commit the updated
`tailwind-compiled.css` alongside your HTML changes.

## 1. Push the repo to GitHub

Render deploys from a git remote, not a local folder.

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

(Create the empty repo on GitHub first if you haven't - github.com/new.)

## 2. Supabase — database

1. [supabase.com](https://supabase.com) → New project. Pick a region close to
   your users (e.g. `eu-central-1` for Poland).
2. Once it's provisioned: **SQL Editor** → paste the contents of
   [`supabase/schema.sql`](supabase/schema.sql) → Run. This creates the
   `devices`, `billing_events`, `export_log` tables and the
   `consume_export_entitlement()` function - everything the billing
   layer needs, no Supabase Auth involved.
3. **Project Settings → Database → Connection string** → copy the
   "Transaction" pooler URI (port 6543). This is your `DATABASE_URL`.

## 3. Stripe — products & keys

1. [dashboard.stripe.com](https://dashboard.stripe.com) → make sure
   you're in **Test mode** first (toggle top-right) - do the whole setup
   in test mode before ever touching Live mode.
2. **Product catalog → Add product**, twice:
   - "Single export" — one-time price, 5.00 PLN
   - "30-day unlimited pass" — one-time price, 30.00 PLN

   For both: make sure the price is **one time**, not recurring - these
   are one-off purchases per the pricing model, not subscriptions.
3. Copy each Price's ID (starts `price_...`, on the product page) →
   these are `STRIPE_PRICE_EXPORT` / `STRIPE_PRICE_PASS`.
4. **Developers → API keys** → copy the **Secret key** (`sk_test_...`
   for now) → this is `STRIPE_SECRET_KEY`.
5. Webhook signing secret comes in step 5 below (needs the backend's
   live URL first, which doesn't exist until step 4).

## 4. Render — deploy both services

**Option A (recommended): Blueprint.** Render dashboard → **New +** →
**Blueprint** → point it at your GitHub repo → it reads
[`render.yaml`](render.yaml) and creates both services (`algopuzzle-api`
web service + `algopuzzle-frontend` static site) in one go. It
will prompt you for the `sync: false` env vars listed there
(`DATABASE_URL`, `STRIPE_SECRET_KEY`, etc.) during setup.

**Option B: manual**, if you'd rather click through it:
- **New + → Web Service** → your repo → Runtime: Python → Build command
  `pip install -r requirements.txt` → Start command
  `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **New + → Static Site** → your repo → Build command: (leave empty) →
  Publish directory: `.`
- In the Web Service's **Environment** tab, add every variable from
  [`.env.example`](.env.example) (real values, not the placeholders).

Either way, once deployed you'll have two URLs, e.g.:
- API: `https://algopuzzle-api.onrender.com`
- Frontend: `https://algopuzzle-frontend.onrender.com`

**Two follow-up edits now that these URLs exist:**
1. In `index_1.html`, set `PRODUCTION_API_BASE` (search for it) to the
   API URL, commit, push - Render redeploys the frontend automatically.
2. In the API service's env vars on Render, set `ALLOWED_ORIGINS` and
   `SITE_URL` to the frontend URL.

## 5. Stripe webhook (needs the API's real URL from step 4)

1. Stripe dashboard (still Test mode) → **Developers → Webhooks → Add
   endpoint**.
2. Endpoint URL: `https://algopuzzle-api.onrender.com/api/billing/webhook`
3. Events to send: `checkout.session.completed` (that's the only one
   `billing.py` currently acts on).
4. Copy the **Signing secret** (`whsec_...`) shown after creating it →
   set as `STRIPE_WEBHOOK_SECRET` in Render's env vars.

## 6. Test it for real (still Stripe Test mode)

1. Open the deployed frontend URL, build a strategy, export twice (uses
   up the 2 free exports), export a third time → paywall should appear.
2. Click "Single export" → should redirect to a real Stripe Checkout
   page. Pay with a [Stripe test card](https://docs.stripe.com/testing)
   (`4242 4242 4242 4242`, any future date, any CVC).
3. Should redirect back with a "Payment received" toast, and the next
   export should go through without hitting the paywall again.
4. Check **Stripe dashboard → Developers → Webhooks → (your endpoint)**
   — you should see a `checkout.session.completed` event with a 200
   response logged.
5. Check Supabase → **Table Editor → devices** — the row for your
   browser should show `paid_export_credits` incremented (or
   `pass_expires_at` set, if you tested the pass instead).

## 7. Domain (whenever you've picked one)

Once you own a domain:
1. Point it at Render (Render's docs: **Settings → Custom Domains** on
   each service) - typically `app.yourdomain.pl` → frontend,
   `api.yourdomain.pl` → backend.
2. Update `SITE_URL` and `ALLOWED_ORIGINS` (API service env vars) to the
   new `app.yourdomain.pl` URL.
3. Update `PRODUCTION_API_BASE` in `index_1.html` to the new
   `api.yourdomain.pl` URL.
4. Set `COOKIE_DOMAIN` (API service env var) to `.yourdomain.pl`
   (leading dot) - this is what lets the device cookie work across the
   `app.` / `api.` subdomain split.
5. Update the Stripe webhook endpoint URL (step 5) to the new domain.

## 8. Go live (real money)

Only after step 6 passes cleanly in Test mode:
1. Stripe dashboard → flip to **Live mode** → repeat step 3 (create the
   same 2 products/prices in Live mode - test and live are entirely
   separate catalogs) and step 5 (a separate Live-mode webhook endpoint
   + its own signing secret).
2. Swap `STRIPE_SECRET_KEY`, `STRIPE_PRICE_EXPORT`, `STRIPE_PRICE_PASS`,
   `STRIPE_WEBHOOK_SECRET` in Render to the Live-mode values.
3. Do one real, small, real-money test purchase yourself before telling
   anyone else the shop is open.

## 9. Staging environment (2026-08-26 addition)

Set up once, ahead of building the strategy-marketplace feature (see
`mockups/strategy-of-the-week.html`) — a second, disposable pair of
Render services that deploy from the `staging` git branch instead of
`main`, so new features get a real end-to-end test against a real
deployed URL before ever touching `main`/production.

**Deliberate scope decision (2026-08-26): Render only, nothing else,
for now.** No Supabase, no Stripe — not even Test mode — get configured
on staging at this point. There's no marketplace code yet to test that
would need either of them, so wiring them up now would just be
unused config to maintain. This isn't a workaround: `billing.py`/`db.py`
are already built to run with neither configured — see
`BillingNotConfigured`/`_require_configured()` in `billing.py` and the
`if settings.DATABASE_URL:` branch in `device_identity.py` — missing
`DATABASE_URL`/`STRIPE_SECRET_KEY` just logs a warning and exports stay
unlimited, no crash. That's exactly the mode staging runs in until a
feature actually needs billing to test against. When that day comes,
revisit this section and decide then (Stripe Test mode is cheap - one
account, no duplication needed; Supabase needs an explicit choice
between the shared prod project and a dedicated one, see the git history
of this section for the fuller writeup of that tradeoff).

1. **Push the `staging` branch** (already created locally as of this
   doc update): `git push -u origin staging`.
2. **Render → New + → Web Service** (NOT via Blueprint — Blueprint sync
   is tied to one branch for every service in `render.yaml`, so the
   second environment has to be created by hand):
   - Name: **exactly** `algopuzzle-api-staging` (the name determines the
     default `*.onrender.com` hostname, which `index_1.html`'s
     `STAGING_API_BASE`/`IS_STAGING` check — see that file — hardcodes;
     if Render appends a suffix because the name's taken, update that
     constant to match).
   - Branch: `staging`
   - Runtime: Python, Region: Frankfurt (matches everything else, so
     latency stays comparable if/when this does start talking to
     Supabase)
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Plan: Free
3. **Render → New + → Static Site**:
   - Name: **exactly** `algopuzzle-frontend-staging`
   - Branch: `staging`
   - Build command: (leave empty), Publish directory: `.`
   - Plan: Free
4. **Env vars on `algopuzzle-api-staging`** (Environment tab) — just
   one, for now:
   - `PYTHON_VERSION` = `3.12.8`

   That's it. Leave `DATABASE_URL`, every `STRIPE_*`/`SUPABASE_*` var,
   `COOKIE_SIGNING_SECRET`, `ALLOWED_ORIGINS`, `SITE_URL` all unset -
   `settings.py`'s `os.getenv(...)` defaults handle every one of them
   safely (device cookies still work, they just use an insecure default
   signing secret - fine for a throwaway staging environment with no
   real payment data ever flowing through it; revisit if that stops
   being true).
5. **No env vars needed on `algopuzzle-frontend-staging`** — static
   site; `index_1.html`'s `IS_STAGING` check picks the right backend
   automatically based on its own hostname (see step 2's name note
   above).
6. **Test it:** open the staging frontend URL, build a strategy, export
   it - should work end-to-end (unlimited exports, no paywall, since
   billing is unconfigured). That's the whole test for now.
7. **Promoting to production:** once a feature built on `staging` is
   verified here, merge `staging` → `main` and push. Render's production
   services (still wired to `main`) redeploy automatically; the staging
   services keep running independently for the next round of work.
8. **Whenever marketplace work actually needs Stripe/Supabase to test
   against:** come back to this section and make that call explicitly
   then - don't silently add it because it seemed like the "complete"
   thing to do. Ask first.
