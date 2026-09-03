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
-- Entitlement model (checked in this order, see _require_export_entitlement
-- in main.py):
--   1. Logged-in user with active pro (user_entitlements.is_pro, see the
--      "Manual pro-status + email tracking" section below) - works from
--      any device once logged in. True either from a purchased 30-day
--      pass or a manual grant (e.g. the owner's own account).
--   2. Logged-in user with account-level exports_available > 0 (same
--      table) - a separate, additive bucket, manually grantable or
--      credited by a single-export purchase made while logged in.
--   3. Per-device_id: first 2 exports free (free_exports_used, capped at
--      FREE_EXPORT_LIMIT), then pay-per-export credits (5 zl each,
--      paid_export_credits counts down). Both anonymous, one browser -
--      completely untouched by steps 1-2, so nothing about being
--      logged out ever gets worse.
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
-- is_pro/is_pro_until/email/exports_available (2026-08-29 addition, see
-- the "Manual pro-status + email tracking" section further down) are
-- declared directly here so a from-scratch install matches the live
-- schema in one place - that section's ALTER/rename statements are what
-- actually bring an existing production table up to this shape.
create table if not exists user_entitlements (
    user_id       uuid primary key references auth.users (id) on delete cascade,
    -- Manual on/off switch, e.g. for the owner's own account or a future
    -- comped account - see the "Manual pro-status + email tracking"
    -- section for exactly how this interacts with is_pro_until.
    is_pro         boolean not null default false,
    is_pro_until   timestamptz,
    -- Login email, denormalized from auth.users (kept in sync by trigger)
    -- so this table is browsable/editable in the Supabase Table Editor
    -- without joining to auth.users.
    email          text,
    -- Additive bucket of exports for THIS account specifically - manually
    -- grantable, and what a single-export purchase credits when the
    -- buyer is logged in. Independent of devices.paid_export_credits.
    exports_available integer not null default 0,
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
    amount_total_cents       integer, -- Stripe amounts are in the smallest currency unit
    currency                 text,
    created_at                timestamptz not null default now()
);

-- Pricing moved from PLN to USD (2026-08-14 decision) - renames the
-- column on any database that already has it under the old name from
-- before that switch. No-op (and safe to re-run) once it's done.
do $$
begin
    if exists (
        select 1 from information_schema.columns
         where table_name = 'billing_events' and column_name = 'amount_total_grosze'
    ) then
        alter table billing_events rename column amount_total_grosze to amount_total_cents;
    end if;
end $$;

-- BUG FIX (found 2026-08-29, live in production until this line): billing_events
-- already existed in production from before user_id was added to this table's
-- CREATE TABLE above (for day-pass purchases, logged-in) - same gap as
-- export_log's strategy_meta below, just never given the matching ALTER TABLE.
-- Concretely: EVERY real Stripe webhook for a day-pass purchase, and every
-- logged-in single-export-credit purchase (2026-08-29 addition), was hitting
-- "column user_id of relation billing_events does not exist" and rolling back
-- the WHOLE grant (see db.py's _conn() - one failed statement rolls back the
-- entire transaction, including the user_entitlements upsert that ran first)
-- - i.e. these purchases silently never actually got granted. This is what
-- actually adds the column on a database that predates it.
alter table billing_events add column if not exists user_id uuid references auth.users (id);

-- ============================================================
-- Marketplace strategy purchases (2026-08-31 addition) - a third product
-- line alongside export credits / the 30-day pass above: buying one of the
-- rotating "Strategy of the Week" strategies (marketplace_strategies.py on
-- the backend, MARKETPLACE_STRATEGIES in assets/marketplace-data.js on the
-- frontend) for a one-time price, yours forever. Same device_id-primary/
-- user_id-optional shape as export-credit purchases (NOT the pass's
-- login-required model) - a single strategy buy should never be gated
-- behind creating an account. See billing.create_checkout_session(kind=
-- "strategy_purchase"), db.grant_strategy_purchase/get_owned_strategy_ids/
-- has_purchased_strategy, and main.py's POST /api/billing/checkout/strategy
-- + GET /api/marketplace/purchases.
--
-- Deliberately NOT its own table: ownership is only ever checked as "does
-- a purchase event exist for this device/user + strategy_id", never
-- counted or decremented like paid_export_credits/exports_available are -
-- so billing_events (already the append-only ledger for the two purchase
-- types above) doubles as the source of truth here too, one less table to
-- keep in sync. strategy_id is free text, not a foreign key - the
-- strategy catalog lives in Python/JS, not its own DB table.
alter table billing_events add column if not exists strategy_id text;
create index if not exists idx_billing_events_strategy_lookup
    on billing_events (strategy_id) where strategy_id is not null;

-- Buyer's email, captured from Stripe Checkout same as devices.billing_email
-- / user_entitlements.billing_email are for the other two purchase types -
-- there's no per-buyer entitlement row for strategy purchases to hang this
-- off of (see above), so it lives directly on the event row instead.
-- Nullable and only ever populated for event_type = 'strategy_purchase'
-- as of this writing; general-purpose enough to reuse for the other two
-- event types later if that's ever useful.
alter table billing_events add column if not exists email text;

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
    -- What the exported strategy actually contained (2026-08-15 addition -
    -- see main.py's _summarize_strategy()) - assets/timeframes/indicators
    -- used, rule count, whether SL/TP were set, etc. Always derived
    -- SERVER-SIDE from the already-parsed, already-validated StrategyIR,
    -- never taken as-is from the client, so this can't be spoofed by
    -- editing the request body. Nullable only so old rows from before
    -- this column existed don't need backfilling. Only takes effect on a
    -- FRESH database via this CREATE TABLE - export_log already existed
    -- in production, so the explicit ALTER TABLE further down is what
    -- actually adds it there; this definition just keeps a from-scratch
    -- install matching the live schema in one place.
    -- Shape (all keys always present once populated):
    --   {"assets": ["EURUSD", ...], "timeframes": ["PERIOD_M15", ...],
    --    "rule_count": 2, "indicator_kinds": ["RSI", "MA", ...],
    --    "directions": ["BUY", "SELL"], "uses_sl": true, "uses_tp": true,
    --    "max_positions": [1, 3]}
    strategy_meta jsonb,
    created_at    timestamptz not null default now()
);

-- export_log already existed in production before strategy_meta was added
-- (2026-08-15) - `create table if not exists` above is a no-op against an
-- existing table, it does NOT add missing columns to it, so this table
-- being on either a fresh database or the live one both need this
-- explicit, idempotent ADD COLUMN (same reasoning as the rename above).
-- Without it, the very first export after deploying the code that writes
-- to this column would fail outright with "column does not exist".
alter table export_log add column if not exists strategy_meta jsonb;

-- BUG FIX (found 2026-08-29, live in production until this line): user_id
-- had the exact same gap as strategy_meta above - present in the CREATE TABLE
-- (added alongside the pass feature, 2026-08-14/15) but never given its own
-- ALTER TABLE for the production table that already existed by then.
-- Concretely: EVERY export by a logged-in user with an active pass
-- (log_user_pass_export -> consumed_from='pass') or an account-level export
-- credit (consume_account_export_credit -> consumed_from='account_credit',
-- 2026-08-29 addition) was hitting "column user_id of relation export_log
-- does not exist" and raising a 500 - anonymous/device-only exports were
-- never affected, which is why this went unnoticed until someone with a
-- real pass tried to export.
alter table export_log add column if not exists user_id uuid references auth.users (id);

create index if not exists idx_export_log_device on export_log (device_id, created_at desc);
-- Powers "which assets do exports actually target" without a jsonb scan
-- of the whole table every time - see the export_asset_popularity view
-- below, which is what actually reads this.
create index if not exists idx_export_log_strategy_meta on export_log using gin (strategy_meta);

-- ============================================================
-- General site-behavior event log (2026-08-15 addition) - a single
-- append-only table for product/conversion questions beyond exports
-- alone: did the paywall get shown before checkout started, how often
-- is a strategy actually saved, where does signup drop off. NOT a full
-- session-replay/heatmap tool - just enough structured events for real
-- product decisions, cheap to query.
--
-- Identity follows the same model as the rest of this schema: anonymous
-- device_id everywhere (every visitor gets one, see device_identity.py),
-- user_id only once actually logged in. Nothing new invented here.
--
-- event_type is a free-text column in the table itself, but the API
-- layer (see ALLOWED_ANALYTICS_EVENTS in main.py) only ever inserts one
-- of a fixed, reviewed allowlist - so this can't silently fill up with
-- arbitrary junk event names from a buggy or malicious client. metadata
-- is a small jsonb blob whose shape depends on event_type (see that same
-- allowlist for what each one is expected to carry).
-- ============================================================
create table if not exists analytics_events (
    id          bigserial primary key,
    device_id   uuid references devices (device_id),
    user_id     uuid references auth.users (id),
    event_type  text not null,
    metadata    jsonb,
    path        text, -- which page fired this, e.g. '/index_1.html' - useful once landing-page events are added too, not just the builder
    created_at  timestamptz not null default now()
);

create index if not exists idx_analytics_events_type_time on analytics_events (event_type, created_at desc);
create index if not exists idx_analytics_events_device on analytics_events (device_id, created_at desc);

-- ============================================================
-- Convenience views - the two questions this was actually requested for
-- ("what asset did the user pick", "full visibility into on-site
-- behaviour") answered directly in the Supabase Table Editor / SQL
-- Editor with no query-writing needed.
-- ============================================================

-- One row per (asset, day) - unnests strategy_meta->'assets' since a
-- single export can name more than one (multi-rule strategies). Ordered
-- for "what are people actually building" at a glance.
create or replace view export_asset_popularity as
select
    asset,
    count(*) as export_count,
    max(created_at) as last_exported_at
from export_log, jsonb_array_elements_text(coalesce(strategy_meta -> 'assets', '[]'::jsonb)) as asset
group by asset
order by export_count desc;

-- One row per (event_type, day) - the fastest way to eyeball a funnel
-- (e.g. paywall_shown vs. checkout_started vs. export_succeeded on the
-- same day) without hand-writing the group-by every time.
create or replace view analytics_daily_summary as
select
    date_trunc('day', created_at) as day,
    event_type,
    count(*) as event_count,
    count(distinct device_id) as distinct_devices
from analytics_events
group by 1, 2
order by 1 desc, 3 desc;

-- has_active_pass() used to be defined here, checking pass_expires_at
-- directly. It's now defined further down (2026-08-29 addition, see the
-- "Manual pro-status + email tracking" section), after is_pro/
-- is_pro_until exist, since it now also accounts for manual pro grants.

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
-- Manual pro-status + email tracking (2026-08-29 addition).
--
-- user_entitlements grows from "one row per past pass-buyer" into "one
-- row per account, period" - every signup gets a row from now on (see
-- the auth.users trigger below), carrying its login email so this table
-- can be browsed/edited directly in the Supabase Table Editor without
-- joining to auth.users.
--
-- is_pro is a manual on/off switch, is_pro_until an optional expiry:
--   - is_pro = true, is_pro_until = null   -> pro forever (e.g. the
--     owner's own account, flipped by hand for demo/promo purposes).
--   - is_pro = true, is_pro_until = <date> -> pro until that date, then
--     automatically not-pro again with nobody having to flip anything
--     back by hand. This is exactly how a purchased 30-day pass works
--     (grant_day_pass sets both).
-- has_active_pass() below is the ONLY place this is evaluated - nothing
-- else should read is_pro directly.
--
-- exports_available is a separate, additive bucket of exports for a
-- LOGGED-IN account specifically (manually grantable, and what a
-- single-export purchase credits when the buyer is logged in - see
-- grant_export_credits in db.py) - it does not touch or replace the
-- anonymous per-device free/paid counting in `devices`, which keeps
-- working exactly as before for anyone not logged in.
--
-- The table's own CREATE TABLE (further up) already declares these
-- columns for a from-scratch install - everything below this point is
-- what actually migrates an existing production table into that shape.
-- ============================================================

-- 1) pass_expires_at -> is_pro_until (same column, clearer name now that
-- it's driven by manual grants too, not only purchased passes). No-op on
-- a fresh install, where the column is already named is_pro_until.
do $$
begin
    if exists (
        select 1 from information_schema.columns
         where table_name = 'user_entitlements' and column_name = 'pass_expires_at'
    ) then
        alter table user_entitlements rename column pass_expires_at to is_pro_until;
    end if;
end $$;

alter table user_entitlements add column if not exists is_pro boolean not null default false;
alter table user_entitlements add column if not exists email text;
alter table user_entitlements add column if not exists exports_available integer not null default 0;

-- Backfill is_pro for any row that already has an expiry date set (i.e.
-- previously bought a pass, even a since-expired one) - this restates
-- exactly what has_active_pass() already computed from that date alone,
-- so it grants nothing new; it just makes is_pro consistent with
-- is_pro_until for rows that predate this column.
update user_entitlements set is_pro = true where is_pro_until is not null and not is_pro;

-- Backfill email from auth.users for rows that predate this column.
update user_entitlements ue set email = au.email
  from auth.users au
 where au.id = ue.user_id and ue.email is null;

-- 2) Auto-create a row for every signup (not just pass buyers), so this
-- table becomes the single place with every account's email. Standard
-- Supabase pattern: security definer, since triggers on auth.users need
-- elevated privilege to write into the public schema.
create or replace function public.handle_new_auth_user() returns trigger
  security definer set search_path = public
  language plpgsql as $$
