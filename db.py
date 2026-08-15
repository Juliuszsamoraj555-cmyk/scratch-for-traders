"""
Postgres access layer for billing/entitlements — see supabase/schema.sql
for the tables and the consume_export_entitlement() function this module
calls into.

Uses a small psycopg (v3) connection pool, with every call wrapped in
run_in_threadpool so the sync DB driver doesn't block the asyncio event
loop that other requests' handlers run on. Deliberately NOT using an ORM:
this schema is small and stable, and raw SQL is easier to audit line-by-
line for money-handling code than an ORM's generated queries would be.

(psycopg v3, not psycopg2 - psycopg2-binary has no prebuilt wheel for
newer Python versions yet at the time this was written, psycopg3 does.
Query syntax is unaffected: both use %s-style placeholders.)
"""
from __future__ import annotations

import datetime
import hashlib
import uuid
from contextlib import contextmanager
from typing import Optional

from fastapi.concurrency import run_in_threadpool
# Jsonb, not Json - psycopg3 has two separate wrapper classes, one per
# Postgres type (json vs jsonb). Every jsonb-typed column/function
# parameter in schema.sql (export_log.strategy_meta, analytics_events.
# metadata, consume_export_entitlement's p_strategy_meta, ...) needs the
# argument adapted as an actual `jsonb`-typed value - Json here adapts as
# plain `json` instead, which Postgres does NOT implicitly cast to jsonb
# for function-overload resolution, so every call using it failed
# outright with "function ... does not exist" (confirmed against a real
# production traceback, psycopg.errors.UndefinedFunction, 2026-08-15).
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from settings import settings

_pool: Optional[ConnectionPool] = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not settings.DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set - billing features need a Supabase "
                "Postgres connection string. See .env.example."
            )
        _pool = ConnectionPool(
            conninfo=settings.DATABASE_URL,
            min_size=1,
            max_size=10,
            open=True,
            # DATABASE_URL is Supabase's Transaction-mode pooler (PgBouncer,
            # port 6543) - it hands out a different real server connection
            # per transaction, but psycopg's server-side prepared-statement
            # cache assumes a stable connection. Left on, this produces
            # exactly the kind of sporadic, hard-to-reproduce query
            # failures seen in production (intermittent 500s on
            # /api/billing/status that didn't reproduce in careful
            # sequential testing) - disabling it is Supabase's own
            # documented fix for this pooler mode.
            kwargs={"prepare_threshold": None},
        )
    return _pool


@contextmanager
def _conn():
    pool = _get_pool()
    with pool.connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def hash_ip(ip: str) -> str:
    """Salted hash of a client IP - stored only as a secondary abuse
    signal (e.g. flagging one IP behind many device_ids in a short
    window), never the raw address. Salted with the same secret used to
    sign the device cookie so it isn't reversible without it."""
    salted = f"{settings.COOKIE_SIGNING_SECRET}:{ip}".encode("utf-8")
    return hashlib.sha256(salted).hexdigest()


# --------------------------------------------------------------------------
# Device identity
# --------------------------------------------------------------------------

def _get_or_create_device_sync(device_id: Optional[str], ip_hash: str) -> str:
    with _conn() as conn, conn.cursor() as cur:
        if device_id:
            cur.execute("select device_id from devices where device_id = %s", (device_id,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "update devices set last_seen_at = now(), ip_hash = %s where device_id = %s",
                    (ip_hash, device_id),
                )
                return str(row[0])
            # Cookie present but no matching row (fresh DB, or the row was
            # purged) - recreate it under the SAME id so the cookie the
            # browser already has stays valid instead of silently
            # resetting the user's free-export count.
            cur.execute(
                "insert into devices (device_id, ip_hash) values (%s, %s)",
                (device_id, ip_hash),
            )
            return device_id

        new_id = str(uuid.uuid4())
        cur.execute(
            "insert into devices (device_id, ip_hash) values (%s, %s)",
            (new_id, ip_hash),
        )
        return new_id


async def get_or_create_device(device_id: Optional[str], ip: str) -> str:
    return await run_in_threadpool(_get_or_create_device_sync, device_id, hash_ip(ip))


# --------------------------------------------------------------------------
# Entitlement status (read-only, for the frontend to render "X free
# exports left" / paywall state)
# --------------------------------------------------------------------------

def _get_entitlement_status_sync(device_id: str) -> dict:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select free_exports_used, paid_export_credits, pass_expires_at "
            "from devices where device_id = %s",
            (device_id,),
        )
        row = cur.fetchone()
        if not row:
            return {
                "free_exports_used": 0,
                "free_exports_remaining": settings.FREE_EXPORT_LIMIT,
                "paid_export_credits": 0,
                "pass_active": False,
                "pass_expires_at": None,
            }
        free_used, credits, pass_expires_at = row
        pass_active = bool(
            pass_expires_at and pass_expires_at > datetime.datetime.now(datetime.timezone.utc)
        )
        return {
            "free_exports_used": free_used,
            "free_exports_remaining": max(0, settings.FREE_EXPORT_LIMIT - free_used),
            "paid_export_credits": credits,
            "pass_active": pass_active,
            "pass_expires_at": pass_expires_at.isoformat() if pass_expires_at else None,
        }


