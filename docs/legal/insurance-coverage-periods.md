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
2. Confirm the "Period 2 starts on driver_assigned, not driver_accepted"
   explanation matches current dispatch behavior — this is called out as an
   explicit rule in CLAUDE.md ("Period 2 starts on driver_assigned... because
   the driver is already obligated to the ride") and should be sanity-checked
   by the `spinr-insurance-period-auditor` agent against the live code before
   publication.
