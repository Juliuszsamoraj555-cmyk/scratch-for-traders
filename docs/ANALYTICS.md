# Analytics, consent and Google Ads conversion tracking

Set up 2026-09-03, ahead of the first paid campaign. Before this the site
had **no** analytics of any kind — no GA4, no Search Console, no tag —
and the only events recorded were fired inside the builder, so anyone
arriving on a marketing page and leaving was invisible. That is the
population paid traffic is made of.

## Where the IDs go

One file: [`assets/analytics.js`](../assets/analytics.js), in the CONFIG
block at the very top. Nothing else in the codebase needs editing.

```js
const GA4_ID  = 'G-8W3F8H6PLY';   // already set
const ADS_ID  = '';               // 'AW-123456789'
const ADS_LABELS = { signup: '', export: '', purchase: '' };
```

**Until `ADS_ID` is filled in the file is inert**: no external script
loads, no cookie banner appears, and events still record to our own
database exactly as before. That is deliberate — the site must not show
a consent banner for tags that don't exist yet.

### Getting the Google Ads values

Google Ads → Tools → Conversions → create three conversion actions, all
of type **Website**, tracking method **Google tag / manual**:

| Action name | Category | Value |
|---|---|---|
| Sign up | Sign-up | none |
| Export completed | Other | none |
| Purchase | Purchase | use different values |

Each one shows a snippet containing `AW-123456789/AbC-D_efG-h12_34-567`.
The part **before** the slash is `ADS_ID` (same for all three); the part
**after** is that action's entry in `ADS_LABELS`.

Do **not** paste Google's raw snippet into the pages. `analytics.js`
already loads the same tag, and pasting it again would both double-load
it and bypass consent.

### Search Console

Since GA4 is live, verify the property in Search Console with the
**Google Analytics** method — one click, no tag needed. Only fall back to
the HTML-tag method if that fails, in which case the tag goes in
`index.html`'s `<head>`.

## Consent (EU)

The business is in Poland and targets EU traffic, so Google's tag runs
under **Consent Mode v2**:

- All four signals (`ad_storage`, `ad_user_data`, `ad_personalization`,
  `analytics_storage`) default to **denied**, set *before* the tag loads.
  Google then receives cookieless pings only.
- A banner offers Accept / Decline; the choice is stored in
  `localStorage` under `algoPuzzle.consent.v1` and applied on later
  visits without asking again.
- `ad_user_data` and `ad_personalization` are the two v2 signals Google
  has required for EEA traffic since March 2024. Without them, Ads
  conversion tracking and remarketing degrade badly.

Enhanced conversions are deliberately **not** enabled — that would hash
and send the visitor's email to Google, which is a separate consent and
privacy-policy question, not a default to flip on quietly.

## Events

`window.apTrack(type, metadata)` records to **both** our own
`analytics_events` table and GA4. `window.apConversion(name, params)`
reports a Google Ads conversion. Both are safe no-ops before the Ads ID
exists.

`event_type` must be listed in `ALLOWED_ANALYTICS_EVENTS` in `main.py`
or the backend drops it **silently, by design**. Add it there first —
this is exactly why the first test of the new events recorded nothing
until the backend was restarted.

| Event | Fired where | Also an Ads conversion |
|---|---|---|
| `landing_view` | homepage load | no |
| `cta_clicked` | any `[data-cta]` on the homepage | no |
| `builder_opened` | builder load | no |
| `new_strategy_started`, `strategy_saved` | builder | no |
| `auth_modal_shown`, `login_completed` | auth modal | no |
| `signup_completed` | account created | **signup** |
| `paywall_shown`, `checkout_started` | paywall | no |
| `export_succeeded` | after a .zip is actually delivered | **export** |
| `strategy_downloaded` | marketplace file delivered | no |
| `purchase_completed` | the real Stripe success pages | **purchase** |

### Two rules these follow, worth keeping

**Conversions fire on delivery, not on intent.** `export_succeeded` is
fired after the file reaches the trader, not when the button is clicked
— a click that then hit the paywall, a validation error or a network
failure is not an export. Counting it as one would inflate the
conversion rate every ad decision is made on.

**Conversion values are never read from the URL.** Each purchase page
hardcodes its own price (mirroring `EXPORT_PRICE_CENTS` /
`PASS_PRICE_CENTS` in `settings.py`, or reading the catalog for a
strategy). A query string is trivially edited, and a value the visitor
can set themselves would poison Google's bidding data.

Purchase conversions live on `thank-you/export/`, `thank-you/pass/` and
`strategy-detail.html?...&purchased=1` — the three real `success_url`
targets in `billing.py`. The `?checkout=success` branch in
`index_1.html` is a stale path from an older redirect and deliberately
reports nothing, so one sale can't be counted twice.

## Local development writes to the production database

`DATABASE_URL` in `.env` points at the live Supabase project, so events
fired while testing on `localhost` land in the real
`analytics_events` table. A handful of `landing_view` / `cta_clicked`
rows from 2026-09-03 with `path = /index.html` are from setting this up.
Filter by date if it matters; nothing separates them automatically.
