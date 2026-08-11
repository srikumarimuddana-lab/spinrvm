"""Legacy-import exclusion for driver earnings surfaces.

``services/booking_import_service.py`` imported the previous app's completed
bookings into ``rides`` so riders and drivers keep their trip HISTORY. Those
rides carry real ``driver_earnings``, and because the new app derives
``payable_balance`` live from completed rides, the importer also wrote one
offsetting ``payouts`` row per driver (``payout_type='legacy_import'``,
``status='completed'``) equal to the sum of their imported earnings. Net
payable delta per driver is exactly $0.

That keeps the *balance* right but makes every earnings TOTAL wrong:

- Lifetime/period earnings include money the previous app already paid.
- "Total paid out" includes the synthetic offset, which was never a real
  transfer to a bank.
- Worst of all the two halves land in DIFFERENT periods: an imported ride
  keeps its original ``ride_completed_at`` (a legacy date) while the offset
  payout is stamped with the IMPORT date. A weekly/monthly statement covering
  the legacy dates shows inflated earnings with no offset, and the statement
  covering the import date shows a large payout with no matching earnings.

Spinr's earnings reports, statements, history and balance therefore describe
THIS app's money only. Both halves are dropped together, so the arithmetic is
unchanged where it was already correct:

    before:  (real rides + legacy rides) - (real payouts + legacy offset)
    after:    real rides                 -  real payouts

— identical, because the offset equals the legacy rides by construction.
Dropping only one half would silently move a driver's payable balance, which
is why every caller must use both helpers together.

Imported rides remain fully visible in ride history and in the rider/driver
trip lists; this module only governs money math.

Only ``booking_import_service`` ever writes ``rides.legacy_import_metadata``,
so "metadata is NULL" is exactly "not a legacy-imported ride". The filter is
applied server-side (PostgREST ``is.null``) so legacy rows never consume the
query's row budget.
"""

from __future__ import annotations

from typing import Any

# Merge into a `rides` filter dict to exclude legacy-imported rides.
# `None` compiles to PostgREST `is.null` (repositories/_base.py) — a plain
# `= NULL` would match zero rows and silently zero out every earnings figure.
EXCLUDE_LEGACY_RIDES: dict[str, Any] = {"legacy_import_metadata": None}

# The synthetic offset the importer wrote to cancel the imported earnings.
# Never a real bank transfer — excluded from "paid out" alongside the rides.
LEGACY_OFFSET_PAYOUT_TYPE = "legacy_import"


def is_legacy_ride(ride: dict[str, Any]) -> bool:
    """True when this ride came from the previous app's booking import."""
    return bool(ride.get("legacy_import_metadata"))


def drop_legacy_rides(rides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Post-fetch companion to ``EXCLUDE_LEGACY_RIDES``.

    For callers that cannot add a filter to their query (repository helpers
    with a fixed signature, or rows already in hand).
    """
    return [r for r in rides if not is_legacy_ride(r)]


def is_legacy_offset_payout(payout: dict[str, Any]) -> bool:
    """True for the importer's synthetic offset row."""
    return payout.get("payout_type") == LEGACY_OFFSET_PAYOUT_TYPE


def drop_legacy_offset_payouts(payouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the offset rows that pair with legacy-imported rides."""
    return [p for p in payouts if not is_legacy_offset_payout(p)]
