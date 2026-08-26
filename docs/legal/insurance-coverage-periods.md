# Spinr Insurance Coverage Periods — Plain-Language One-Pager (Draft)

> **What this is.** A standalone, plain-language explainer of the four TNC
> insurance coverage periods (CLAUDE.md's "Insurance periods" table),
> reachable from the in-app Safety Center. ToS §6/§13 explain this well in
> legal-document prose, but a post-accident dispute is exactly the moment a
> rider or driver needs this explained without re-reading a Terms document —
> this page exists to be found in that moment, not just to restate the same
> content in a different format.
>
> **This is a draft, not legal advice**, and describes Saskatchewan's TNC
> commercial insurance framework as implemented in this codebase
> (`driver_insurance_periods` table, `go_online` checks). It should be
> reviewed against current SGI Auto Fund rules and by counsel before
> publication, and must stay consistent with `docs/legal/terms-of-service.md`
> §6/§13 — this page explains the same rule in plainer language, it doesn't
> create a different one.

---

## BEGIN DRAFT

UNDERSTANDING YOUR INSURANCE COVERAGE ON A SPINR TRIP

Saskatchewan requires ride-share platforms to track which insurance coverage
applies at every moment a driver has the app open. Here's what that means in
plain language.

APP OFF

Your personal auto insurance applies, the same as any other time you're not
driving for Spinr. Spinr provides no coverage.

APP ON, WAITING FOR A RIDE (PERIOD 1)

The driver is online and available, but hasn't been matched to a ride yet.
Spinr's contingent commercial liability coverage applies in addition to the
driver's personal insurance during this period.

DRIVER ON THE WAY TO PICK YOU UP (PERIOD 2)

From the moment a driver accepts your ride request until they pick you up,
Spinr's primary commercial insurance coverage applies. This starts as soon
as the driver is assigned to your ride — even before they've accepted — 
because at that point the driver is already committed to the trip.

YOU'RE IN THE CAR (PERIOD 3)

From pickup to drop-off, Spinr's primary commercial coverage applies with
full coverage for the trip in progress.

WHY THIS MATTERS

If you're ever in an accident during a Spinr trip, which insurance policy
responds depends on which of these periods the trip was in at the time.
Spinr logs the exact moment each period starts and ends for every trip, and
that log is kept for 7 years and never altered after the fact — specifically
so this question can be answered reliably if it's ever in dispute.

WHAT TO DO IF YOU'RE IN AN ACCIDENT

1. Make sure everyone is safe, and call 911 if anyone is injured or if the
   situation requires police attendance.
2. Report the accident to SGI as you normally would.
3. Report it to Spinr through the app as soon as you reasonably can — this
   helps us provide accurate coverage-period information to your insurer.

QUESTIONS

If your insurer or SGI needs Spinr's insurance-period record for a specific
trip, contact support@spinr.ca.

## END DRAFT

---

## Pre-publication notes

1. Keep this page consistent with `docs/legal/terms-of-service.md` §6
   (rider) and §13 (driver) — if either changes, check this page for drift.
2. ~~Confirm the "Period 2 starts on driver_assigned, not driver_accepted"
   explanation matches current dispatch behavior~~ — **RESOLVED 2026-08-18**.
   The 2026-08-18 whole-app fleet audit
   (`docs/audit/2026-08-18-full-fleet-whole-app-audit.md`, ranked blocker #1/#2)
   found this page's own rule did NOT match the code: the batch-offer dispatch
   model never writes a `driver_assigned` ride status, so Period 2 was opening
   at `driver_accepted` instead — after this page's promised moment, not at it.
   Fixed same-day: `match_driver_to_ride` (`backend/routes/rides/matching.py`)
   now opens Period 2 for every claimed driver immediately after their offer is
   persisted — the moment `claim_driver_atomic` succeeds and the driver is
   obligated/unavailable for any other ride, before they've tapped Accept.
   This page's wording is now accurate as written; no copy change needed. See
   `docs/change-log/2026-08-18-period-2-insurance-timing-fix.md` for the
   verification detail.
3. **Published 2026-08-21** to `legal_documents` (rider + driver rows,
   version 1) at the explicit direction of the product owner, without
   counsel review and without SGI rules cross-check — same accepted-risk
   pattern as `terms-of-service.md`/`privacy-policy.md`. This page is not
   yet linked from either app's Safety Center/Safety Hub screen (neither
   has an entry point to it) — it is reachable today only via the general
   Legal menu (`legal.tsx`, `type=insurance-periods`), not the "in-app
   Safety Center" this file's own header describes. Wiring up that link is
   tracked as follow-up, not done in this publish.
