"""Unit tests for booking_import_service.py's cancelled/failed legacy import
path (added 2026-08-20, A41 — docs/change-log/2026-08-20-legacy-cancelled-
failed-booking-import.md).

Sibling of test_booking_import_service.py, which covers the original
completed-ride path and is left unmodified except for one renamed test (see
that file's own docstring on `test_unrecognized_status_bookings_are_skipped`).
This file uses its own minimal fake Supabase client rather than importing the
other test module's, matching this repo's existing per-file fake-client
convention (each importer test file owns its fake).
"""

from backend.services import booking_import_service as svc

SERVICE_AREA = {"id": "sa-1", "name": "Saskatoon"}
VEHICLE_TYPE = {"id": "vt-1", "name": "Economy"}
BATCH = "20260820000000"


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
        self._filters.append((op, col, val))
        return self

    def limit(self, _n):
        return self

    def insert(self, rows):
        self.store.setdefault(self.table, []).extend(rows)
        return self

    def execute(self):
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


def _customer(**overrides):
    row = {"_id": "cus-1", "country_code": "1", "phone": "3065551111", "name": "Legacy Rider"}
    row.update(overrides)
    return row


def _driver(**overrides):
    row = {"_id": "drv-legacy-1", "country_code": "1", "phone": "3065552222", "name": "Legacy Driver"}
    row.update(overrides)
    return row


def _cancelled_booking(**overrides):
    """A pre-trip legacy cancellation: no start_ride_at, no complete_delivery_at."""
    row = {
        "_id": "bk-1",
        "booking_id": "CB1234567",
        "booking_status": "cancelled",
        "customer_id": "cus-1",
        "driver_id": "drv-legacy-1",
        "created_at": "1770197933837",
        "start_ride_at": "",
        "complete_delivery_at": "",
        "pickup_address": "123 Main St, Regina, SK",
        "pickup_lat": "50.4099027",
        "pickup_long": "-104.6554126",
        "drop_address": "456 Broad St, Regina, SK",
        "drop_lat": "50.4059968",
        "drop_long": "-104.6377971",
        "cancelled_by": "customer",
        "cancelled_reason": "Change in Travel plans.",
    }
    row.update(overrides)
    return row


def _plan(bookings, customers=None, drivers=None, earnings=None):
    return svc.build_plan(
        bookings,
        customers if customers is not None else [_customer()],
        drivers if drivers is not None else [_driver()],
        earnings if earnings is not None else [],
        service_area=SERVICE_AREA,
        vehicle_type=VEHICLE_TYPE,
        batch=BATCH,
    )


# --------------------------------------------------------------------------
# Basic import: driver match, rider-only match, no match
# --------------------------------------------------------------------------


def test_cancelled_row_with_driver_match_imports(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking()])
    assert not plan.errors
    (ride,) = plan.rides_to_insert
    assert ride["status"] == "cancelled"
    assert ride["rider_id"] == "user-1"
    assert ride["driver_id"] == "drv-1"
    assert plan.stats["cancelled_target_rows"] == 1
    assert plan.stats["cancelled_failed_rides_planned"] == 1


def test_cancelled_row_with_only_rider_match_imports_null_driver(monkeypatch):
    _install_fake(monkeypatch, drivers=[])
    plan = _plan([_cancelled_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["rider_id"] == "user-1"
    assert ride["driver_id"] is None
    assert plan.stats["cancelled_failed_unmatched_drivers"] == 1


def test_failed_row_with_no_matches_is_skipped(monkeypatch):
    _install_fake(monkeypatch, users=[], drivers=[])
    plan = _plan(
        [
            _cancelled_booking(
                booking_status="failed",
                driver_id="",
                cancelled_by="",
                cancelled_reason="",
            )
        ]
    )
    assert plan.rides_to_insert == []
    assert plan.stats["cancelled_failed_skipped_unmatched_both"] == 1
    assert plan.stats["failed_target_rows"] == 1


# --------------------------------------------------------------------------
# Anomalous "looks completed" rows -- imported as completed, $0 fare
# (disposition decided 2026-08-20; see
# docs/change-log/2026-08-20-anomalous-legacy-rows-payment-verification.md
# and docs/change-log/2026-08-20-anomalous-rows-zero-fare-completed-import.md)
# --------------------------------------------------------------------------


def _anomalous_booking(**overrides):
    """The 7-row anomaly: booking_status='failed' but structurally a
    completed trip (both start_ride_at and complete_delivery_at populated),
    with real total_amount/you_earn figures the old app never actually
    collected (0/225 `failed` bookings have a payments.csv record)."""
    row = _cancelled_booking(
        booking_status="failed",
        start_ride_at="1770198000000",
        complete_delivery_at="1770198600000",
        distance_in_km="3.2",
        total_amount="9.16",
        you_earn="7.54",
    )
    row.update(overrides)
    return row


def test_row_with_both_timestamps_imports_as_completed_zero_fare(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_anomalous_booking()])
    assert not plan.errors
    (ride,) = plan.rides_to_insert
    assert ride["status"] == "completed"
    assert plan.stats["cancelled_failed_zero_fare_completed"] == 1
    assert plan.stats["cancelled_failed_rides_planned"] == 0  # not counted as a cancelled row


def test_anomalous_row_writes_no_fare_earnings_or_payout(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_anomalous_booking()])
    (ride,) = plan.rides_to_insert
    for field in (
        "base_fare",
        "distance_fare",
        "time_fare",
        "total_fare",
        "tip_amount",
        "driver_earnings",
        "admin_earnings",
    ):
        assert ride[field] == 0.0, field
    assert ride["grand_total"] == "0"
    assert plan.payouts_to_insert == []
    assert plan.stats["sum_offset_payouts"] == 0.0


