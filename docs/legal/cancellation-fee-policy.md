# Spinr Cancellation & No-Show Fee Policy — Draft for Legal Review

> **What this is.** Promotes ToS §5's one paragraph into a standalone,
> linkable reference page — the thing a rider actually searches for
> mid-dispute ("why was I charged a cancellation fee") shouldn't require
> re-reading the full Terms of Service. Meant to be linked directly from the
> receipt/dispute flow, not just reachable through the Legal menu.
>
> **This is a draft, not legal advice.** The specific dollar amounts and
> time windows below are placeholders — they are pricing/product decisions,
> not legal ones, and must come from the actual fare-service configuration,
> not be invented here. Cross-check against `services/fare_service.py` and
> `.claude/context/domain-payments.md` before filling them in.

---

## BEGIN DRAFT

SPINR CANCELLATION AND NO-SHOW FEE POLICY

Last updated: [INSERT PUBLICATION DATE]

This page explains when a cancellation or no-show fee applies. It doesn't
replace the Terms of Service — if anything here conflicts with the Terms of
Service, the Terms of Service controls.

CANCELLING BEFORE A DRIVER ACCEPTS

You can cancel a ride request before a driver accepts it at no charge.

CANCELLING AFTER A DRIVER ACCEPTS

Once a driver has accepted your ride and is on the way, a cancellation fee
may apply if you cancel more than [NUMBER, E.G. 2 MINUTES] after acceptance,
because the driver has already committed time and travel to reach you. The
app will always show you whether a fee applies to that specific cancellation
before you confirm the cancellation — you will never be charged a fee you
weren't told about in advance.

The cancellation fee is [AMOUNT, E.G. A FLAT $X OR A FORMULA] and goes to
the driver, consistent with Spinr's no-commission model — Spinr does not
keep any part of a cancellation fee.

NO-SHOW FEES

If a driver arrives at your pickup location and you do not show up within
[NUMBER, E.G. 5 MINUTES] of arrival, and the driver cancels the trip as a
result, a no-show fee may apply. The app notifies you when your driver has
arrived so you have a chance to respond before a no-show fee is charged.

CANCELLING AFTER THE TRIP STARTS

Once a trip is in progress, it cannot be cancelled by either party — this
matches Spinr's ride-state rules (a trip in the `in_progress` state can only
move to `completed`). If something goes wrong mid-trip, contact Support
after the trip ends, or use the in-app SOS feature if you are in immediate
danger.

DRIVER CANCELLATIONS

If a driver cancels after accepting your ride, you are not charged a
cancellation fee. Repeated driver cancellations may affect the rides that
driver is offered, as described in the Independent Contractor Agreement.

DISPUTING A CANCELLATION OR NO-SHOW FEE

If you believe a cancellation or no-show fee was charged in error, contact
Support through the app within [NUMBER, E.G. 60 DAYS] of the charge. Spinr
will review the trip's timeline and issue a refund where the fee was applied
incorrectly.

## END DRAFT

---

## Pre-publication notes

1. **Every dollar amount and time window is a placeholder** — pull the real
   figures from `services/fare_service.py` / the cancellation-fee
   configuration, and keep this page in sync if those figures change (this
   is exactly the kind of drift risk a standalone reference page creates —
   worth a code comment pointing back here if feasible).
2. Confirm this doesn't duplicate content already correct in
   `docs/legal/terms-of-service.md` §5 in a way that could drift out of
   sync — consider this page authoritative on specifics, and simplify §5 in
   the Terms of Service to reference it, next time that document is revised.
