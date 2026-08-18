"""Unit tests for backend/services/booking_import_service.py.

These cover the plan builder against an in-memory fake Supabase client (no real
DB), focused on the money invariants that keep imported history from creating
withdrawable driver balance, the hazard fields that keep imported rides out of
the live background loops, and the resume/idempotency semantics.
"""

from decimal import Decimal

import pytest

from backend.services import booking_import_service as svc

SERVICE_AREA = {"id": "sa-1", "name": "Saskatoon"}
VEHICLE_TYPE = {"id": "vt-1", "name": "Economy"}
BATCH = "20260729000000"


class _FakeExecute:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._count = None

    def select(self, _cols, count=None):
        self._count = count
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def ilike(self, col, val):
        self._filters.append(("ilike", col, val))
        return self

    def filter(self, col, op, val):
        # Only the JSONB arrow form the service uses: "col->>key"
        self._filters.append((op, col, val))
        return self

    def limit(self, _n):
        return self

    def insert(self, rows):
        self.store.setdefault(self.table, []).extend(rows)
        return self

    def update(self, values):
        self._update = values
        return self

    def execute(self):
        if getattr(self, "_update", None) is not None:
            rows = list(self.store.get(self.table, []))
            for op, col, val in self._filters:
                if op == "eq":
                    rows = [r for r in rows if r.get(col) == val]
            for r in rows:
                r.update(self._update)
            return _FakeExecute(rows)

        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if "->>" in col:
                base, key = col.split("->>", 1)
                rows = [r for r in rows if (r.get(base) or {}).get(key) == val]
            elif op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
            elif op == "ilike":
                needle = str(val).strip("%").lower()
                rows = [r for r in rows if needle in str(r.get(col, "")).lower()]
        return _FakeExecute(rows, count=len(rows) if self._count == "exact" else None)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _FakeQuery(name, self.store)


def _install_fake(monkeypatch, *, users=None, drivers=None, rides=None, payouts=None):
    store = {
        "users": users if users is not None else [{"id": "user-1", "phone": "+13065551111"}],
        "drivers": drivers if drivers is not None else [{"id": "drv-1", "phone": "+13065552222"}],
        "rides": rides or [],
        "payouts": payouts or [],
        "service_areas": [SERVICE_AREA],
        "vehicle_types": [VEHICLE_TYPE],
    }
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))
    return store


def _booking(**overrides):
    """A completed legacy booking: $20.53 charged, $16.72 to the driver."""
    row = {
        "_id": "bk-1",
        "booking_id": "CB1234567",
        "booking_status": "completed",
        "customer_id": "cus-1",
        "driver_id": "drv-legacy-1",
        "created_at": "1770197933837",
        "start_ride_at": "1770198000000",
        "complete_delivery_at": "1770198600000",
        "arrived_pickup_loc_at": "1770197950000",
        "pickup_address": "123 Main St, Regina, SK",
        "pickup_lat": "50.4099027",
        "pickup_long": "-104.6554126",
        "drop_address": "456 Broad St, Regina, SK",
        "drop_lat": "50.4059968",
        "drop_long": "-104.6377971",
        "distance_in_km": "2.4",
        "total_amount": "20.53",
        "gst": "0.98",
        "city_fees": "0.30",
        "infrastructure_fees": "0.20",
        "insurance_fees": "0.10",
        "coupon_discount": "0",
        "tip_driver": "0",
        "you_earn": "16.72",
        "payment_method": "card",
    }
    row.update(overrides)
    return row


def _customer(**overrides):
    row = {"_id": "cus-1", "country_code": "1", "phone": "3065551111", "name": "Legacy Rider"}
    row.update(overrides)
    return row


def _driver(**overrides):
    row = {"_id": "drv-legacy-1", "country_code": "1", "phone": "3065552222", "name": "Legacy Driver"}
    row.update(overrides)
    return row


