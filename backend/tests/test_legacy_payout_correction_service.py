"""Pins the legacy_payout_correction_service filter chain against small,
synthetic CSVs (never the real export — that's PII and stays out of git).

No live Supabase/Stripe calls anywhere in this module; `_fetch_already_imported`
is always mocked here, matching the module's own "read-only, no writes" contract.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services import legacy_payout_correction_service as svc

pytestmark = pytest.mark.unit


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def csv_set(tmp_path):
    payments = [
        # kept: due, resolvable booking, real (non-test) tenant
        {
            "_id": "p1",
            "booking_id": "b1",
            "driver_id": "d1",
            "customer_id": "c1",
            "payout_amount": "10.00",
            "pending_amount_status": "due",
        },
        # dropped: not 'due'
        {
            "_id": "p2",
            "booking_id": "b2",
            "driver_id": "d1",
            "customer_id": "c1",
            "payout_amount": "5.00",
            "pending_amount_status": "paid",
        },
        # dropped: booking_id has no matching row in bookings.csv (unresolved)
        {
            "_id": "p3",
            "booking_id": "does-not-exist",
            "driver_id": "d1",
            "customer_id": "c1",
            "payout_amount": "7.00",
            "pending_amount_status": "due",
        },
        # dropped: test-tenant driver (country_code 91)
        {
            "_id": "p4",
            "booking_id": "b4",
            "driver_id": "d-test",
            "customer_id": "c1",
            "payout_amount": "3.00",
            "pending_amount_status": "due",
        },
        # dropped: test-tenant customer (yopmail)
        {
            "_id": "p5",
            "booking_id": "b5",
            "driver_id": "d1",
            "customer_id": "c-test",
            "payout_amount": "4.00",
            "pending_amount_status": "due",
        },
        # kept: second real due row, different driver — goes to group B (not imported)
        {
            "_id": "p6",
            "booking_id": "b6",
            "driver_id": "d2",
            "customer_id": "c1",
            "payout_amount": "2.50",
            "pending_amount_status": "due",
        },
    ]
    bookings = [{"_id": bid} for bid in ("b1", "b4", "b5", "b6")]
    drivers = [
        {"_id": "d1", "country_code": "1", "email": "real@example.com"},
        {"_id": "d2", "country_code": "1", "email": "real2@example.com"},
        {"_id": "d-test", "country_code": "91", "email": "vendor-test@example.com"},
    ]
    customers = [
        {"_id": "c1", "country_code": "1", "email": "rider@example.com"},
        {"_id": "c-test", "country_code": "1", "email": "someone@yopmail.com"},
    ]

    paths = {}
    for name, rows in (
        ("payments", payments),
        ("bookings", bookings),
        ("drivers", drivers),
        ("customers", customers),
    ):
        p = tmp_path / f"{name}.csv"
        _write_csv(p, rows)
        paths[name] = p
    return paths


def test_filter_chain_keeps_only_due_resolvable_non_test_rows(csv_set):
    with patch.object(svc, "_fetch_already_imported", return_value={}):
        plan = svc.build_correction_plan(
            csv_set["payments"], csv_set["bookings"], csv_set["drivers"], csv_set["customers"]
        )
    # p1 and p6 survive; p2 (not due), p3 (unresolved), p4/p5 (test tenant) don't.
    kept_ids = {r.old_booking_id for r in plan.rows}
    assert kept_ids == {"b1", "b6"}
    assert plan.unresolved_row_count == 1
    assert plan.stats["kept_after_filters"] == 2


def test_group_a_vs_group_b_split_on_already_imported(csv_set):
    with patch.object(svc, "_fetch_already_imported", return_value={"b1": ("ride-1", "spinr-driver-1")}):
        plan = svc.build_correction_plan(
            csv_set["payments"], csv_set["bookings"], csv_set["drivers"], csv_set["customers"]
        )
    a_ids = {r.old_booking_id for r in plan.group_a}
    b_ids = {r.old_booking_id for r in plan.group_b}
    assert a_ids == {"b1"}
    assert b_ids == {"b6"}
    assert plan.group_a[0].spinr_ride_id == "ride-1"
    assert plan.group_a[0].spinr_driver_id == "spinr-driver-1"


def test_totals_match_sum_of_kept_rows(csv_set):
    with patch.object(svc, "_fetch_already_imported", return_value={}):
        plan = svc.build_correction_plan(
            csv_set["payments"], csv_set["bookings"], csv_set["drivers"], csv_set["customers"]
        )
    # Exact Decimal equality, not pytest.approx — plan.stats now carries the
    # real Decimal (build_correction_plan no longer coerces to float), so
    # there's no binary-float drift left to tolerate.
    assert plan.stats["total_amount"] == Decimal("12.50")  # 10.00 + 2.50


def test_print_report_makes_no_writes_and_is_human_readable(csv_set):
    with patch.object(svc, "_fetch_already_imported", return_value={}) as mock_fetch:
        plan = svc.build_correction_plan(
            csv_set["payments"], csv_set["bookings"], csv_set["drivers"], csv_set["customers"]
        )
        report = svc.print_report(plan)
    mock_fetch.assert_called_once()
    assert "DRY RUN (no writes performed)" in report
    assert "No payouts table writes, no ride inserts, no Stripe calls were made." in report
    assert "$12.50" in report


# ── Write path (2026-08-17) ─────────────────────────────────────────────


def _row(old_booking_id, driver_id, amount, ride_id="ride-x"):
    return svc.CorrectionRow(
        old_booking_id=old_booking_id,
        old_driver_id="old-" + old_booking_id,
        payout_amount=Decimal(str(amount)),
        already_imported=True,
        spinr_ride_id=ride_id,
        spinr_driver_id=driver_id,
    )


def _plan(*rows, group_b=()):
    return svc.CorrectionPlan(rows=list(rows) + list(group_b))


class _FakeTable:
    """Minimal chainable mock: every chain method returns self; .execute()
    returns whatever was queued for that table via `queue_select`. `.insert`/
    `.update` calls are recorded on `inserted`/`updated` for assertions."""

    def __init__(self):
        self._select_data: dict[str, list[dict]] = {}
        self.inserted: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self._insert_error: Exception | None = None
        self._current_table: str | None = None
        self._pending_update: dict | None = None

    def queue_select(self, table_name: str, rows: list[dict]) -> None:
        self._select_data[table_name] = rows

    def raise_on_insert(self, exc: Exception) -> None:
        """Every insert of this exact row conflicts (simulates a row that
        already exists from a previous/concurrent run) — not a one-shot
        transient error."""
        self._insert_error = exc

    def table(self, name):
        self._current_table = name
        return self

    def select(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def eq(self, col, val):
        # `.update(patch).eq("id", val).execute()` — the id arrives AFTER
        # the patch dict, so finalize the pending update here rather than
        # in `update()`. `.select().eq(...).eq(...)` (no pending update) is
        # a no-op passthrough.
        if self._pending_update is not None and col == "id":
            self.updated.append((val, self._pending_update))
            self._pending_update = None
        return self

    def insert(self, rows):
        payload = rows if isinstance(rows, list) else [rows]
        if self._insert_error is not None:
            raise self._insert_error
        self.inserted.extend(payload)
        return self

    def update(self, patch_dict):
        self._pending_update = patch_dict
        return self

    def execute(self):
        result = MagicMock()
        result.data = self._select_data.get(self._current_table, [])
        return result


def test_build_write_plan_classifies_ready_hold_excluded_deferred():
    excluded_id = next(iter(svc.STRIPE_CROSSCHECK_EXCLUDED_DRIVER_IDS))
    rows = [
        _row("b-ready", "driver-with-stripe", "10.00"),
        _row("b-hold", "driver-no-stripe", "5.00"),
        _row("b-excluded", excluded_id, "22.43"),
        _row("b-unmatched", None, "4.02"),
    ]
    group_b = [svc.CorrectionRow("b-deferred", "old-d", Decimal("1.00"), already_imported=False)]
    plan = _plan(*rows, group_b=group_b)

    fake = _FakeTable()
    fake.queue_select("drivers", [{"id": "driver-with-stripe", "stripe_account_id": "acct_123"}])
    with patch.object(svc, "supabase", fake):
        write_plan = svc.build_write_plan(plan)

    assert [r.old_booking_id for r in write_plan.ready_rows] == ["b-ready"]
    assert write_plan.ready_rows[0].status == "ready_for_transfer"
    assert [r.old_booking_id for r in write_plan.hold_rows] == ["b-hold"]
    assert write_plan.hold_rows[0].status == "awaiting_stripe_onboarding"
    assert [r.old_booking_id for r in write_plan.excluded_stripe_crosscheck] == ["b-excluded"]
    assert [r.old_booking_id for r in write_plan.excluded_unmatched_driver] == ["b-unmatched"]
    assert [r.old_booking_id for r in write_plan.deferred_not_imported] == ["b-deferred"]
    assert write_plan.stats["ready_amount"] == Decimal("10.00")
    assert write_plan.stats["hold_amount"] == Decimal("5.00")


def test_stripe_crosscheck_exclusions_are_never_ready_or_held():
    """The 3 named 2026-08-16 exclusions must never appear in ready_rows or
    hold_rows regardless of Stripe-account presence -- excluded means
    excluded, not silently downgraded to held."""
    excluded_id = next(iter(svc.STRIPE_CROSSCHECK_EXCLUDED_DRIVER_IDS))
    plan = _plan(_row("b1", excluded_id, "22.43"))
    fake = _FakeTable()
    fake.queue_select("drivers", [{"id": excluded_id, "stripe_account_id": "acct_999"}])
    with patch.object(svc, "supabase", fake):
        write_plan = svc.build_write_plan(plan)
    assert write_plan.ready_rows == []
    assert write_plan.hold_rows == []
    assert len(write_plan.excluded_stripe_crosscheck) == 1


def test_print_write_report_makes_no_writes():
    plan = _plan(_row("b1", "driver-1", "10.00"))
    fake = _FakeTable()
    fake.queue_select("drivers", [])
    with patch.object(svc, "supabase", fake):
        write_plan = svc.build_write_plan(plan)
    report = svc.print_write_report(write_plan)
    assert "No payouts table writes, no Stripe calls were made." in report
    assert not fake.inserted
    assert not fake.updated


def test_commit_write_plan_inserts_ready_and_hold_rows_with_correct_status():
    write_plan = svc.WritePlan(
        ready_rows=[svc.WriteRow("id-ready", "driver-1", "b-ready", "ride-1", Decimal("10.00"), "ready_for_transfer")],
        hold_rows=[
            svc.WriteRow("id-hold", "driver-2", "b-hold", "ride-2", Decimal("5.00"), "awaiting_stripe_onboarding")
        ],
    )
    fake = _FakeTable()
    with patch.object(svc, "supabase", fake):
        result = svc.commit_write_plan(write_plan)

    assert result == {"inserted": 2, "skipped_existing": 0}
    by_id = {r["id"]: r for r in fake.inserted}
    assert by_id["id-ready"]["status"] == "ready_for_transfer"
    assert by_id["id-ready"]["payout_type"] == svc.PAYOUT_TYPE
    assert by_id["id-hold"]["status"] == "awaiting_stripe_onboarding"
    # str(Decimal) only at the serialization boundary (payouts.amount is
    # NUMERIC as of migration 331); the input Decimal is exact.
    assert by_id["id-ready"]["amount"] == "10.00"


def test_commit_write_plan_is_idempotent_on_rerun():
    """A deterministic id colliding with a previous run's row must be
    skipped, not raised — matches stripe_payout_sync_service's contract."""
    write_plan = svc.WritePlan(
        ready_rows=[svc.WriteRow("dup-id", "driver-1", "b1", "ride-1", Decimal("10.00"), "ready_for_transfer")]
    )
    fake = _FakeTable()

    class _Dup(Exception):
        code = "23505"

    fake.raise_on_insert(_Dup("duplicate key value"))
    with patch.object(svc, "supabase", fake):
        result = svc.commit_write_plan(write_plan)
    assert result == {"inserted": 0, "skipped_existing": 1}


