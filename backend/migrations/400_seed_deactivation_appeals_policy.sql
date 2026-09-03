-- 400_seed_deactivation_appeals_policy.sql
--
-- Seeds the driver-only 'deactivation-appeals' legal_documents row (1 row --
-- driver-only per shared/config/legalDocs.ts's DRIVER_ONLY_LEGAL_DOC_TYPES;
-- riders never see this document). This was the one remaining gap left by
-- migration 361 (which published the other 6 shared-audience docs but not
-- this driver-only one) -- driver-app's policies.tsx has listed
-- 'deactivation-appeals' since #4557 shipped, so drivers opening it have
-- been seeing "No Driver Deactivation & Appeals Policy has been added yet."
--
-- Content is copied verbatim from docs/legal/driver-deactivation-appeals-
-- policy.md as of 2026-09-02, at the explicit direction of the product
-- owner and WITHOUT counsel review -- same accepted-risk pattern already
-- recorded for ToS/Privacy/the migration-361 batch. One content difference
-- from the original draft, not just a publish step: the three bracketed
-- numeric SLA placeholders ([NUMBER, E.G. 5 BUSINESS DAYS] etc.) had no
-- backing constant anywhere in backend/ (confirmed independently twice --
-- 2026-08-20 legal-readiness pass, re-confirmed 2026-09-02) and were
-- rewritten to non-numeric commitments ("as quickly as possible") rather
-- than shipped as literal unresolved brackets or an invented number. See
-- the document's own header note and docs/legal/legal-text-publication-
-- checklist.md for the full reasoning and the still-open follow-up (a real
-- numeric SLA from the safety team).
--
-- Requires migration 360 (widens legal_documents.doc_type CHECK to allow
-- 'deactivation-appeals') -- already applied, this migration only inserts.
--
-- Idempotent: ON CONFLICT (audience, doc_type) DO NOTHING -- never
-- overwrites a row an admin may have already edited via the dashboard
-- since this seed value was drafted. Forward-compatible, no schema change,
-- no locks (1-row insert on a tiny table).
--
-- Rollback: DELETE FROM legal_documents WHERE audience = 'driver' AND
--   doc_type = 'deactivation-appeals' AND version = 1;

INSERT INTO legal_documents (audience, doc_type, content, version)
VALUES
    ('driver', 'deactivation-appeals', $doc0$SPINR DRIVER DEACTIVATION AND APPEALS POLICY

Last updated: September 2, 2026

This policy explains when Spinr may place a hold on or deactivate a driver
account, and how to appeal that decision. It applies in addition to the
Independent Contractor Agreement and the Terms of Service.

TEMPORARY HOLDS

A temporary hold pauses your ability to go online while Spinr's safety team
investigates a specific report — for example, an accident, a harassment
complaint, or another safety incident. A hold is a precaution, not a
finding of fault. You will be notified that a hold has been placed and, at
a minimum, told the general category of the report (for example, "a safety
complaint from a rider") without necessarily identifying the reporting
party, to protect their safety.

Spinr aims to complete a safety investigation and resolve a temporary hold
as quickly as possible, and will keep you updated on status — including
telling you if an investigation is taking longer because it involves a
third party (such as police or SGI) whose timeline Spinr does not control.

DOCUMENT-EXPIRY HOLDS

Separately from a safety hold, you will not be able to go online if a
required document — license, insurance, vehicle inspection, Criminal Record
Check, or Vulnerable Sector Check — has expired. This is an automatic,
non-punitive system check, not a safety investigation, and resolves as soon
as you upload a valid renewed document.

PERMANENT DEACTIVATION

Spinr may permanently deactivate a driver account where an investigation
substantiates a serious violation — for example, a safety incident caused by
the driver, discrimination or refusal of a service animal, harassment,
fraud, or a pattern of conduct violations under the Community Guidelines —
or where a driver no longer meets the eligibility requirements in the
Independent Contractor Agreement (for example, a driving abstract that no
longer qualifies) and cannot cure the issue.

Spinr will tell you the reason for a permanent deactivation, except where
doing so would compromise an active investigation, reveal a reporting
party's identity in a way that creates a safety risk, or is otherwise
restricted by law.

HOW TO APPEAL

If your account is deactivated, you may appeal at any time using the
Appeal screen in the driver app. Include any information you believe is
relevant — Spinr's safety team did not have, or may not have correctly
weighed. A different reviewer than the one who made the original decision
is expected to review your appeal, which will receive a response as soon
as possible. An appeal outcome is final, but you may raise a legal claim
independently of this appeals process where you believe you have one — this
policy does not waive any right you have under the Independent Contractor
Agreement or applicable law.

REACTIVATION

If an appeal succeeds, or if a temporary hold resolves in your favor, your
account is reactivated and any documents on file are re-checked for current
validity before you can go online again.$doc0$, 1)
ON CONFLICT (audience, doc_type) DO NOTHING;

NOTIFY pgrst, 'reload schema';
