"""Capacity watchdog: the tripwire that buys time to upgrade a Supabase tier.

Supabase compute does not autoscale, so the alert has to fire early enough for
a human to act. These tests pin the behaviour that makes it trustworthy:
sustained signals rather than spikes, no alert storm during a long incident, and
never raising out of the loop.

See backend/utils/capacity_watchdog.py and docs/runbooks/capacity-scaling.md.
"""

from __future__ import annotations

import pytest

from utils import capacity_watchdog as cw

WEBHOOK = "https://hooks.example.test/spinr"


@pytest.fixture(autouse=True)
def _reset_watchdog_state():
    """The module keeps per-process signal history; isolate every test."""
    cw._consecutive.clear()
    cw._last_alerted.clear()
    cw._last_counters.clear()
    yield
    cw._consecutive.clear()
    cw._last_alerted.clear()
    cw._last_counters.clear()


class _AlertRecorder:
    """Captures alerts instead of posting them."""

    def __init__(self):
        self.posted = []

    async def __call__(self, signal, text, webhook_url):
        self.posted.append({"signal": signal, "text": text, "webhook_url": webhook_url})

    @property
    def signals(self):
        return [a["signal"] for a in self.posted]


@pytest.fixture
def alerts(monkeypatch):
    recorder = _AlertRecorder()
    monkeypatch.setattr(cw, "_post_alert", recorder)
    return recorder


def _patch_stats(monkeypatch, *, queue_depth=0, breaker_state="closed", threads=8, max_workers=64):
    monkeypatch.setattr(
        cw,
        "get_db_pool_stats",
        lambda: {
            "queue_depth": queue_depth,
            "breaker_state": breaker_state,
            "threads": threads,
            "max_workers": max_workers,
        },
    )


def _patch_metrics(monkeypatch, *, rejected=None, violations=0.0):
    """rejected: dict of reason -> count. violations: total across paths."""
    rejected = rejected or {}
    counters = {}
    if rejected:
        counters["spinr_db_calls_rejected_total"] = {
            ((("reason", reason),)): count for reason, count in rejected.items()
        }
    if violations:
        counters["spinr_rate_limit_violation_total"] = {
            ((("path", "/rides/estimate"),)): violations / 2,
            ((("path", "/rides/active"),)): violations / 2,
        }
    monkeypatch.setattr(cw, "_metrics_snapshot", lambda: {"counters": counters, "gauges": {}, "histograms": {}})


# --------------------------------------------------------------------------
# Signal 1 — sustained DB pool saturation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_saturation_alerts_only_after_sustained_ticks(monkeypatch, alerts):
    """A single spike is the queue doing its job. Only a sustained one is a
    problem worth waking someone for."""
    _patch_stats(monkeypatch, queue_depth=cw.QUEUE_DEPTH_THRESHOLD + 10)
    _patch_metrics(monkeypatch)

    for _ in range(cw.SUSTAIN_TICKS - 1):
        await cw._tick(WEBHOOK)
    assert alerts.signals == [], "alerted before the signal was sustained"

    await cw._tick(WEBHOOK)
    assert alerts.signals == ["db_pool_saturation"]


@pytest.mark.asyncio
async def test_pool_saturation_counter_resets_on_a_healthy_tick(monkeypatch, alerts):
    """Intermittent spikes must not accumulate into a false alert."""
    _patch_metrics(monkeypatch)

    for _ in range(cw.SUSTAIN_TICKS - 1):
        _patch_stats(monkeypatch, queue_depth=cw.QUEUE_DEPTH_THRESHOLD + 10)
        await cw._tick(WEBHOOK)

    _patch_stats(monkeypatch, queue_depth=0)  # recovered
    await cw._tick(WEBHOOK)

    _patch_stats(monkeypatch, queue_depth=cw.QUEUE_DEPTH_THRESHOLD + 10)
    await cw._tick(WEBHOOK)

    assert alerts.signals == [], "spike history leaked across a healthy tick"


@pytest.mark.asyncio
async def test_queue_depth_at_threshold_does_not_alert(monkeypatch, alerts):
    """Threshold is exclusive — exactly 50 is the documented working point."""
    _patch_stats(monkeypatch, queue_depth=cw.QUEUE_DEPTH_THRESHOLD)
    _patch_metrics(monkeypatch)
    for _ in range(cw.SUSTAIN_TICKS + 1):
        await cw._tick(WEBHOOK)
    assert alerts.signals == []


