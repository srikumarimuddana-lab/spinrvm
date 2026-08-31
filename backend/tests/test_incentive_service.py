"""services/incentive_service.py — the shared "which incentives apply" rule.

Two layers are pinned here:

1. The historical filters (active / service area / vehicle type / amount > 0),
   which were copy-pasted five times and had drifted looser on the three
   display paths than on the two settlement paths.
2. The eligibility columns migration 96 added and NOTHING honoured until
   migration 375 — start_date/end_date, the conditions JSONB, bonus_type
   ='percentage' and the max_budget cap. Those are gated on
   `incentive_eligibility_enforced`, so both flag states are pinned: off must
   reproduce the pre-375 behaviour exactly.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.services.incentive_service import (
    MatchedIncentive,
    incentive_display_payload,
    match_ride_incentives,
    record_incentive_claims,
)

pytestmark = pytest.mark.anyio

# 14:00 in America/Regina (UTC-6, no DST) — outside a 07-09/16-19 peak window.
NOW = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)

RIDE = {
    "service_area_id": "area-1",
    "vehicle_type_id": "vt-std",
    "distance_km": 10,
    "driver_earnings": 15,
}


class _Chain:
    """Query-builder stand-in that applies eq/in_ for real, so a filter the code
    relies on cannot be trivially satisfied by a stub that ignores it."""

    def __init__(self, rows, log):
        self.rows, self.log = list(rows), log

    def select(self, *a, **kw):
        return self

    def eq(self, col, val, *a, **kw):
        self.log.append(("eq", (col, val)))
        if col != "is_active":
            self.rows = [r for r in self.rows if r.get(col) == val]
        return self

    def or_(self, *a, **kw):
        self.log.append(("or_", a))
        return self

    def is_(self, *a, **kw):
        self.log.append(("is_", a))
        return self

    def in_(self, col, vals, *a, **kw):
        self.log.append(("in_", (col, tuple(vals))))
        self.rows = [r for r in self.rows if r.get(col) in set(vals)]
        return self

    def execute(self):
        return MagicMock(data=self.rows)


class _DB:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.log, self.inserted, self.updated = [], [], []
        self.area_row = None
        self.find_one_raises = False
        self.supabase = MagicMock()
        self.supabase.table = lambda name: _Chain(self.tables.get(name, []), self.log)

    async def run_sync(self, fn):
        return fn()

    async def insert_one(self, table, row):
        self.inserted.append((table, row))
        self.tables.setdefault(table, []).append(row)

    async def update_one(self, table, filt, updates):
        self.updated.append((table, filt, updates))

    async def find_one(self, table, filters):
        self.log.append(("find_one", (table, tuple(sorted(filters.items())))))
        if self.find_one_raises:
            raise RuntimeError("service_areas down")
        return self.area_row


def _inc(**kw):
    base = {
        "id": "inc-1",
        "name": "Peak Bonus",
        "bonus_amount": "5.00",
        "bonus_type": "flat",
        "incentive_type": "per_ride",
        "conditions": {},
        "service_area_id": None,
        "vehicle_type_id": None,
        "start_date": None,
        "end_date": None,
        "max_budget": None,
    }
    base.update(kw)
    return base


async def _match(rows, *, ride=RIDE, claims=None, enforce=True, now=NOW):
    db = _DB({"ride_incentives": rows, "ride_incentive_claims": claims or []})
    return db, await match_ride_incentives(db, ride, now=now, enforce=enforce)


# ── Historical filters (apply in both flag states) ──────────────────────────


async def test_area_scoped_ride_accepts_global_and_own_area():
    db, matched = await _match([_inc()], enforce=False)
    assert [m.id for m in matched] == ["inc-1"]
    or_args = [c[1][0] for c in db.log if c[0] == "or_"]
    assert or_args == ["service_area_id.is.null,service_area_id.eq.area-1"]


async def test_ride_without_service_area_only_matches_global_incentives():
    """Settlement restricted an area-less ride to globally-scoped incentives;
    the display paths applied no area filter and quoted every area's bonus."""
    db, _ = await _match([_inc()], ride={**RIDE, "service_area_id": None}, enforce=False)
    assert ("is_", ("service_area_id", "null")) in db.log
    assert not [c for c in db.log if c[0] == "or_"]


async def test_zero_and_negative_incentives_are_never_quoted():
    _, matched = await _match(
        [_inc(id="zero", bonus_amount="0"), _inc(id="neg", bonus_amount="-1"), _inc(id="live")],
        enforce=False,
    )
    assert [m.id for m in matched] == ["live"]


