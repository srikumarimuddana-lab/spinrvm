-- Migration 25: refresh_tokens + token_version (audit P0-S3)
--
-- Until now:
--   • Rider/driver access tokens had a hardcoded 30-day TTL (see
--     backend/dependencies.py::create_jwt_token) and there was no way
--     to revoke one once issued — a leaked token granted 30 days of
--     unauthorised access.
--   • Admin tokens had NO expiry at all (see routes/admin/auth.py —
--     jwt.encode without an exp claim).
--   • The "single device login" mechanism (users.current_session_id)
--     only protected rider/driver tokens and was bypassed the moment
--     a second device logged in; it offered no admin-initiated kill.
--
-- This migration introduces two independent revocation primitives:
--
--   1. refresh_tokens — one row per active refresh token, stored as a
--      sha256 hash (never the plaintext). A /auth/refresh call rotates
--      the row (revokes the old, inserts a new, chains via replaced_by
--      so we can detect stolen-and-replayed tokens). /auth/logout
--      stamps revoked_at.
--
--   2. token_version — a bump counter on users and admin_staff. The
--      access token carries the token_version at issue time; the
--      middleware rejects the token if current_version > issued_version.
--      This gives admins a "force-logout-all-sessions" primitive with
--      one UPDATE, without touching the refresh_tokens table.
--
-- Deployment note: existing in-flight access tokens carry no
-- token_version claim; the middleware treats a missing claim as 0,
-- which matches the default value below, so they keep working until
-- they expire naturally. Bumping a user's token_version to 1 is what
-- kills the outstanding tokens.

-- ── refresh_tokens ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       TEXT NOT NULL,
    -- SHA-256 hex of the raw refresh token. 64 chars, UNIQUE so a
    -- collision is a programmer bug, not an attack.
    token_hash    TEXT NOT NULL UNIQUE,
    -- "rider" | "admin" — refresh tokens are scoped to the auth
    -- context they were minted in, so a leaked rider refresh token
    -- can't be exchanged for an admin access token and vice versa.
    audience      TEXT NOT NULL DEFAULT 'rider',
    issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ,
    -- When a refresh succeeds we revoke the old row and INSERT a new
    -- one; replaced_by points at the successor so we can reconstruct
    -- the chain during incident response. A re-use of an already-
    -- replaced token indicates theft and should trigger a cascade
    -- revocation of the whole chain.
    replaced_by   UUID REFERENCES refresh_tokens(id) ON DELETE SET NULL,
    -- Forensic context. ip is stored as text (not inet) because we
    -- take it from X-Forwarded-For which is client-controlled.
    user_agent    TEXT,
    ip            TEXT
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user
    ON refresh_tokens (user_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires
    ON refresh_tokens (expires_at)
    WHERE revoked_at IS NULL;

ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY;

-- ── token_version ────────────────────────────────────────────────
-- Add to users if missing. Default 0 so the middleware comparison
-- (payload.token_version ?? 0) == (row.token_version ?? 0) is true
-- for every token issued before this migration.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

-- admin_staff uses its own row set; same pattern.
ALTER TABLE admin_staff
    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;