# --------------------------------------------------------------------------
# Signal 2 — DB call rejections
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_open_alerts_immediately_without_waiting_to_sustain(monkeypatch, alerts):
    """The breaker only opens after real failures, so there is nothing to wait
    and see about — this is the most urgent signal the watchdog has."""
    _patch_stats(monkeypatch, breaker_state="open")
    _patch_metrics(monkeypatch, rejected={"circuit_open": 0})
    await cw._tick(WEBHOOK)  # establish baseline
    assert alerts.signals == []

    _patch_metrics(monkeypatch, rejected={"circuit_open": 12})
    await cw._tick(WEBHOOK)
    assert alerts.signals == ["db_circuit_open"]
    assert "12" in alerts.posted[0]["text"]


@pytest.mark.asyncio
async def test_first_tick_does_not_alert_on_accumulated_history(monkeypatch, alerts):
    """A replica up for hours has a large counter total. The first observation
    establishes a baseline; alerting on it would fire on ancient history."""
    _patch_stats(monkeypatch)
    _patch_metrics(monkeypatch, rejected={"circuit_open": 9999})
    await cw._tick(WEBHOOK)
    assert alerts.signals == []


@pytest.mark.asyncio
async def test_deadline_rejections_alert_less_urgently_than_circuit_open(monkeypatch, alerts):
    _patch_stats(monkeypatch)
    _patch_metrics(monkeypatch, rejected={"deadline_timeout": 0})
    await cw._tick(WEBHOOK)

    _patch_metrics(monkeypatch, rejected={"deadline_timeout": 5})
    await cw._tick(WEBHOOK)
    assert alerts.signals == ["db_calls_rejected"]


@pytest.mark.asyncio
async def test_steady_counter_produces_no_alert(monkeypatch, alerts):
    """Only an *increase* matters — a counter sitting still means it stopped."""
    _patch_stats(monkeypatch)
    _patch_metrics(monkeypatch, rejected={"circuit_open": 7})
    await cw._tick(WEBHOOK)
    await cw._tick(WEBHOOK)
    await cw._tick(WEBHOOK)
    assert alerts.signals == []


# --------------------------------------------------------------------------
# Signal 3 — rate-limit pressure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_pressure_alerts_when_sustained(monkeypatch, alerts):
    _patch_stats(monkeypatch)
    per_tick = (cw.RATE_LIMIT_VIOLATIONS_PER_MIN_THRESHOLD + 60) * (cw.INTERVAL_SECONDS / 60.0)

    total = 0.0
    _patch_metrics(monkeypatch, violations=total)
    await cw._tick(WEBHOOK)  # baseline

    for _ in range(cw.SUSTAIN_TICKS):
        total += per_tick
        _patch_metrics(monkeypatch, violations=total)
        await cw._tick(WEBHOOK)

    assert alerts.signals == ["rate_limit_pressure"]


@pytest.mark.asyncio
async def test_modest_rate_limit_activity_does_not_alert(monkeypatch, alerts):
    """Some 429s are normal. Only volume indicates capacity trouble."""
    _patch_stats(monkeypatch)
    total = 0.0
    for _ in range(cw.SUSTAIN_TICKS + 2):
        _patch_metrics(monkeypatch, violations=total)
        await cw._tick(WEBHOOK)
        total += 2
    assert alerts.signals == []


# --------------------------------------------------------------------------
# Cooldown, resilience, config
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_prevents_an_alert_storm_during_a_long_incident(monkeypatch):
    """Real _post_alert this time — a 30-minute incident at a 60 s tick would
    otherwise post ~30 messages per replica."""
    posts = []

    class _FakeResponse:
        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            posts.append(json)
            return _FakeResponse()

    monkeypatch.setattr(cw.httpx, "AsyncClient", _FakeClient)
    _patch_stats(monkeypatch, queue_depth=cw.QUEUE_DEPTH_THRESHOLD + 10)
    _patch_metrics(monkeypatch)

    for _ in range(cw.SUSTAIN_TICKS + 20):
        await cw._tick(WEBHOOK)

    assert len(posts) == 1, f"cooldown failed to throttle: {len(posts)} posts"


