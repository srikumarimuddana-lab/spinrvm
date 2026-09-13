-- 419: 7 SettingsUpdateRequest fields with no `settings`-table column anywhere
-- in this repo's tracked schema -- found while closing ACTION_ITEMS.md C110
-- (generalizing test_settings_column_parity.py's regression check to every
-- field, not just migration 313's original 24) and confirmed by
-- spinr-test-coverage-reviewer's adversarial pass on that fix.
--
-- Unlike the other 15 fields the C110 fix baselined as pre-migration-tracking
-- (confirmed present in backend/supabase_schema.sql's bootstrap
-- `CREATE TABLE settings (...)` block), these 7 appear in NEITHER
-- supabase_schema.sql NOR any file under backend/migrations/ -- there is no
-- tracked evidence the `settings` table has ever had these columns:
--   company_app_download_url, safety_team_email, safety_team_phone,
--   sos_show_share_trip, sos_show_report_issue, new_ride_requests_enabled,
--   dispute_stripe_evidence_submission_enabled
--
-- Two of these are kill switches (new_ride_requests_enabled,
-- dispute_stripe_evidence_submission_enabled), and both of those fields' own
-- Change Impact Logs (docs/change-log/2026-08-22-g5-new-ride-requests-kill-switch.md
-- section 10, docs/change-log/2026-08-18-c23-dispute-evidence-pack-and-submission.md)
-- explicitly say they were never exercised against a real Supabase `settings`
-- row -- exactly the class of gap migration 313/415/418 exist to close before
-- someone hits it for the first time during an incident (313's own header:
-- "the 500 fires exactly when someone first tries to change one"). Rather
-- than leave that open or guess at the answer, this migration adds the
-- columns outright -- the direct fix, not another paper-only allowlist entry.
--
-- Defaults chosen to match each field's existing Python-level fallback
-- (schemas.py's AppSettings / the call sites reading settings.get(...)), so
-- applying this migration changes no live behavior:
--   new_ride_requests_enabled       -> TRUE  (kill switch; must default running,
--                                              same rule as 313's surge/dispatch
--                                              switches -- see schemas.py:403)
--   dispute_stripe_evidence_submission_enabled -> FALSE (opt-in, ships dark --
--                                              routes/admin/dispute_evidence_submission.py's
--                                              own header: "default false/unset")
--   sos_show_share_trip             -> TRUE  (routes/settings.py:84's .get(..., True))
--   sos_show_report_issue           -> TRUE  (routes/settings.py:85's .get(..., True))
--   company_app_download_url        -> ''    (schemas.py:454)
--   safety_team_email               -> ''    (routes/settings.py:78's .get(..., ""))
--   safety_team_phone               -> ''    (routes/settings.py:79's .get(..., ""))
--
-- Rollback:
--   ALTER TABLE public.settings
--     DROP COLUMN IF EXISTS company_app_download_url,
--     DROP COLUMN IF EXISTS safety_team_email,
--     DROP COLUMN IF EXISTS safety_team_phone,
--     DROP COLUMN IF EXISTS sos_show_share_trip,
--     DROP COLUMN IF EXISTS sos_show_report_issue,
--     DROP COLUMN IF EXISTS new_ride_requests_enabled,
--     DROP COLUMN IF EXISTS dispute_stripe_evidence_submission_enabled;
--
-- Forward-compatible: additive defaulted columns; older backends ignore them.

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS company_app_download_url TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS safety_team_email TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS safety_team_phone TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS sos_show_share_trip BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS sos_show_report_issue BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS new_ride_requests_enabled BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS dispute_stripe_evidence_submission_enabled BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN public.settings.company_app_download_url IS
    'App store / download link surfaced in transactional emails (utils/rider_emails.py, utils/company_details.py). Empty = CTA omitted.';
COMMENT ON COLUMN public.settings.safety_team_email IS
    'Safety team contact email shown per routes/settings.py. Empty = not configured.';
COMMENT ON COLUMN public.settings.safety_team_phone IS
    'Safety team contact phone shown per routes/settings.py. Empty = not configured.';
COMMENT ON COLUMN public.settings.sos_show_share_trip IS
    'Whether the SOS screen offers "share trip". Default true (matches routes/settings.py''s existing fallback).';
COMMENT ON COLUMN public.settings.sos_show_report_issue IS
    'Whether the SOS screen offers "report issue". Default true (matches routes/settings.py''s existing fallback).';
COMMENT ON COLUMN public.settings.new_ride_requests_enabled IS
    'Kill switch: false stops new ride bookings (routes/rides -- create_ride). Default true so applying this migration cannot silently stop live bookings.';
COMMENT ON COLUMN public.settings.dispute_stripe_evidence_submission_enabled IS
    'Dark-launch gate for admin-triggered Stripe dispute-evidence submission (routes/admin/dispute_evidence_submission.py). Default false -- ships dark, flip on after staging verification.';
