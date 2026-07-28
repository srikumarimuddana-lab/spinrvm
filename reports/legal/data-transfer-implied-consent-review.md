# Data Transfer Module — Implied-Consent Basis Review

**Owner:** Privacy Officer / Legal
**Due:** No hard deadline — the feature is already live (see Risk if missed); recommend closing within one quarter
**Regulation:** PIPEDA Principle 4.3 (Consent) and Principle 4.5 (Limiting Use, Disclosure, and Retention) — specifically, whether a *secondary use* of already-collected personal information is consistent with the purposes for which it was originally collected
**Risk if missed:** Not a launch blocker (feature is already in production), but the module's PIA (`docs/privacy/2026-07-28-pia-data-transfer-export.md`) cannot be closed out as fully reviewed, and Spinr is relying on an engineer's (non-legal) reasoning as its only documented consent position for a High-sensitivity data flow

---

## This is item R-G from `docs/privacy/2026-07-28-pia-data-transfer-export.md`

That PIA's Section 8 lists 7 recommendations (R-A through R-G). R-A through R-F are closed (code changes, a runbook update, and a documented data-minimization decision — see the PIA's Section 8 and `ACTION_ITEMS.md` item B11 for what was done in each). **R-G is the only one that cannot be closed by engineering** — it asks for a legal/privacy determination, not a code or process change, and no privacy-officer or legal role is currently assigned in this repo to make it. This document exists to hand that determination to a human with the authority to make it, with everything needed to do so in one place.

## Background — what the module does

Spinr's admin-only **Data Transfer** module (`backend/routes/admin/data_transfer_export.py`, gated to `super_admin` only as of 2026-07-28) lets an admin export a full-fidelity, **unredacted** bundle of up to 100 driver or rider records — profile data, government-ID documents (raw file bytes), ride history (including exact pickup/dropoff GPS coordinates), and the regulatory insurance-period audit trail. Its stated purpose is moving records **between Spinr's own environments** (e.g., seeding a staging environment with realistic data, or migrating between the primary/standby Fly.io/Railway regions) — it is not a rider/driver-facing self-access tool (that's a separate, redacted flow: `backend/routes/drivers/tax_exports.py`).

This is a **secondary use** of personal information: the data was originally collected to provide the ride-share service (account creation, driver eligibility verification, trip fulfillment), not for internal cross-environment data movement. No rider or driver is told at signup, or at any other point, that their record may be copied this way.

## The specific question for legal/privacy review

**Does moving a rider's or driver's full personal-information record — including government ID documents and precise GPS ride history — between Spinr's own internal environments (for operational purposes like staging-environment seeding or environment migration) fall within the scope of the consent already obtained at account signup, under PIPEDA Principle 4.3?**

Or does this specific secondary use require:
- (a) a distinct disclosure in Spinr's privacy policy naming this kind of internal data movement, and/or
- (b) a narrower operational control (e.g., synthetic/anonymized data for non-production environments instead of real records), and/or
- (c) nothing further — the current implied-consent basis is adequate as-is.

## What's already been assessed (and what hasn't)

The PIA's Section 5 (Consent) and Section 3 (Personal Information Inventory) lay out the engineering-side reasoning already done:

> "Consent — Implied only, inherited from original account signup consent. This module represents a secondary use (cross-environment transfer) that was not separately itemized at consent time. Not necessarily a violation (PIPEDA allows reasonable secondary uses consistent with the original purpose), but worth explicit legal review rather than assumption."

That reasoning is **the PIA author's analysis, not a legal opinion**, and should be treated as a starting point for review, not a conclusion to rubber-stamp.

Two facts worth flagging directly, since they weren't fully surfaced in the PIA itself:

1. **Spinr's current privacy-policy draft does not mention this use case.** A search of `docs/legal/privacy-policy.md` (itself still marked "Draft for Legal Review," not yet published) turned up no language covering internal cross-environment data movement, staging-environment seeding, or any comparable secondary use. If the review concludes a distinct disclosure is needed (option (a) above), that draft is the natural place to add it — and since it isn't published yet, this could be resolved before first publication rather than as a later amendment.
2. **The data movement stays entirely within Spinr's own Supabase project/environments** (per PIA Section 6) — there's no third-party recipient. This matters for the "reasonable secondary use" analysis: it's an internal-to-internal transfer, not a disclosure to an external party, which is a materially different risk profile than the module's biggest completed remediation (R-A: access is now `super_admin`-only) already reflects on the access-control side. Whether internal-only movement changes the consent analysis (versus the disclosure/safeguards analysis, which is separately addressed) is itself part of the question above.

## What a closed-out review looks like

Record the determination directly in the PIA:
1. Add a dated entry to `docs/privacy/2026-07-28-pia-data-transfer-export.md` Section 8, R-G, following the same "**[LOW] R-G — RESOLVED \<date\>.**" pattern already used for R-A through R-F, stating the conclusion and reasoning (or a pointer to a fuller memo if one is produced separately).
2. Fill in the `Reviewed by` row of the PIA's Section 9 sign-off table with a real name, role, and date — it currently reads "pending — no privacy officer role identified in this repo."
3. If the determination is "needs a distinct disclosure" (option (a) above), open a follow-up item to add that language to `docs/legal/privacy-policy.md` before it publishes, and reference it from this document's Status table below.
4. If the determination recommends an operational control change (option (b) above — e.g., synthetic data for non-production seeding instead of real records), that becomes a new ACTION_ITEMS.md entry for engineering, out of scope for this document to design.

## Contact

- **Internal escalation:** whoever owns PIPEDA compliance decisions at Spinr (no privacy-officer role is currently named in this repo — recommend formally assigning one; see PIA Section 5, Accountability, which flags the same gap)
- **Engineering questions / module owner:** Data Transfer module maintainer (`backend/services/data_transfer/`, `backend/routes/admin/data_transfer_*.py`)
- **Source PIA:** `docs/privacy/2026-07-28-pia-data-transfer-export.md`
- **Tracking item:** `ACTION_ITEMS.md`, B11, R-G

---

## Status

| Step | Owner | Status | Date |
|------|-------|--------|------|
| Assign a named privacy-officer / legal reviewer for this determination | Spinr leadership | ⬜ Open | — |
| Review the question above and reach a determination | Privacy Officer / Legal | ⬜ Open | — |
| Record the determination in the PIA (Section 8, R-G) and Section 9 sign-off | Privacy Officer / Legal | ⬜ Open | — |
| If disclosure is required: add language to `docs/legal/privacy-policy.md` before publication | Legal | ⬜ Open | — |
| If an operational control change is recommended: file a new ACTION_ITEMS.md entry | Legal → Engineering | ⬜ Open | — |
