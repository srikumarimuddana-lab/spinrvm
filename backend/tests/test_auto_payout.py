"""Tests for the weekly auto-payout service (utils/auto_payout.py).

Covers the review-fleet findings for PR #3925:
- Balance computation + parity with the /drivers/balance endpoint
- Batch idempotency, resume-from-crash, partial status
- DuplicateRecordError dispatch (completed / reserved / failed / in-flight
  conflict via migration 250's one-in-flight index)
- Stripe error taxonomy: permanent -> failed; retryable/ambiguous -> stays
  reserved (no timeout-after-success double-pay)
- Stale-reserved sweep: same-key replay window, new-key retryable retries,
  escalation to manual reconcile
- Eligibility gates (CRA GST/SIN, payouts-disabled, suspended), $10/$5,000
  bounds, Transfer call kwargs (idempotency key, cents, transfer_group)
- Instant payout kill switch (gate unit + endpoint wiring) and the 410 stub
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.utils.error_handling import DuplicateRecordError

DRIVER_ID = "driver_auto_001"
WEEK_KEY = "2026-W33"
BATCH_ID = f"auto-batch-{WEEK_KEY}"
PAYOUT_ID = f"auto-{DRIVER_ID}-{WEEK_KEY}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _ride(**extra):
    return {
        "id": "ride_1",
        "driver_id": DRIVER_ID,
        "status": "completed",
        "driver_earnings": "25.00",
        "base_fare": "20.00",
        "distance_fare": "3.00",
        "time_fare": "2.00",
        "tip_amount": "5.00",
        "tax_amount": "3.25",
        "cancellation_fee_driver": None,
        "fare_breakdown_snapshot": None,
        "legacy_import_metadata": {},
        **extra,
    }


def _driver(**extra):
    """Fully-eligible driver: Stripe account with payouts enabled, not
    suspended, CRA GST BN + SIN on file."""
    return {
        "id": DRIVER_ID,
        "user_id": "user_auto_001",
        "stripe_account_id": "acct_TEST_AUTO",
        "stripe_payouts_enabled": True,
        "is_suspended": False,
        "gst_bn": "123456789RT0001",
        "sin": None,
        "stripe_id_number_provided": True,
        **extra,
    }


def _mock_db(tables: dict):
    """get_rows side_effect keyed on (table, status filter)."""

    async def side_effect(table, filters=None, **kw):
        filters = filters or {}
        if table == "rides":
            status = filters.get("status")
            status = getattr(status, "value", status)  # RideStatus enum or str
            return tables.get(f"rides_{status}", [])
        return tables.get(table, [])

    return side_effect


def _sb_claims(claims=None):
    mock_sb = MagicMock()
    mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value = MagicMock(data=claims or [])
    return mock_sb


_SETTINGS_OK = {"stripe_secret_key": "sk_test_xxx", "auto_payout_enabled": True}


@contextmanager
def _apply(patches):
    with ExitStack() as es:
        for p in patches:
            es.enter_context(p)
        yield


def _run_patches(tables, insert=None, update=None, settings=None, transfer=None):
    """Standard patch stack for run_weekly_auto_payout tests."""
    insert = insert if insert is not None else AsyncMock()
    update = update if update is not None else AsyncMock(return_value=[{"id": BATCH_ID}])
    stack = [
        patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
        patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=_mock_db(tables)),
        patch("backend.utils.auto_payout.db_supabase.insert_one", insert),
        patch("backend.utils.auto_payout.db_supabase.update_one", update),
        patch("backend.utils.auto_payout.db_supabase.supabase", _sb_claims()),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=settings or _SETTINGS_OK)),
        patch("backend.utils.auto_payout._notify_driver", new_callable=AsyncMock),
    ]
    if transfer is not None:
        stack.append(patch("stripe.Transfer.create", transfer))
    return stack


# ── Balance computation ────────────────────────────────────────────────


class TestComputePayableBalance:
    async def _balance(self, tables, claims=None):
        from backend.utils.auto_payout import _compute_payable_balance

        with (
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=_mock_db(tables)),
            patch("backend.utils.auto_payout.db_supabase.supabase", _sb_claims(claims)),
        ):
            return await _compute_payable_balance(DRIVER_ID)

    @pytest.mark.anyio
    async def test_basic_balance(self):
        balance = await self._balance({"rides_completed": [_ride(driver_earnings="50.00", tax_amount="5.00")]})
        assert balance == Decimal("55.00")

    @pytest.mark.anyio
    async def test_deducts_completed_payouts(self):
        balance = await self._balance(
            {
                "rides_completed": [_ride(driver_earnings="100.00", tax_amount="0.00")],
                "payouts": [{"amount": "30.00", "status": "completed", "payout_type": "auto"}],
            }
        )
        assert balance == Decimal("70.00")

    @pytest.mark.anyio
    async def test_reserved_rows_still_deduct(self):
        # A reserved (in-flight or stranded) row must keep the money earmarked —
        # this is the anti-double-pay direction of the taxonomy.
        balance = await self._balance(
            {
                "rides_completed": [_ride(driver_earnings="100.00", tax_amount="0.00")],
                "payouts": [{"amount": "40.00", "status": "reserved", "payout_type": "auto"}],
            }
        )
        assert balance == Decimal("60.00")

    @pytest.mark.anyio
    async def test_excludes_reversed_failed_and_synced(self):
        balance = await self._balance(
            {
                "rides_completed": [_ride(driver_earnings="100.00", tax_amount="0.00")],
                "payouts": [
                    {"amount": "30.00", "status": "reversed", "payout_type": "auto"},
                    {"amount": "20.00", "status": "failed", "payout_type": "auto"},
                    {"amount": "50.00", "status": "completed", "payout_type": "stripe_sync"},
                ],
            }
        )
        assert balance == Decimal("100.00")

    @pytest.mark.anyio
    async def test_parity_with_balance_endpoint(self):
        """The batch must pay exactly what GET /drivers/balance displays."""
        from backend.routes.drivers.earnings import get_driver_balance
        from backend.utils.auto_payout import _compute_payable_balance

        tables = {
            "drivers": [_driver()],
            "rides_completed": [
                _ride(driver_earnings="80.00", tax_amount="4.00"),
                _ride(id="ride_2", driver_earnings="20.00", tax_amount="1.00", tip_amount="2.00"),
            ],
            "rides_cancelled": [{"cancellation_fee_driver": "5.00"}],
            "driver_bonuses": [{"amount": "10.00", "kind": "quest"}],
            "payouts": [{"amount": "30.00", "status": "completed", "payout_type": "auto"}],
        }
        with (
            patch("backend.db_supabase.get_rows", side_effect=_mock_db(tables)),
            patch("backend.db_supabase.supabase", _sb_claims()),
        ):
            endpoint = await get_driver_balance(current_user={"id": "user_auto_001"})
            batch = await _compute_payable_balance(DRIVER_ID)
        assert str(batch) == endpoint["payable_balance"]


# ── Batch run ──────────────────────────────────────────────────────────


class TestRunWeeklyAutoPayout:
    @pytest.mark.anyio
    async def test_skips_already_completed_batch(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {"auto_payout_batches": [{"id": BATCH_ID, "week_key": WEEK_KEY, "status": "completed"}]}
        with (
            patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=_mock_db(tables)),
        ):
            result = await run_weekly_auto_payout()
        assert result["status"] == "already_completed"

    @pytest.mark.anyio
    async def test_fresh_running_batch_skips(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {
            "auto_payout_batches": [
                {"id": BATCH_ID, "status": "running", "started_at": _iso(datetime.now(timezone.utc))}
            ]
        }
        with (
            patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=_mock_db(tables)),
        ):
            result = await run_weekly_auto_payout()
        assert result["status"] == "already_running"

    @pytest.mark.anyio
    async def test_stale_running_batch_is_claimed_and_resumed(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        stale = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
        tables = {
            "auto_payout_batches": [{"id": BATCH_ID, "status": "running", "started_at": stale}],
            "drivers": [_driver()],
            "rides_completed": [_ride(driver_earnings="50.00", tax_amount="5.00")],
        }
        transfer = MagicMock(return_value=MagicMock(id="tr_RESUME"))
        with _apply(
            _run_patches(tables, transfer=transfer),
        ):
            result = await run_weekly_auto_payout()
        assert result["resumed"] is True
        assert result["status"] == "completed"
        assert result["drivers_paid"] == 1

    @pytest.mark.anyio
    async def test_lost_resume_claim_backs_off(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        stale = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
        tables = {"auto_payout_batches": [{"id": BATCH_ID, "status": "running", "started_at": stale}]}
        with _apply(
            _run_patches(tables, update=AsyncMock(return_value=[])),
        ):
            result = await run_weekly_auto_payout()
        assert result["status"] == "already_running"

    @pytest.mark.anyio
    async def test_skips_when_disabled_via_settings(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        inserts = []

        async def track_insert(table, doc):
            inserts.append(table)

        with _apply(
            _run_patches(
                {},
                insert=AsyncMock(side_effect=track_insert),
                settings={"auto_payout_enabled": False, "stripe_secret_key": "sk_test_xxx"},
            ),
        ):
            result = await run_weekly_auto_payout()
        assert result["status"] == "disabled"
        assert inserts == []

    @pytest.mark.anyio
    async def test_skips_when_stripe_not_configured(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        insert = AsyncMock()
        with _apply(
            _run_patches({}, insert=insert, settings={"stripe_secret_key": ""}),
        ):
            result = await run_weekly_auto_payout()
        assert result["status"] == "stripe_not_configured"
        insert.assert_not_awaited()

    @pytest.mark.anyio
    async def test_concurrent_batch_insert_backs_off(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        with _apply(
            _run_patches({}, insert=AsyncMock(side_effect=DuplicateRecordError())),
        ):
            result = await run_weekly_auto_payout()
        assert result["status"] == "already_running"

    @pytest.mark.anyio
    async def test_pays_eligible_driver_with_exact_transfer_args(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {
            "drivers": [_driver()],
            "rides_completed": [_ride(driver_earnings="50.00", tax_amount="5.00")],
        }
        inserts, updates = [], []

        async def track_insert(table, doc):
            inserts.append((table, doc))

        async def track_update(table, filters, doc):
            updates.append((table, filters, doc))
            return [{"id": filters.get("id")}]

        transfer = MagicMock(return_value=MagicMock(id="tr_TEST_123"))
        with _apply(
            _run_patches(
                tables,
                insert=AsyncMock(side_effect=track_insert),
                update=AsyncMock(side_effect=track_update),
                transfer=transfer,
            ),
        ):
            result = await run_weekly_auto_payout()

        assert result["status"] == "completed"
        assert result["drivers_paid"] == 1

        reserve = next(d for t, d in inserts if t == "payouts")
        assert reserve["status"] == "reserved"
        assert reserve["payout_type"] == "auto"
        assert reserve["amount"] == Decimal("55.00")  # Decimal, not float

        kwargs = transfer.call_args.kwargs
        assert kwargs["amount"] == 5500
        assert kwargs["currency"] == "cad"
        assert kwargs["destination"] == "acct_TEST_AUTO"
        assert kwargs["idempotency_key"] == f"auto-payout-{DRIVER_ID}-{WEEK_KEY}"
        assert kwargs["transfer_group"] == f"auto-{WEEK_KEY}"
        assert kwargs["metadata"]["payout_id"] == PAYOUT_ID

        batch_update = next(u for u in updates if u[0] == "auto_payout_batches" and "status" in u[2])
        assert batch_update[2]["status"] == "completed"
        assert batch_update[2]["total_amount"] == Decimal("55.00")

    @pytest.mark.anyio
    async def test_balance_exactly_ten_dollars_pays(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {"drivers": [_driver()], "rides_completed": [_ride(driver_earnings="10.00", tax_amount="0.00")]}
        transfer = MagicMock(return_value=MagicMock(id="tr_MIN"))
        with _apply(
            _run_patches(tables, transfer=transfer),
        ):
            result = await run_weekly_auto_payout()
        assert result["drivers_paid"] == 1

    @pytest.mark.anyio
    async def test_skips_driver_below_minimum(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {"drivers": [_driver()], "rides_completed": [_ride(driver_earnings="5.00", tax_amount="0.00")]}
        with _apply(
            _run_patches(tables),
        ):
            result = await run_weekly_auto_payout()
        assert result["drivers_paid"] == 0
        assert result["drivers_eligible"] == 0

    @pytest.mark.anyio
    async def test_over_cap_balance_skipped_for_review(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {"drivers": [_driver()], "rides_completed": [_ride(driver_earnings="9000.00", tax_amount="0.00")]}
        transfer = MagicMock()
        with _apply(
            _run_patches(tables, transfer=transfer),
        ):
            result = await run_weekly_auto_payout()
        transfer.assert_not_called()
        assert result["drivers_paid"] == 0
        assert result["status"] in ("partial", "failed")  # surfaced, not silent

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "override,reason",
        [
            ({"stripe_account_id": None}, "no_stripe_account"),
            ({"stripe_payouts_enabled": False}, "stripe_payouts_disabled"),
            ({"is_suspended": True}, "suspended"),
            ({"gst_bn": None}, "missing_gst"),
            ({"gst_bn": "12345"}, "missing_gst"),
            ({"sin": None, "stripe_id_number_provided": False}, "missing_sin"),
        ],
    )
    async def test_eligibility_gates_skip(self, override, reason):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {
            "drivers": [_driver(**override)],
            "rides_completed": [_ride(driver_earnings="50.00", tax_amount="0.00")],
        }
        transfer = MagicMock()
        with _apply(
            _run_patches(tables, transfer=transfer),
        ):
            result = await run_weekly_auto_payout()
        transfer.assert_not_called()
        assert result["drivers_paid"] == 0
        assert result["skipped"].get(reason) == 1

    @pytest.mark.anyio
    async def test_permanent_stripe_failure_marks_failed(self):
        import stripe as stripe_lib

        from backend.utils.auto_payout import run_weekly_auto_payout

        err_ns = getattr(stripe_lib, "error", stripe_lib)
        exc = getattr(err_ns, "InvalidRequestError")("No such destination", param=None)
        tables = {"drivers": [_driver()], "rides_completed": [_ride(driver_earnings="50.00", tax_amount="0.00")]}
        updates = []

        async def track_update(table, filters, doc):
            updates.append((table, filters, doc))
            return [{"id": filters.get("id")}]

        with _apply(
            _run_patches(tables, update=AsyncMock(side_effect=track_update), transfer=MagicMock(side_effect=exc)),
        ):
            result = await run_weekly_auto_payout()

        assert result["status"] == "failed"  # sole driver failed
        assert result["drivers_failed"] == 1
        row_update = next(u for u in updates if u[0] == "payouts")
        assert row_update[2]["status"] == "failed"
        assert row_update[2]["failure_reason"]

    @pytest.mark.anyio
    async def test_ambiguous_stripe_failure_stays_reserved(self):
        """Timeout-after-success must NOT be marked failed — a failed row
        re-enters the balance and next week's fresh key would double-pay."""
        import stripe as stripe_lib

        from backend.utils.auto_payout import run_weekly_auto_payout

        err_ns = getattr(stripe_lib, "error", stripe_lib)
        exc = getattr(err_ns, "APIConnectionError")("read timeout")
        tables = {"drivers": [_driver()], "rides_completed": [_ride(driver_earnings="50.00", tax_amount="0.00")]}
        updates = []

        async def track_update(table, filters, doc):
            updates.append((table, filters, doc))
            return [{"id": filters.get("id")}]

        with _apply(
            _run_patches(tables, update=AsyncMock(side_effect=track_update), transfer=MagicMock(side_effect=exc)),
        ):
            result = await run_weekly_auto_payout()

        payout_updates = [u for u in updates if u[0] == "payouts"]
        assert payout_updates, "reserved row should get a failure_reason annotation"
        assert all(u[2].get("status") != "failed" for u in payout_updates)
        assert result["status"] in ("partial", "failed")

    @pytest.mark.anyio
    async def test_mixed_outcome_marks_batch_partial(self):
        import stripe as stripe_lib

        from backend.utils.auto_payout import run_weekly_auto_payout

        err_ns = getattr(stripe_lib, "error", stripe_lib)
        driver_b = _driver(id="driver_auto_002", user_id="user_auto_002", stripe_account_id="acct_B")
        tables = {
            "drivers": [_driver(), driver_b],
            "rides_completed": [_ride(driver_earnings="50.00", tax_amount="0.00")],
        }
        calls = {"n": 0}

        def transfer_side_effect(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return MagicMock(id="tr_OK")
            raise getattr(err_ns, "InvalidRequestError")("bad account", param=None)

        with _apply(
            _run_patches(tables, transfer=MagicMock(side_effect=transfer_side_effect)),
        ):
            result = await run_weekly_auto_payout()

        assert result["drivers_paid"] == 1
        assert result["drivers_failed"] == 1
        assert result["status"] == "partial"

    @pytest.mark.anyio
    async def test_duplicate_reserve_completed_row_counts_paid(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {
            "drivers": [_driver()],
            # Earnings high enough that the pre-existing completed payout row
            # still leaves a >= $10 balance, so the reserve step is reached.
            "rides_completed": [_ride(driver_earnings="100.00", tax_amount="0.00")],
            "payouts": [{"id": PAYOUT_ID, "amount": "55.00", "status": "completed", "payout_type": "auto"}],
        }
        transfer = MagicMock()
        with _apply(
            _run_patches(tables, insert=AsyncMock(side_effect=[None, DuplicateRecordError()]), transfer=transfer),
        ):
            result = await run_weekly_auto_payout()
        transfer.assert_not_called()
        assert result["drivers_paid"] == 1

    @pytest.mark.anyio
    async def test_duplicate_reserve_missing_row_is_inflight_conflict(self):
        """Migration 250's one-in-flight index fired for an unrelated row —
        must be surfaced loudly, not mistaken for this week's payout."""
        from backend.utils.auto_payout import run_weekly_auto_payout

        async def get_rows(table, filters=None, **kw):
            filters = filters or {}
            if table == "payouts" and filters.get("id") == PAYOUT_ID:
                return []  # this week's row does not exist — conflict was the partial index
            if table == "drivers":
                return [_driver()]
            if table == "rides" and filters.get("status") == "completed":
                return [_ride(driver_earnings="50.00", tax_amount="0.00")]
            return []

        transfer = MagicMock()
        with (
            patch("backend.utils.auto_payout.current_week_key", return_value=WEEK_KEY),
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=get_rows),
            patch(
                "backend.utils.auto_payout.db_supabase.insert_one",
                AsyncMock(side_effect=[None, DuplicateRecordError()]),
            ),
            patch("backend.utils.auto_payout.db_supabase.update_one", AsyncMock(return_value=[{}])),
            patch("backend.utils.auto_payout.db_supabase.supabase", _sb_claims()),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=_SETTINGS_OK)),
            patch("backend.utils.auto_payout._notify_driver", new_callable=AsyncMock),
            patch("stripe.Transfer.create", transfer),
        ):
            result = await run_weekly_auto_payout()
        transfer.assert_not_called()
        assert result["drivers_paid"] == 0
        assert any("inflight_conflict" in e for e in result["errors"])