def _earning(**overrides):
    row = {
        "_id": "earn-1",
        "booking_id": "bk-1",
        "amount": "16.72",
        "admin_comission_amount": "3.09",
        "booking_amount": "20.53",
        "earning_type": "salary",
    }
    row.update(overrides)
    return row


def _plan(bookings, customers=None, drivers=None, earnings=None):
    return svc.build_plan(
        bookings,
        customers if customers is not None else [_customer()],
        drivers if drivers is not None else [_driver()],
        earnings if earnings is not None else [_earning()],
        service_area=SERVICE_AREA,
        vehicle_type=VEHICLE_TYPE,
        batch=BATCH,
    )


# --------------------------------------------------------------------------
# Money invariants — the payable-balance contract
# --------------------------------------------------------------------------


def test_payable_components_equal_legacy_driver_earning(monkeypatch):
    """Σ(base+distance+time+tip) must equal what the driver actually earned."""
    _install_fake(monkeypatch)
    plan = _plan([_booking()])
    assert not plan.errors
    (ride,) = plan.rides_to_insert
    payable = ride["base_fare"] + ride["distance_fare"] + ride["time_fare"] + ride["tip_amount"]
    assert payable == pytest.approx(16.72)
    assert ride["driver_earnings"] == pytest.approx(16.72)


def test_offset_payout_cancels_imported_earnings_exactly(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan(
        [_booking(), _booking(_id="bk-2", booking_id="CB7654321")],
        earnings=[_earning(), _earning(_id="earn-2", booking_id="bk-2")],
    )
    (payout,) = plan.payouts_to_insert
    imported = sum(r["base_fare"] + r["distance_fare"] + r["time_fare"] + r["tip_amount"] for r in plan.rides_to_insert)
    # str(Decimal), not float -- payouts.amount is NUMERIC as of migration 331.
    assert isinstance(payout["amount"], str)
    assert float(payout["amount"]) == pytest.approx(imported)
    assert payout["driver_id"] == "drv-1"
    assert payout["status"] == "completed"
    assert payout["payout_type"] == "legacy_import"
    assert payout["id"] == f"legacy-import-{BATCH}-drv-1"


def test_tip_is_carried_in_tip_amount_not_double_counted(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(tip_driver="3.00", total_amount="23.53")], earnings=[_earning(amount="19.72")])
    (ride,) = plan.rides_to_insert
    assert ride["tip_amount"] == pytest.approx(3.00)
    # base_fare holds the earning net of tip, so the sum still equals 19.72
    assert ride["base_fare"] == pytest.approx(16.72)
    payable = ride["base_fare"] + ride["distance_fare"] + ride["time_fare"] + ride["tip_amount"]
    assert payable == pytest.approx(19.72)
    # snapshot total matches too
    assert ride["driver_earnings_snapshot"]["total"] == pytest.approx(19.72)
    tip_lines = [ln for ln in ride["fare_breakdown_snapshot"]["lines"] if ln["type"] == "tip"]
    assert len(tip_lines) == 1, "an explicit tip line prevents the history endpoint appending a synthetic one"


def test_receipt_lines_sum_to_legacy_total_charged(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan(
        [_booking(coupon_discount="1.50", tip_driver="2.00", total_amount="21.03")], earnings=[_earning(amount="18.72")]
    )
    (ride,) = plan.rides_to_insert
    lines_sum = sum(ln["amount"] for ln in ride["fare_breakdown_snapshot"]["lines"])
    assert lines_sum == pytest.approx(21.03)
    assert ride["fare_breakdown_snapshot"]["grand_total"] == pytest.approx(21.03)


def test_grand_total_is_bill_excluding_tip(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(tip_driver="3.00", total_amount="23.53")], earnings=[_earning(amount="19.72")])
    (ride,) = plan.rides_to_insert
    # grand_total/area_fees_total/tax_amount/discount_amount are NUMERIC rides
    # columns (verified via information_schema, 2026-08-18) -- serialized as
    # str(Decimal), not float(), so they round-trip exact into Postgres. See
    # ACTION_ITEMS.md B29.
    assert ride["grand_total"] == "20.53"
    # grand_total == total_fare + area_fees + tax − discount
    rebuilt = (
        Decimal(str(ride["total_fare"]))
        + Decimal(ride["area_fees_total"])
        + Decimal(ride["tax_amount"])
        - Decimal(ride["discount_amount"])
    )
    assert rebuilt == Decimal(ride["grand_total"])


