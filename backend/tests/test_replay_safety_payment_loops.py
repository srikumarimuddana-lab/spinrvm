"""Replay-safety guarantees for payment-touching background loops.

CLAUDE.md, Background loops:
  > The 7 startup loops in core/lifespan.py all run on every replica
  > simultaneously. A new loop must satisfy the replay-safety contract or
  > it will cause duplicate writes, charges, or notifications.

These tests pin the contract for two loops that touch Stripe:

  1. ``payment_retry.retry_failed_payments`` — Stripe ``idempotency_key``
     on PaymentIntent.confirm + atomic claim on the payment_retry_count
     bump. Two replicas confirming the same intent at the same retry
     count must produce the same key (so Stripe dedupes the charge) and
     only one must fire the rider/driver push (so the rider isn't
     notified twice).

  2. ``payment_retry.retry_stuck_payouts`` — atomic conditional update
     on ``status='pending'``. Two replicas trying to flip the same payout
     to 'failed' must result in exactly one driver "Payout failed" push.

  3. ``corporate_autotopup._process_one`` — Stripe ``idempotency_key`` on
     PaymentIntent.create derived from observable state
     ``(wallet_id, today_date, today_sum_cents, topup_amount_cents)``.
     Two replicas seeing the same today_sum produce the same key, so
     Stripe collapses the duplicate. Once the first top-up clears via
     webhook, today_sum advances and the next tick legitimately uses a
     different key.

The tests use ``asyncio.run()`` rather than ``@pytest.mark.anyio`` because
pytest-asyncio isn't installed in the project's runtime; anyio is loaded
via conftest.py for fixtures only. Running the coroutine directly keeps
this file independent of either plugin's quirks.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

# ── payment_retry.retry_failed_payments ───────────────────────────────


def _ride_row(**overrides) -> dict:
    base = {
        "id": "ride_1",
        "rider_id": "rider_1",
        "driver_id": "driver_1",
        "payment_status": "failed",
        "payment_intent_id": "pi_1",
        "payment_retry_count": 0,
        "created_at": "2099-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _settings_with_secret() -> dict:
    return {"stripe_secret_key": "sk_test_replay"}


def test_retry_idempotency_key_is_deterministic_from_ride_and_count():
    """Two replicas processing the same (ride_id, retry_count) must
    pass identical ``idempotency_key`` to Stripe so the duplicate
    confirm is collapsed at the API boundary."""
    ride = _ride_row(payment_retry_count=2)

    fake_intent = MagicMock(status="requires_payment_method", amount=2550)
    captured_keys: list[str] = []

    def _capture_confirm(_pi_id, **kwargs):
        captured_keys.append(kwargs.get("idempotency_key", ""))
        return MagicMock(status="processing")

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=_settings_with_secret())),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch(
            "utils.payment_retry.db.update_one",
            AsyncMock(return_value={"id": "ride_1"}),
        ),
        patch("stripe.PaymentIntent.retrieve", return_value=fake_intent),
        patch("stripe.PaymentIntent.confirm", side_effect=_capture_confirm),
    ):
        from utils.payment_retry import retry_failed_payments

        # Run twice — simulating two replicas reading the same retry_count.
        asyncio.run(retry_failed_payments())
        asyncio.run(retry_failed_payments())

    assert len(captured_keys) == 2
    # Key shape: ride-confirm-<ride_id>-<amount_cents>-retry-<attempt>.
    # payment_retry_count=2 → retry_count=2 → attempt=3.
    # Two replicas with the same retry_count still produce identical keys,
    # so concurrent confirms for the same attempt still dedupe at Stripe.
    assert captured_keys[0] == "ride-confirm-ride_1-2550-retry-3"
    assert captured_keys[0] == captured_keys[1], (
        "Two replicas confirming the same PI must produce identical idempotency keys"
    )


def test_retry_atomic_claim_loser_skips_driver_push():
    """When the conditional update returns None (another replica won
    the race), the driver-side 'Payment retry in progress' push must
    NOT fire."""
    ride = _ride_row(payment_retry_count=0)
    fake_intent = MagicMock(status="requires_payment_method")
    push_calls: list[tuple] = []

    async def _capture_push(*args, **kwargs):
        push_calls.append((args, kwargs))

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=_settings_with_secret())),
        patch("utils.payment_retry.send_push_notification", _capture_push),
        # Loser of the race — claim returns None.
        patch("utils.payment_retry.db.update_one", AsyncMock(return_value=None)),
        patch("stripe.PaymentIntent.retrieve", return_value=fake_intent),
        patch("stripe.PaymentIntent.confirm", return_value=MagicMock()),
    ):
        from utils.payment_retry import retry_failed_payments

        asyncio.run(retry_failed_payments())

    assert push_calls == [], "Loser of the atomic claim must not fire driver push"


def test_retry_atomic_claim_winner_fires_driver_push():
    """The replica that wins the conditional update is the one that
    notifies the driver. Confirmation: when update_one returns a row,
    push_notification is called exactly once."""
    ride = _ride_row(payment_retry_count=0)
    fake_intent = MagicMock(status="requires_payment_method")
    push_calls: list[tuple] = []

    async def _capture_push(*args, **kwargs):
        push_calls.append((args, kwargs))

    async def fake_get_rows(table, *args, **kwargs):
        if table == "drivers":
            # N3 (ACTION_ITEMS.md): rides.driver_id is a drivers.id, not a
            # users.id — resolved via a batched "drivers" lookup before the
            # push fires.
            return [{"id": "driver_1", "user_id": "driver_1_user"}]
        return [ride]

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(side_effect=fake_get_rows)),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=_settings_with_secret())),
        patch("utils.payment_retry.send_push_notification", _capture_push),
        patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": "ride_1"})),
        patch("stripe.PaymentIntent.retrieve", return_value=fake_intent),
        patch("stripe.PaymentIntent.confirm", return_value=MagicMock()),
    ):
        from utils.payment_retry import retry_failed_payments

        asyncio.run(retry_failed_payments())

    assert len(push_calls) == 1
    # First positional arg is the resolved users.id, not rides.driver_id.
    assert push_calls[0][0][0] == "driver_1_user"


def test_retry_atomic_claim_filters_on_prior_count():
    """The claim filter must include payment_retry_count=<prior> so two
    replicas racing on the same row cannot both win."""
    ride = _ride_row(payment_retry_count=1)
    fake_intent = MagicMock(status="requires_payment_method")
    update_calls: list[tuple] = []

    async def _capture_update(table, filters, update):
        update_calls.append((table, dict(filters), update))
        return {"id": "ride_1"}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[ride])),
        patch("utils.payment_retry.get_app_settings", AsyncMock(return_value=_settings_with_secret())),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch("utils.payment_retry.db.update_one", _capture_update),
        patch("stripe.PaymentIntent.retrieve", return_value=fake_intent),
        patch("stripe.PaymentIntent.confirm", return_value=MagicMock()),
    ):
        from utils.payment_retry import retry_failed_payments

        asyncio.run(retry_failed_payments())

    assert update_calls, "expected an update call"
    _, filters, _ = update_calls[0]
    assert filters.get("id") == "ride_1"
    assert filters.get("payment_retry_count") == 1, (
        "Atomic claim must filter on the prior retry count to prevent double-bump"
    )


# ── payment_retry.retry_stuck_payouts ─────────────────────────────────


def test_stuck_payout_loser_does_not_notify_driver():
    """Two replicas race to flip a payout pending → failed. The loser
    sees update_one return None and must skip the driver push so the
    driver isn't paged twice."""
    payout = {
        "id": "payout_1",
        "driver_id": "driver_1",
        "status": "pending",
        "retry_count": 5,  # > MAX_RETRIES (3)
    }
    push_calls: list[tuple] = []

    async def _capture_push(*args, **kwargs):
        push_calls.append((args, kwargs))

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[payout])),
        patch("utils.payment_retry.send_push_notification", _capture_push),
        # Loser — claim returns None (another replica already flipped status).
        patch("utils.payment_retry.db.update_one", AsyncMock(return_value=None)),
    ):
        from utils.payment_retry import retry_stuck_payouts

        asyncio.run(retry_stuck_payouts())

    assert push_calls == []