# ── Blocked-driver notification + ops work-list ────────────────────────


class TestBlockedDriverVisibility:
    @pytest.mark.anyio
    async def test_blocked_driver_with_balance_is_notified_and_recorded(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {
            "drivers": [_driver(gst_bn=None)],
            "rides_completed": [_ride(driver_earnings="120.00", tax_amount="0.00")],
        }
        notify = AsyncMock()
        with _apply(
            [
                *_run_patches(tables),
                patch("backend.utils.auto_payout._notify_driver", notify),
            ]
        ):
            result = await run_weekly_auto_payout()

        assert result["skipped"]["missing_gst"] == 1
        assert result["skipped_drivers"]["missing_gst"] == [DRIVER_ID]
        notify.assert_awaited_once()
        _, title, body, data = notify.await_args.args
        assert "GST" in title
        assert "$120.00" in body  # tells them how much is being held
        assert data["type"] == "auto_payout_blocked"
        assert data["reason"] == "missing_gst"

    @pytest.mark.anyio
    async def test_blocked_driver_without_balance_is_not_notified(self):
        """A driver who never drove has nothing held up — telling them a
        payout was blocked would be noise, and untrue."""
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {"drivers": [_driver(gst_bn=None)], "rides_completed": []}
        notify = AsyncMock()
        with _apply(
            [
                *_run_patches(tables),
                patch("backend.utils.auto_payout._notify_driver", notify),
            ]
        ):
            result = await run_weekly_auto_payout()

        assert result["skipped"]["missing_gst"] == 1  # still counted
        assert result["skipped_drivers"] == {}  # but not on the ops work-list
        notify.assert_not_awaited()

    @pytest.mark.anyio
    async def test_suspended_driver_recorded_but_not_pushed(self):
        """Suspension is a support conversation, not an automated nudge."""
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {
            "drivers": [_driver(is_suspended=True)],
            "rides_completed": [_ride(driver_earnings="120.00", tax_amount="0.00")],
        }
        notify = AsyncMock()
        with _apply(
            [
                *_run_patches(tables),
                patch("backend.utils.auto_payout._notify_driver", notify),
            ]
        ):
            result = await run_weekly_auto_payout()

        assert result["skipped_drivers"]["suspended"] == [DRIVER_ID]
        notify.assert_not_awaited()

    @pytest.mark.anyio
    async def test_skipped_summary_persisted_on_batch_row(self):
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {
            "drivers": [_driver(gst_bn=None)],
            "rides_completed": [_ride(driver_earnings="120.00", tax_amount="0.00")],
        }
        updates = []

        async def track_update(table, filters, doc):
            updates.append((table, filters, doc))
            return [{"id": filters.get("id")}]

        with _apply(
            [
                *_run_patches(tables, update=AsyncMock(side_effect=track_update)),
                patch("backend.utils.auto_payout._notify_driver", new_callable=AsyncMock),
            ]
        ):
            await run_weekly_auto_payout()

        batch_update = next(u for u in updates if u[0] == "auto_payout_batches" and "skipped_summary" in u[2])
        summary = batch_update[2]["skipped_summary"]
        assert summary["counts"]["missing_gst"] == 1
        assert summary["drivers_with_balance"]["missing_gst"] == [DRIVER_ID]

    @pytest.mark.anyio
    async def test_batch_records_per_service_area_breakdown(self):
        """The run is fleet-wide, but must record a per-market slice so the
        admin page can report by service area."""
        from backend.utils.auto_payout import run_weekly_auto_payout

        tables = {
            "drivers": [
                _driver(service_area_id="sa_regina"),
                _driver(id="drv_saskatoon", user_id="u2", service_area_id="sa_saskatoon"),
            ],
            "rides_completed": [_ride(driver_earnings="60.00", tax_amount="0.00")],
        }
        with _apply([*_run_patches(tables, transfer=MagicMock(return_value=MagicMock(id="tr_area")))]):
            result = await run_weekly_auto_payout()

        areas = result["area_summary"]
        assert areas["sa_regina"]["paid"] == 1
        assert areas["sa_regina"]["amount"] == "60.00"
        assert areas["sa_saskatoon"]["paid"] == 1

    @pytest.mark.anyio
    async def test_blocked_drivers_filter_by_service_area(self):
        """Area filter must be pushed into the query, not applied after —
        otherwise a market's list is capped by other markets' drivers."""
        from backend.utils.auto_payout import find_blocked_drivers

        seen_filters = []

        async def get_rows(table, filters=None, **kw):
            filters = filters or {}
            if table == "drivers":
                seen_filters.append(filters)
                return [_driver(gst_bn=None, service_area_id="sa_regina")]
            if table == "rides":
                status = filters.get("status")
                status = getattr(status, "value", status)
                return [_ride(driver_earnings="75.00", tax_amount="0.00")] if status == "completed" else []
            return []

        with (
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=get_rows),
            patch("backend.utils.auto_payout.db_supabase.supabase", _sb_claims()),
        ):
            blocked = await find_blocked_drivers(limit=50, service_area_id="sa_regina")

        assert seen_filters[0] == {"service_area_id": "sa_regina"}
        assert blocked[0]["service_area_id"] == "sa_regina"

    @pytest.mark.anyio
    async def test_find_blocked_drivers_reports_reason_and_amount(self):
        from backend.utils.auto_payout import find_blocked_drivers

        tables = {
            "drivers": [_driver(stripe_payouts_enabled=False), _driver(id="ok_driver", user_id="u2")],
            "rides_completed": [_ride(driver_earnings="75.00", tax_amount="0.00")],
        }
        with (
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=_mock_db(tables)),
            patch("backend.utils.auto_payout.db_supabase.supabase", _sb_claims()),
        ):
            blocked = await find_blocked_drivers(limit=50)

        assert len(blocked) == 1
        assert blocked[0]["driver_id"] == DRIVER_ID
        assert blocked[0]["reason"] == "stripe_payouts_disabled"
        assert blocked[0]["pending_amount"] == "75.00"


