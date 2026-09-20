-- ============================================================
-- CLEANUP of analytics written by LOCAL testing (2026-09-20).
-- Run it yourself in Supabase -> SQL Editor, ONE STEP AT A TIME.
-- Nothing here was run by Claude. Steps 1 and 2 only read.
--
-- What happened: a local backend reads .env, and .env's DATABASE_URL is the
-- production project. Every click made while testing the first-visit guide on
-- localhost was written to analytics_events, including event types that the
-- deployed site has never had. The code is now guarded (assets/analytics.js,
-- index_1.html and main.py::_is_dev_request skip analytics from localhost),
-- so this is a one-off tidy-up, not something to repeat.
--
-- Why this is safe to delete: the deployed backend does not allow these nine
-- event types at all, so no real visitor can have produced them. Any device
-- that fired one is a development browser, and everything else that device
-- did today (builder_opened, new_strategy_started, ...) is test noise too.
-- ============================================================

-- STEP 1 (read only): the rows that cannot be real traffic.
select event_type, count(*) as row_count,
       min(created_at) as first_seen, max(created_at) as last_seen
from analytics_events
where event_type in (
  'guide_started', 'guide_step_done', 'guide_completed', 'guide_dismissed',
  'onboarding_shown', 'example_loaded', 'export_clicked',
  'verify_email_shown', 'verify_email_completed'
)
group by 1
order by 2 desc;

-- STEP 2 (read only): every device that fired one of them, and what ELSE it
-- recorded. Expect one or two devices with a large number of events. If a
-- device here is one you do not recognise as your own testing, stop and look.
with dev as (
  select distinct device_id from analytics_events
  where event_type in (
    'guide_started', 'guide_step_done', 'guide_completed', 'guide_dismissed',
    'onboarding_shown', 'example_loaded', 'export_clicked',
    'verify_email_shown', 'verify_email_completed'
  )
)
select a.device_id, count(*) as events,
       count(*) filter (where a.event_type in ('builder_opened', 'new_strategy_started', 'strategy_saved',
                                               'paywall_shown', 'auth_modal_shown')) as real_looking_events,
       min(a.created_at) as first_seen, max(a.created_at) as last_seen
from analytics_events a join dev using (device_id)
group by 1
order by 2 desc;

-- STEP 3 (WRITES): delete every analytics event of those devices from today.
-- The date bound is the moment the local backend was started; it keeps this
-- from ever touching an older row. It prints how many rows it removed.
with dev as (
  select distinct device_id from analytics_events
  where event_type in (
    'guide_started', 'guide_step_done', 'guide_completed', 'guide_dismissed',
    'onboarding_shown', 'example_loaded', 'export_clicked',
    'verify_email_shown', 'verify_email_completed'
  )
),
removed as (
  delete from analytics_events
  where device_id in (select device_id from dev)
    and created_at >= '2026-09-20 12:00:00+00'
  returning 1
)
select count(*) as analytics_rows_deleted from removed;

-- STEP 4 (WRITES, optional): the test browser also created rows in `devices`
-- ("new visitors"). Remove those that nothing else refers to. Run STEP 2's
-- device ids through this only if the count in step 3 looked right.
delete from devices d
where d.created_at >= '2026-09-20 12:00:00+00'
  and not exists (select 1 from analytics_events x where x.device_id = d.device_id)
  and not exists (select 1 from export_log x where x.device_id = d.device_id)
  and not exists (select 1 from strategy_save_log x where x.device_id = d.device_id)
  and not exists (select 1 from strategy_of_the_week_downloads x where x.device_id = d.device_id)
  and not exists (select 1 from billing_events x where x.device_id = d.device_id)
  -- only devices with no event left at all AND created during the test window;
  -- a real visitor from that window would still have analytics rows, so would
  -- not match. Check the rows first:  select * from devices where created_at >= '2026-09-20 12:00:00+00';
  ;