async def test_other_vehicle_type_is_skipped_and_untyped_is_kept():
    _, matched = await _match(
        [_inc(id="xl", vehicle_type_id="vt-xl"), _inc(id="any", vehicle_type_id=None)],
        enforce=False,
    )
    assert [m.id for m in matched] == ["any"]


async def test_only_active_incentives_are_queried():
    db, _ = await _match([], enforce=False)
    assert ("eq", ("is_active", True)) in db.log


async def test_db_failure_raises_rather_than_quoting_zero():
    db = _DB()
    db.supabase.table = MagicMock(side_effect=RuntimeError("ride_incentives down"))
    with pytest.raises(RuntimeError):
        await match_ride_incentives(db, RIDE, enforce=False)


# ── Flag OFF must reproduce pre-375 behaviour exactly ───────────────────────


async def test_flag_off_still_pays_an_expired_over_budget_incentive():
    """The whole point of the rollout flag: off changes nothing, so this ships
    dark and can be verified on staging before it moves any money."""
    _, matched = await _match(
        [_inc(end_date="2026-01-01T00:00:00Z", max_budget=100)],
        claims=[{"ride_id": "r0", "incentive_id": "inc-1", "bonus_amount": "999"}],
        enforce=False,
    )
    assert [str(m.bonus) for m in matched] == ["5.00"]


async def test_flag_off_pays_a_percentage_incentive_as_dollars():
    """Pre-375 bug, preserved while the flag is off: bonus_amount=10 on a
    percentage row was paid as $10, not 10%."""
    _, matched = await _match([_inc(bonus_amount="10", bonus_type="percentage")], enforce=False)
    assert matched[0].bonus == Decimal("10.00")


# ── Flag ON: date window ────────────────────────────────────────────────────


async def test_expired_campaign_is_skipped():
    _, matched = await _match([_inc(end_date="2026-01-01T00:00:00Z")])
    assert matched == []


async def test_campaign_not_yet_started_is_skipped():
    _, matched = await _match([_inc(start_date="2026-12-01T00:00:00Z")])
    assert matched == []


async def test_campaign_inside_its_window_is_paid():
    _, matched = await _match(
        [_inc(start_date="2026-01-01T00:00:00Z", end_date="2026-12-31T00:00:00Z")]
    )
    assert len(matched) == 1


async def test_unparseable_date_bound_fails_open():
    """A malformed date must not silently withhold a bonus a driver was quoted;
    it logs at error and the bound is treated as open."""
    _, matched = await _match([_inc(end_date="not-a-date")])
    assert len(matched) == 1


# ── Flag ON: budget cap ─────────────────────────────────────────────────────


async def test_budget_cap_is_enforced_from_the_claims_ledger():
    """budget_used was never incremented by anything, so the ledger — not that
    column — is the source of truth for how much a campaign has paid out."""
    _, matched = await _match(
        [_inc(max_budget=100)],
        claims=[{"ride_id": "r0", "incentive_id": "inc-1", "bonus_amount": "96"}],
    )
    assert matched == []


async def test_bonus_that_exactly_reaches_the_cap_is_still_paid():
    _, matched = await _match(
        [_inc(max_budget=100)],
        claims=[{"ride_id": "r0", "incentive_id": "inc-1", "bonus_amount": "95"}],
    )
    assert len(matched) == 1


async def test_uncapped_incentive_skips_the_ledger_read_entirely():
    """The budget sum runs on the dispatch hot path, so an uncapped fleet must
    not pay for a query it cannot use."""
    db, matched = await _match([_inc(max_budget=None)])
    assert len(matched) == 1
    assert not [c for c in db.log if c[0] == "in_"]


# ── Flag ON: conditions JSONB ───────────────────────────────────────────────


async def test_min_distance_condition_is_enforced_on_the_booked_distance():
    _, short = await _match([_inc(conditions={"min_distance_km": 20})])
    _, long_enough = await _match([_inc(conditions={"min_distance_km": 5})])
    assert short == []
    assert len(long_enough) == 1


async def test_peak_hours_are_evaluated_in_local_time_not_utc():
    """NOW is 20:00 UTC = 14:00 in America/Regina. A 16-19 window must not
    match; evaluating in UTC would wrongly place it inside."""
    _, outside = await _match([_inc(conditions={"peak_hours": [7, 9, 16, 19]})])
    _, inside = await _match([_inc(conditions={"peak_hours": [12, 15]})])
    assert outside == []
    assert len(inside) == 1


async def test_malformed_peak_hours_fails_open():
    _, matched = await _match([_inc(conditions={"peak_hours": [7, 9, 16]})])
    assert len(matched) == 1


