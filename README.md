# AlgoPuzzle — MVP

A no-code visual builder that compiles drag-and-drop trading blocks into a deployable MetaTrader 5 (`.mq5`), MetaTrader 4 (`.mq4`), or cTrader (`.cs` cBot) strategy. Live at [algopuzzle.com](https://algopuzzle.com).

## Run it locally

1. **Backend**
   ```bash
   pip install -r requirements.txt
   uvicorn main:app --reload --port 8000
   ```

2. **Frontend**
   Two separate HTML files, not one: `index.html` is the marketing/landing page (SEO front door, links to the builder); `index_1.html` is the actual builder app. To work on the builder, open `index_1.html` directly in your browser (double-click it, or serve it with any static server, e.g. `python -m http.server 5500`). It talks to the backend at `http://127.0.0.1:8000`.
   Billing/auth are optional locally — with no `.env` / `DATABASE_URL` set, `billing.py`/`db.py` no-op with a warning and exports are unlimited. See `docs/DEPLOYMENT.md` for setting up the real Stripe/Supabase-backed billing.

## How it works

- **index_1.html** defines custom Blockly blocks (Asset, Timeframe, indicators, price/volume operands, comparisons, THEN actions, risk/sizing, and a top-level "IF Strategy Rule" wrapper — multiple rules, even across different assets, can be combined into one export). Clicking an export button walks the block tree with a custom serializer (`serializeWorkspace`) and posts a clean JSON payload to `/api/generate*` — it does not rely on Blockly's generic XML dump, so the backend gets a predictable shape.
- **main.py** validates that JSON with Pydantic, converts it into one shared `StrategyIR` (`parse_strategy()`), then renders it to whichever platform was requested — `render_mql5()` (MetaTrader 5), `render_mql4()` (MetaTrader 4), or `render_csharp()` (cTrader cBot) — writes a `README.txt` setup guide, zips both in memory, and streams the `.zip` back as a download. See `docs/HANDOFF.md` for why all three renderers share one IR instead of three separate generators.

## Notes / scope

- Multiple `IF` rules on the canvas export as one combined EA/cBot — see `docs/HANDOFF.md` for the one real platform constraint this has (same-symbol rules merge into a single position on netting accounts).
- Backtesting is intentionally out of scope for the builder itself — review the generated code and run it through your platform's own Strategy Tester / cTrader backtester (then a demo account) before trading it live.
- `==` comparisons compile to a small-tolerance check (`MathAbs()` on MT5/MT4, the equivalent on cTrader), since floating-point values shouldn't be compared with strict equality.

## More documentation

- [`docs/HANDOFF.md`](docs/HANDOFF.md) — full project handoff: architecture, design decisions, current state, open items.
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — step-by-step production deploy checklist (Supabase → Stripe → Render → domain).
- [`docs/SEO_KEYWORDS.md`](docs/SEO_KEYWORDS.md) — target-keyword tracking for the landing page and blog.
- [`docs/SOCIAL_MEDIA_GUIDE.md`](docs/SOCIAL_MEDIA_GUIDE.md) — visual template, copy rules, and platform mechanics for X and Instagram posts.
- [`blog/CONTENT_GUIDE.md`](blog/CONTENT_GUIDE.md) — process for writing a new blog article.
