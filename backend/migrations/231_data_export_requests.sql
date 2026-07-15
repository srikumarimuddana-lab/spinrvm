-- 231_data_export_requests.sql
--
-- PIPEDA DSAR (Data Subject Access Request) queue. Backs POST /users/data-export
-- ("Download my data" in the rider/driver apps) and the admin DSAR endpoints
-- (GET /admin/dsars, PATCH /admin/dsars/{id}/status). The table was referenced
-- by the code (routes/users.py, routes/admin/users.py) but never created, so the
-- export request failed with "could not find table public.data_export_requests".
--
-- PIPEDA s.9 requires a response within 30 days of receipt; response_due_at is
-- stamped at request time (requested_at + 30 days) so admins can prioritise
-- rows approaching or past their deadline.
--
-- Columns:
--   • id               UUID        — request id (PK; app supplies a uuid)
--   • user_id          TEXT        — the requesting user (id only; matches the
--                                    repo's TEXT user_id convention)
--   • status           TEXT        — pending | in_progress | completed | rejected
--   • requested_at     TIMESTAMPTZ — when the request was submitted
--   • response_due_at  TIMESTAMPTZ — PIPEDA 30-day SLA deadline (ordered on)
--   • updated_at       TIMESTAMPTZ — last admin status change (nullable)
--   • completed_at     TIMESTAMPTZ — when marked completed (nullable)
--
-- PIPEDA-safe: stores the acting user id + request status/timestamps only. Do
-- NOT add the exported payload or any PII here — the export itself is generated
-- and delivered out-of-band; this table only tracks the request lifecycle.
--
-- Forward-compatible: brand-new table, no change to any hot-path schema. The
-- DSAR write is best-effort behind a try/except in the route.
--
-- Rollback:
--   DROP TABLE IF EXISTS data_export_requests;

CREATE TABLE IF NOT EXISTS data_export_requests (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'completed', 'rejected')),
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    response_due_at  TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);

-- Admin queue: filter by status, ordered by the SLA deadline ascending
-- (GET /admin/dsars uses order="response_due_at"). Composite covers both the
-- status-filtered and the unfiltered (deadline-only) scans.
CREATE INDEX IF NOT EXISTS idx_data_export_requests_status_due
    ON data_export_requests (status, response_due_at);

-- Per-user lookups (a user's own DSAR history).
CREATE INDEX IF NOT EXISTS idx_data_export_requests_user
    ON data_export_requests (user_id, requested_at DESC);

-- RLS: this DSAR table is exclusively backend-written/read — POST /users/data-export
-- (insert) and the admin DSAR endpoints all go through the backend service role,
-- which bypasses RLS. The frontend anon/authenticated key never touches it
-- directly. Enable RLS with NO client policies so anon/authenticated are denied
-- entirely — the same "service role only" posture as the sibling DSAR table
-- 200_data_export_objects.sql (and 226_price_searches.sql / 220_corporate_email_otp_records.sql).
-- This also keeps the request lifecycle (response_due_at SLA, audit log) owned
-- solely by the route rather than lettable via a direct client insert.
ALTER TABLE data_export_requests ENABLE ROW LEVEL SECURITY;
