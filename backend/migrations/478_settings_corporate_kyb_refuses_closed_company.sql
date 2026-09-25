-- 478_settings_corporate_kyb_refuses_closed_company.sql
--
-- Kill switch for the KYB closed-company guard.
--
-- Bug: POST /admin/corporate-accounts/{id}/kyb-review wrote status
-- 'active' (approve) or 'suspended' (reject) with no check of the current
-- status, so a KYB decision on a CLOSED company reopened it after its
-- wallet had been wound down and its Stripe subscription cancelled. The
-- company-side resubmit (POST /company/{id}/kyb/submit) had the same shape
-- under a race: it read 'suspended', then flipped to 'pending_verification'
-- unconditionally, so a close landing in between was overwritten.
--
-- With this flag on (the default), both paths re-read the company, refuse a
-- closed one with 409, and compare-and-set the status they read (409 if it
-- changed underneath). 'closed' is terminal (domain-corporate.md).
--
-- Default TRUE: the un-flagged behaviour was the bug, so the fix ships live
-- and the flag exists only as an emergency kill switch (domain-corporate.md
-- flag convention). Code reads it with .get(..., True), so it is also on
-- before this migration is applied.
--
-- Change log: docs/change-log/2026-09-25-kyb-decision-closed-guard.md
--
-- Rollback:
--   UPDATE settings SET corporate_kyb_refuses_closed_company = false WHERE id = 'app_settings';  -- behavioural rollback, no deploy
--   ALTER TABLE settings DROP COLUMN IF EXISTS corporate_kyb_refuses_closed_company;

ALTER TABLE public.settings
    ADD COLUMN IF NOT EXISTS corporate_kyb_refuses_closed_company BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN public.settings.corporate_kyb_refuses_closed_company IS
    'When true (default), KYB review and KYB resubmit refuse to change the status of a closed corporate account (409) and compare-and-set on the status they read. Emergency kill switch only; false restores the old unconditional status write.';
