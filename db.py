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
        _pool = ConnectionPool(conninfo=settings.DATABASE_URL, min_size=1, max_size=10, open=True)
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

def _consume_export_sync(device_id: str, platform: str) -> tuple[bool, str]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select granted, consumed_from from consume_export_entitlement(%s, %s, %s)",
            (device_id, platform, settings.FREE_EXPORT_LIMIT),
        )
        granted, consumed_from = cur.fetchone()
        return bool(granted), consumed_from


async def consume_export(device_id: str, platform: str) -> tuple[bool, str]:
    """Returns (granted, consumed_from). consumed_from is 'free' | 'credit'
    | 'pass' when granted=True, or 'none' when granted=False (caller
    should respond 402 Payment Required)."""
    return await run_in_threadpool(_consume_export_sync, device_id, platform)


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
    amount_grosze: int,
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
            " amount_total_grosze, currency) "
            "values (%s, %s, 'export_credit_purchase', %s, %s, %s)",
            (stripe_event_id, device_id, session_id, amount_grosze, currency),
        )
        return True


async def grant_export_credits(
    device_id: str,
    n: int,
    stripe_event_id: str,
    session_id: str,
    amount_grosze: int,
    currency: str,
    email: Optional[str] = None,
) -> bool:
    return await run_in_threadpool(
        _grant_export_credits_sync, device_id, n, stripe_event_id, session_id, amount_grosze, currency, email
    )


def _grant_day_pass_sync(
    device_id: str,
    days: int,
    stripe_event_id: str,
    session_id: str,
    amount_grosze: int,
    currency: str,
    email: Optional[str],
) -> bool:
    with _conn() as conn, conn.cursor() as cur:
        if _event_already_applied(cur, stripe_event_id):
            return False
        # Extends from the later of "now" or the current expiry, so
        # buying a pass while one is still active stacks the days instead
        # of wasting whatever time was left.
        cur.execute(
            "update devices set "
            "pass_expires_at = greatest(coalesce(pass_expires_at, now()), now()) + (%s || ' days')::interval, "
            "billing_email = coalesce(%s, billing_email) "
            "where device_id = %s",
            (days, email, device_id),
        )
        cur.execute(
            "insert into billing_events "
            "(stripe_event_id, device_id, event_type, stripe_checkout_session_id, "
            " amount_total_grosze, currency) "
            "values (%s, %s, 'day_pass_purchase', %s, %s, %s)",
            (stripe_event_id, device_id, session_id, amount_grosze, currency),
        )
        return True


async def grant_day_pass(
    device_id: str,
    days: int,
    stripe_event_id: str,
    session_id: str,
    amount_grosze: int,
    currency: str,
    email: Optional[str] = None,
) -> bool:
    return await run_in_threadpool(
        _grant_day_pass_sync, device_id, days, stripe_event_id, session_id, amount_grosze, currency, email
    )
