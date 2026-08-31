# Spinr Data Retention Schedule (Public Reference) — Draft for Legal Review

> **What this is.** A standalone, at-a-glance table version of the retention
> periods currently described in prose across `privacy-policy.md` §4 and
> Part B §11/§12. A user or auditor checking "how long is my data kept"
> shouldn't have to re-read policy prose to find one number — this page is
> the single place all the retention figures live, cited by the Privacy
> Policy rather than duplicated in it.
>
> **This is a draft, not legal advice**, and every figure below is copied
> from the Privacy Policy draft and CLAUDE.md as of this review — it is not
> a substitute for fixing the underlying issues those source documents
> already flag: the 2-vs-3-year GPS retention contradiction in
> `docs/data-classification.md` (code enforces **3 years** via migrations
> 50/323 — `data-classification.md` is the stale side and needs correcting to
> match). The 30-day account-deletion enforcement gap (DV-8) is **now
> closed** — `retention_purge_loop` runs every ~24h and calls
> `purge_pii_retention()`, whose Step N (`backend/migrations/296_pipeda_30day_profile_scrub.sql`)
> enforces the 30-day scrub (`ACTION_ITEMS.md` B18, closed 2026-08-10). **Do
> not publish this page until the GPS-figure contradiction is resolved** — a
> clean table of promises Spinr isn't yet technically keeping is worse than
> the same promise buried in
> prose, because it's easier to point to and easier to be wrong about.

---

## BEGIN DRAFT

SPINR DATA RETENTION SCHEDULE

Last updated: [INSERT PUBLICATION DATE]

This table summarizes how long we keep different categories of your
information, and why. It's a companion to our Privacy Policy, which explains
these retention periods in more detail — if anything here conflicts with
the Privacy Policy, the Privacy Policy controls.

| Data category | Retention period | Why |
|---|---|---|
| Trip records (fare, route summary) | 7 years | Tax and financial audit requirements |
| Driver/vehicle linkage to a trip | 7 years | Saskatchewan transportation regulatory requirement |
| Insurance coverage-period logs | 7 years, never altered after the fact | Insurance-audit requirement |
| Full GPS trail of a completed trip | 90 days, then deleted | Not needed after trip reconciliation |
| GPS point at pickup and drop-off only | 3 years | Regulatory and insurance-audit requirement |
| In-ride chat messages | 90 days after trip | Support and safety review |
| Background check results (drivers) | [PENDING LEGAL/SAFETY DECISION — no retention period is set or enforced yet; see background-check-consent.md] | CRA and regulatory record-keeping |
| Account information, after a deletion request | Removed within 30 days, except categories above | PIPEDA right to deletion, balanced against regulatory retention |

WHY SOME DATA OUTLIVES A DELETION REQUEST

Saskatchewan's transportation regulations require certain trip, driver, and
insurance records to be kept for a fixed period regardless of a deletion
request. Where this applies, we anonymize what we can (for example, removing
your name and rounding saved addresses) while keeping the financial and
insurance-audit record intact, as described in our Privacy Policy.

## END DRAFT

---

## Pre-publication notes — do not skip these

1. **Resolve the 2-vs-3-year GPS contradiction first.** `docs/data-classification.md`
   states 2 years for the pickup/drop-off GPS trace in two places; this
   table (like the Privacy Policy draft) uses 3 years per CLAUDE.md and
   `docs/runbooks/data-breach.md`. Fix the internal document, not this one,
   since 3 years is the figure stated in the more authoritative source.
2. **30-day deletion enforcement (DV-8) — closed 2026-08-10.** The scheduled
   job exists and runs: `retention_purge_loop` (~24h, `backend/core/lifespan.py`)
   → `purge_pii_retention()`, with the 30-day profile scrub in
   `backend/migrations/296_pipeda_30day_profile_scrub.sql` (Step N). This
   promise is now backed by code, not aspirational. (Corrected 2026-08-27;
   the earlier "not enforced yet" note was stale.)
3. This page is intentionally a mirror of Privacy Policy §4, not a
   replacement for it — keep both in sync if either changes.
