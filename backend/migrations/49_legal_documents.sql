-- Audience-aware legal documents. Riders and drivers need different
-- Terms of Service and Privacy Policy text but the legacy /settings/legal
-- endpoint returns a single shared blob. This table stores per-(audience,
-- doc_type) content; the legacy endpoint stays in place as a fallback
-- so existing clients keep working until they migrate to /legal-documents.
--
-- audience: rider | driver
-- doc_type: tos | privacy
-- (audience, doc_type) is the natural key.

CREATE TABLE IF NOT EXISTS legal_documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audience    TEXT NOT NULL CHECK (audience IN ('rider', 'driver')),
    doc_type    TEXT NOT NULL CHECK (doc_type IN ('tos', 'privacy')),
    content     TEXT NOT NULL DEFAULT '',
    version     INTEGER NOT NULL DEFAULT 1,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (audience, doc_type)
);

COMMENT ON TABLE legal_documents IS
    'Per-audience Terms of Service and Privacy Policy text. Edited via admin dashboard.';
COMMENT ON COLUMN legal_documents.audience IS 'rider | driver';
COMMENT ON COLUMN legal_documents.doc_type IS 'tos | privacy';

NOTIFY pgrst, 'reload schema';