def test_anomalous_row_payment_status_is_pending_not_failed(monkeypatch):
    """Must never be 'failed'/'processing'/'requires_action' -- those are
    exactly what payment_retry.py's retry_failed_payments() scans for any
    status != 'cancelled' row, which would try to collect real payment on
    this $0 legacy row. auth_status must also stay unset so
    preauth_capture.py's completed+pending sweep can't claim it either."""
    _install_fake(monkeypatch)
    plan = _plan([_anomalous_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["payment_status"] == "pending"
    assert "auth_status" not in ride


def test_anomalous_row_preserves_gps_and_timestamps(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_anomalous_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["pickup_lat"] == 50.4099027
    assert ride["ride_started_at"] == svc.parse_epoch_ms("1770198000000")
    assert ride["ride_completed_at"] == svc.parse_epoch_ms("1770198600000")
    assert ride["distance_km"] == 3.2


def test_anomalous_row_legacy_metadata_flags_disposition(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_anomalous_booking()])
    (ride,) = plan.rides_to_insert
    meta = ride["legacy_import_metadata"]
    assert meta["anomalous_looks_completed_zero_fare"] is True
    assert meta["original_booking_status"] == "failed"
    assert meta["source"] == svc.IMPORT_SOURCE


def test_anomalous_row_driver_is_recounted_unlike_normal_cancelled_rows(monkeypatch):
    """Unlike the normal cancelled/failed branch (never recounted -- those
    rows are never status='completed'), this row IS a real completed ride
    and total_rides (a plain COUNT of status='completed') must include it."""
    _install_fake(monkeypatch)
    plan = _plan([_anomalous_booking()])
    assert "drv-1" in plan.driver_ids_to_recount


def test_anomalous_row_matches_completed_idempotency_on_rerun(monkeypatch):
    """Re-running against the same CSV must skip the row already written --
    matched on legacy_import_metadata->>old_booking_id, same as every other
    path in this importer (_fetch_already_imported is status-agnostic).
    Inserts the planned ride directly rather than going through
    commit_plan()/recount_drivers(), which this test file's minimal fake
    Supabase client doesn't implement (recount idempotency is covered
    separately in test_booking_import_service.py)."""
    booking = _anomalous_booking()
    store = _install_fake(monkeypatch)
    plan1 = _plan([booking])
    store["rides"].extend(plan1.rides_to_insert)
    # Simulate a second run against the same CSV.
    already = svc._fetch_already_imported()
    assert booking["_id"] in already
    plan2 = _plan([booking])
    assert plan2.rides_to_insert == []
    assert plan2.stats["cancelled_failed_skipped_already_imported"] == 1


def test_anomalous_row_gets_period_3_only_no_arrived_at_in_export(monkeypatch):
    """The anomalous branch's own fixture never carries
    arrived_pickup_loc_at (not part of the 7-row anomaly's data), so only
    Period 3 (started->completed) is reconstructible -- Period 2 is never
    fabricated from a missing timestamp, same rule as the normal completed
    path."""
    _install_fake(monkeypatch)
    plan = _plan([_anomalous_booking()])
    (ride,) = plan.rides_to_insert
    periods = plan.insurance_periods_to_insert
    assert [p["period"] for p in periods] == [3]
    (p3,) = periods
    assert p3["driver_id"] == "drv-1"
    assert p3["ride_id"] == ride["id"]
    assert p3["started_at"] == ride["ride_started_at"]
    assert p3["ended_at"] == ride["ride_completed_at"]
    assert p3["is_reconstructed"] is True


def test_normal_cancelled_row_never_gets_insurance_periods(monkeypatch):
    """A genuinely cancelled/failed row (rides.status stays 'cancelled',
    never 'completed') gets no driver-liability reconstruction attempted --
    matches migration 332's own scope, which only ever covered completed
    rides."""
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking()])
    assert plan.rides_to_insert  # the ride itself still imports
    assert plan.insurance_periods_to_insert == []


