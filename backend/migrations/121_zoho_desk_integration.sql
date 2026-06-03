-- 121_zoho_desk_integration.sql
--
-- Rollback:
--   DROP TABLE IF EXISTS zoho_desk_config;
--
-- Purpose: Zoho Desk admin integration. Stores the single-row OAuth2
-- connection config + cached access token for the Zoho Desk REST API.
-- Tickets are NOT mirrored locally — the admin panel proxies the Zoho
-- Desk API live through the backend. Only the OAuth connection state
-- lives here so it survives restarts and is shared across replicas.
--
-- Security: this table holds OAuth client secrets + refresh/access tokens.
-- It is backend-only. RLS is ENABLED with NO policies, so the anon and
-- authenticated PostgREST roles are denied all access; the backend's
-- service-role key bypasses RLS by design (same model as other
-- credential-bearing tables). Never expose this table to the frontend.
--
-- Forward-compatible: single-row table, created idempotently. Safe to run
-- against live traffic (no locks on existing hot tables).

CREATE TABLE IF NOT EXISTS zoho_desk_config (
    -- Single logical row; pinned id so upserts are deterministic.
    id                       TEXT        PRIMARY KEY DEFAULT 'default',
    enabled                  BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Zoho data center code: 'com' (US), 'ca' (Canada), 'eu', 'in',
    -- 'com.au', 'jp'. Selects the accounts + desk API domains. Spinr is
    -- Canadian, so 'ca' (zohocloud.ca) is the expected production value.
    data_center              TEXT        NOT NULL DEFAULT 'ca',
    org_id                   TEXT,
    default_department_id    TEXT,
    -- OAuth2 self-client / refresh-token credentials (admin-managed).
    client_id                TEXT,
    client_secret            TEXT,
    refresh_token            TEXT,
    -- Cached short-lived access token (refreshed on demand by the backend).
    access_token             TEXT,
    access_token_expires_at  TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the single row so the backend always has a config to read/update.
INSERT INTO zoho_desk_config (id)
VALUES ('default')
ON CONFLICT (id) DO NOTHING;

-- Backend-only table: deny anon/authenticated, service role bypasses RLS.
ALTER TABLE zoho_desk_config ENABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