def test_payout_id_for_is_deterministic():
    assert svc.payout_id_for("abc123") == svc.payout_id_for("abc123")
    assert svc.payout_id_for("abc123") != svc.payout_id_for("xyz789")


def test_fire_ready_transfers_calls_stripe_once_per_ready_row(monkeypatch):
    fake = _FakeTable()
    fake.queue_select(
        "payouts",
        [{"id": "id-ready", "driver_id": "driver-1", "amount": 10.0}],
    )
    fake.queue_select("drivers", [{"id": "driver-1", "stripe_account_id": "acct_123"}])

    fake_stripe = MagicMock()
    fake_transfer = MagicMock()
    fake_transfer.id = "tr_abc"
    fake_stripe.Transfer.create.return_value = fake_transfer

    import sys

    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    with patch.object(svc, "supabase", fake):
        result = svc.fire_ready_transfers("sk_test_123")

    assert result == {"fired": 1, "failed": []}
    fake_stripe.Transfer.create.assert_called_once()
    call_kwargs = fake_stripe.Transfer.create.call_args.kwargs
    assert call_kwargs["destination"] == "acct_123"
    assert call_kwargs["amount"] == 1000  # $10.00 -> cents
    assert call_kwargs["idempotency_key"] == "legacy-correction-transfer-id-ready"
    # The update recorded against payouts must mark it completed with the
    # real transfer id, not just "fired and forgotten".
    update_id, patch_dict = fake.updated[-1]
    assert update_id == "id-ready"
    assert patch_dict["status"] == "completed"
    assert patch_dict["stripe_transfer_id"] == "tr_abc"