def test_fees_and_tax_land_in_their_own_columns(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["tax_amount"] == "0.98"
    assert ride["tax_breakdown"] == {"GST": {"rate": 5.0, "amount": 0.98}}
    assert ride["area_fees_total"] == "0.60"
    assert {f["name"] for f in ride["area_fees_breakdown"]} == {"City fee", "Infrastructure fee", "Insurance fee"}


def test_payout_gst_amount_preserved_raw_not_merged_into_tax(monkeypatch):
    """The fare-scaling GST component (payout_gst_amount) is a real number
    the old export carries separately from the commission-GST this importer
    writes to tax_amount. It must be preserved for a later reconciliation
    decision, not silently added into tax_amount (that would be answering a
    business question -- what the correct historical rider-facing GST was --
    that the code has no basis to answer). See docs/change-log/
    2026-08-15-legacy-payout-correction-plan.md."""
    _install_fake(monkeypatch)
    plan = _plan([_booking(gst="0.98", payout_gst_amount="4.50")])
    (ride,) = plan.rides_to_insert
    assert ride["tax_amount"] == "0.98"  # unchanged -- still commission-GST
    assert ride["legacy_import_metadata"]["old_payout_gst_amount"] == pytest.approx(4.50)


def test_negative_residual_is_an_error_not_a_silent_clamp(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(gst="50.00")])
    assert not plan.rides_to_insert
    assert any("exceed the total charged" in e.message for e in plan.errors)


def test_tip_larger_than_earning_is_an_error(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(tip_driver="99.00")], earnings=[_earning(amount="16.72")])
    assert not plan.rides_to_insert
    assert any("smaller than the tip" in e.message for e in plan.errors)


# --------------------------------------------------------------------------
# Hazard fields — keep imported rides out of the live loops
# --------------------------------------------------------------------------


def test_hazard_fields_are_set_on_every_imported_ride(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["status"] == "completed"
    # 'paid' keeps it out of GET /rides/active's unpaid branch and away from
    # the payment retry loop.
    assert ride["payment_status"] == "paid"
    assert ride["paid_at"]
    # pre-claimed so the nightly distance reconciliation sweep skips it
    assert ride["distance_reconciled_at"]
    # the column defaults to 2.0 — a legacy ride had no booking fee
    assert ride["booking_fee"] == 0.0
    assert ride["surge_multiplier"] == 1.0
    assert ride["vehicle_type_id"] == "vt-1"
    # `started_at` is a generated column; writing it would fail the insert
    assert "started_at" not in ride
    assert ride["ride_started_at"]


def test_fare_components_are_never_null(monkeypatch):
    """NULL in these three would raise Decimal('None') in _build_fare_breakdown."""
    _install_fake(monkeypatch)
    plan = _plan([_booking()])
    (ride,) = plan.rides_to_insert
    for col in ("base_fare", "distance_fare", "time_fare", "tip_amount", "total_fare"):
        assert ride[col] is not None
        assert isinstance(ride[col], float)


def test_no_rating_is_fabricated(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking()])
    (ride,) = plan.rides_to_insert
    assert "rider_rating" not in ride
    assert "ride_code" not in ride


def test_provenance_metadata_carries_no_pii(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking()])
    (ride,) = plan.rides_to_insert
    meta = ride["legacy_import_metadata"]
    assert meta["source"] == svc.IMPORT_SOURCE
    assert meta["old_booking_id"] == "bk-1"
    assert meta["old_booking_code"] == "CB1234567"
    assert meta["batch"] == BATCH
    blob = repr(meta)
    for pii in ("Legacy Rider", "Legacy Driver", "3065551111", "3065552222", "Main St"):
        assert pii not in blob