async def test_empty_conditions_impose_no_constraint():
    _, matched = await _match([_inc(incentive_type="min_distance", conditions={})])
    assert len(matched) == 1


# ── Flag ON: percentage bonuses ─────────────────────────────────────────────


async def test_percentage_bonus_resolves_against_the_driver_fare_share():
    _, matched = await _match([_inc(bonus_amount="10", bonus_type="percentage")])
    assert matched[0].bonus == Decimal("1.50")  # 10% of driver_earnings 15


async def test_percentage_bonus_rounding_to_zero_is_dropped():
    _, matched = await _match(
        [_inc(bonus_amount="1", bonus_type="percentage")],
        ride={**RIDE, "driver_earnings": 0.2},
    )
    assert matched == []


# ── Rollout-switch resolution (migration 376) ───────────────────────────────
#
# enforce = settings.incentive_eligibility_enforced
#           OR service_areas.incentive_eligibility_enforced
#
# Incentives are configured per service area, so the switch governing them is
# too. OR, not AND: requiring both would make a freshly-enabled area silently
# do nothing until the global was also on.


async def _resolve(monkeypatch, *, global_flag, area, ride=None, db=None):
    """Run the real resolution path with the global setting stubbed."""
    import backend.services.incentive_service as svc

    async def _settings():
        return {"incentive_eligibility_enforced": global_flag}

    monkeypatch.setattr(svc, "get_app_settings", _settings)
    db = db or _DB({"ride_incentives": [_inc()], "ride_incentive_claims": []})
    db.area_row = area
    matched = await svc.match_ride_incentives(
        db, ride if ride is not None else RIDE, now=NOW, service_area=area
    )
    return db, matched


async def test_area_flag_alone_enables_enforcement(monkeypatch):
    """The staged rollout: one city on, the fleet switch still off."""
    _, matched = await _resolve(
        monkeypatch,
        global_flag=False,
        area={"id": "area-1", "incentive_eligibility_enforced": True},
    )
    # Enforcement is ON, so the expired incentive below would be dropped —
    # prove it by using one that only enforcement would reject.
    db = _DB({"ride_incentives": [_inc(end_date="2026-01-01T00:00:00Z")], "ride_incentive_claims": []})
    _, expired = await _resolve(
        monkeypatch,
        global_flag=False,
        area={"id": "area-1", "incentive_eligibility_enforced": True},
        db=db,
    )
    assert len(matched) == 1
    assert expired == []


async def test_global_flag_alone_enables_enforcement(monkeypatch):
    """The fleet-wide master switch still works with the area flag off."""
    db = _DB({"ride_incentives": [_inc(end_date="2026-01-01T00:00:00Z")], "ride_incentive_claims": []})
    _, matched = await _resolve(
        monkeypatch,
        global_flag=True,
        area={"id": "area-1", "incentive_eligibility_enforced": False},
        db=db,
    )
    assert matched == []


async def test_both_off_leaves_enforcement_off(monkeypatch):
    """Default state: an expired incentive still pays, exactly as pre-375."""
    db = _DB({"ride_incentives": [_inc(end_date="2026-01-01T00:00:00Z")], "ride_incentive_claims": []})
    _, matched = await _resolve(
        monkeypatch,
        global_flag=False,
        area={"id": "area-1", "incentive_eligibility_enforced": False},
        db=db,
    )
    assert len(matched) == 1


async def test_ride_without_an_area_falls_back_to_the_global_switch(monkeypatch):
    """No area row exists to carry a per-area flag, so only the global one can
    govern such a ride."""
    db = _DB({"ride_incentives": [_inc(end_date="2026-01-01T00:00:00Z")], "ride_incentive_claims": []})
    _, matched = await _resolve(
        monkeypatch,
        global_flag=True,
        area=None,
        ride={**RIDE, "service_area_id": None},
        db=db,
    )
    assert matched == []


async def test_area_is_fetched_when_the_caller_does_not_supply_it(monkeypatch):
    """Callers without the row still get per-area resolution."""
    import backend.services.incentive_service as svc

    async def _settings():
        return {"incentive_eligibility_enforced": False}

    monkeypatch.setattr(svc, "get_app_settings", _settings)
    db = _DB({"ride_incentives": [_inc(end_date="2026-01-01T00:00:00Z")], "ride_incentive_claims": []})
    db.area_row = {"id": "area-1", "incentive_eligibility_enforced": True}

    matched = await svc.match_ride_incentives(db, RIDE, now=NOW)

    assert matched == []
    assert any(c[0] == "find_one" for c in db.log), "area should have been fetched"


