-- ============================================================
-- ONE-TIME MIGRATION - RUN EXACTLY ONCE, IN THIS ORDER.
-- Paste into Supabase -> SQL Editor. Do NOT fold this into schema.sql.
--
-- Why order matters: this marks every account that has ALREADY clicked its
-- confirmation link as verified, using auth.users.email_confirmed_at. That
-- column is only trustworthy while Supabase's "Confirm email" setting is
-- still ON. The moment you switch it off, Supabase stamps email_confirmed_at
-- on every new signup automatically, and running step 2 afterwards would
-- wrongly mark all of those as verified.
--
--   STEP 1  (this file, 1a + 1b)   run the SQL below
--   STEP 2  Supabase -> Authentication -> Email Templates -> "Magic Link":
--           add the line   Your code: {{ .Token }}
--           (without it the purchase-time email only contains a link, and
--           the page asks for a code that is not in the email)
--   STEP 3  Supabase -> Authentication -> Sign In / Providers -> Email
--           -> turn OFF "Confirm email"
--   STEP 3a (optional, 3a below)   let the accounts that never confirmed log in
--   STEP 4  Configure a custom SMTP sender in Supabase (the built-in mail has
--           a very low hourly limit); the code email depends on it.
--   STEP 5  LAST: set REQUIRE_VERIFIED_EMAIL=1 on the Render API service (and
--           redeploy). Until this is set the purchase gate is OFF and checkout
--           behaves exactly as it did before this feature existed, so nothing
--           above can lock a buyer out while it is half done.
-- ============================================================

-- 1a) the column (same statement as in schema.sql, harmless to repeat)
alter table user_entitlements add column if not exists email_verified_at timestamptz;

-- 1b) accounts that already confirmed by link keep counting as verified
update user_entitlements ue
   set email_verified_at = au.email_confirmed_at
  from auth.users au
 where au.id = ue.user_id
   and au.email_confirmed_at is not null
   and ue.email_verified_at is null;

-- Sanity check, expect verified = number of previously confirmed accounts:
-- select count(*) filter (where email_verified_at is not null) as verified,
--        count(*) as total from user_entitlements;

-- 3a) OPTIONAL, only AFTER step 2. Accounts created while confirmation was
-- required and never confirmed are still refused at login ("Email not
-- confirmed") even with the setting off. This lets them in. They are NOT
-- marked verified, so they still have to enter a code before buying.
-- update auth.users set email_confirmed_at = now() where email_confirmed_at is null;
