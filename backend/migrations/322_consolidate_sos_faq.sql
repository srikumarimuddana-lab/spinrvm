-- 322_consolidate_sos_faq.sql
--
-- Content fix, not a schema change: the SOS/emergency-safety FAQ was authored
-- twice, independently, in migrations 212 (driver, Saskatchewan set) and 230
-- (rider set) — two hand-typed copies of the same 911-disclaimer language with
-- no shared source, already worded slightly differently. That's the single
-- most compliance-sensitive sentence in the FAQ set (CLAUDE.md: SOS "never
-- auto-dials and never claims to replace calling emergency services"), so two
-- independently editable copies is a drift risk on exactly the wrong content.
--
-- Fix: deactivate both audience-specific rows (soft delete via is_active,
-- per CLAUDE.md's additive-over-destructive convention — no hard DELETE, and
-- the audit-log domain-safety trail is preserved) and seed one consolidated
-- row using audience='both', the value the schema already supports
-- (migration 48) for exactly this case. The public /faqs API and the
-- search_faqs AI tool both already match audience='both' for either rider or
-- driver callers (backend/routes/faqs.py, backend/ai/tools_support.py), so no
-- code change is required for the new row to serve both apps.
--
-- The consolidated answer keeps the rider-only detail (trip tracking/sharing)
-- and the driver-only detail (trips logged for safety and regulatory
-- purposes) alongside the one canonical SOS sentence, so no information is
-- lost for either audience.
--
-- Idempotent: the deactivation is a no-op on re-run (WHERE is_active = true
-- guard), and the insert is insert-if-not-exists by (question, audience).
-- Forward-compatible, no schema change, no locks.
--
-- Rollback: (manual)
--   UPDATE faqs SET is_active = true, updated_at = now()
--     WHERE audience IN ('driver', 'rider')
--     AND question IN ('What safety features does the driver app have?',
--                       'What safety features does Spinr have?');
--   DELETE FROM faqs WHERE audience = 'both'
--     AND question = 'What safety features does Spinr have?';

-- Deactivate the two independently-authored, audience-specific copies.
UPDATE faqs
SET is_active = false, updated_at = now()
WHERE is_active = true
  AND (
    (audience = 'driver' AND question = 'What safety features does the driver app have?')
    OR (audience = 'rider' AND question = 'What safety features does Spinr have?')
  );

-- Seed the single consolidated audience='both' replacement.
INSERT INTO faqs (id, question, answer, category, audience, is_active, created_at)
SELECT gen_random_uuid()::text, v.question, v.answer, v.category, 'both', true, now()
FROM (
    VALUES
    ('What safety features does Spinr have?',
     'Every trip is tracked, and riders can share their trip status with someone they trust. If you ever need help, in-app SOS notifies your emergency contacts and our safety team and offers one-tap 911. SOS never auto-dials and is not a replacement for calling 911 — if anyone is in danger, call 911 first. Trips are logged for safety and regulatory purposes.',
     'safety')
) AS v(question, answer, category)
WHERE NOT EXISTS (
    SELECT 1 FROM faqs f WHERE f.question = v.question AND f.audience = 'both'
);
