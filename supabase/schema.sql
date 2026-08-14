-- AlgoPuzzle — billing schema
-- ============================================================
-- No Supabase Auth here on purpose. Users are identified by a signed,
-- httpOnly `device_id` cookie the backend issues on first contact (see
-- main.py's device-identity dependency) — there is no login screen.
-- This file is meant to be run once against a fresh Supabase Postgres
-- database (SQL Editor in the Supabase dashboard, or `psql "$DATABASE_URL"
-- -f supabase/schema.sql`).
--
-- Entitlement model (per device_id):
--   1. First 2 exports are free (free_exports_used counts up to
--      FREE_EXPORT_LIMIT, enforced in app code, not here).
--   2. Pay-per-export credits (5 zl each) - paid_export_credits counts
--      down.
--   3. A 30-day "unlimited" pass (30 zl) - pass_expires_at, checked
--      first since it's the best deal for the user once active.
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

-- One row per Stripe webhook event actually applied - the unique
-- constraint on stripe_event_id is what makes the webhook handler safe
-- to call twice with the same event (Stripe retries on timeout/5xx).
create table if not exists billing_events (
    id                       bigserial primary key,
    stripe_event_id          text not null unique,
    device_id                uuid references devices (device_id),
    event_type               text not null, -- 'export_credit_purchase' | 'day_pass_purchase'
    stripe_checkout_session_id text,
    amount_total_grosze      integer, -- Stripe amounts are in the smallest currency unit
    currency                 text,
    created_at                timestamptz not null default now()
);

-- Append-only log of every export actually delivered - lets you see
-- where a device's entitlement went (useful for support questions like
-- "I paid but it says I have 0 credits") and for basic abuse monitoring.
create table if not exists export_log (
    id            bigserial primary key,
    device_id     uuid not null references devices (device_id),
    platform      text not null, -- 'mt5' | 'mt4' | 'ctrader'
    consumed_from text not null, -- 'free' | 'credit' | 'pass'
    created_at    timestamptz not null default now()
);

create index if not exists idx_export_log_device on export_log (device_id, created_at desc);

-- ============================================================
-- consume_export_entitlement: the one place that decides "can this
-- device export right now" and atomically deducts the right bucket.
-- Row-locked (FOR UPDATE) so two near-simultaneous clicks from the same
-- device can't both slip through on the last free export.
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
    v_pass_exp      timestamptz;
    v_ip_free_count integer;
begin
    select free_exports_used, paid_export_credits, pass_expires_at
      into v_free_used, v_credits, v_pass_exp
      from devices
     where device_id = p_device_id
     for update;

    if not found then
        return query select false, 'none'::text;
        return;
    end if;

    -- 1) Active day-pass wins first - it's "unlimited", nothing to deduct.
    if v_pass_exp is not null and v_pass_exp > now() then
        insert into export_log (device_id, platform, consumed_from)
        values (p_device_id, p_platform, 'pass');
        update devices set last_seen_at = now() where device_id = p_device_id;
        return query select true, 'pass'::text;
        return;
    end if;

    -- 2) Free allowance - gated by both the device's own count AND the
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
        -- IP cap hit: fall through to credits/pass/none below instead of
        -- granting another free export, same as if v_free_used had
        -- already reached p_free_limit.
    end if;

    -- 3) Paid per-export credits.
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

    -- 4) Nothing left.
    return query select false, 'none'::text;
end;
$$ language plpgsql;