def test_row_with_only_start_timestamp_is_still_a_normal_cancellation(monkeypatch):
    """Only ONE of the two timestamps populated is not the anomaly -- still
    a legitimate pre-trip-adjacent cancellation, must import normally."""
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(start_ride_at="1770198000000", complete_delivery_at="")])
    assert len(plan.rides_to_insert) == 1
    (ride,) = plan.rides_to_insert
    assert ride["status"] == "cancelled"
    assert plan.stats["cancelled_failed_zero_fare_completed"] == 0


def test_blank_status_rows_are_excluded(monkeypatch):
    """The export's 2 blank (`""`) booking_status rows: genuinely unknown,
    unsafe to guess, excluded entirely -- not completed/cancelled/failed."""
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(booking_status="")])
    assert plan.rides_to_insert == []
    assert plan.stats["skipped_not_completed"] == 1
    assert plan.stats["cancelled_target_rows"] == 0
    assert plan.stats["failed_target_rows"] == 0


# --------------------------------------------------------------------------
# cancelled_by / cancellation_type mapping
# --------------------------------------------------------------------------


def test_cancelled_by_customer_maps_to_rider(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(cancelled_by="customer")])
    (ride,) = plan.rides_to_insert
    assert ride["cancelled_by"] == "rider"
    assert ride["cancellation_type"] == "rider_cancel"


def test_cancelled_by_driver_maps_to_driver(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(cancelled_by="driver")])
    (ride,) = plan.rides_to_insert
    assert ride["cancelled_by"] == "driver"
    assert ride["cancellation_type"] == "driver_cancel"


def test_cancelled_by_blank_maps_to_system_no_drivers_found(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(cancelled_by="")])
    (ride,) = plan.rides_to_insert
    assert ride["cancelled_by"] == "system"
    assert ride["cancellation_type"] == "no_drivers_found"


def test_cancellation_reason_uses_legacy_text_when_present(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(cancelled_reason="The passenger did not show up.")])
    (ride,) = plan.rides_to_insert
    assert ride["cancellation_reason"] == "The passenger did not show up."


def test_cancellation_reason_falls_back_when_blank(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(booking_status="failed", cancelled_reason="", cancelled_by="")])
    (ride,) = plan.rides_to_insert
    assert ride["cancellation_reason"] == svc.NO_DRIVER_FOUND_REASON
    assert ride["cancellation_reason"]  # never NULL/empty


# --------------------------------------------------------------------------
# cancelled_at estimation + status/metadata
# --------------------------------------------------------------------------


def test_cancelled_at_is_estimated_from_created_at_and_flagged(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking()])
    (ride,) = plan.rides_to_insert
    assert ride["cancelled_at"] == ride["created_at"]
    assert ride["legacy_import_metadata"]["cancelled_at_estimated"] is True


def test_status_is_always_cancelled_for_both_legacy_statuses(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan(
        [
            _cancelled_booking(booking_status="cancelled"),
            _cancelled_booking(_id="bk-2", booking_id="CB2", booking_status="failed"),
        ]
    )
    assert len(plan.rides_to_insert) == 2
    assert {r["status"] for r in plan.rides_to_insert} == {"cancelled"}


def test_legacy_metadata_carries_original_booking_status(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(booking_status="failed")])
    (ride,) = plan.rides_to_insert
    meta = ride["legacy_import_metadata"]
    assert meta["original_booking_status"] == "failed"
    assert meta["source"] == svc.IMPORT_SOURCE
    assert meta["old_booking_id"] == "bk-1"
    assert meta["batch"] == BATCH


# --------------------------------------------------------------------------
# No money: the "skip payout-offset logic, keep GPS+timestamps" contract
# --------------------------------------------------------------------------