def test_fire_ready_transfers_falls_back_to_hold_when_account_missing(monkeypatch):
    """A row that was ready at commit time but whose driver's Stripe account
    disappeared before firing must not attempt a doomed Transfer -- it goes
    back to held, not failed (this isn't the driver's fault)."""
    fake = _FakeTable()
    fake.queue_select("payouts", [{"id": "id-ready", "driver_id": "driver-1", "amount": 10.0}])
    fake.queue_select("drivers", [])  # account gone

    fake_stripe = MagicMock()
    import sys

    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    with patch.object(svc, "supabase", fake):
        result = svc.fire_ready_transfers("sk_test_123")

    assert result == {"fired": 0, "failed": []}
    fake_stripe.Transfer.create.assert_not_called()
    update_id, patch_dict = fake.updated[-1]
    assert update_id == "id-ready"
    assert patch_dict["status"] == "awaiting_stripe_onboarding"


def test_fire_ready_transfers_marks_failed_on_stripe_error(monkeypatch):
    fake = _FakeTable()
    fake.queue_select("payouts", [{"id": "id-ready", "driver_id": "driver-1", "amount": 10.0}])
    fake.queue_select("drivers", [{"id": "driver-1", "stripe_account_id": "acct_123"}])

    fake_stripe = MagicMock()
    fake_stripe.Transfer.create.side_effect = RuntimeError("stripe down")
    import sys

    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    with patch.object(svc, "supabase", fake):
        result = svc.fire_ready_transfers("sk_test_123")

    assert result == {"fired": 0, "failed": ["id-ready"]}
    update_id, patch_dict = fake.updated[-1]
    assert update_id == "id-ready"
    assert patch_dict["status"] == "failed"
    assert "stripe down" in patch_dict["failure_reason"]