async def get_entitlement_status(device_id: str) -> dict:
    return await run_in_threadpool(_get_entitlement_status_sync, device_id)


# --------------------------------------------------------------------------
# Consuming an export (the one place that decides "can this device export
# right now" - see consume_export_entitlement() in supabase/schema.sql for
# the atomic, row-locked logic)
# --------------------------------------------------------------------------

def _consume_export_sync(
    device_id: str, platform: str, ip: Optional[str], strategy_meta: Optional[dict]
) -> tuple[bool, str]:
    # Same salted hash as get_or_create_device used when it stored this
    # device's ip_hash - has to match or the IP-level free-export count
    # in consume_export_entitlement() would never find this device's rows.
    ip_hash = hash_ip(ip) if ip else None
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select granted, consumed_from from consume_export_entitlement(%s, %s, %s, %s, %s, %s, %s)",
            (
                device_id,
                platform,
                settings.FREE_EXPORT_LIMIT,
                ip_hash,
                settings.IP_FREE_EXPORT_LIMIT,
                settings.IP_FREE_EXPORT_WINDOW_HOURS,
                Jsonb(strategy_meta) if strategy_meta is not None else None,
            ),
        )
        granted, consumed_from = cur.fetchone()
        return bool(granted), consumed_from


async def consume_export(
    device_id: str, platform: str, ip: Optional[str] = None, strategy_meta: Optional[dict] = None
) -> tuple[bool, str]:
    """Returns (granted, consumed_from). consumed_from is 'free' | 'credit'
    | 'pass' when granted=True, or 'none' when granted=False (caller
    should respond 402 Payment Required). ip is optional only so old call
    sites don't break - omitting it just disables the per-IP free-export
    cap for that call (see consume_export_entitlement() in schema.sql).
    strategy_meta (see _summarize_strategy() in main.py) is stored
    alongside the export_log row consume_export_entitlement() inserts,
    when one actually gets granted - omitted (None) it's simply not
    recorded, same as before this existed."""
    return await run_in_threadpool(_consume_export_sync, device_id, platform, ip, strategy_meta)


# --------------------------------------------------------------------------
# Granting entitlement after a Stripe payment (called from the webhook
# handler in billing.py). Both are idempotent on stripe_event_id, since
# Stripe retries webhook delivery on any non-2xx response or timeout and
# must be safe to receive the same event twice.
# --------------------------------------------------------------------------

def _event_already_applied(cur, stripe_event_id: str) -> bool:
    cur.execute("select 1 from billing_events where stripe_event_id = %s", (stripe_event_id,))
    return cur.fetchone() is not None


def _grant_export_credits_sync(
    device_id: str,
    n: int,
    stripe_event_id: str,
    session_id: str,
    amount_cents: int,
    currency: str,
    email: Optional[str],
) -> bool:
    with _conn() as conn, conn.cursor() as cur:
        if _event_already_applied(cur, stripe_event_id):
            return False
        cur.execute(
            "update devices set paid_export_credits = paid_export_credits + %s, "
            "billing_email = coalesce(%s, billing_email) where device_id = %s",
            (n, email, device_id),
        )
        cur.execute(
            "insert into billing_events "
            "(stripe_event_id, device_id, event_type, stripe_checkout_session_id, "
            " amount_total_cents, currency) "
            "values (%s, %s, 'export_credit_purchase', %s, %s, %s)",
            (stripe_event_id, device_id, session_id, amount_cents, currency),
        )
        return True


async def grant_export_credits(
    device_id: str,
    n: int,
    stripe_event_id: str,
    session_id: str,
    amount_cents: int,
    currency: str,
    email: Optional[str] = None,
) -> bool:
    return await run_in_threadpool(
        _grant_export_credits_sync, device_id, n, stripe_event_id, session_id, amount_cents, currency, email
    )