def test_stuck_payout_winner_notifies_driver_once():
    payout = {
        "id": "payout_1",
        "driver_id": "driver_1",
        "status": "pending",
        "retry_count": 5,
    }
    push_calls: list[tuple] = []

    async def _capture_push(*args, **kwargs):
        push_calls.append((args, kwargs))

    async def fake_get_rows(table, *args, **kwargs):
        if table == "drivers":
            # N3 (ACTION_ITEMS.md): payouts.driver_id is a drivers.id, not a
            # users.id — resolved via a batched "drivers" lookup before the
            # push fires.
            return [{"id": "driver_1", "user_id": "driver_1_user"}]
        return [payout]

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(side_effect=fake_get_rows)),
        patch("utils.payment_retry.send_push_notification", _capture_push),
        patch("utils.payment_retry.db.update_one", AsyncMock(return_value={"id": "payout_1"})),
    ):
        from utils.payment_retry import retry_stuck_payouts

        asyncio.run(retry_stuck_payouts())

    assert len(push_calls) == 1
    # First positional arg is the resolved users.id, not payouts.driver_id.
    assert push_calls[0][0][0] == "driver_1_user"


def test_stuck_payout_claim_filters_on_status_pending():
    """The claim filter must include status='pending' so racing replicas
    cannot both flip the same row to failed."""
    payout = {
        "id": "payout_1",
        "driver_id": "driver_1",
        "status": "pending",
        "retry_count": 5,
    }
    update_calls: list[tuple] = []

    async def _capture_update(table, filters, update):
        update_calls.append((table, dict(filters), update))
        return {"id": "payout_1"}

    with (
        patch("utils.payment_retry.db.get_rows", AsyncMock(return_value=[payout])),
        patch("utils.payment_retry.send_push_notification", AsyncMock()),
        patch("utils.payment_retry.db.update_one", _capture_update),
    ):
        from utils.payment_retry import retry_stuck_payouts

        asyncio.run(retry_stuck_payouts())

    assert update_calls
    _, filters, _ = update_calls[0]
    assert filters.get("id") == "payout_1"
    assert filters.get("status") == "pending"


