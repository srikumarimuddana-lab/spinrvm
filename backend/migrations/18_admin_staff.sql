-- ============================================================
-- 18. ADMIN_STAFF — Multi-admin with role-based module access
-- ============================================================
-- Used by routes/admin/staff.py and routes/admin/auth.py for
-- dashboard login with email/password (separate from rider/driver
-- phone+OTP auth).

CREATE TABLE IF NOT EXISTS admin_staff (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    first_name      TEXT NOT NULL DEFAULT '',
    last_name       TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'custom',          -- super_admin | operations | support | finance | custom
    modules         JSONB NOT NULL DEFAULT '["dashboard"]'::JSONB,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_login      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_admin_staff_email ON admin_staff (email);

ALTER TABLE admin_staff ENABLE ROW LEVEL SECURITY;