# ── Stale-reserved sweep ───────────────────────────────────────────────


class TestStaleReservedSweep:
    def _row(self, age_hours: float, reason: str = "", attempts: int = 0):
        ts = _iso(datetime.now(timezone.utc) - timedelta(hours=age_hours))
        return {
            "id": PAYOUT_ID,
            "driver_id": DRIVER_ID,
            "amount": "55.00",
            "status": "reserved",
            "payout_type": "auto",
            "failure_reason": reason,
            "auto_retry_count": attempts,
            "created_at": ts,
            "updated_at": ts,
        }

    async def _sweep(self, row, transfer):
        from backend.utils.auto_payout import sweep_stale_reserved

        updates = []

        async def track_update(table, filters, doc):
            updates.append((table, filters, doc))
            return [{}]

        async def get_rows(table, filters=None, **kw):
            filters = filters or {}
            if table == "payouts":
                return [row]
            if table == "drivers":
                return [_driver()]
            return []

        with (
            patch("backend.utils.auto_payout.db_supabase.get_rows", side_effect=get_rows),
            patch("backend.utils.auto_payout.db_supabase.update_one", AsyncMock(side_effect=track_update)),
            patch("backend.utils.auto_payout._notify_driver", new_callable=AsyncMock),
            patch("stripe.Transfer.create", transfer),
        ):
            counts = await sweep_stale_reserved("sk_test_xxx")
        return counts, updates

    @pytest.mark.anyio
    async def test_fresh_row_left_alone(self):
        transfer = MagicMock()
        counts, updates = await self._sweep(self._row(age_hours=0.1), transfer)
        transfer.assert_not_called()
        assert counts == {"retried": 0, "completed": 0, "escalated": 0}

    @pytest.mark.anyio
    async def test_ambiguous_row_replays_same_key_within_window(self):
        """Within Stripe's key window, replaying the SAME key returns the
        original transfer if it actually succeeded — resolving the ambiguity
        without a second transfer."""
        transfer = MagicMock(return_value=MagicMock(id="tr_REPLAY"))
        counts, updates = await self._sweep(self._row(age_hours=2, reason="ambiguous: read timeout"), transfer)
        assert counts["completed"] == 1
        assert transfer.call_args.kwargs["idempotency_key"] == f"auto-payout-{DRIVER_ID}-{WEEK_KEY}"
        assert any(u[2].get("status") == "completed" for u in updates)

    @pytest.mark.anyio
    async def test_retryable_row_uses_new_key(self):
        transfer = MagicMock(return_value=MagicMock(id="tr_RETRY"))
        counts, updates = await self._sweep(
            self._row(age_hours=2, reason="balance_insufficient: platform balance too low"), transfer
        )
        assert counts["completed"] == 1
        assert transfer.call_args.kwargs["idempotency_key"] == f"auto-payout-{DRIVER_ID}-{WEEK_KEY}-r1"

    @pytest.mark.anyio
    async def test_stale_ambiguous_row_escalates_not_retries(self):
        """Past the same-key window a replay could double-transfer — the row
        must be parked for manual reconcile instead."""
        transfer = MagicMock()
        counts, updates = await self._sweep(self._row(age_hours=25, reason="ambiguous: read timeout"), transfer)
        transfer.assert_not_called()
        assert counts["escalated"] == 1
        reason_update = next(u for u in updates if "failure_reason" in u[2])
        assert reason_update[2]["failure_reason"].startswith("needs_manual_reconcile")

    @pytest.mark.anyio
    async def test_escalated_row_not_touched_again(self):
        transfer = MagicMock()
        counts, updates = await self._sweep(self._row(age_hours=30, reason="needs_manual_reconcile: earlier"), transfer)
        transfer.assert_not_called()
        assert updates == []