# --------------------------------------------------------------------------
# Filtering and phone matching
# --------------------------------------------------------------------------


def test_non_completed_bookings_are_skipped(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(booking_status="cancelled"), _booking(_id="bk-2", booking_status="failed")])
    assert plan.rides_to_insert == []
    assert plan.stats["skipped_not_completed"] == 2


def test_test_accounts_are_skipped(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking()], customers=[_customer(country_code="91")])
    assert plan.rides_to_insert == []
    assert plan.stats["skipped_test_account"] == 1


def test_unmatched_rider_imports_with_null_link(monkeypatch):
    _install_fake(monkeypatch, users=[])
    plan = _plan([_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["rider_id"] is None
    assert ride["driver_id"] == "drv-1"
    assert plan.stats["unmatched_riders"] == 1


def test_unmatched_driver_imports_but_gets_no_offset_payout(monkeypatch):
    _install_fake(monkeypatch, drivers=[])
    plan = _plan([_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["driver_id"] is None
    assert plan.payouts_to_insert == []
    assert plan.driver_ids_to_recount == set()


def test_ride_with_neither_party_matched_is_skipped(monkeypatch):
    _install_fake(monkeypatch, users=[], drivers=[])
    plan = _plan([_booking()])
    assert plan.rides_to_insert == []
    assert plan.stats["skipped_unmatched_both"] == 1


def test_phone_matching_normalizes_to_e164(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking()], customers=[_customer(phone="(306) 555-1111")])
    (ride,) = plan.rides_to_insert
    assert ride["rider_id"] == "user-1"


# --------------------------------------------------------------------------
# Earnings fallback, timestamps, idempotency
# --------------------------------------------------------------------------


def test_missing_earnings_row_falls_back_to_you_earn(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking()], earnings=[])
    (ride,) = plan.rides_to_insert
    assert ride["driver_earnings"] == pytest.approx(16.72)
    assert ride["admin_earnings"] == 0.0
    assert plan.stats["earnings_fallback_rows"] == 1
    assert any("you_earn" in w.message for w in plan.warnings)


def test_multiple_earning_rows_are_summed(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan(
        [_booking()],
        earnings=[_earning(amount="10.00"), _earning(_id="earn-2", amount="6.72")],
    )
    (ride,) = plan.rides_to_insert
    assert ride["driver_earnings"] == pytest.approx(16.72)


def test_referral_earning_rows_are_ignored(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan(
        [_booking()], earnings=[_earning(), _earning(_id="earn-r", booking_id="", earning_type="refer", amount="10")]
    )
    (ride,) = plan.rides_to_insert
    assert ride["driver_earnings"] == pytest.approx(16.72)


def test_epoch_ms_timestamps_are_converted_to_iso_utc(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["created_at"].startswith("2026-02-04T")
    assert ride["created_at"].endswith("+00:00")
    # 10 minutes between start and completion
    assert ride["duration_minutes"] == 10


def test_missing_start_timestamp_estimates_duration(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(start_ride_at="")])
    (ride,) = plan.rides_to_insert
    assert "ride_started_at" not in ride
    assert ride["duration_minutes"] >= 1
    assert plan.stats["missing_start_rows"] == 1


def test_missing_completion_timestamp_is_an_error(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(complete_delivery_at="")])
    assert not plan.rides_to_insert
    assert any(e.field == "complete_delivery_at" for e in plan.errors)


def test_unparseable_coordinates_are_an_error(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(pickup_lat="")])
    assert not plan.rides_to_insert
    assert any(e.field == "coordinates" for e in plan.errors)


def test_duplicate_legacy_id_in_csv_is_an_error(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(), _booking()])
    assert len(plan.rides_to_insert) == 1
    assert any("duplicate legacy booking" in e.message for e in plan.errors)


def test_already_imported_booking_is_skipped_on_rerun(monkeypatch):
    _install_fake(
        monkeypatch,
        rides=[{"id": "r-1", "legacy_import_metadata": {"source": svc.IMPORT_SOURCE, "old_booking_id": "bk-1"}}],
    )
    plan = _plan([_booking()])
    assert plan.rides_to_insert == []
    assert plan.stats["skipped_already_imported"] == 1


def test_existing_offset_payout_is_not_duplicated(monkeypatch):
    _install_fake(monkeypatch, payouts=[{"id": f"legacy-import-{BATCH}-drv-1", "driver_id": "drv-1"}])
    plan = _plan([_booking()])
    assert plan.rides_to_insert  # the ride still imports
    assert plan.payouts_to_insert == []


# --------------------------------------------------------------------------
# Commit
# --------------------------------------------------------------------------


def test_commit_refuses_when_plan_has_errors(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_booking(gst="50.00")])
    with pytest.raises(RuntimeError, match="refusing to commit"):
        svc.commit_plan(plan)


