"""services/incentive_service.py — the shared "which incentives apply" rule.

The rule used to be copy-pasted five times, and the three display copies had
drifted looser than the two settlement copies in ways that over-quoted the
driver. These pin the settlement semantics the shared matcher now encodes:

  - active only
  - scoped to the ride's service area, or globally scoped when the ride has
    none (NOT "every area's incentives")
  - matching the ride's vehicle type, or untyped
  - worth more than zero
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.incentive_service import (
    incentive_display_payload,
    match_ride_incentives,
)

pytestmark = pytest.mark.unit


class _Chain:
    """Supabase query-builder stand-in that records the filters applied."""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def select(self, *a, **kw):
        self.calls.append(("select", a))
        return self

    def eq(self, *a, **kw):
        self.calls.append(("eq", a))
        return self

    def or_(self, *a, **kw):
        self.calls.append(("or_", a))
        return self

    def is_(self, *a, **kw):
        self.calls.append(("is_", a))
        return self

    def execute(self):
        return MagicMock(data=self._rows)


def _db(rows):
    chain = _Chain(rows)
    db = MagicMock()
    db.supabase.table = MagicMock(return_value=chain)
    db.run_sync = AsyncMock(side_effect=lambda fn: fn())
    return db, chain


def _inc(**kw):
    base = {
        "id": "inc-1",
        "name": "Peak Bonus",
        "bonus_amount": "5.00",
        "incentive_type": "per_ride",
        "service_area_id": None,
        "vehicle_type_id": None,
    }
    base.update(kw)
    return base


async def test_area_scoped_ride_accepts_global_and_own_area():
    db, chain = _db([_inc()])
    matched = await match_ride_incentives(
        db, {"service_area_id": "area-1", "vehicle_type_id": "vt-std"}
    )
    assert [m["id"] for m in matched] == ["inc-1"]
    or_args = [c[1][0] for c in chain.calls if c[0] == "or_"]
    assert or_args == ["service_area_id.is.null,service_area_id.eq.area-1"]


async def test_ride_without_service_area_only_matches_global_incentives():
    """The regression this module exists for.

    Settlement restricts an area-less ride to globally-scoped incentives; the
    display paths applied no area filter at all and so quoted every area's
    bonus, none of which would ever be claimed.
    """
    db, chain = _db([_inc()])
    await match_ride_incentives(db, {"service_area_id": None, "vehicle_type_id": "vt-std"})
    assert ("is_", ("service_area_id", "null")) in chain.calls
    assert not [c for c in chain.calls if c[0] == "or_"]


async def test_zero_value_incentive_is_never_quoted():
    """Settlement skips bonus_amount <= 0; the display paths counted it and
    rendered a +$0.00 chip for an incentive disabled by zeroing."""
    db, _ = _db([_inc(id="zero", bonus_amount="0"), _inc(id="live", bonus_amount="2.25")])
    matched = await match_ride_incentives(db, {"service_area_id": "area-1"})
    assert [m["id"] for m in matched] == ["live"]


async def test_negative_incentive_is_skipped():
    db, _ = _db([_inc(bonus_amount="-1.00")])
    assert await match_ride_incentives(db, {"service_area_id": "area-1"}) == []


async def test_other_vehicle_type_is_skipped_and_untyped_is_kept():
    db, _ = _db([_inc(id="xl", vehicle_type_id="vt-xl"), _inc(id="any", vehicle_type_id=None)])
    matched = await match_ride_incentives(
        db, {"service_area_id": "area-1", "vehicle_type_id": "vt-std"}
    )
    assert [m["id"] for m in matched] == ["any"]


async def test_only_active_incentives_are_queried():
    db, chain = _db([])
    await match_ride_incentives(db, {"service_area_id": "area-1"})
    assert ("eq", ("is_active", True)) in chain.calls


async def test_db_failure_raises_rather_than_quoting_zero():
    """An empty result and a failed lookup mean different things to a driver
    being quoted a bonus — each caller decides how to degrade, so the matcher
    must not swallow the error into an empty list."""
    db = MagicMock()
    db.supabase.table = MagicMock(side_effect=RuntimeError("ride_incentives down"))
    with pytest.raises(RuntimeError):
        await match_ride_incentives(db, {"service_area_id": "area-1"})


def test_display_payload_shape_and_total():
    items, total = incentive_display_payload(
        [_inc(name="Peak Bonus", bonus_amount="5.00"), _inc(name="Airport", bonus_amount="1.50")]
    )
    assert items == [
        {"name": "Peak Bonus", "bonus_amount": 5.0, "incentive_type": "per_ride"},
        {"name": "Airport", "bonus_amount": 1.5, "incentive_type": "per_ride"},
    ]
    assert total == 6.5


def test_display_payload_totals_in_decimal_not_float():
    """0.1 + 0.2 in binary floats is 0.30000000000000004 — the driver-facing
    total must not carry that."""
    items, total = incentive_display_payload(
        [_inc(bonus_amount="0.10"), _inc(bonus_amount="0.20")]
    )
    assert total == 0.30
    assert Decimal(str(total)) == Decimal("0.30")


def test_display_payload_defaults_missing_name_and_type():
    """Both driver clients type incentive_type as always-present."""
    items, _ = incentive_display_payload([{"bonus_amount": "3.00"}])
    assert items == [{"name": "Incentive", "bonus_amount": 3.0, "incentive_type": "per_ride"}]
