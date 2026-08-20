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
may apply if you cancel more than 2 minutes after acceptance (currently 120
seconds by default, admin-configurable per service area), because the
driver has already committed time and travel to reach you. The app will
always show you whether a fee applies to that specific cancellation before
you confirm the cancellation — you will never be charged a fee you weren't
told about in advance.

The cancellation fee is a flat amount, currently $4.50 by default (admin-
configurable). $4.00 goes to the driver to compensate the time and travel
already spent reaching you, and $0.50 is a Spinr service portion. This fee
is separate from — and does not change — Spinr's 0% commission on the fare
of a completed ride: drivers still keep 100% of every fare they actually
drive.

NO-SHOW FEES

If a driver arrives at your pickup location and you do not show up within 5
minutes of arrival (currently 300 seconds by default, admin-configurable
per service area), and the driver cancels the trip as a result, a no-show
fee may apply. The app notifies you when your driver has arrived so you
have a chance to respond before a no-show fee is charged.

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

1. **Fixed 2026-08-19**: the dollar-amount paragraph previously claimed
   "Spinr does not keep any part of a cancellation fee" — that was factually
   wrong. `backend/schemas.py` defines both `cancellation_fee_driver`
   (default $4.00) and `cancellation_fee_admin` (default $0.50), both
   admin-configurable (`routes/admin/settings.py`), and
   `routes/rides/cancellation.py` actually charges and records both amounts
   on every fee-eligible cancellation. Corrected the paragraph to state the
   real split rather than a false no-commission claim, and to make clear
   this fee is distinct from the 0%-commission fare itself.
2. **Fixed 2026-08-20** (`spinr-legal-readiness-reviewer`): the post-acceptance
   grace period and no-show wait were filled in from real config —
   `backend/services/cancellation_service.py:58` reads
   `free_cancel_window_seconds` (default 120s = 2 minutes) and
   `backend/routes/drivers/ride_cancel.py:324` reads `noshow_wait_seconds`
   (default 300s = 5 minutes), both admin/service-area configurable the same
   way the fee-split amounts are (`backend/routes/admin/service_areas.py`).
   Worded as "currently N by default" to reflect that a service area can
   override it, matching how the fee-split paragraph is phrased.
3. **Still open — genuinely unverified, do not invent**: the dispute-window
   bracket (`[NUMBER, E.G. 60 DAYS]`). Read `backend/routes/disputes.py` in
   full — `create_dispute()` has no time-based cutoff at all; it only checks
   the ride is completed/cancelled and that no other dispute is already open
   on that ride. There is no `dispute_window_days` or any expiry check
   anywhere in the file. This is a genuine gap between what the draft wants
   to promise and what the backend enforces — either product/fare-config
   needs to pick a number, or the backend needs to actually enforce a window
   before this page can cite one as a hard promise. Do not guess.
3. Confirm this doesn't duplicate content already correct in
   `docs/legal/terms-of-service.md` §5 in a way that could drift out of
   sync — consider this page authoritative on specifics, and simplify §5 in
   the Terms of Service to reference it, next time that document is revised.
