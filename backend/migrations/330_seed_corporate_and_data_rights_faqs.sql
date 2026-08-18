-- 330_seed_corporate_and_data_rights_faqs.sql
--
-- Content addition, not a schema change: fills two coverage gaps flagged by
-- the FAQ content audit — nothing in the 51 rows seeded by migrations
-- 210/212/230 (plus the 322/325/327 fixes on top of them) covers corporate
-- accounts or the PIPEDA data-access/deletion rights CLAUDE.md commits to,
-- despite both being real, shipped features. A rider on a company plan, or
-- anyone exercising their access/deletion right, currently has no FAQ entry
-- to find and has to reach a human for something that's otherwise self-serve.
--
-- Every fact below was grounded against the actual code before writing it,
-- not guessed:
--   - Corporate: rider-app/app/work-profile.tsx (screen title "Work Profile",
--     "Work Mode"/"Personal Mode" toggle), rider-app/app/ride-options.tsx
--     (policy-block confirm sheet, "Company account" payment-option label),
--     .claude/context/domain-payments.md ("Surge never applies to
--     corporate-paid rides"). Corporate/Work Mode only exists in rider-app —
--     drivers are never paid via a corporate account, so these 2 rows are
--     audience='rider', not 'both'.
--   - Data rights: backend/routes/users.py's real `POST /data-export` (30-day
--     PIPEDA s.9 SLA, self-fulfilled email, usually much faster) and
--     `DELETE /account` (locks immediately, reactivate by signing in any
--     time before the 7-year SK Transportation Act retention ceiling, then a
--     hard delete — NOT anonymized in between). Both apps have an equivalent
--     self-serve settings screen, so these 2 rows are audience='both'.
--
-- Caught by a spinr-migration-reviewer pass before merge: the first draft's
-- data-export answer said "Export Data (driver app)" — the driver-app button
-- is actually labelled "Email My Data" (driver-app/i18n/en.json
-- settings.downloadData) — and claimed a specific "usually within 24 hours"
-- turnaround with no code backing it (POST /data-export fires a fire-and-
-- forget spawn() with an explicit no-guarantee fallback path when no email
-- is on file). Both fixed: the driver-app button name corrected, the
-- fabricated hour count replaced with "as soon as it's ready" — the same
-- guidance-only-no-fabricated-SLA rule migration 210's own header comment
-- states.
--
-- NOTE (found while researching this, not fixed here — flagged to the user
-- instead): the in-app *confirmation dialog* copy for account deletion in
-- both apps currently says something different from, and less generous
-- than, what the backend actually does —
--   rider-app/i18n/en-CA.json "privacy.delete_confirm_msg": "This will
--     schedule your account for deletion after a 30-day grace period..."
--   driver-app/i18n/en.json "settings.deleteAccountConfirmMsg": "...a 30-day
--     recovery window... after that, all your data, earnings history, and
--     ride records are permanently deleted."
-- Neither matches `delete_account_pipeda` in backend/routes/users.py, which
-- locks the account immediately and allows reactivation any time before the
-- real 7-year retention ceiling — not 30 days, and ride/earnings records are
-- kept attributable for that full window, not deleted at 30 days. The FAQ
-- answer below states the accurate (backend) behavior rather than repeating
-- the confirm-dialog's wrong "30 days" claim; the dialog copy itself is a
-- separate, unrelated i18n bug in a different surface (the deletion
-- confirmation flow, not the FAQ content), out of scope for a content-only
-- FAQ migration and not touched here.
--
-- Idempotent: insert-if-not-exists by (question, audience), matching the
-- pattern in migrations 210/212/230. Forward-compatible, no schema change,
-- no locks.
--
-- Rollback: (manual) DELETE FROM faqs WHERE question IN (
--   'How does billing work on a corporate account?',
--   'Why was my ride blocked by company policy?',
--   'How do I request a copy of my data?',
--   'How do I delete my account?'
-- );

INSERT INTO faqs (id, question, answer, category, audience, is_active, created_at)
SELECT gen_random_uuid()::text, v.question, v.answer, v.category, v.audience, true, now()
FROM (
    VALUES
    ('How does billing work on a corporate account?',
     'If your company has set you up with a Spinr work account, open Work Profile and switch to Work Mode before you book — your ride is then paid from your company''s balance instead of your personal payment method, and shows as "Company account" on the receipt. Spinr never applies surge pricing to company-paid rides.',
     'account', 'rider'),
    ('Why was my ride blocked by company policy?',
     'Your company sets its own rules for work rides — like a fare limit or allowed times — and Spinr checks a booking against them before you confirm. If a ride doesn''t qualify, you''ll see the reason and can adjust the ride or switch to Personal Mode to pay yourself instead.',
     'account', 'rider'),
    ('How do I request a copy of my data?',
     'Open your account settings and tap Download My Data (rider app) or Email My Data (driver app). We''ll email you a copy as soon as it''s ready, and always within the 30 days PIPEDA allows us to take.',
     'account', 'both'),
    ('How do I delete my account?',
     'Open your account settings and tap Delete Account, then confirm. Your account is locked right away, and you can sign back in any time to reactivate it. Ride and trip records are kept for the retention period Saskatchewan''s Transportation Act requires (7 years) and then permanently deleted; your profile details — name, email, saved addresses, payment methods — are scrubbed within 30 days of your request.',
     'account', 'both')
) AS v(question, answer, category, audience)
WHERE NOT EXISTS (
    SELECT 1 FROM faqs f WHERE f.question = v.question AND f.audience = v.audience
);
