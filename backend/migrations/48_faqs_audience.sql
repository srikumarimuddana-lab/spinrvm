-- FAQs are now audience-aware: rider-app and driver-app fetch the same
-- /faqs endpoint but filtered by their role. Existing rows default to
-- 'both' so nothing disappears from either app after the migration.
-- Values: rider | driver | both.

ALTER TABLE faqs
    ADD COLUMN IF NOT EXISTS audience TEXT NOT NULL DEFAULT 'both';

COMMENT ON COLUMN faqs.audience IS
    'Which app(s) see this FAQ: rider | driver | both';

CREATE INDEX IF NOT EXISTS idx_faqs_audience ON faqs (audience) WHERE is_active = true;

NOTIFY pgrst, 'reload schema';
