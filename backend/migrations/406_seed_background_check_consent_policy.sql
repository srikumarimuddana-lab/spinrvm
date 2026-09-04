-- 406_seed_background_check_consent_policy.sql
--
-- Seeds the driver-only 'background-check-consent' legal_documents row --
-- 1 row, driver-only per shared/config/legalDocs.ts's
-- DRIVER_ONLY_LEGAL_DOC_TYPES (riders never see this document). This was
-- the last of the 9 tracked legal documents with no published row at all
-- (migration 361 published 6 shared-audience docs, migration 400 published
-- 'deactivation-appeals'; this one had no seed migration until now).
-- driver-app's crc-consent.tsx has fetched
-- GET /legal-documents?audience=driver&type=background-check-consent since
-- it shipped, falling back to "This consent form has not been published
-- yet. Contact support before continuing." until this migration runs.
--
-- Content is copied verbatim from docs/legal/background-check-consent.md
-- (BEGIN DRAFT / END DRAFT section) as of 2026-09-04, at the explicit
-- direction of the product owner and WITHOUT counsel review -- same
-- accepted-risk pattern already recorded for ToS/Privacy/migration-361's
-- batch/deactivation-appeals (migration 400). Two known gaps, carried
-- forward from docs/legal/legal-text-publication-checklist.md rather than
-- silently resolved:
--
--   1. Retention figure is intentionally non-numeric ("for a limited
--      period after that consistent with Spinr's regulatory retention
--      obligations described in the Privacy Policy") -- no authoritative
--      CRC/VSC-specific retention figure exists anywhere in this codebase.
--      Same treatment as migration 400's SLA gaps: don't invent a number.
--   2. The "what happens if the result affects your eligibility" paragraph
--      promises the driver is told the specific requirement affected and
--      may respond before a final decision. Partially verified against
--      code: routes/admin/documents.py does push a rejection notification
--      naming the specific reason (including for "background" document
--      types) when a document is rejected, but there is no CRC/VSC-
--      specific pre-action response gate beyond the same general appeals
--      flow already used for every other deactivation
--      (driver-deactivation-appeals-policy.md's "How to appeal" section,
--      services/driver_appeals.py). Confirm with the safety/eligibility
--      team before treating this paragraph as a fully verified process
--      guarantee, not just a notify-on-rejection + general-appeal reality.
--
-- Requires migration 360 (widens legal_documents.doc_type CHECK to allow
-- 'background-check-consent') -- already applied, this migration only
-- inserts.
--
-- Idempotent: ON CONFLICT (audience, doc_type) DO NOTHING -- never
-- overwrites a row an admin may have already edited via the dashboard
-- since this seed value was drafted. Forward-compatible, no schema change,
-- no locks (1-row insert on a tiny table).
--
-- Rollback: DELETE FROM legal_documents WHERE audience = 'driver' AND
--   doc_type = 'background-check-consent' AND version = 1;

INSERT INTO legal_documents (audience, doc_type, content, version)
VALUES
    ('driver', 'background-check-consent', $doc0$CONSENT TO CRIMINAL RECORD CHECK AND VULNERABLE SECTOR CHECK

Before you can drive on the Spinr platform, we need your consent to collect
and review a Criminal Record Check and Vulnerable Sector Check that you
obtain yourself, and to renew this consent at least annually while you
continue to drive.

HOW THIS WORKS

Unlike some platforms, Spinr does not send your information to a
background-check company. Instead, you personally request a Criminal
Record Check and Vulnerable Sector Check from your local police service
(or the RCMP detachment serving your area, if your municipality does not
have its own police service), and you give Spinr the result. The exact
process, fee, and processing time depend on which police service issues
your check — your city's driver requirements will tell you the specific
steps.

WHAT WE COLLECT AND WHY

We collect and review the Criminal Record Check and Vulnerable Sector
Check result you provide, along with your name and date of birth to match
it to your driver profile. We use the result solely to confirm you meet
Spinr's driver eligibility requirement of no major violations and no
Criminal Code driving offences in the past three years, as described in
the Independent Contractor Agreement. We do not use this information for
any other purpose, and we do not request or receive anything beyond the
result document itself — we never contact a police service directly to
obtain your check.

HOW WE STORE IT

Your background-check result is stored encrypted and is never shared with
riders or with any other Spinr driver. Access inside Spinr is limited to
the staff who review driver eligibility.

HOW LONG WE KEEP IT

We retain the result of your background check, and the record of each
renewal, for as long as you are an active driver, and for a limited period
after that consistent with Spinr's regulatory retention obligations
described in the Privacy Policy.

ANNUAL RENEWAL

Your consent here covers the initial check and each annual renewal while
you continue to drive. You are responsible for obtaining a current check
from your police service before each renewal and submitting it to Spinr.
You will be prompted to reconfirm before each renewal. If a current check
is not on file, you will not be able to go online.

WHAT HAPPENS IF THE RESULT AFFECTS YOUR ELIGIBILITY

If a check result means you no longer meet Spinr's eligibility
requirements, we will tell you the specific requirement affected before
taking any action on your account, consistent with our obligations under
applicable consumer-reporting and human-rights law, and you may respond
before a final decision is made.

YOUR CONSENT

By checking the box below and continuing, you consent to Spinr collecting
and reviewing the Criminal Record Check and Vulnerable Sector Check result
you provide from your police service, for the purpose and retention
period described above.

[ ] I consent to Spinr collecting and reviewing my Criminal Record Check
and Vulnerable Sector Check as described above.$doc0$, 1)
ON CONFLICT (audience, doc_type) DO NOTHING;

NOTIFY pgrst, 'reload schema';