# ── corporate_autotopup._process_one ──────────────────────────────────


def _autotopup_wallet(**overrides) -> dict:
    base = {
        "id": "wallet_1",
        "company_id": "company_1",
        "auto_topup_amount": 500,
        "auto_topup_daily_cap": 5000,
    }
    base.update(overrides)
    return base


def _expected_autotopup_key(wallet_id: str, today_sum: float, topup_amount: float) -> str:
    seed = (
        f"autotopup:{wallet_id}:{date.today().isoformat()}:"
        f"{int(round(today_sum * 100))}:{int(round(topup_amount * 100))}"
    )
    return hashlib.sha256(seed.encode()).hexdigest()


def test_autotopup_idempotency_key_is_sha256_of_observable_state():
    """Stripe gets a SHA-256 idempotency_key derived from
    (wallet, today_date, today_sum_cents, topup_amount_cents). Two
    replicas with identical reads produce identical keys."""
    wallet = _autotopup_wallet()
    company = {"id": "company_1", "stripe_customer_id": "cus_1", "status": "active"}
    captured_keys: list[str] = []

    def _capture_create(**kwargs):
        captured_keys.append(kwargs.get("idempotency_key", ""))
        return MagicMock(id="pi_topup")

    with (
        patch("utils.corporate_autotopup.list_wallets_needing_autotopup", AsyncMock(return_value=[wallet])),
        patch("utils.corporate_autotopup.get_corporate_account_by_id", AsyncMock(return_value=company)),
        patch("utils.corporate_autotopup.sum_autotopups_today", AsyncMock(return_value=0)),
        patch("utils.corporate_autotopup.get_default_payment_method", AsyncMock(return_value="pm_1")),
        patch("utils.corporate_autotopup.get_app_settings", AsyncMock(return_value=_settings_with_secret())),
        patch("stripe.PaymentIntent.create", side_effect=_capture_create),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        asyncio.run(run_autotopup_tick())
        asyncio.run(run_autotopup_tick())

    assert len(captured_keys) == 2
    expected = _expected_autotopup_key("wallet_1", today_sum=0, topup_amount=500)
    assert captured_keys[0] == expected
    assert captured_keys[0] == captured_keys[1], "Two replicas seeing the same today_sum must produce the same key"
    # Sanity: SHA-256 hex digest is 64 chars, matching the length budget
    # for Stripe's 255-char idempotency_key cap.
    assert len(captured_keys[0]) == 64


def test_autotopup_key_changes_when_today_sum_advances():
    """After the first top-up clears via webhook, today_sum advances.
    The next tick must compute a *different* key so a legitimate second
    top-up isn't blocked by the idempotency lookup."""
    wallet = _autotopup_wallet()
    company = {"id": "company_1", "stripe_customer_id": "cus_1", "status": "active"}
    captured_keys: list[str] = []

    def _capture_create(**kwargs):
        captured_keys.append(kwargs.get("idempotency_key", ""))
        return MagicMock(id="pi_topup")

    # First tick: today_sum = 0 (no top-ups yet)
    with (
        patch("utils.corporate_autotopup.list_wallets_needing_autotopup", AsyncMock(return_value=[wallet])),
        patch("utils.corporate_autotopup.get_corporate_account_by_id", AsyncMock(return_value=company)),
        patch("utils.corporate_autotopup.sum_autotopups_today", AsyncMock(return_value=0)),
        patch("utils.corporate_autotopup.get_default_payment_method", AsyncMock(return_value="pm_1")),
        patch("utils.corporate_autotopup.get_app_settings", AsyncMock(return_value=_settings_with_secret())),
        patch("stripe.PaymentIntent.create", side_effect=_capture_create),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        asyncio.run(run_autotopup_tick())

    # Second tick: today_sum = 500 (the first top-up cleared)
    with (
        patch("utils.corporate_autotopup.list_wallets_needing_autotopup", AsyncMock(return_value=[wallet])),
        patch("utils.corporate_autotopup.get_corporate_account_by_id", AsyncMock(return_value=company)),
        patch("utils.corporate_autotopup.sum_autotopups_today", AsyncMock(return_value=500)),
        patch("utils.corporate_autotopup.get_default_payment_method", AsyncMock(return_value="pm_1")),
        patch("utils.corporate_autotopup.get_app_settings", AsyncMock(return_value=_settings_with_secret())),
        patch("stripe.PaymentIntent.create", side_effect=_capture_create),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        asyncio.run(run_autotopup_tick())

    assert len(captured_keys) == 2
    assert captured_keys[0] != captured_keys[1], (
        "When today_sum advances, the idempotency key must change so the next "
        "legitimate top-up isn't deduplicated against the previous one"
    )


def test_autotopup_key_changes_per_wallet():
    """Different wallets in the same tick must produce different keys
    so Stripe doesn't collapse two distinct corporate top-ups."""
    wallets = [_autotopup_wallet(id="wallet_a"), _autotopup_wallet(id="wallet_b")]
    company = {"id": "company_1", "stripe_customer_id": "cus_1", "status": "active"}
    captured_keys: list[str] = []

    def _capture_create(**kwargs):
        captured_keys.append(kwargs.get("idempotency_key", ""))
        return MagicMock(id="pi_topup")

    with (
        patch("utils.corporate_autotopup.list_wallets_needing_autotopup", AsyncMock(return_value=wallets)),
        patch("utils.corporate_autotopup.get_corporate_account_by_id", AsyncMock(return_value=company)),
        patch("utils.corporate_autotopup.sum_autotopups_today", AsyncMock(return_value=0)),
        patch("utils.corporate_autotopup.get_default_payment_method", AsyncMock(return_value="pm_1")),
        patch("utils.corporate_autotopup.get_app_settings", AsyncMock(return_value=_settings_with_secret())),
        patch("stripe.PaymentIntent.create", side_effect=_capture_create),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        asyncio.run(run_autotopup_tick())

    assert len(captured_keys) == 2
    assert captured_keys[0] != captured_keys[1]


# ── ledger_projection.project_pending_legs ────────────────────────────


def test_projection_duplicate_batch_insert_is_treated_as_written():
    """Two replicas projecting the same event concurrently: the loser's
    whole-batch insert_many hits UNIQUE(event_id, account, side) with
    23505 — which must count as SUCCESS (legs are present), with no
    LEGS_LOST escalation. This is the projection's actual replay-safety
    mechanism; the Redis lock is only a throttle."""
    from services import ledger_service as ls

    legs = ls.build_charge_legs(2000, 1500, 220)

    async def dup_insert(_table, _rows):
        raise RuntimeError('duplicate key value violates unique constraint "financial_event_entries_uniq" (23505)')

    with (
        patch.object(ls.db_supabase, "insert_many", side_effect=dup_insert),
        patch.object(ls, "escalate") as escalate,
    ):
        ok = asyncio.run(ls.write_legs("evt_dup", legs, ride_id="ride_1", event_type="stripe_charge", check_flag=False))

    assert ok is True, "a concurrent duplicate projection must read as already-written"
    escalate.assert_not_called()


def test_projection_loop_heartbeats_even_when_lock_skipped():
    """Follower replicas must still look alive to the loop watchdog:
    heartbeat fires on the lock-not-acquired path and the tick is skipped
    (the lock is a throttle — skipping is fine, dying silently is not)."""
    from utils import ledger_projection as lp

    beats: list[str] = []

    async def _no_sleep(_secs):
        raise asyncio.CancelledError  # exit after one iteration

    with (
        patch.object(lp, "redis_set_nx", AsyncMock(return_value=False)),
        patch.object(lp, "project_pending_legs", AsyncMock()) as tick,
        patch.object(lp, "_record_heartbeat", side_effect=beats.append),
        patch.object(lp.asyncio, "sleep", side_effect=_no_sleep),
    ):
        try:
            asyncio.run(lp.ledger_projection_loop())
        except asyncio.CancelledError:
            pass

    tick.assert_not_awaited()
    assert beats == ["ledger_projection (15min)"], "heartbeat must fire on the lock-skip path"


def test_projection_loop_reacquires_its_own_lock_on_the_next_wake():
    """REGRESSION: the throttle lock must expire before the pod's next wake.

    With TTL = 1.5x interval against a 1x interval sleep, the pod that ran the
    last tick woke to find its OWN key still alive, failed SET NX, and slept
    another full interval — so a loop documented (and registered) as
    "15min" actually ticked every ~30 minutes, halving backfill throughput.

    Simulated against a virtual clock with real SET NX EX semantics, and with
    the jitter pinned to its most adverse value (the SHORTEST sleep), which is
    the case the TTL has to survive.
    """
    from utils import ledger_projection as lp

    clock = {"t": 0.0}
    expiries: dict[str, float] = {}
    wakes = {"n": 0}

    async def fake_set_nx(key, _value, ttl):
        exp = expiries.get(key)
        if exp is not None and exp > clock["t"]:
            return False
        expiries[key] = clock["t"] + ttl
        return True

    async def fake_sleep(secs):
        clock["t"] += secs
        wakes["n"] += 1
        if wakes["n"] >= 2:
            raise asyncio.CancelledError

    with (
        patch.object(lp, "redis_set_nx", side_effect=fake_set_nx),
        patch.object(lp, "project_pending_legs", AsyncMock()) as tick,
        patch.object(lp, "_record_heartbeat"),
        patch.object(lp.asyncio, "sleep", side_effect=fake_sleep),
        # uniform(-delta, +delta) -> -delta: the shortest sleep the loop can take.
        patch.object(lp.random, "uniform", side_effect=lambda lo, _hi: lo),
    ):
        try:
            asyncio.run(lp.ledger_projection_loop())
        except asyncio.CancelledError:
            pass

    assert tick.await_count == 2, (
        "the single replica must tick once per interval; a TTL longer than the "
        "minimum sleep makes it skip its own next wake and halves the cadence"
    )
