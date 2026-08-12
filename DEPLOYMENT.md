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
[`render.yaml`](render.yaml) and creates both services (`scratch-for-traders-api`
web service + `scratch-for-traders-frontend` static site) in one go. It
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
- API: `https://scratch-for-traders-api.onrender.com`
- Frontend: `https://scratch-for-traders-frontend.onrender.com`

**Two follow-up edits now that these URLs exist:**
1. In `index_1.html`, set `PRODUCTION_API_BASE` (search for it) to the
   API URL, commit, push - Render redeploys the frontend automatically.
2. In the API service's env vars on Render, set `ALLOWED_ORIGINS` and
   `SITE_URL` to the frontend URL.

## 5. Stripe webhook (needs the API's real URL from step 4)

1. Stripe dashboard (still Test mode) → **Developers → Webhooks → Add
   endpoint**.
2. Endpoint URL: `https://scratch-for-traders-api.onrender.com/api/billing/webhook`
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