@pytest.mark.asyncio
async def test_alert_names_the_replica_and_the_runbook(monkeypatch):
    posts = []

    class _FakeResponse:
        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            posts.append(json)
            return _FakeResponse()

    monkeypatch.setattr(cw.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setenv("FLY_MACHINE_ID", "d891ee3f4a2b18")

    await cw._post_alert("db_pool_saturation", "something is wrong", WEBHOOK)

    assert len(posts) == 1
    text = posts[0]["text"]
    assert "d891ee3f4a2b18" in text, "alert must say which replica it came from"
    assert "capacity-scaling.md" in text, "alert must point at the runbook"


@pytest.mark.asyncio
async def test_webhook_failure_is_logged_not_raised(monkeypatch):
    """A watchdog that cannot alert must not also crash the loop — that would
    turn a degraded database into a dead process."""

    class _ExplodingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            raise RuntimeError("webhook unreachable")

    monkeypatch.setattr(cw.httpx, "AsyncClient", _ExplodingClient)
    await cw._post_alert("db_pool_saturation", "text", WEBHOOK)  # must not raise

    # Cooldown must NOT be marked on a failed post, or one flaky webhook call
    # would silence the signal for the next 30 minutes.
    assert "db_pool_saturation" not in cw._last_alerted


@pytest.mark.asyncio
async def test_tick_without_webhook_still_refreshes_metrics(monkeypatch, alerts):
    """No ALERT_WEBHOOK_URL in dev — the gauge refresh must still happen so
    /metrics scrapes are current."""
    called = {"n": 0}

    def _stats():
        called["n"] += 1
        return {"queue_depth": 0, "breaker_state": "closed", "threads": 1, "max_workers": 64}

    monkeypatch.setattr(cw, "get_db_pool_stats", _stats)
    _patch_metrics(monkeypatch)

    await cw._tick(None)

    assert called["n"] == 1
    assert alerts.signals == []


# --------------------------------------------------------------------------
# Email channel
# --------------------------------------------------------------------------


class _EmailRecorder:
    def __init__(self, result=True):
        self.sent = []
        self._result = result

    async def __call__(self, **kwargs):
        self.sent.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def emails(monkeypatch):
    recorder = _EmailRecorder()
    import utils.email_provider as ep

    monkeypatch.setattr(ep, "send_transactional_email", recorder)
    return recorder


def _patch_recipients(monkeypatch, value):
    monkeypatch.setattr(cw, "_alert_recipients", lambda: value)


@pytest.mark.asyncio
async def test_email_only_config_still_alerts(monkeypatch, emails):
    """Email must work with NO webhook configured — the guard used to be
    `if webhook_url:` around every alert, which would have made an email-only
    setup completely silent."""
    _patch_recipients(monkeypatch, ["ops@spinr.ca"])
    _patch_stats(monkeypatch, queue_depth=cw.QUEUE_DEPTH_THRESHOLD + 10)
    _patch_metrics(monkeypatch)

    for _ in range(cw.SUSTAIN_TICKS):
        await cw._tick(None)  # no webhook at all

    assert len(emails.sent) == 1
    assert emails.sent[0]["to"] == "ops@spinr.ca"
    assert "db_pool_saturation" in emails.sent[0]["subject"]


@pytest.mark.asyncio
async def test_email_body_carries_the_numbers_replica_and_runbook(monkeypatch, emails):
    _patch_recipients(monkeypatch, ["ops@spinr.ca"])
    monkeypatch.setenv("FLY_MACHINE_ID", "d891ee3f4a2b18")

    await cw._post_alert("db_pool_saturation", "Queue depth 73", None)

    body = emails.sent[0]["text"]
    assert "Queue depth 73" in body
    assert "d891ee3f4a2b18" in body
    assert "capacity-scaling.md" in body


@pytest.mark.asyncio
async def test_every_recipient_is_emailed(monkeypatch, emails):
    _patch_recipients(monkeypatch, ["ops@spinr.ca", "oncall@spinr.ca"])
    await cw._post_alert("db_circuit_open", "text", None)
    assert [e["to"] for e in emails.sent] == ["ops@spinr.ca", "oncall@spinr.ca"]


@pytest.mark.asyncio
async def test_one_bad_recipient_does_not_block_the_others(monkeypatch):
    """During an incident, reaching 1 of 2 people beats an all-or-nothing send."""
    sent = []

    async def _flaky(**kwargs):
        if kwargs["to"] == "broken@spinr.ca":
            raise RuntimeError("SES rejected")
        sent.append(kwargs["to"])
        return True

    import utils.email_provider as ep

    monkeypatch.setattr(ep, "send_transactional_email", _flaky)
    _patch_recipients(monkeypatch, ["broken@spinr.ca", "ops@spinr.ca"])

    await cw._post_alert("db_circuit_open", "text", None)

    assert sent == ["ops@spinr.ca"]
    # One channel delivered, so the cooldown IS stamped.
    assert "db_circuit_open" in cw._last_alerted


@pytest.mark.asyncio
async def test_webhook_failure_does_not_cost_the_email(monkeypatch, emails):
    """The whole point of independent channels: a dead Slack workspace must
    still leave you with the email."""

    class _ExplodingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            raise RuntimeError("slack down")

    monkeypatch.setattr(cw.httpx, "AsyncClient", _ExplodingClient)
    _patch_recipients(monkeypatch, ["ops@spinr.ca"])

    await cw._post_alert("db_pool_saturation", "text", WEBHOOK)

    assert len(emails.sent) == 1
    assert "db_pool_saturation" in cw._last_alerted  # email delivered → cooldown stamped


@pytest.mark.asyncio
async def test_total_delivery_failure_does_not_consume_the_cooldown(monkeypatch):
    """If NOTHING got through, retry next tick rather than going quiet for 30 min."""

    class _ExplodingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            raise RuntimeError("slack down")

    async def _dead_email(**kwargs):
        return False

    import utils.email_provider as ep

    monkeypatch.setattr(cw.httpx, "AsyncClient", _ExplodingClient)
    monkeypatch.setattr(ep, "send_transactional_email", _dead_email)
    _patch_recipients(monkeypatch, ["ops@spinr.ca"])

    await cw._post_alert("db_pool_saturation", "text", WEBHOOK)

    assert "db_pool_saturation" not in cw._last_alerted


@pytest.mark.asyncio
async def test_no_channel_configured_sends_nothing(monkeypatch, emails):
    _patch_recipients(monkeypatch, [])
    await cw._post_alert("db_pool_saturation", "text", None)
    assert emails.sent == []
    assert cw._last_alerted == {}


@pytest.mark.asyncio
async def test_cooldown_is_shared_across_channels(monkeypatch, emails):
    """One cooldown per signal, not one per channel — otherwise email and Slack
    would drift out of step and double the effective alert volume."""
    _patch_recipients(monkeypatch, ["ops@spinr.ca"])
    await cw._post_alert("db_pool_saturation", "first", None)
    await cw._post_alert("db_pool_saturation", "second", None)
    assert len(emails.sent) == 1


def test_alert_recipients_parses_comma_separated(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", "a@spinr.ca, b@spinr.ca ,, c@spinr.ca", raising=False)
    assert cw._alert_recipients() == ["a@spinr.ca", "b@spinr.ca", "c@spinr.ca"]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_alert_recipients_empty_when_unset(monkeypatch, value):
    from core.config import settings

    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", value, raising=False)
    assert cw._alert_recipients() == []


def test_counter_total_sums_across_label_sets():
    snap = {
        "counters": {
            "spinr_db_calls_rejected_total": {
                ((("reason", "circuit_open"),)): 3.0,
                ((("reason", "deadline_timeout"),)): 4.0,
            }
        }
    }
    assert cw._counter_total(snap, "spinr_db_calls_rejected_total") == 7.0
    assert cw._counter_total(snap, "spinr_db_calls_rejected_total", "reason", "circuit_open") == 3.0
    assert cw._counter_total(snap, "nonexistent_metric") == 0.0


def test_delta_ignores_a_counter_reset():
    """A process restart resets counters; a negative delta is not real traffic."""
    cw._last_counters.clear()
    assert cw._delta("x", 100.0) is None  # baseline
    assert cw._delta("x", 150.0) == 50.0
    assert cw._delta("x", 10.0) is None  # reset, not a -140 delta


# --------------------------------------------------------------------------
# The pool-stats accessor this loop depends on
# --------------------------------------------------------------------------


def test_get_db_pool_stats_reports_live_executor_state():
    """The reason this accessor exists: the gauges inside run_sync only update
    on the success path, so a saturated-then-idle pool read stale. Sampling the
    executor directly must not depend on a query having just run."""
    from repositories._base import get_db_pool_stats

    stats = get_db_pool_stats()

    assert set(stats) == {"queue_depth", "threads", "max_workers", "breaker_state"}
    assert isinstance(stats["queue_depth"], int)
    assert stats["max_workers"] >= 1
    assert stats["breaker_state"] in {"closed", "open", "half_open", "unknown"}


def test_get_db_pool_stats_refreshes_the_queue_depth_gauge():
    from repositories._base import get_db_pool_stats
    from utils.metrics import snapshot

    get_db_pool_stats()
    gauges = snapshot()["gauges"]
    assert "spinr_db_thread_pool_queue_depth" in gauges