async def test_supplying_the_area_avoids_the_lookup(monkeypatch):
    """Dispatch already holds the row; resolving the flag must not add a query
    to the offer→accept path."""
    import backend.services.incentive_service as svc

    async def _settings():
        return {"incentive_eligibility_enforced": False}

    monkeypatch.setattr(svc, "get_app_settings", _settings)
    db = _DB({"ride_incentives": [_inc()], "ride_incentive_claims": []})

    await svc.match_ride_incentives(
        db, RIDE, now=NOW, service_area={"id": "area-1", "incentive_eligibility_enforced": False}
    )

    assert not [c for c in db.log if c[0] == "find_one"]


async def test_area_lookup_failure_falls_back_to_the_global_switch(monkeypatch):
    """An unreadable area must not flip enforcement ON for a ride quoted
    without it — the damaging direction is denying a promised bonus."""
    import backend.services.incentive_service as svc

    async def _settings():
        return {"incentive_eligibility_enforced": False}

    monkeypatch.setattr(svc, "get_app_settings", _settings)
    db = _DB({"ride_incentives": [_inc(end_date="2026-01-01T00:00:00Z")], "ride_incentive_claims": []})
    db.find_one_raises = True

    matched = await svc.match_ride_incentives(db, RIDE, now=NOW)

    assert len(matched) == 1, "failed area read must leave enforcement off"


# ── Display payload ─────────────────────────────────────────────────────────


def test_display_payload_shape_and_total():
    items, total = incentive_display_payload(
        [
            MatchedIncentive(id="a", name="Peak Bonus", incentive_type="per_ride", bonus=Decimal("5.00")),
            MatchedIncentive(id="b", name="Airport", incentive_type="area_boost", bonus=Decimal("1.50")),
        ]
    )
    assert items == [
        {"name": "Peak Bonus", "bonus_amount": 5.0, "incentive_type": "per_ride"},
        {"name": "Airport", "bonus_amount": 1.5, "incentive_type": "area_boost"},
    ]
    assert total == 6.5


def test_display_payload_totals_in_decimal_not_float():
    """0.1 + 0.2 in binary floats is 0.30000000000000004."""
    _, total = incentive_display_payload(
        [
            MatchedIncentive(id="a", name="A", incentive_type="per_ride", bonus=Decimal("0.10")),
            MatchedIncentive(id="b", name="B", incentive_type="per_ride", bonus=Decimal("0.20")),
        ]
    )
    assert total == 0.30


# ── Claim recording ─────────────────────────────────────────────────────────


async def test_claims_are_written_and_budget_used_refreshed():
    db = _DB({"ride_incentive_claims": [{"ride_id": "r0", "incentive_id": "inc-1", "bonus_amount": "90"}]})
    matched = [MatchedIncentive(id="inc-1", name="Peak", incentive_type="per_ride", bonus=Decimal("5.00"))]

    total = await record_incentive_claims(db, "ride-9", "drv-1", matched, now=NOW)

    assert total == Decimal("5.00")
    assert [r["incentive_id"] for _, r in db.inserted] == ["inc-1"]
    assert db.inserted[0][1]["ride_id"] == "ride-9"
    # Recomputed from the ledger (90 prior + 5 new), never incremented in place.
    assert db.updated == [("ride_incentives", {"id": "inc-1"}, {"budget_used": 95.0})]


async def test_a_retried_settlement_never_pays_the_same_bonus_twice():
    db = _DB({"ride_incentive_claims": [{"ride_id": "ride-9", "incentive_id": "inc-1", "bonus_amount": "5.00"}]})
    matched = [MatchedIncentive(id="inc-1", name="Peak", incentive_type="per_ride", bonus=Decimal("5.00"))]

    total = await record_incentive_claims(db, "ride-9", "drv-1", matched, now=NOW)

    assert total == Decimal("0")
    assert db.inserted == []


async def test_a_claim_on_another_ride_does_not_block_this_one():
    db = _DB({"ride_incentive_claims": [{"ride_id": "other", "incentive_id": "inc-1", "bonus_amount": "5.00"}]})
    matched = [MatchedIncentive(id="inc-1", name="Peak", incentive_type="per_ride", bonus=Decimal("5.00"))]

    assert await record_incentive_claims(db, "ride-9", "drv-1", matched, now=NOW) == Decimal("5.00")


async def test_nothing_matched_writes_nothing():
    db = _DB()
    assert await record_incentive_claims(db, "ride-9", "drv-1", [], now=NOW) == Decimal("0")
    assert db.inserted == []
