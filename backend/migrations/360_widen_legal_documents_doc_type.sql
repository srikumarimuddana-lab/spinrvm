-- 360_widen_legal_documents_doc_type.sql
--
-- legal_documents.doc_type's CHECK constraint (migration 49) only ever
-- allowed 'tos' | 'privacy', but backend/routes/legal_documents.py's
-- ALLOWED_TYPES (public read) and backend/routes/admin/legal_documents.py's
-- ALLOWED_TYPES (admin write) have long since grown to 10 values -- the app
-- and admin dashboard already offer all 10 doc types, but every insert for
-- the other 8 has been silently rejected at the DB layer since those routes
-- shipped. This widens the constraint to match the app-level allow-lists
-- exactly, so the admin dashboard's existing legal-documents editor (and the
-- seed in migration 361) actually works for every doc_type it already
-- presents.
--
-- Forward-compatible: DROP + ADD CHECK on a tiny table (a handful of rows
-- today), no CONCURRENTLY concern, momentary lock is negligible. Purely
-- additive to the allowed value set -- no existing row's doc_type value is
-- affected, nothing already in the table can violate the new constraint.
--
-- Rollback: ALTER TABLE legal_documents DROP CONSTRAINT legal_documents_doc_type_check;
--   ALTER TABLE legal_documents ADD CONSTRAINT legal_documents_doc_type_check
--     CHECK (doc_type IN ('tos', 'privacy'));
--   (would only be safe to run if no row uses one of the newly-allowed
--   doc_type values -- check first.)

ALTER TABLE legal_documents DROP CONSTRAINT IF EXISTS legal_documents_doc_type_check;

ALTER TABLE legal_documents ADD CONSTRAINT legal_documents_doc_type_check
    CHECK (doc_type IN (
        'tos',
        'privacy',
        'community-guidelines',
        'non-discrimination',
        'accessibility',
        'cancellation-fees',
        'promotions-referral',
        'insurance-periods',
        'deactivation-appeals',
        'background-check-consent'
    ));

COMMENT ON COLUMN legal_documents.doc_type IS
    'tos | privacy | community-guidelines | non-discrimination | accessibility | cancellation-fees | promotions-referral | insurance-periods | deactivation-appeals | background-check-consent -- keep in sync with routes/legal_documents.py and routes/admin/legal_documents.py ALLOWED_TYPES';

NOTIFY pgrst, 'reload schema';
