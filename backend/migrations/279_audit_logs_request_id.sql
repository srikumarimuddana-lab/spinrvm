-- 279_audit_logs_request_id.sql
-- Corporate + admin portal review, round 2: "no correlation ID links an
-- admin action to the Sentry error or downstream row it caused." The
-- request-scoped correlation ID already exists everywhere else --
-- core/middleware.py's RequestIDMiddleware mints/reads X-Request-ID on
-- every request, binds it into loguru's context (so every log line in
-- that request already carries it), and utils/sentry_scrub.py already
-- promotes a "request_id" log-extra key onto Sentry event tags. The one
-- place it was never recorded is audit_logs itself -- the row that says
-- "admin X did Y to resource Z" had no way to be joined back to the
-- Sentry event or log lines from that same request.
--
-- Nullable, additive: existing rows get NULL (pre-dates this change; no
-- backfill possible, the original request is gone). New rows get it via
-- utils/audit_logger.py reading utils/log_context.py's request-scoped
-- ContextVar (populated by RequestIDMiddleware in the same commit).
--
-- Index: SOC/threat-hunting's stated use case is "take a Sentry event or
-- log line, find the admin action that caused it" -- a lookup by
-- request_id, hence the index rather than leaving it unindexed like a
-- free-text column.
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_audit_logs_request_id;
--   ALTER TABLE audit_logs DROP COLUMN IF EXISTS request_id;

ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS request_id TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_audit_logs_request_id ON audit_logs (request_id) WHERE request_id IS NOT NULL;
