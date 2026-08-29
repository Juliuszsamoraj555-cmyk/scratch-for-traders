"""
Centralized environment configuration for the billing/entitlement layer.
See .env.example for every variable this app can use.

Nothing here has a real secret as a default. Locally, `python-dotenv`
loads a `.env` file if one exists (gitignored - never commit it); on
Render, these are injected directly as environment variables in the
service's dashboard.

Deliberately NOT using pydantic-settings/BaseSettings here to avoid a
new dependency for what's a handful of os.getenv() calls - keeps the
"cheapest possible" footprint from ballooning.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # no-op if there's no .env file (e.g. on Render, where env
                # vars are injected by the platform instead)


def _split_origins(raw: str) -> list[str]:
    return [o.strip() for o in raw.split(",") if o.strip()]


class Settings:
    # --- Database (Supabase Postgres connection string) ---
    # Format: postgresql://postgres:[password]@[host]:5432/postgres
    # Find it in Supabase dashboard -> Project Settings -> Database ->
    # Connection string (use the "Transaction" pooler URI on port 6543
    # for serverless-friendly pooling, or the direct 5432 URI otherwise).
    DATABASE_URL: str | None = os.getenv("DATABASE_URL")

    # --- Stripe ---
    STRIPE_SECRET_KEY: str | None = os.getenv("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET: str | None = os.getenv("STRIPE_WEBHOOK_SECRET")
    # Price IDs for the two one-time Prices created in the Stripe dashboard
    # (Products -> Add product -> One time). NOT Payment Link URLs, NOT
    # Subscription Prices - these are one-time-purchase Price IDs.
    STRIPE_PRICE_EXPORT: str | None = os.getenv("STRIPE_PRICE_EXPORT")
    STRIPE_PRICE_PASS: str | None = os.getenv("STRIPE_PRICE_PASS")

    # --- Supabase Auth (email+password login, for the 30-day pass only -
    # the free tier and single-export purchases stay anonymous/device_id
    # based, see device_identity.py) ---
    # Project Settings -> API -> Project URL.
    SUPABASE_URL: str | None = os.getenv("SUPABASE_URL")
    # Project Settings -> API -> Project API keys -> "anon" / "public".
    # Safe to be non-secret (it's the same key the frontend embeds
    # directly) - stored here mainly so the backend can validate it's
    # configured, not because it needs to stay hidden.
    SUPABASE_ANON_KEY: str | None = os.getenv("SUPABASE_ANON_KEY")
    # Project Settings -> API -> JWT Settings -> JWT Secret. Backend-only,
    # real secret - used to verify (not issue) the access tokens Supabase
    # Auth hands the frontend after login, via HS256. Never expose this
    # one to the frontend.
    SUPABASE_JWT_SECRET: str | None = os.getenv("SUPABASE_JWT_SECRET")

    # --- Anonymous device identity (signed cookie, no login) ---
    # MUST be set to a long random value in production - anyone who knows
    # this can forge a device_id cookie and claim someone else's credits.
    # Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
    COOKIE_SIGNING_SECRET: str = os.getenv("COOKIE_SIGNING_SECRET", "dev-insecure-secret-change-me")
    # Leave unset for local dev / single-origin deploys. Once a domain is
    # chosen and frontend+backend live on subdomains of it (e.g.
    # app.yourdomain.pl + api.yourdomain.pl), set this to
    # ".yourdomain.pl" so the device cookie is shared between them.
    COOKIE_DOMAIN: str | None = os.getenv("COOKIE_DOMAIN")

    # --- CORS / redirect URLs ---
    # Comma-separated list of exact origins allowed to call the API with
    # credentials (cookies). Cannot be "*" once cookies are involved -
    # browsers reject wildcard + credentials.
    ALLOWED_ORIGINS: list[str] = _split_origins(
        os.getenv("ALLOWED_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080")
    )
    # Where the frontend lives - used to build Stripe Checkout success/
    # cancel redirect URLs. Update this the moment a real domain exists.
    SITE_URL: str = os.getenv("SITE_URL", "http://localhost:8080")

    # --- MT5 export: install walkthrough video ---
    # Hosted on YouTube rather than embedded in the zip - a raw video file
    # was blowing every /api/generate (MT5) response up by ~40MB, which is
    # what a strategy.mq5 + README.txt zip should never weigh. Instead the
    # zip ships a tiny redirect page pointing here (see
    # _add_mt5_tutorial_video() in main.py). Empty by default so nothing is
    # added until a real link exists - set this in Render's env vars once
    # the video is live.
    MT5_TUTORIAL_VIDEO_URL: str = os.getenv("MT5_TUTORIAL_VIDEO_URL", "")

    # --- Pricing (cents = smallest USD unit, matches Stripe's integer
    # amounts - the actual charged amount always comes from the Stripe
    # Price objects themselves (STRIPE_PRICE_EXPORT/PASS below), these
    # two are only used for display text that has to exist before ever
    # talking to Stripe, e.g. the 402 paywall message in main.py) ---
    FREE_EXPORT_LIMIT: int = int(os.getenv("FREE_EXPORT_LIMIT", "2"))
    EXPORT_PRICE_CENTS: int = int(os.getenv("EXPORT_PRICE_CENTS", "200"))   # $2.00
    PASS_PRICE_CENTS: int = int(os.getenv("PASS_PRICE_CENTS", "999"))      # $9.99
    PASS_DURATION_DAYS: int = int(os.getenv("PASS_DURATION_DAYS", "30"))

    # --- Abuse mitigation: per-IP cap on the FREE bucket only ---
    # A device_id cookie alone resets to a fresh 2 free exports the moment
    # it's cleared (incognito, "clear site data", a different browser) -
    # this doesn't stop that, but it stops the same network from doing it
    # in a loop: once this many FREE exports have come from one hashed IP
    # within the window, further devices on that IP fall straight through
    # to "buy a credit / pass" instead of getting more free ones. Paid
    # credits and an active pass are real payments, so they're never
    # capped by this. Deliberately generous (not "2 per IP") so a shared
    # office/campus/CGNAT connection with several genuine traders doesn't
    # get blocked - this only catches someone actually looping the
    # cookie-reset trick many times in a row.
    IP_FREE_EXPORT_LIMIT: int = int(os.getenv("IP_FREE_EXPORT_LIMIT", "15"))
    IP_FREE_EXPORT_WINDOW_HOURS: int = int(os.getenv("IP_FREE_EXPORT_WINDOW_HOURS", "24"))


settings = Settings()
