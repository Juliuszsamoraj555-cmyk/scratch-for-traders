-- AlgoPuzzle — billing schema
-- ============================================================
-- Two parallel identity systems, on purpose:
--   - Free exports + single-export credits: no login. Identified by a
--     signed, httpOnly `device_id` cookie the backend issues on first
--     contact (see main.py's device-identity dependency) - tied to one
--     browser, which is fine for a one-off purchase.
--   - The 30-day pass: DOES need Supabase Auth login (email+password),
--     because it's meant to work across every device the trader uses
--     for a month, and a device_id cookie can't do that - see
--     user_entitlements below. Everything else about "no accounts" still
--     holds; this is the one deliberate exception (2026-08-14 decision).
--
-- This file is meant to be run once against a fresh Supabase Postgres
-- database (SQL Editor in the Supabase dashboard, or `psql "$DATABASE_URL"
-- -f supabase/schema.sql`) - safe to re-run any time after too, every
-- statement is idempotent (create if not exists / create or replace).
--
-- Entitlement model:
--   1. Logged-in user with an active pass (user_entitlements) - checked
--      first, works from any device once logged in.
--   2. Per-device_id: first 2 exports free (free_exports_used, capped at
--      FREE_EXPORT_LIMIT), then pay-per-export credits (5 zl each,
--      paid_export_credits counts down). Both anonymous, one browser.
-- ============================================================

create extension if not exists pgcrypto; -- for gen_random_uuid()

create table if not exists devices (
    device_id            uuid primary key default gen_random_uuid(),
    created_at           timestamptz not null default now(),
    last_seen_at         timestamptz not null default now(),
    -- Salted hash of the request IP, never the raw IP - secondary abuse
    -- signal only (e.g. "50 different device_ids from one IP today"),
    -- not a primary key or unique constraint. See hash_ip() in db.py.
    ip_hash               text,
    free_exports_used     integer not null default 0,
    paid_export_credits   integer not null default 0,
    pass_expires_at        timestamptz,
    -- Captured from Stripe Checkout if the user pays - optional, used
    -- only for future re-engagement (e.g. "your pass expires tomorrow"),
    -- never required.
    billing_email          text
);

create index if not exists idx_devices_ip_hash on devices (ip_hash);

-- One row per logged-in trader with an active or past 30-day pass.
-- References auth.users (managed by Supabase Auth - the "public" side
-- of a login row lives here, not in auth.users itself, per Supabase's
-- own recommended pattern). Deliberately separate from `devices`: a
-- pass needs to work from every browser/device this person logs into,
-- not just the one that happened to buy it.
create table if not exists user_entitlements (
    user_id       uuid primary key references auth.users (id) on delete cascade,
    pass_expires_at timestamptz,
    -- Same purpose as devices.billing_email - Stripe Checkout captures
    -- it, used for future re-engagement, never required. Usually the
    -- same as the trader's login email but kept separately since Stripe
    -- doesn't guarantee they match (e.g. paying with a work card).
    billing_email  text,
    created_at     timestamptz not null default now()
);

-- One row per Stripe webhook event actually applied - the unique
-- constraint on stripe_event_id is what makes the webhook handler safe
-- to call twice with the same event (Stripe retries on timeout/5xx).
-- Exactly one of device_id/user_id is set per row: device_id for
-- export-credit purchases (anonymous), user_id for pass purchases
-- (logged-in) - see settings.py / main.py for which flow sets which.
create table if not exists billing_events (
    id                       bigserial primary key,
    stripe_event_id          text not null unique,
    device_id                uuid references devices (device_id),
    user_id                  uuid references auth.users (id),
    event_type               text not null, -- 'export_credit_purchase' | 'day_pass_purchase'
    stripe_checkout_session_id text,
    amount_total_grosze      integer, -- Stripe amounts are in the smallest currency unit
    currency                 text,
    created_at                timestamptz not null default now()
);

-- Append-only log of every export actually delivered - lets you see
-- where a device's entitlement went (useful for support questions like
-- "I paid but it says I have 0 credits") and for basic abuse monitoring.
-- device_id is always set (every request gets one, logged in or not) -
-- user_id is only set when the export was actually granted via an
-- active pass (consumed_from = 'pass').
create table if not exists export_log (
    id            bigserial primary key,
    device_id     uuid not null references devices (device_id),
    user_id       uuid references auth.users (id),
    platform      text not null, -- 'mt5' | 'mt4' | 'ctrader'
    consumed_from text not null, -- 'free' | 'credit' | 'pass'
    created_at    timestamptz not null default now()
);