# ── Batch window (Regina local time) ───────────────────────────────────


class TestBatchWindow:
    def test_sunday_morning_regina_is_in_window(self):
        from zoneinfo import ZoneInfo

        from backend.utils.auto_payout import _is_batch_window

        tz = ZoneInfo("America/Regina")
        assert _is_batch_window(datetime(2026, 8, 16, 6, 0, tzinfo=tz)) is True  # Sunday 6am
        assert _is_batch_window(datetime(2026, 8, 16, 5, 59, tzinfo=tz)) is False  # Sunday 5:59am
        assert _is_batch_window(datetime(2026, 8, 15, 20, 0, tzinfo=tz)) is False  # Saturday evening
        assert _is_batch_window(datetime(2026, 8, 17, 12, 0, tzinfo=tz)) is False  # Monday


# ── Instant payout kill switch ─────────────────────────────────────────


class TestInstantPayoutKillSwitch:
    @pytest.mark.anyio
    async def test_blocks_when_disabled_for_service_area(self):
        from backend.routes.drivers.payouts import _require_instant_payout_enabled

        async def mock_get_rows(table, filters, **kw):
            return [{"id": "sa_001", "instant_payout_enabled": False}] if table == "service_areas" else []

        with patch("backend.routes.drivers.payouts.db_supabase.get_rows", side_effect=mock_get_rows):
            with pytest.raises(HTTPException) as exc_info:
                await _require_instant_payout_enabled({"service_area_id": "sa_001"})
            assert exc_info.value.status_code == 403

    @pytest.mark.anyio
    async def test_allows_when_enabled_for_service_area(self):
        from backend.routes.drivers.payouts import _require_instant_payout_enabled

        async def mock_get_rows(table, filters, **kw):
            return [{"id": "sa_001", "instant_payout_enabled": True}] if table == "service_areas" else []

        with patch("backend.routes.drivers.payouts.db_supabase.get_rows", side_effect=mock_get_rows):
            await _require_instant_payout_enabled({"service_area_id": "sa_001"})

    @pytest.mark.anyio
    async def test_allows_when_no_service_area(self):
        from backend.routes.drivers.payouts import _require_instant_payout_enabled

        await _require_instant_payout_enabled({"service_area_id": None})

    @pytest.mark.anyio
    async def test_allows_when_service_area_missing_from_db(self):
        from backend.routes.drivers.payouts import _require_instant_payout_enabled

        async def mock_get_rows(table, filters, **kw):
            return []

        with patch("backend.routes.drivers.payouts.db_supabase.get_rows", side_effect=mock_get_rows):
            await _require_instant_payout_enabled({"service_area_id": "sa_missing"})

    @pytest.mark.anyio
    async def test_endpoint_wires_the_gate(self):
        """Deleting the gate call in request_instant_payout must fail a test —
        every prior endpoint test used drivers with no service_area_id, so the
        gate could have been unwired without CI noticing."""
        from starlette.requests import Request as StarletteRequest

        from backend.routes.drivers import InstantPayoutRequest, request_instant_payout

        async def mock_get_rows(table, filters=None, **kw):
            filters = filters or {}
            if table == "drivers":
                return [_driver(service_area_id="sa_001")]
            if table == "service_areas":
                return [{"id": "sa_001", "instant_payout_enabled": False}]
            return []

        req = InstantPayoutRequest(amount=Decimal("50.00"))
        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/drivers/payouts/instant", "query_string": b"", "headers": []}
        )
        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=mock_get_rows)):
            with pytest.raises(HTTPException) as exc_info:
                await request_instant_payout(req=req, request=request, current_user={"id": "user_auto_001"})
        assert exc_info.value.status_code == 403


# ── Standard cashout 410 ──────────────────────────────────────────────


class TestStandardCashoutDisabled:
    @pytest.mark.anyio
    async def test_request_payout_returns_410(self):
        from backend.routes.drivers.payouts import request_payout

        with pytest.raises(HTTPException) as exc_info:
            await request_payout(current_user={"id": "user_001"})
        assert exc_info.value.status_code == 410
        assert len(exc_info.value.detail) <= 140  # driver-app toast clamps at 140 chars
