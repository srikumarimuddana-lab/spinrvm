"""`grand_total = 0` is ambiguous, and for now we only measure it.

Three money call sites resolved a ride's total with
``ride.get("grand_total") or ride.get("total_fare") or 0``. The ``or`` means
"if falsy", not "if missing", and those diverge exactly when ``grand_total`` is
**0** -- which has two incompatible meanings:

  * a genuinely free ride, or
  * a row predating migration 46, which added the column as
    ``DECIMAL(10,2) DEFAULT 0`` rather than NULL, so "never computed" is also 0.

Both fall back to ``total_fare``, the pre-tax subtotal. Correct for the legacy
row; an overcharge for the free ride -- and at the guest-corporate call site it
is the amount actually charged.

Picking either meaning without production data risks real money in both
directions (a visible overcharge, or a silent $0 settlement on a ride the driver
is still paid for). So `_ride_total_with_fallback` preserves the old arithmetic
EXACTLY and only counts the ambiguous case.

**That is what these tests are for.** The valuable assertion here is the
negative one: the returned number must equal what the old expression returned,
for every shape. If a later change starts "fixing" the value, these fail -- and
that change needs to be a deliberate decision with the data in hand, not a
drive-by.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.services.payment_service import _ride_total_with_fallback
from backend.utils import metrics

_METRIC = "spinr_payment_zero_grand_total_fallback_total"


def _counter(site: str, case: str = "legacy_row") -> float:
    """metrics._labels_to_key sorts the label pairs, so "case" precedes "site".

    The default is "legacy_row" because a ride dict with no `discount_amount`
    classifies that way — which is every fixture here except the free-ride one.
    """
    key = (("case", case), ("site", site))
    return metrics.snapshot()["counters"].get(_METRIC, {}).get(key, 0)


def _old_expression(ride: dict) -> Decimal:
    """Verbatim copy of the expression all three sites used before this change.

    Kept here, rather than hand-writing expected values, so the equivalence is
    checked against the real thing.
    """
    from backend.services.payment_service import _d, _round

    return _round(_d(ride.get("grand_total") or ride.get("total_fare") or 0))


class TestValueIsUnchanged:
    """The behaviour contract: instrument, do not alter."""

    @pytest.mark.parametrize(
        "ride",
        [
            {"grand_total": "44.40", "total_fare": "40.00"},  # normal, taxed
            {"grand_total": 44.40, "total_fare": 40.00},  # numeric from the driver
            {"grand_total": None, "total_fare": "40.00"},  # never set -> fall back
            {"total_fare": "40.00"},  # key absent -> fall back
            {"grand_total": 0, "total_fare": "40.00"},  # THE ambiguous case
            {"grand_total": "0.00", "total_fare": "40.00"},  # string zero is truthy
            {"grand_total": 0, "total_fare": 0},  # nothing to disagree with
            {},  # empty
            {"grand_total": "-5.00", "total_fare": "40.00"},  # negative is truthy
        ],
    )
    def test_matches_the_original_expression(self, ride):
        assert _ride_total_with_fallback(ride, site="test") == _old_expression(ride)


class TestAmbiguousCaseIsCounted:
    def test_numeric_zero_with_a_nonzero_fare_is_counted(self):
        before = _counter("probe_a")

        result = _ride_total_with_fallback({"grand_total": 0, "total_fare": "40.00"}, site="probe_a")

        assert _counter("probe_a") == before + 1
        # ...and still charges what it charged yesterday.
        assert result == Decimal("40.00")

    def test_the_site_label_is_carried_through(self):
        """Each call site gets its own label, because only one of the three is an
        amount actually charged -- the counter has to distinguish them."""
        before = _counter("guest_corporate_auto_settle")

        _ride_total_with_fallback({"grand_total": 0, "total_fare": "12.34"}, site="guest_corporate_auto_settle")

        assert _counter("guest_corporate_auto_settle") == before + 1


class TestTheCaseLabelDiscriminates:
    """`discount_amount` separates the two meanings deterministically, per row.

    fare_service's `grand_total = total_fare + fees + tax - discount` subtracts
    the discount from grand_total but NOT from total_fare. So a genuinely free
    ride carries a discount roughly equal to the fare, while a row predating
    migration 46 (which added the column DEFAULT 0) carries none. This is what
    makes one logged occurrence answer the question, instead of an aggregate
    counter that only says "it happened".
    """

    def test_a_discount_marks_it_a_free_ride(self):
        before = _counter("probe_free", "free_ride")

        result = _ride_total_with_fallback(
            {"grand_total": 0, "total_fare": "40.00", "discount_amount": "40.00"},
            site="probe_free",
        )

        assert _counter("probe_free", "free_ride") == before + 1
        # Still charges what it charged yesterday -- this classifies, it does
        # not yet change behaviour.
        assert result == Decimal("40.00")

    def test_no_discount_marks_it_a_legacy_row(self):
        before = _counter("probe_legacy", "legacy_row")
        _ride_total_with_fallback({"grand_total": 0, "total_fare": "40.00"}, site="probe_legacy")
        assert _counter("probe_legacy", "legacy_row") == before + 1

    def test_an_explicit_zero_discount_is_also_a_legacy_row(self):
        """A column present and 0 is the same evidence as absent: no discount
        was applied, so a $0 grand_total cannot be explained by one."""
        before = _counter("probe_zero_disc", "legacy_row")
        _ride_total_with_fallback(
            {"grand_total": 0, "total_fare": "40.00", "discount_amount": 0},
            site="probe_zero_disc",
        )
        assert _counter("probe_zero_disc", "legacy_row") == before + 1


class TestUnambiguousCasesAreNotCounted:
    """A counter that fires on the normal path is a counter nobody reads."""

    def test_a_missing_grand_total_is_not_ambiguous(self):
        """None means 'not set', which the fallback handles correctly and
        unambiguously -- no signal wanted."""
        before = _counter("probe_b")
        _ride_total_with_fallback({"grand_total": None, "total_fare": "40.00"}, site="probe_b")
        assert _counter("probe_b") == before

    def test_an_absent_key_is_not_ambiguous(self):
        before = _counter("probe_c")
        _ride_total_with_fallback({"total_fare": "40.00"}, site="probe_c")
        assert _counter("probe_c") == before

    def test_a_string_zero_is_not_ambiguous(self):
        """If PostgREST hands this column back as a string, "0.00" is truthy, so
        it is used as-is and the ambiguity never arises. Pinning this is how the
        production log line's `type=` field gets its meaning."""
        before = _counter("probe_d")
        _ride_total_with_fallback({"grand_total": "0.00", "total_fare": "40.00"}, site="probe_d")
        assert _counter("probe_d") == before

    def test_zero_against_a_zero_fare_is_not_ambiguous(self):
        """Nothing disagrees, so there is nothing to report."""
        before = _counter("probe_e")
        _ride_total_with_fallback({"grand_total": 0, "total_fare": 0}, site="probe_e")
        assert _counter("probe_e") == before

    def test_a_normal_taxed_ride_is_not_counted(self):
        before = _counter("probe_f")
        _ride_total_with_fallback({"grand_total": "44.40", "total_fare": "40.00"}, site="probe_f")
        assert _counter("probe_f") == before
