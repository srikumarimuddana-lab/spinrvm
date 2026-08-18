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

from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

# Merge into a `rides` filter dict to exclude legacy-imported rides.
#
# A26 (docs/audit/2026-08-11-driver-rider-migration-audit.md, found
# 2026-08-11, confirmed live against production): this used to be
# {"legacy_import_metadata": None}, which repositories/_base.py compiles to
# PostgREST `is.null` — real SQL `IS NULL`. That's the right pattern for an
# ordinary nullable column, but `rides.legacy_import_metadata` is declared
# `NOT NULL DEFAULT '{}'::jsonb` (migration 268) — no row, imported or not,
# can ever be SQL NULL there. `IS NULL` against a `NOT NULL` column matches
# ZERO ROWS, ALWAYS — not "zero legacy rows," but every row the filter
# touches. Verified live: a driver with 1 real, non-legacy completed ride
# got 0 rows back from the exact query this constant used to compile to,
# meaning `total_rides`/`total_earnings` on their balance screen read 0/
# $0.00 despite having real, unpaid-out earnings.
#
# The correct predicate is equality against the column's own "not imported"
# default, `'{}'::jsonb` — but a bare `{"legacy_import_metadata": {}}` value
# doesn't work either: `_apply_filters` treats ANY dict value as an
# operator-map (for `$gte`/`$in`/etc.), so an empty dict is read as "no
# operators" and silently applies no filter at all, which would widen every
# query using this constant to include legacy rows again. `$eq` makes the
# intent explicit and unambiguous.
EXCLUDE_LEGACY_RIDES: dict[str, Any] = {"legacy_import_metadata": {"$eq": {}}}

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


# ── Driver-facing sunset for previous-app payout history ────────────────
#
# The "Previous app" presentation (payout-history section, statement PDF
# notes and rows, the balance-card note) is TRANSITION messaging: it exists
# so migrated drivers can reconcile what they remember being paid. The
# operator set an end date — after 2026-08-31 (inclusive, America/Regina,
# the statements timezone) driver-facing surfaces stop showing previous-app
# money entirely.
#
# The sunset is PRESENTATION ONLY. The stripe_sync rows themselves are never
# deleted or filtered from admin surfaces, T4A/tax exports, or the stored
# statement totals — tax reporting and the 7-year Saskatchewan retention
# rules do not care about the app's transition UX.
PREVIOUS_APP_VISIBLE_UNTIL = date(2026, 8, 31)
_OPS_TZ = ZoneInfo("America/Regina")


def previous_app_history_visible(today: date | None = None) -> bool:
    """True while driver-facing surfaces should still show previous-app money.

    ``today`` is injectable for tests; production callers pass nothing and
    get the operator's calendar day (America/Regina), so the switch flips at
    local midnight, not UTC.
    """
    if today is None:
        today = datetime.now(_OPS_TZ).date()
    return today <= PREVIOUS_APP_VISIBLE_UNTIL


def is_legacy_offset_payout(payout: dict[str, Any]) -> bool:
    """True for the importer's synthetic offset row."""
    return payout.get("payout_type") == LEGACY_OFFSET_PAYOUT_TYPE


def drop_legacy_offset_payouts(payouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the offset rows that pair with legacy-imported rides."""
    return [p for p in payouts if not is_legacy_offset_payout(p)]


# ── CR-4108 (issue #4108, D1 decision option (a): "re-label, don't rewrite") ─
#
# For the 186 legacy-imported rides, ``rides.tax_amount`` /
# ``tax_breakdown`` hold a real number, but the WRONG BASE for "GST the
# rider paid on this fare": bookings.csv's "gst" column is
# commission_gst_amount (GST on Spinr's own platform-commission fee), not
# fare-GST — see services/booking_import_service.py's comment on the
# mismatch, confirmed 2026-08-15 by sampling every row in the export. The
# correct historical fare-GST figure is not recoverable from the export.
#
# Product-owner decision (approved, see docs/change-log for this CR): do
# NOT touch tax_amount's stored value anywhere — instead, every surface
# that displays/exports it computes a label distinguishing the two
# meanings at serialization time. Never persisted; never used to alter the
# underlying figure.
TAX_BASIS_FARE_GST = "fare_gst"
TAX_BASIS_COMMISSION_GST_LEGACY_IMPORT = "commission_gst_legacy_import"

# Short, human-readable footnote for rider/admin-facing documents (receipts,
# invoices) where a legacy row's tax_amount is shown. Kept short enough to
# sit as a single receipt/invoice line without disrupting layout for the
# 99%+ of rides this never applies to.
LEGACY_TAX_NOTE = (
    "Tax shown for this ride is Spinr's platform-fee GST from the previous "
    "app (commission-GST), carried over at import — not GST calculated on "
    "the fare. The original fare-GST figure was not preserved by the import."
)


def tax_basis_for_ride(ride: dict[str, Any]) -> str:
    """Computed, display-only label for what ``ride``'s ``tax_amount`` /
    ``tax_breakdown`` represent — ``TAX_BASIS_COMMISSION_GST_LEGACY_IMPORT``
    for one of the 186 legacy-imported rides, ``TAX_BASIS_FARE_GST``
    (the normal, correct meaning) for every other ride.

    Derived from ``legacy_import_metadata`` presence on every call; never
    stored, and never used to change ``tax_amount``'s numeric value.
    """
    return TAX_BASIS_COMMISSION_GST_LEGACY_IMPORT if is_legacy_ride(ride) else TAX_BASIS_FARE_GST


def legacy_tax_note_for_ride(ride: dict[str, Any]) -> Optional[str]:
    """``LEGACY_TAX_NOTE`` for a legacy-imported ride, else ``None`` — so a
    caller can drop the note field entirely (rather than emit an empty
    string) for the non-legacy common case."""
    return LEGACY_TAX_NOTE if is_legacy_ride(ride) else None
