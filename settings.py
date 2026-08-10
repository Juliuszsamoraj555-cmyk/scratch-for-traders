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

    # --- Pricing (grosze = smallest PLN unit, matches Stripe's integer amounts) ---
    FREE_EXPORT_LIMIT: int = int(os.getenv("FREE_EXPORT_LIMIT", "2"))
    EXPORT_PRICE_GROSZE: int = int(os.getenv("EXPORT_PRICE_GROSZE", "500"))   # 5.00 zl
    PASS_PRICE_GROSZE: int = int(os.getenv("PASS_PRICE_GROSZE", "3000"))     # 30.00 zl
    PASS_DURATION_DAYS: int = int(os.getenv("PASS_DURATION_DAYS", "30"))


settings = Settings()