def test_commit_writes_rides_payouts_and_recounts_driver(monkeypatch):
    store = _install_fake(monkeypatch, drivers=[{"id": "drv-1", "phone": "+13065552222", "total_rides": 0}])
    plan = _plan([_booking()])
    svc.commit_plan(plan)
    assert len(store["rides"]) == 1
    assert len(store["payouts"]) == 1
    # total_rides is recomputed as a COUNT, so a re-run cannot double it
    assert store["drivers"][0]["total_rides"] == 1
    svc.recount_drivers(["drv-1"])
    assert store["drivers"][0]["total_rides"] == 1


# --------------------------------------------------------------------------
# CSV parsing quirks of the legacy export
# --------------------------------------------------------------------------


def test_read_csv_drops_trailing_unnamed_column(tmp_path):
    """The legacy booking export emits one more field than its header.

    The surplus value is a trailing, unnamed Mongo field. It must be dropped
    without disturbing the named columns (a naive DictReader read crashes on
    the list csv puts under the None key).
    """
    p = tmp_path / "bookings.csv"
    p.write_text(
        "_id,booking_id,total_amount\n"
        "bk-1,CB1,20.53,69c1c0365fc1dc6d1e3e530f\n"  # non-blank surplus
        "bk-2,CB2,3.50,\n"  # blank surplus (trailing comma)
        "bk-3,CB3,7.25\n",  # no surplus
        encoding="utf-8",
    )
    rows = svc.read_csv(p)
    assert [r["_id"] for r in rows] == ["bk-1", "bk-2", "bk-3"]
    assert [r["total_amount"] for r in rows] == ["20.53", "3.50", "7.25"]
    assert all(None not in r for r in rows)


def test_read_csv_leaves_short_rows_for_validation(tmp_path):
    """A short row must surface as empty, not be silently treated as zero."""
    p = tmp_path / "bookings.csv"
    p.write_text("_id,booking_id,total_amount\nbk-1,CB1\n", encoding="utf-8")
    (row,) = svc.read_csv(p)
    assert row["total_amount"] == ""


def test_read_csv_text_matches_read_csv_exactly(tmp_path):
    """The upload path must parse identically to the CLI path.

    An HTTP upload hands us bytes, not a path. If read_csv_text ever drifts
    from read_csv (e.g. by copying rider_import's header-normalizing version,
    which has no None-key guard) the legacy export's trailing unnamed column
    would crash the import or silently shift columns.
    """
    content = "_id,booking_id,total_amount\nbk-1,CB1,20.53,69c1c0365fc1dc6d1e3e530f\nbk-2,CB2,3.50,\nbk-3,CB3,7.25\n"
    p = tmp_path / "bookings.csv"
    p.write_text(content, encoding="utf-8")

    assert svc.read_csv_text(content) == svc.read_csv(p)


