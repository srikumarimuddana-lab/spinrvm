-- 326_merge_duplicate_onboarding_faqs.sql
--
-- Content fix, not a schema change: migrations 210 (general driver set) and
-- 212 (Saskatchewan driver set) each independently authored the same three
-- onboarding-status topics, worded slightly differently — a driver searching
-- or browsing the Help Center would see what looks like the same question
-- twice with two answers that don't quite agree (near-duplicate content,
-- flagged in the FAQ content audit alongside the SOS duplication fixed by
-- migration 322).
--
-- Fix: for each of the 3 pairs, keep the better-written side and deactivate
-- the other (soft delete via is_active, matching the additive-over-
-- destructive convention already used by migration 322 — no hard DELETE, no
-- audit-trail loss). One kept row (the "check status" pair) gets a small
-- text merge — the losing row's explicit "Spinr Driver app" naming is folded
-- into the winning row's fuller answer (which includes a support-contact
-- fallback the losing row's didn't have) — the other two pairs' winning
-- rows are left as-is.
--
--   1. "check application status"
--      keep    (210) 'How do I check the status of my driver application?'
--      retire  (212) 'How do I check the status of my application?'
--      — kept for the fuller answer (has a "waited a while, contact
--        support" fallback the other doesn't); merged in the other's
--        explicit "Spinr Driver app" naming.
--   2. "how long does review/approval take"
--      keep    (210) 'How long does document review and approval take?'
--      retire  (212) 'How long does approval take?'
--      — near word-for-word duplicate answers; kept the more specific
--        question phrasing ("document review and approval").
--   3. "can support fast-track approval"
--      keep    (212) 'Can support activate or approve my account faster?'
--      retire  (210) 'Can you activate or approve my account?'
--      — kept for the more natural real-world question phrasing and the
--        slightly more complete answer (also names insurance, not just CRC,
--        among what must be current).
--
-- Two other near-duplicate pairs were found while auditing this (driver
-- "When and how do I get paid[for my trips]?" in both 210/212, and
-- "I can't start/accepted a ride but can't start it" in both) but are OUT
-- OF SCOPE here — this migration is scoped to the onboarding-status
-- duplicates specifically, per the task that requested it. Flagged
-- separately rather than folded in silently.
--
-- Idempotent: the deactivation UPDATEs guard on is_active = true, and the
-- one answer-merging UPDATE guards on the OLD answer text, so both are
-- no-ops on re-run. Forward-compatible, no schema change, no locks.
--
-- Rollback: (manual)
--   UPDATE faqs SET is_active = true, updated_at = now()
--     WHERE audience = 'driver' AND question IN (
--       'How do I check the status of my application?',
--       'How long does approval take?',
--       'Can you activate or approve my account?'
--     );
--   UPDATE faqs SET
--     answer = 'Open the driver app and go to your Account / Onboarding section to see your current status. Review starts once all your required documents are uploaded and readable. If it has been a while with no update, contact support.',
--     embedding = NULL, embedding_model = NULL, updated_at = now()
--     WHERE audience = 'driver' AND question = 'How do I check the status of my driver application?'
--     AND answer = 'Open the Spinr Driver app and go to your Account / Onboarding section to see your current status. Review starts once all your required documents are uploaded and readable. If it has been a while with no update, contact support.';
--   (the AND answer= guard mirrors the forward migration's idempotency principle — matching the
--   post-326 text so a hand-edit made after 326 applied isn't clobbered by the rollback either.)

-- ---------- 1. "check application status" — keep 210, merge in 212's naming ----------

UPDATE faqs SET answer =
    'Open the Spinr Driver app and go to your Account / Onboarding section to see your current status. Review starts once all your required documents are uploaded and readable. If it has been a while with no update, contact support.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND is_active = true AND question = 'How do I check the status of my driver application?'
  AND answer = 'Open the driver app and go to your Account / Onboarding section to see your current status. Review starts once all your required documents are uploaded and readable. If it has been a while with no update, contact support.';

UPDATE faqs SET is_active = false, updated_at = now()
WHERE audience = 'driver' AND is_active = true
  AND question = 'How do I check the status of my application?'
  AND answer = 'Open the Spinr Driver app and go to your Account / Onboarding section to see your current application status. Review starts once all required documents are uploaded and readable.';

-- ---------- 2. "how long does review/approval take" — keep 210 as-is ----------

UPDATE faqs SET is_active = false, updated_at = now()
WHERE audience = 'driver' AND is_active = true
  AND question = 'How long does approval take?'
  AND answer = 'Review begins once every required document is uploaded and clear, and how long it takes depends on volume and whether anything needs to be re-submitted. Check your status in the app — if a document was rejected you''ll see the reason so you can fix it quickly. Contact support if you''ve been waiting longer than expected.';

-- ---------- 3. "can support fast-track approval" — keep 212 as-is ----------

UPDATE faqs SET is_active = false, updated_at = now()
WHERE audience = 'driver' AND is_active = true
  AND question = 'Can you activate or approve my account?'
  AND answer = 'Approval is done by our review team — it is not automatic and support cannot skip the review. Make sure every required document is uploaded and none are expired (including your Criminal Record Check). You''ll be able to go online as soon as your account is approved. Check Account / Onboarding in the app to see your current status and whether anything is missing or expired.';