create index if not exists idx_export_log_device on export_log (device_id, created_at desc);

-- ============================================================
-- has_active_pass: does this logged-in user currently have a paid,
-- unexpired 30-day pass? No row-locking needed (unlike the function
-- below) - a pass is unlimited-while-active, there's no shared counter
-- to race on, just a read. main.py calls this FIRST, before ever
-- touching device_id-based entitlement, whenever the request carries a
-- valid Supabase Auth token - see get_current_user() in main.py.
-- ============================================================
create or replace function has_active_pass(p_user_id uuid) returns boolean as $$
    select exists(
        select 1 from user_entitlements
         where user_id = p_user_id
           and pass_expires_at is not null
           and pass_expires_at > now()
    );
$$ language sql stable;

-- ============================================================
-- consume_export_entitlement: the one place that decides "can this
-- device export right now" (free / paid-credit only - an active pass is
-- checked separately by has_active_pass() above, before this is ever
-- called) and atomically deducts the right bucket. Row-locked (FOR
-- UPDATE) so two near-simultaneous clicks from the same device can't
-- both slip through on the last free export.
--
-- p_ip_hash / p_ip_free_limit / p_ip_window_hours gate the FREE bucket
-- only (see IP_FREE_EXPORT_LIMIT in settings.py for why): a device_id
-- cookie alone resets to a fresh free allowance the moment it's cleared,
-- so this additionally counts how many FREE exports (across ANY
-- device_id) have come from the same hashed IP recently, and stops
-- handing out more free ones once that's hit - paid credits and an
-- active pass are unaffected, since those are real payments. Passing
-- p_ip_hash as null (or omitting it) disables this check entirely -
-- every "= p_ip_hash" comparison is simply unknown/false against null,
-- so v_ip_free_count comes back 0 and behaviour is identical to before
-- this was added.
--
-- Returns a single row: (granted boolean, consumed_from text).
-- consumed_from is 'free' | 'credit' | 'pass' when granted, or
-- 'none' when not granted (out of everything - app should 402).
-- ============================================================
create or replace function consume_export_entitlement(
    p_device_id uuid,
    p_platform text,
    p_free_limit integer default 2,
    p_ip_hash text default null,
    p_ip_free_limit integer default 15,
    p_ip_window_hours integer default 24
) returns table(granted boolean, consumed_from text) as $$
declare
    v_free_used     integer;
    v_credits       integer;
    v_ip_free_count integer;
begin
    select free_exports_used, paid_export_credits
      into v_free_used, v_credits
      from devices
     where device_id = p_device_id
     for update;

    if not found then
        return query select false, 'none'::text;
        return;
    end if;

    -- 1) Free allowance - gated by both the device's own count AND the
    -- shared-IP count, so clearing cookies alone can't loop past this.
    if v_free_used < p_free_limit then
        select count(*) into v_ip_free_count
          from export_log el
          join devices d on d.device_id = el.device_id
         where p_ip_hash is not null
           and d.ip_hash = p_ip_hash
           and el.consumed_from = 'free'
           and el.created_at > now() - (p_ip_window_hours || ' hours')::interval;

        if v_ip_free_count < p_ip_free_limit then
            update devices
               set free_exports_used = free_exports_used + 1,
                   last_seen_at = now()
             where device_id = p_device_id;
            insert into export_log (device_id, platform, consumed_from)
            values (p_device_id, p_platform, 'free');
            return query select true, 'free'::text;
            return;
        end if;
        -- IP cap hit: fall through to credits/none below instead of
        -- granting another free export, same as if v_free_used had
        -- already reached p_free_limit.
    end if;

    -- 2) Paid per-export credits.
    if v_credits > 0 then
        update devices
           set paid_export_credits = paid_export_credits - 1,
               last_seen_at = now()
         where device_id = p_device_id;
        insert into export_log (device_id, platform, consumed_from)
        values (p_device_id, p_platform, 'credit');
        return query select true, 'credit'::text;
        return;
    end if;

    -- 3) Nothing left.
    return query select false, 'none'::text;
end;
$$ language plpgsql;