def test_no_fare_earnings_or_payout_fields_are_written(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan(
        [
            _cancelled_booking(driver_id="drv-legacy-1"),
        ]
    )
    (ride,) = plan.rides_to_insert
    for field in (
        "total_fare",
        "base_fare",
        "distance_fare",
        "time_fare",
        "tax_amount",
        "tax_breakdown",
        "tip_amount",
        "area_fees_breakdown",
        "area_fees_total",
        "discount_amount",
        "driver_earnings",
        "admin_earnings",
        "driver_earnings_snapshot",
        "fare_breakdown_snapshot",
        "duration_minutes",
        "grand_total",
    ):
        assert field not in ride, f"cancelled/failed import must not write {field!r}"

    assert plan.payouts_to_insert == []
    assert plan.driver_ids_to_recount == set()


def test_matched_driver_not_added_to_recount_set(monkeypatch):
    """Even with a matched driver, cancelled/failed rows must not trigger a
    total_rides recount -- total_rides counts status='completed' only."""
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking()])
    assert plan.rides_to_insert  # sanity: the row did import
    assert "drv-1" not in plan.driver_ids_to_recount


# --------------------------------------------------------------------------
# Coordinates / addresses / idempotency
# --------------------------------------------------------------------------


def test_missing_coordinates_is_an_error_not_silently_skipped(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(pickup_lat="")])
    assert plan.rides_to_insert == []
    assert plan.stats["cancelled_failed_skipped_missing_coordinates"] == 1
    assert any(e.field == "coordinates" for e in plan.errors)


def test_blank_address_falls_back_with_warning(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(pickup_address="")])
    (ride,) = plan.rides_to_insert
    assert ride["pickup_address"] == "Address unavailable (imported ride)"
    assert any(w.field == "pickup_address" for w in plan.warnings)


def test_already_imported_cancelled_row_is_skipped_on_rerun(monkeypatch):
    _install_fake(
        monkeypatch,
        rides=[{"id": "r-1", "legacy_import_metadata": {"source": svc.IMPORT_SOURCE, "old_booking_id": "bk-1"}}],
    )
    plan = _plan([_cancelled_booking()])
    assert plan.rides_to_insert == []
    assert plan.stats["cancelled_failed_skipped_already_imported"] == 1


# --------------------------------------------------------------------------
# Existing completed-ride path stays untouched by this branch
# --------------------------------------------------------------------------


def test_completed_and_cancelled_rows_coexist_in_one_batch(monkeypatch):
    """A CSV with both a completed and a cancelled row must import both,
    with the completed row unaffected by the new branch (still gets fare/
    earnings/offset payout; rides_planned still counts it, not the
    cancelled row)."""
    _install_fake(monkeypatch)
    completed = {
        "_id": "bk-completed-1",
        "booking_id": "CBCOMPLETED",
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
        "coupon_discount": "0",
        "tip_driver": "0",
        "you_earn": "16.72",
        "payment_method": "card",
    }
    plan = _plan(
        [completed, _cancelled_booking()],
        earnings=[
            {
                "_id": "earn-1",
                "booking_id": "bk-completed-1",
                "amount": "16.72",
                "admin_comission_amount": "3.09",
                "booking_amount": "20.53",
                "earning_type": "salary",
            }
        ],
    )
    assert not plan.errors
    assert len(plan.rides_to_insert) == 2
    assert plan.stats["rides_planned"] == 1  # completed-only, unchanged meaning
    assert plan.stats["cancelled_failed_rides_planned"] == 1
    assert plan.stats["total_rides_planned"] == 2
    statuses = {r["status"] for r in plan.rides_to_insert}
    assert statuses == {"completed", "cancelled"}
    # the completed row still got its offset payout; the cancelled row didn't
    assert len(plan.payouts_to_insert) == 1
    assert plan.driver_ids_to_recount == {"drv-1"}


# --------------------------------------------------------------------------
# Test-account filtering: driver check only applies when a driver exists
# --------------------------------------------------------------------------


def test_test_account_customer_is_skipped(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking()], customers=[_customer(country_code="91")])
    assert plan.rides_to_insert == []
    assert plan.stats["skipped_test_account"] == 1


def test_no_driver_at_all_is_not_treated_as_test_account(monkeypatch):
    """Unlike the completed path (which always has a driver), most
    cancelled/failed rows have none at all -- that must not be mistaken for
    a non-Canadian test-account driver."""
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking(driver_id="")])
    assert len(plan.rides_to_insert) == 1
    assert plan.stats["skipped_test_account"] == 0


def test_test_account_driver_is_skipped_when_driver_present(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_cancelled_booking()], drivers=[_driver(country_code="91")])
    assert plan.rides_to_insert == []
    assert plan.stats["skipped_test_account"] == 1