def test_read_csv_text_does_not_normalize_headers():
    """Legacy headers are matched verbatim; normalizing them breaks the map."""
    rows = svc.read_csv_text("_id,booking_id,total_amount\nbk-1,CB1,20.53\n")
    assert set(rows[0]) == {"_id", "booking_id", "total_amount"}


def test_read_csv_text_handles_crlf_and_bom():
    """Windows-exported CSVs carry CRLF line endings and often a UTF-8 BOM."""
    rows = svc.read_csv_text("﻿_id,booking_id,total_amount\r\nbk-1,CB1,20.53\r\n")
    assert rows == [{"_id": "bk-1", "booking_id": "CB1", "total_amount": "20.53"}]


def test_read_csv_text_rejects_empty_content():
    with pytest.raises(ValueError, match="no header row"):
        svc.read_csv_text("")


# --- driver total_rides recount (RPC + fallback) ---------------------------


class _FakeRpcSupabase(_FakeSupabase):
    """Fake that supports the migration-269 set-based recount RPC."""

    def __init__(self, store):
        super().__init__(store)
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if name != "recount_driver_total_rides":
            raise AssertionError(f"unexpected rpc: {name}")
        ids = set(params["p_driver_ids"])
        for drv in self.store["drivers"]:
            if drv["id"] in ids:
                drv["total_rides"] = sum(
                    1 for r in self.store["rides"] if r.get("driver_id") == drv["id"] and r.get("status") == "completed"
                )
        return _FakeExecute([])


def test_recount_prefers_single_rpc_call_over_per_driver_loop(monkeypatch):
    """One set-based call, not two round-trips per driver.

    A 64-driver import through the per-driver loop is 128 sequential calls —
    long enough for the admin endpoint to time out mid-loop and leave counters
    updated for only some drivers.
    """
    store = {
        "users": [],
        "drivers": [
            {"id": "drv-1", "phone": "+13065552222", "total_rides": 0},
            {"id": "drv-2", "phone": "+13065553333", "total_rides": 0},
        ],
        "rides": [
            {"driver_id": "drv-1", "status": "completed"},
            {"driver_id": "drv-1", "status": "completed"},
            {"driver_id": "drv-2", "status": "completed"},
            {"driver_id": "drv-2", "status": "cancelled"},
        ],
        "payouts": [],
        "service_areas": [SERVICE_AREA],
        "vehicle_types": [VEHICLE_TYPE],
    }
    fake = _FakeRpcSupabase(store)
    monkeypatch.setattr(svc, "supabase", fake)

    svc.recount_drivers(["drv-1", "drv-2"])

    assert len(fake.rpc_calls) == 1
    assert fake.rpc_calls[0][1] == {"p_driver_ids": ["drv-1", "drv-2"]}
    assert store["drivers"][0]["total_rides"] == 2
    assert store["drivers"][1]["total_rides"] == 1  # cancelled ride excluded


def test_recount_falls_back_when_rpc_missing(monkeypatch):
    """The CLI must still work against a DB without migration 269 applied."""
    store = _install_fake(
        monkeypatch,
        drivers=[{"id": "drv-1", "phone": "+13065552222", "total_rides": 0}],
        rides=[
            {"driver_id": "drv-1", "status": "completed"},
            {"driver_id": "drv-1", "status": "completed"},
        ],
    )
    # _FakeSupabase has no .rpc at all — the same shape as a database where the
    # function does not exist.
    svc.recount_drivers(["drv-1"])
    assert store["drivers"][0]["total_rides"] == 2


def test_recount_with_no_drivers_makes_no_calls(monkeypatch):
    store = {
        "users": [],
        "drivers": [],
        "rides": [],
        "payouts": [],
        "service_areas": [SERVICE_AREA],
        "vehicle_types": [VEHICLE_TYPE],
    }
    fake = _FakeRpcSupabase(store)
    monkeypatch.setattr(svc, "supabase", fake)
    svc.recount_drivers([])
    assert fake.rpc_calls == []