begin
    insert into public.user_entitlements (user_id, email)
    values (new.id, new.email)
    on conflict (user_id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_auth_user();

-- Keeps user_entitlements.email in sync if a trader ever changes their
-- login email - otherwise this table would silently go stale for them.
create or replace function public.handle_auth_user_email_change() returns trigger
  security definer set search_path = public
  language plpgsql as $$
begin
    if new.email is distinct from old.email then
        update public.user_entitlements set email = new.email where user_id = new.id;
    end if;
    return new;
end;
$$;

drop trigger if exists on_auth_user_email_updated on auth.users;
create trigger on_auth_user_email_updated
    after update of email on auth.users
    for each row execute function public.handle_auth_user_email_change();

-- One-time backfill: every account that exists TODAY (not just past
-- buyers, and not only new signups going forward) gets a row immediately.
insert into user_entitlements (user_id, email)
select id, email from auth.users
on conflict (user_id) do nothing;

-- ============================================================
-- has_active_pass: does this logged-in user currently have pro access
-- right now - whether from a manual grant or a purchased pass? No
-- row-locking needed (unlike consume_export_entitlement/
-- consume_account_export_credit below) - this is a read, there's no
-- shared counter to race on. main.py calls this FIRST, before ever
-- touching device_id-based or account-credit-based entitlement, whenever
-- the request carries a valid Supabase Auth token - see
-- get_current_user() in main.py.
-- (Replaces the earlier pass_expires_at-only version of this function -
-- `create or replace` below is enough, no drop needed since the
-- signature is unchanged.)
-- ============================================================
create or replace function has_active_pass(p_user_id uuid) returns boolean as $$
    select exists(
        select 1 from user_entitlements
         where user_id = p_user_id
           and is_pro
           and (is_pro_until is null or is_pro_until > now())
    );
$$ language sql stable;

-- ============================================================
-- ACCOUNT-BASED EXPORTS (2026-09-03) - every export now requires a
-- logged-in account, free ones included, and the free allowance is
-- counted PER ACCOUNT rather than per device (explicit product
-- decision: an account per exporter is worth more than the friction it
-- costs, and a device_id cookie resets to a fresh free allowance the
-- moment it's cleared, which an account does not).
--
-- free_exports_used is the account's own counter, the direct
-- counterpart of devices.free_exports_used - which, along with the
-- whole anonymous per-device flow below, is now DEAD for exports. Both
-- the column and consume_export_entitlement() are deliberately left in
-- place rather than dropped: they still hold the real history of every
-- export delivered before this change, and nothing is gained by
-- destroying that.
-- ============================================================
alter table user_entitlements add column if not exists free_exports_used integer not null default 0;

-- ============================================================
-- consume_account_export: the one place that decides "can this ACCOUNT
-- export right now", and atomically deducts the right bucket. Replaces
-- consume_account_export_credit(), which only knew about purchased
-- credits - the free allowance lived on the device back then. Returns
-- (granted, consumed_from) to match consume_export_entitlement's shape;
-- consumed_from is 'free' | 'account_credit' when granted, 'none' when
-- the account is out of everything (the app should 402).
--
-- An active pro grant is still checked separately, BEFORE this is ever
-- called (has_active_pass above) - pro is unlimited, so there's nothing
-- here to deduct for it.
--
-- Free allowance is spent BEFORE purchased credits on purpose: same
-- total either way, but it leaves the credit the trader actually paid
-- for available for whenever they need it, instead of silently burning
-- it while a free one was still sitting there.
--
-- Row-locked (FOR UPDATE) so two near-simultaneous exports from the
-- same account can't both slip through on the last free one.
--
-- p_device_id is recorded but never consulted: export_log.device_id is
-- NOT NULL and simply says which browser the file was delivered to, not
-- which entitlement paid for it.
-- ============================================================
drop function if exists consume_account_export_credit(uuid, uuid, text, jsonb);

create or replace function consume_account_export(
    p_user_id uuid,
    p_device_id uuid,
    p_platform text,
    p_free_limit integer default 2,
    p_strategy_meta jsonb default null
) returns table(granted boolean, consumed_from text) as $$
declare
    v_credits   integer;
    v_free_used integer;
begin
    -- Belt-and-suspenders: the auth.users trigger above already creates a
    -- row per signup, but a missing row must not read as "out of free
    -- exports" for someone who has never exported at all.
    insert into user_entitlements (user_id) values (p_user_id)
    on conflict (user_id) do nothing;

    select exports_available, free_exports_used
      into v_credits, v_free_used
      from user_entitlements
     where user_id = p_user_id
     for update;

    -- 1) The account's free allowance.
    if v_free_used < p_free_limit then
        update user_entitlements
           set free_exports_used = free_exports_used + 1
         where user_id = p_user_id;
        insert into export_log (device_id, user_id, platform, consumed_from, strategy_meta)
        values (p_device_id, p_user_id, p_platform, 'free', p_strategy_meta);
        return query select true, 'free'::text;
        return;
    end if;

    -- 2) Purchased / manually granted account credits.
    if v_credits > 0 then
        update user_entitlements
           set exports_available = exports_available - 1
         where user_id = p_user_id;
        insert into export_log (device_id, user_id, platform, consumed_from, strategy_meta)
        values (p_device_id, p_user_id, p_platform, 'account_credit', p_strategy_meta);
        return query select true, 'account_credit'::text;
        return;
    end if;

    -- 3) Nothing left.
    return query select false, 'none'::text;
end;
$$ language plpgsql;

create or replace function consume_export_entitlement(
    p_device_id uuid,
    p_platform text,
    p_free_limit integer default 2,
    p_ip_hash text default null,
    p_ip_free_limit integer default 15,
    p_ip_window_hours integer default 24,
    p_strategy_meta jsonb default null
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
            insert into export_log (device_id, platform, consumed_from, strategy_meta)
            values (p_device_id, p_platform, 'free', p_strategy_meta);
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
        insert into export_log (device_id, platform, consumed_from, strategy_meta)
        values (p_device_id, p_platform, 'credit', p_strategy_meta);
        return query select true, 'credit'::text;
        return;
    end if;

    -- 3) Nothing left.
    return query select false, 'none'::text;
end;
$$ language plpgsql;
