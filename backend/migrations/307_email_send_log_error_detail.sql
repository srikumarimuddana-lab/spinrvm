-- 307_email_send_log_error_detail.sql
--
-- email_send_log records provider/status/message_id for every transactional
-- email attempt, but never the actual provider error string — so a
-- persistently-failing send (e.g. corporate portal OTP email, silently
-- failing since 2026-08-03 per email_send_log's own provider='none'/
-- status='failed' rows) has no diagnosable detail short of pulling live
-- application logs (Fly/Sentry), which aren't always available.
--
-- utils/email_provider.py's _try_ses/_try_resend already capture and
-- PIPEDA-redact the underlying exception (recipient address stripped out,
-- SES/Resend response bodies never logged raw — see their existing comments)
-- before logging it; this column lets that same already-sanitized string be
-- persisted instead of only reaching stdout/Sentry.
--
-- Append-only table (per CLAUDE.md migration conventions) — this is an
-- additive column, no backfill, no data mutation of existing rows.
--
-- rollback: ALTER TABLE public.email_send_log DROP COLUMN IF EXISTS error_detail;
--   Safe at any time — no other code reads this column, and it carries no
--   FK/index/constraint for anything else to depend on.

ALTER TABLE public.email_send_log
    ADD COLUMN IF NOT EXISTS error_detail TEXT;

COMMENT ON COLUMN public.email_send_log.error_detail IS
    'Best-effort, PIPEDA-redacted provider error string for a failed/degraded '
    'send (e.g. SES MessageRejected, Resend non-2xx status, or an app_settings '
    'load failure). NULL for a successful send or a send skipped for a reason '
    'that is not itself an error (suppressed). Populated by '
    'utils/email_provider.py; never contains the recipient address.';
