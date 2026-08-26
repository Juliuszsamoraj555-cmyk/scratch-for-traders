/* ============================================================
   MARKETPLACE DATA + OWNERSHIP LAYER (2026-08-26, staging)
   ============================================================
   Shared by strategy-of-the-week.html, strategy-detail.html, and
   index_1.html's "My Strategies" -> "Purchases" tab - one source of
   truth so the three pages can't silently disagree about what a
   strategy is called or whether it's owned.

   THIS IS THE UI/UX ARCHITECTURE PHASE. Per explicit instruction
   (2026-08-26): real strategy content, real prices, and the real Stripe
   purchase flow are being built LAST, on purpose - this file exists so
   the full click-through experience (browse -> buy -> owned -> load
   into builder) can be designed and reviewed now, without waiting on
   any of that. Two things below are placeholders and are clearly
   marked - do not treat either as real before they're replaced:

   1. MARKETPLACE_STRATEGIES' content/prices - realistic-looking so the
      layout can be judged honestly, but invented for this draft.
   2. The ownership layer (getOwnedStrategyIds/markStrategyOwned) -
      backed by localStorage, not a real purchase. There is no backend
      endpoint for this yet. When the real Stripe + Supabase-backed
      version is built, replace ONLY the body of the three functions in
      the "OWNERSHIP" section below with real fetch() calls against a
      future /api/marketplace/purchases-shaped endpoint - every caller
      already goes through these functions, so nothing else needs to
      change.
   ============================================================ */

/* ------------------------------------------------------------
   STRATEGY CATALOG (placeholder content - see header comment)
   ------------------------------------------------------------
   blockly_state: null means "no real strategy blueprint authored yet".
   Every place that would use it (Load into Builder, Download for
   MT5/MT4/cTrader) already checks for null and shows an honest
   "not added yet" message instead of pretending to work - see
   marketplaceLoadIntoBuilder() below and strategy-detail.html.
   ------------------------------------------------------------ */
const MARKETPLACE_STRATEGIES = [
  {
    id: 'gbpusd-trend-h4',
    tier: 'free',
    badge: 'Free strategy of the week',
    style: 'Trend following',
    timeframe: 'H4',
    symbol: 'GBPUSD',
    name: 'GBPUSD, 4-hour candles — trend following',
    description: 'Buys when price closes above the previous candle, with Stop Loss and Take Profit distances set dynamically from market volatility (ATR). Full description and exact parameters shown up front — nothing here is held back.',
    stats: { winRate: 75, returnPct: 11.2, trades: 20, drawdownPct: -0.23 },
    priceLabel: '$0.00',
    blockly_state: null,
  },
  {
    id: 'gbpusd-atr-volatility',
    tier: 'paid',
    featured: true,
    style: 'Momentum',
    timeframe: 'H4',
    symbol: 'GBPUSD',
    name: 'GBPUSD — ATR-driven volatility',
    description: 'A trend-following strategy with dynamic, volatility-based risk sizing. Exact entry conditions and stop sizing unlock after purchase.',
    stats: { winRate: 75, returnPct: 11.2, trades: 20, drawdownPct: -0.23 },
    priceLabel: '$X.XX',
    blockly_state: null,
  },
  {
    id: 'gbpusd-momentum-edge',
    tier: 'paid',
    style: 'Mean reversion',
    timeframe: 'M30',
    symbol: 'GBPUSD',
    name: 'GBPUSD — Momentum Edge',
    description: 'A mean-reversion approach on 30-minute candles. Exact entry conditions and stop sizing unlock after purchase.',
    stats: { winRate: 59, returnPct: 21.8, trades: 29, drawdownPct: null },
    priceLabel: '$X.XX',
    blockly_state: null,
  },
  {
    id: 'xauusd-trend-rider',
    tier: 'paid',
    style: 'Trend',
    timeframe: 'H4',
    symbol: 'XAUUSD',
    name: 'XAUUSD — Trend Rider',
    description: 'A trend-following strategy on gold. Exact entry conditions and stop sizing unlock after purchase.',
    stats: { winRate: 61, returnPct: 19.0, trades: 51, drawdownPct: null },
    priceLabel: '$X.XX',
    blockly_state: null,
  },
  {
    id: 'us100-trend-filter',
    tier: 'paid',
    style: 'Trend',
    timeframe: 'H1',
    symbol: 'US100',
    name: 'US100 — Trend Filter',
    description: 'A trend-following strategy on the US100 index. Exact entry conditions and stop sizing unlock after purchase.',
    stats: { winRate: 76, returnPct: 10.1, trades: 17, drawdownPct: null },
    priceLabel: '$X.XX',
    blockly_state: null,
  },
  {
    id: 'eurusd-rsi14-oversold',
    tier: 'paid',
    style: 'Pullback',
    timeframe: 'M15',
    symbol: 'EURUSD',
    name: 'EURUSD — RSI 14 oversold',
    description: 'A pullback strategy using RSI(14) on 15-minute candles. Exact entry conditions and stop sizing unlock after purchase.',
    stats: { winRate: 52, returnPct: 4.6, trades: 41, drawdownPct: null },
    priceLabel: '$X.XX',
    blockly_state: null,
  },
];

function getMarketplaceStrategy(id) {
  return MARKETPLACE_STRATEGIES.find((s) => s.id === id) || null;
}

/* ------------------------------------------------------------
   OWNERSHIP (MOCK - see header comment, replace before going live)
   ------------------------------------------------------------ */
const MARKETPLACE_MOCK_OWNED_KEY = 'algopuzzle.marketplace.mockOwned.v1';

function getOwnedStrategyIds() {
  try {
    const raw = localStorage.getItem(MARKETPLACE_MOCK_OWNED_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    return [];
  }
}

function isStrategyOwned(id) {
  return getOwnedStrategyIds().includes(id);
}

function markStrategyOwned(id) {
  const owned = getOwnedStrategyIds();
  if (!owned.includes(id)) {
    owned.push(id);
    try {
      localStorage.setItem(MARKETPLACE_MOCK_OWNED_KEY, JSON.stringify(owned));
    } catch (err) {
      console.warn('AlgoPuzzle marketplace: could not persist mock ownership.', err);
    }
  }
}

function getOwnedMarketplaceStrategies() {
  const ownedIds = getOwnedStrategyIds();
  return MARKETPLACE_STRATEGIES.filter((s) => ownedIds.includes(s.id));
}