def _grant_day_pass_sync(
    user_id: str,
    days: int,
    stripe_event_id: str,
    session_id: str,
    amount_cents: int,
    currency: str,
    email: Optional[str],
) -> bool:
    with _conn() as conn, conn.cursor() as cur:
        if _event_already_applied(cur, stripe_event_id):
            return False
        # Upsert: first-ever pass for this user_id inserts a row, buying
        # another one while active extends from the later of "now" or
        # the current expiry (stacks the days instead of wasting whatever
        # time was left) - same logic the old device-based version used,
        # just keyed by user_id/user_entitlements now.
        cur.execute(
            "insert into user_entitlements (user_id, pass_expires_at, billing_email) "
            "values (%s, now() + (%s || ' days')::interval, %s) "
            "on conflict (user_id) do update set "
            "pass_expires_at = greatest(coalesce(user_entitlements.pass_expires_at, now()), now()) "
            "  + (%s || ' days')::interval, "
            "billing_email = coalesce(excluded.billing_email, user_entitlements.billing_email)",
            (user_id, days, email, days),
        )
        cur.execute(
            "insert into billing_events "
            "(stripe_event_id, user_id, event_type, stripe_checkout_session_id, "
            " amount_total_cents, currency) "
            "values (%s, %s, 'day_pass_purchase', %s, %s, %s)",
            (stripe_event_id, user_id, session_id, amount_cents, currency),
        )
        return True


async def grant_day_pass(
    user_id: str,
    days: int,
    stripe_event_id: str,
    session_id: str,
    amount_cents: int,
    currency: str,
    email: Optional[str] = None,
) -> bool:
    """user_id is a Supabase Auth user id (auth.users.id), not a
    device_id - the 30-day pass is the one entitlement that requires
    login, see schema.sql's header comment for why."""
    return await run_in_threadpool(
        _grant_day_pass_sync, user_id, days, stripe_event_id, session_id, amount_cents, currency, email
    )


# --------------------------------------------------------------------------
# Checking/consuming pass entitlement for a logged-in user - see
# has_active_pass() in schema.sql. Called first, before ever touching
# device_id-based entitlement (see main.py's _require_export_entitlement).
# --------------------------------------------------------------------------

def _has_active_pass_sync(user_id: str) -> bool:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("select has_active_pass(%s)", (user_id,))
        return bool(cur.fetchone()[0])


async def has_active_pass(user_id: str) -> bool:
    return await run_in_threadpool(_has_active_pass_sync, user_id)


def _log_user_pass_export_sync(user_id: str, device_id: str, platform: str, strategy_meta: Optional[dict]) -> None:
    # No row-locking/counter here on purpose - an active pass is
    # unlimited-while-active, this is just a delivery log, not a
    # resource being deducted (unlike consume_export_entitlement).
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into export_log (device_id, user_id, platform, consumed_from, strategy_meta) "
            "values (%s, %s, %s, 'pass', %s)",
            (device_id, user_id, platform, Jsonb(strategy_meta) if strategy_meta is not None else None),
        )


async def log_user_pass_export(
    user_id: str, device_id: str, platform: str, strategy_meta: Optional[dict] = None
) -> None:
    return await run_in_threadpool(_log_user_pass_export_sync, user_id, device_id, platform, strategy_meta)


# --------------------------------------------------------------------------
# General site-behavior events (2026-08-15 addition) - see analytics_events
# in schema.sql for the table and the two convenience views, and
# ALLOWED_ANALYTICS_EVENTS in main.py for the fixed event_type allowlist
# the API layer enforces before anything reaches this function. Deliberately
# just an insert, no row-locking or read-back - this is a write-mostly log,
# never a value anything else in the app depends on reading back.
# --------------------------------------------------------------------------

def _log_analytics_event_sync(
    device_id: Optional[str], user_id: Optional[str], event_type: str, metadata: Optional[dict], path: Optional[str]
) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into analytics_events (device_id, user_id, event_type, metadata, path) "
            "values (%s, %s, %s, %s, %s)",
            (device_id, user_id, event_type, Jsonb(metadata) if metadata is not None else None, path),
        )


async def log_analytics_event(
    device_id: Optional[str], user_id: Optional[str], event_type: str,
    metadata: Optional[dict] = None, path: Optional[str] = None,
) -> None:
    return await run_in_threadpool(_log_analytics_event_sync, device_id, user_id, event_type, metadata, path)
