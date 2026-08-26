-- 355_merge_duplicate_crc_and_payout_faqs.sql
--
-- Two questions were seeded twice, once by 210_seed_driver_faqs.sql (July) and
-- again by a later edit (August). 327 merged the onboarding duplicates; these
-- two pairs survived it. Riders and drivers see both in the app's FAQ list, and
-- the website now reads the same table, so the duplication would show there too.
--
--   "What is the Criminal Record Check requirement?"
--     vs "What are the Criminal Record Check (CRC) requirements?"
--   "When and how do I get paid?"
--     vs "When and how do I get paid for my trips?"
--
-- In each pair the newer row has the better question wording and the older one
-- has a detail worth keeping, so the survivor's answer is updated to carry both
-- before the duplicate is retired:
--   * CRC — the older answer states the renewal cadence ("typically annually"),
--     which the newer one softened to "periodically". CLAUDE.md requires annual
--     renewal, so the precise wording wins.
--   * payout — the older answer points at payout settings in the app; the newer
--     one carries the 100%-of-fare line. Both belong.
--
-- Retired by is_active = false rather than DELETE: reversible, keeps the row for
-- audit, and is_active = false is exactly what the "Public read faqs" RLS policy
-- filters on, so the row disappears from the apps and the website immediately.
--
-- Idempotent: matches on exact question text and is a no-op once applied.
-- Rollback: UPDATE faqs SET is_active = true WHERE id IN
--   ('4d99dd06-1072-475d-a850-c1f60812a4e0','8f4db468-9c35-4e38-b15e-95477844fa70');
--   (the survivors' answers are restored from the strings quoted above)

UPDATE faqs
SET answer = 'A current Criminal Record Check that includes a Vulnerable Sector Check is required to drive, and it must be kept up to date — renewed on the schedule Spinr sets, typically annually. An expired check blocks you from going online until you upload a recent one. Upload the most recent copy in the app; check your documents section to see whether the CRC on file is valid or expired.',
    updated_at = now()
WHERE question = 'What are the Criminal Record Check (CRC) requirements?'
  AND is_active = true;

UPDATE faqs
SET answer = 'Drivers keep 100% of the fare. Open the Earnings section in the app to see your completed trips and what you earned on each. For bank-deposit timing or a payout you believe is missing, check your payout settings in the app or contact support and we''ll look into it.',
    updated_at = now()
WHERE question = 'When and how do I get paid for my trips?'
  AND is_active = true;

UPDATE faqs
SET is_active = false,
    updated_at = now()
WHERE question IN (
    'What is the Criminal Record Check requirement?',
    'When and how do I get paid?'
  )
  AND is_active = true;
