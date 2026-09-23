"""Auto-top-up scheduled tick for corporate wallets."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_loop_monitor_state(monkeypatch):
    from utils import loop_monitor

    monkeypatch.setattr(loop_monitor, "_heartbeats", {})
    monkeypatch.setattr(loop_monitor, "_failures", {})


@pytest.mark.asyncio
async def test_triggers_charge_when_balance_below_threshold():
    wallets = [
        {
            "id": "w1",
            "company_id": "c1",
            "balance": "30.00",
            "auto_topup_enabled": True,
            "auto_topup_threshold": "100.00",
            "auto_topup_amount": "500.00",
            "auto_topup_daily_cap": "5000.00",
        }
    ]
    company = {"id": "c1", "stripe_customer_id": "cus_X", "status": "active"}
    intent = MagicMock(id="pi_auto")

    with (
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(return_value=wallets),
        ),
        patch(
            "utils.corporate_autotopup.get_corporate_account_by_id",
            AsyncMock(return_value=company),
        ),
        patch(
            "utils.corporate_autotopup.sum_autotopups_today",
            AsyncMock(return_value=0),
        ),
        patch(
            "utils.corporate_autotopup.get_default_payment_method",
            AsyncMock(return_value="pm_1"),
        ),
        patch("stripe.PaymentIntent.create", return_value=intent) as m_pi,
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_pi.assert_called_once()
    kwargs = m_pi.call_args.kwargs
    assert kwargs["amount"] == 50000  # 500 CAD
    assert kwargs["customer"] == "cus_X"
    assert kwargs["payment_method"] == "pm_1"
    assert kwargs["off_session"] is True
    assert kwargs["confirm"] is True
    assert kwargs["metadata"]["scope"] == "corporate_topup"
    assert kwargs["metadata"]["wallet_id"] == "w1"
    assert kwargs["metadata"]["initiated_by"] == "autotopup"


@pytest.mark.asyncio
async def test_skips_when_daily_cap_reached():
    wallets = [
        {
            "id": "w1",
            "company_id": "c1",
            "balance": "30.00",
            "auto_topup_enabled": True,
            "auto_topup_threshold": "100.00",
            "auto_topup_amount": "500.00",
            "auto_topup_daily_cap": "500.00",
        }
    ]
    with (
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(return_value=wallets),
        ),
        patch(
            "utils.corporate_autotopup.get_corporate_account_by_id",
            AsyncMock(return_value={"status": "active", "stripe_customer_id": "cus_X"}),
        ),
        patch(
            "utils.corporate_autotopup.sum_autotopups_today",
            AsyncMock(return_value=500),
        ),
        patch("stripe.PaymentIntent.create") as m_pi,
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_pi.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_company_not_active():
    wallets = [
        {
            "id": "w1",
            "company_id": "c1",
            "balance": "30.00",
            "auto_topup_enabled": True,
            "auto_topup_threshold": "100.00",
            "auto_topup_amount": "500.00",
            "auto_topup_daily_cap": "5000.00",
        }
    ]
    with (
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(return_value=wallets),
        ),
        patch(
            "utils.corporate_autotopup.get_corporate_account_by_id",
            AsyncMock(return_value={"status": "suspended", "stripe_customer_id": "cus_X"}),
        ),
        patch("stripe.PaymentIntent.create") as m_pi,
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_pi.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_replicas_use_same_stripe_idempotency_key():
    """Two replica instances processing the same wallet simultaneously must
    produce the same Stripe idempotency_key so Stripe deduplicates the charge.

    The key is derived from (wallet_id, date, today_sum_cents, topup_amount_cents).
    Both replicas see the same today_sum (no top-up has cleared between reads),
    so the key is identical and Stripe fires only one charge.
    """
    import asyncio

    wallets = [
        {
            "id": "w1",
            "company_id": "c1",
            "balance": "30.00",
            "auto_topup_enabled": True,
            "auto_topup_threshold": "100.00",
            "auto_topup_amount": "500.00",
            "auto_topup_daily_cap": "5000.00",
        }
    ]
    company = {"id": "c1", "stripe_customer_id": "cus_X", "status": "active"}
    intent = MagicMock(id="pi_auto")
    captured_keys: list[str] = []

    def capture_idempotency(**kwargs):
        captured_keys.append(kwargs.get("idempotency_key", ""))
        return intent

    with (
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(return_value=wallets),
        ),
        patch(
            "utils.corporate_autotopup.get_corporate_account_by_id",
            AsyncMock(return_value=company),
        ),
        patch(
            "utils.corporate_autotopup.sum_autotopups_today",
            AsyncMock(return_value=0),
        ),
        patch(
            "utils.corporate_autotopup.get_default_payment_method",
            AsyncMock(return_value="pm_1"),
        ),
        patch("stripe.PaymentIntent.create", side_effect=capture_idempotency),
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await asyncio.gather(run_autotopup_tick(), run_autotopup_tick())

    assert len(captured_keys) == 2, "Both replicas called Stripe"
    assert captured_keys[0] == captured_keys[1], "Idempotency keys must match so Stripe deduplicates the charge"


@pytest.mark.asyncio
async def test_no_op_when_stripe_secret_missing():
    with (
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": ""}),
        ),
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(),
        ) as m_list,
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_list.assert_not_awaited()


# ── E5 kill switch: corporate_billing_enabled ──────────────────────────────


@pytest.mark.asyncio
async def test_no_op_when_corporate_billing_disabled():
    with (
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test", "corporate_billing_enabled": False}),
        ),
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(),
        ) as m_list,
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_proceeds_when_corporate_billing_key_missing():
    """A settings dict with no corporate_billing_enabled key (legacy row)
    must still proceed -- the flag defaults to enabled."""
    with (
        patch(
            "utils.corporate_autotopup.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
        ),
        patch(
            "utils.corporate_autotopup.list_wallets_needing_autotopup",
            AsyncMock(return_value=[]),
        ) as m_list,
    ):
        from utils.corporate_autotopup import run_autotopup_tick

        await run_autotopup_tick()

    m_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_loop_logs_exc_info_on_tick_failure():
    """Corporate + admin portal review, gap #43: corporate_autotopup_loop's
    top-level error handler logged only the exception's string form,
    discarding the traceback — every other error-logging call in this
    module (e.g. run_autotopup_tick's Stripe-error handler) already
    passes exc_info=True. A DB error or a bug in tick logic outside
    stripe.StripeError landed here with no way to find where it
    actually happened."""
    import asyncio

    from utils import corporate_autotopup

    with (
        patch.object(
            corporate_autotopup,
            "run_autotopup_tick",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch.object(corporate_autotopup, "redis_set_nx", AsyncMock(return_value=True), create=True),
        patch("asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError())),
        patch.object(corporate_autotopup.logger, "error") as mock_error,
    ):
        with pytest.raises(asyncio.CancelledError):
            await corporate_autotopup.corporate_autotopup_loop()

    mock_error.assert_called_once()
    assert mock_error.call_args.kwargs.get("exc_info") is True


@pytest.mark.asyncio
async def test_lock_unavailable_skips_topup_logs_only_error_class_and_records_metrics(monkeypatch):
    import asyncio

    from utils import corporate_autotopup

    # Exercise the real strict-client alias: absent REDIS_URL must not grant a
    # process-local lease, and its fixed error text is safe to log.
    monkeypatch.delenv("REDIS_URL", raising=False)
    tick = AsyncMock()
    monkeypatch.setattr(corporate_autotopup, "run_autotopup_tick", tick)
    sleep = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(corporate_autotopup.asyncio, "sleep", sleep)
    error = MagicMock()
    monkeypatch.setattr(corporate_autotopup.logger, "error", error)
    inc = MagicMock()
    monkeypatch.setattr(corporate_autotopup, "_metric_inc", inc)
    gauge = MagicMock()
    monkeypatch.setattr(corporate_autotopup, "_metric_gauge", gauge)
    heartbeat = MagicMock()
    monkeypatch.setattr(corporate_autotopup, "_record_heartbeat", heartbeat)
    monkeypatch.setattr(corporate_autotopup.random, "random", lambda: 0.5)

    with pytest.raises(asyncio.CancelledError):
        await corporate_autotopup.corporate_autotopup_loop()

    tick.assert_not_awaited()
    error.assert_called_once()
    assert error.call_args.args[1] == "RuntimeError"
    assert "Redis unavailable for distributed lock" not in str(error.call_args)
    inc.assert_any_call("spinr_loop_lock_unavailable_total", {"loop": "corporate_autotopup"})
    inc.assert_any_call("spinr_bgloop_errors_total", {"loop": "corporate_autotopup"})
    gauge.assert_called_once()
    heartbeat.assert_not_called()
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_contention_skips_tick_but_keeps_heartbeat_and_normal_interval(monkeypatch):
    import asyncio

    from utils import corporate_autotopup

    acquire = AsyncMock(return_value=False)
    monkeypatch.setattr(corporate_autotopup, "redis_set_nx", acquire, raising=False)
    tick = AsyncMock()
    monkeypatch.setattr(corporate_autotopup, "run_autotopup_tick", tick)
    sleep = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(corporate_autotopup.asyncio, "sleep", sleep)
    inc = MagicMock()
    monkeypatch.setattr(corporate_autotopup, "_metric_inc", inc)
    heartbeat = MagicMock()
    monkeypatch.setattr(corporate_autotopup, "_record_heartbeat", heartbeat)
    monkeypatch.setattr(corporate_autotopup.random, "random", lambda: 0.5)

    with pytest.raises(asyncio.CancelledError):
        await corporate_autotopup.corporate_autotopup_loop()

    acquire.assert_awaited_once()
    tick.assert_not_awaited()
    inc.assert_not_called()
    heartbeat.assert_called_once_with("corporate_autotopup (10min)")
    sleep.assert_awaited_once_with(600)


@pytest.mark.asyncio
async def test_lock_exception_marks_unhealthy_then_contention_heartbeat_recovers(monkeypatch):
    import asyncio

    from utils import corporate_autotopup, loop_monitor

    name = "corporate_autotopup (10min)"
    loop_monitor.record_heartbeat(name)
    monkeypatch.setattr(
        corporate_autotopup,
        "redis_set_nx",
        AsyncMock(side_effect=[ConnectionError("redis down"), False]),
        raising=False,
    )
    tick = AsyncMock()
    monkeypatch.setattr(corporate_autotopup, "run_autotopup_tick", tick)
    sleeps = 0

    async def sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            assert loop_monitor.get_loop_status([name])["loops"][name]["status"] == "unhealthy"
        else:
            raise asyncio.CancelledError()

    monkeypatch.setattr(corporate_autotopup.asyncio, "sleep", sleep)
    monkeypatch.setattr(corporate_autotopup.random, "random", lambda: 0.5)

    with pytest.raises(asyncio.CancelledError):
        await corporate_autotopup.corporate_autotopup_loop()

    tick.assert_not_awaited()
    assert loop_monitor.get_loop_status([name])["loops"][name]["status"] == "ok"


@pytest.mark.asyncio
async def test_reacquires_after_contention_and_runs_next_tick(monkeypatch):
    import asyncio

    from utils import corporate_autotopup

    acquire = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(corporate_autotopup, "redis_set_nx", acquire, raising=False)
    tick = AsyncMock()
    monkeypatch.setattr(corporate_autotopup, "run_autotopup_tick", tick)
    sleeps = []

    async def _sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(corporate_autotopup.asyncio, "sleep", _sleep)
    heartbeat = MagicMock()
    monkeypatch.setattr(corporate_autotopup, "_record_heartbeat", heartbeat)
    monkeypatch.setattr(corporate_autotopup.random, "random", lambda: 0.5)

    with pytest.raises(asyncio.CancelledError):
        await corporate_autotopup.corporate_autotopup_loop()

    assert [call.args[0] for call in acquire.await_args_list] == [
        "spinr:corporate_autotopup:lock",
        "spinr:corporate_autotopup:lock",
    ]
    assert acquire.await_args_list[0].args[2] == 510
    tick.assert_awaited_once()
    assert sleeps == [600, 600]
    assert heartbeat.call_count == 2


@pytest.mark.asyncio
async def test_lock_expires_before_earliest_jittered_tick(monkeypatch):
    import asyncio

    from utils import corporate_autotopup

    now = 0
    expiry = None
    acquisitions = 0

    async def _acquire(key, owner, ttl):
        nonlocal expiry, acquisitions
        assert key == "spinr:corporate_autotopup:lock"
        assert owner == "test-pod"
        assert ttl == 510
        acquisitions += 1
        if expiry is not None and now < expiry:
            return False
        expiry = now + ttl
        return True

    monkeypatch.setattr(corporate_autotopup, "redis_set_nx", _acquire, raising=False)
    tick = AsyncMock()
    monkeypatch.setattr(corporate_autotopup, "run_autotopup_tick", tick)
    monkeypatch.setattr(corporate_autotopup, "loop_pod_id", lambda: "test-pod", raising=False)
    monkeypatch.setattr(corporate_autotopup.random, "random", lambda: 0.0)
    sleeps = 0

    async def _sleep(seconds):
        nonlocal now, sleeps
        assert seconds == 540
        now += seconds
        sleeps += 1
        if sleeps == 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(corporate_autotopup.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await corporate_autotopup.corporate_autotopup_loop()

    assert acquisitions == 2
    assert tick.await_count == 2
