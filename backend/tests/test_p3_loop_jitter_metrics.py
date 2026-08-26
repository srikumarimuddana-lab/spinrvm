"""Regression tests for B-P3-2: ±10% jitter and per-loop metrics.

Verifies:
  - The jitter formula always produces a sleep in [0.9×, 1.1×) of the interval
  - spinr_bgloop_duration_ms gauge is emitted every tick
  - spinr_bgloop_errors_total counter is emitted only when the tick fails
  - presence_sweeper_loop emits the initial random jitter sleep AND the
    loop-body ±10% jitter sleep (two distinct sleep calls per cycle)

Tests use retention_purge_loop as the primary subject — it has no heavy
module-level imports (db is lazy inside _tick), so no external stubs are
needed. The jitter formula test is module-agnostic.
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps required by presence_sweeper (imported below)
# ---------------------------------------------------------------------------
_STUBS = [
    "supabase",
    "gotrue",
    "postgrest",
    "realtime",
    "stripe",
    "redis",
    "redis.asyncio",
    "loguru",
    "slowapi",
    "slowapi.errors",
    "slowapi.util",
]
for _m in _STUBS:
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()

sys.modules["loguru"].logger = MagicMock()

# ---------------------------------------------------------------------------
# Imports (after stubs)
# ---------------------------------------------------------------------------
from backend.utils.retention_purge import (  # noqa: E402
    INTERVAL_SECONDS,
    retention_purge_loop,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_one_iteration(loop_coro_fn, extra_patches: dict | None = None):
    """
    Run one full iteration of a background loop (all patches applied).

    Returns (sleep_calls, gauge_calls, inc_calls).

    For loops with an initial jitter sleep BEFORE the while loop
    (presence_sweeper_loop), sleep_calls[0] is that initial sleep and
    sleep_calls[1] is the first loop-body sleep.  For loops that go
    straight into the while True, sleep_calls[0] is the loop-body sleep.
    """
    sleep_calls: list[float] = []
    gauge_calls: list[tuple] = []
    inc_calls: list[tuple] = []

    async def run():
        call_count = 0

        async def capture_sleep(seconds):
            nonlocal call_count
            call_count += 1
            sleep_calls.append(seconds)
            # Allow the first sleep call to pass for loops that have an
            # initial jitter before entering the while loop (e.g. presence_sweeper).
            # Raise CancelledError on the *second* sleep — which is always the
            # loop-body sleep — so we capture exactly one full iteration.
            if call_count >= 2:
                raise asyncio.CancelledError()
            # For loops with no initial sleep, the first call IS the loop-body
            # sleep — raise immediately.
            # Heuristic: if there are already gauge calls, we're past the tick.
            if gauge_calls:
                raise asyncio.CancelledError()

        def fake_gauge(name, value, labels):
            gauge_calls.append((name, value, labels))

        def fake_inc(name, labels=None):
            inc_calls.append((name, labels))

        patches = {
            "asyncio.sleep": capture_sleep,
            **(extra_patches or {}),
        }
        ctx_patches = [patch(k, v) for k, v in patches.items()]
        for p in ctx_patches:
            p.start()
        try:
            await loop_coro_fn()
        except asyncio.CancelledError:
            pass
        finally:
            for p in ctx_patches:
                p.stop()

    asyncio.get_event_loop().run_until_complete(run())
    return sleep_calls, gauge_calls, inc_calls


# ---------------------------------------------------------------------------
# 1. Pure-math jitter formula
# ---------------------------------------------------------------------------


def test_jitter_formula_always_in_range():
    """interval * (0.9 + random.random() * 0.2) is always in [0.9×, 1.1×)."""
    import random

    interval = 86400  # largest real interval (retention_purge)
    for _ in range(10_000):
        value = interval * (0.9 + random.random() * 0.2)
        assert interval * 0.9 <= value < interval * 1.1, (
            f"Jitter out of bounds: {value} not in [{interval * 0.9}, {interval * 1.1})"
        )


# ---------------------------------------------------------------------------
# 2. retention_purge_loop — jitter + metrics
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_retention_purge_loop_sleep_has_jitter():
    """Loop-body sleep is in [0.9×, 1.1×) of INTERVAL_SECONDS."""
    sleep_calls: list[float] = []

    async def capture_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    with patch("asyncio.sleep", capture_sleep), patch("backend.utils.retention_purge._tick", AsyncMock()):
        try:
            await retention_purge_loop()
        except asyncio.CancelledError:
            pass

    assert len(sleep_calls) == 1, f"Expected 1 sleep call, got {len(sleep_calls)}"
    s = sleep_calls[0]
    assert INTERVAL_SECONDS * 0.9 <= s <= INTERVAL_SECONDS * 1.1, f"Sleep {s:.1f} outside ±10% of {INTERVAL_SECONDS}"


@pytest.mark.anyio
async def test_retention_purge_loop_emits_duration_gauge():
    """spinr_bgloop_duration_ms gauge is emitted after every tick."""
    sleep_calls: list[float] = []
    gauge_calls: list[tuple] = []

    async def capture_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    def fake_gauge(name, value, labels):
        gauge_calls.append((name, value, labels))

    with (
        patch("asyncio.sleep", capture_sleep),
        patch("backend.utils.retention_purge._tick", AsyncMock()),
        patch("backend.utils.retention_purge._metric_gauge", fake_gauge),
    ):
        try:
            await retention_purge_loop()
        except asyncio.CancelledError:
            pass

    assert len(gauge_calls) == 1
    name, value, labels = gauge_calls[0]
    assert name == "spinr_bgloop_duration_ms"
    assert labels == {"loop": "retention_purge"}
    assert value >= 0, "Duration must be non-negative"


@pytest.mark.anyio
async def test_retention_purge_loop_emits_error_counter_on_failure():
    """spinr_bgloop_errors_total is incremented when the tick raises."""
    inc_calls: list[tuple] = []

    async def failing_tick():
        raise RuntimeError("simulated DB failure")

    async def capture_sleep(seconds):
        raise asyncio.CancelledError()

    def fake_inc(name, labels=None):
        inc_calls.append((name, labels))

    with (
        patch("asyncio.sleep", capture_sleep),
        patch("backend.utils.retention_purge._tick", failing_tick),
        patch("backend.utils.retention_purge._metric_gauge", MagicMock()),
        patch("backend.utils.retention_purge._metric_inc", fake_inc),
    ):
        try:
            await retention_purge_loop()
        except asyncio.CancelledError:
            pass

    assert len(inc_calls) == 1
    name, labels = inc_calls[0]
    assert name == "spinr_bgloop_errors_total"
    assert labels == {"loop": "retention_purge"}


@pytest.mark.anyio
async def test_retention_purge_loop_heartbeat_recorded_on_success():
    """A successful tick refreshes the loop watchdog heartbeat, as before."""
    heartbeat_calls: list[str] = []

    async def capture_sleep(seconds):
        raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", capture_sleep),
        patch("backend.utils.retention_purge._tick", AsyncMock()),
        patch("backend.utils.retention_purge._record_heartbeat", heartbeat_calls.append),
    ):
        try:
            await retention_purge_loop()
        except asyncio.CancelledError:
            pass

    assert heartbeat_calls == ["retention_purge (24h)"]


@pytest.mark.anyio
async def test_retention_purge_loop_heartbeat_not_recorded_on_failure():
    """A failing tick must NOT refresh the heartbeat, so the loop watchdog
    can actually detect a stuck-but-still-ticking failure mode (the loop
    keeps running every 24h on schedule while every individual run fails) —
    see docs/audit/2026-08-19-decision-writeups.md section 9."""
    heartbeat_calls: list[str] = []

    async def failing_tick():
        raise RuntimeError("simulated DB failure")

    async def capture_sleep(seconds):
        raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", capture_sleep),
        patch("backend.utils.retention_purge._tick", failing_tick),
        patch("backend.utils.retention_purge._metric_gauge", MagicMock()),
        patch("backend.utils.retention_purge._metric_inc", MagicMock()),
        patch("backend.utils.retention_purge._record_heartbeat", heartbeat_calls.append),
        patch("sentry_sdk.capture_message", MagicMock(), create=True),
    ):
        try:
            await retention_purge_loop()
        except asyncio.CancelledError:
            pass

    assert heartbeat_calls == []


@pytest.mark.anyio
async def test_retention_purge_loop_sentry_capture_on_failure():
    """A failing tick fires a Sentry `fatal` capture tagged domain=admin,
    mirroring retention_guard_monitor.py's _escalate pattern, so a repeat
    failure actually pages instead of producing only a log line."""
    sentry_capture = MagicMock()

    async def failing_tick():
        raise RuntimeError("simulated DB failure")

    async def capture_sleep(seconds):
        raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", capture_sleep),
        patch("backend.utils.retention_purge._tick", failing_tick),
        patch("backend.utils.retention_purge._metric_gauge", MagicMock()),
        patch("backend.utils.retention_purge._metric_inc", MagicMock()),
        patch("sentry_sdk.capture_message", sentry_capture, create=True),
    ):
        try:
            await retention_purge_loop()
        except asyncio.CancelledError:
            pass

    sentry_capture.assert_called_once()
    _, kwargs = sentry_capture.call_args
    assert kwargs["level"] == "fatal"
    assert kwargs["tags"]["domain"] == "admin"
    assert kwargs["tags"]["surface"] == "backend"


@pytest.mark.anyio
async def test_retention_purge_loop_sentry_capture_carries_no_pii():
    """PIPEDA: the Sentry capture must carry only the exception type, never
    raw PII (names, GPS, emails, etc. — CLAUDE.md's PIPEDA logging rules)."""
    sentry_capture = MagicMock()

    async def failing_tick():
        raise RuntimeError("simulated DB failure")

    async def capture_sleep(seconds):
        raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", capture_sleep),
        patch("backend.utils.retention_purge._tick", failing_tick),
        patch("backend.utils.retention_purge._metric_gauge", MagicMock()),
        patch("backend.utils.retention_purge._metric_inc", MagicMock()),
        patch("sentry_sdk.capture_message", sentry_capture, create=True),
    ):
        try:
            await retention_purge_loop()
        except asyncio.CancelledError:
            pass

    _, kwargs = sentry_capture.call_args
    ctx = kwargs["contexts"]["retention_purge"]
    assert ctx == {"exception_type": "RuntimeError"}


@pytest.mark.anyio
async def test_retention_purge_loop_no_error_counter_on_success():
    """spinr_bgloop_errors_total is NOT emitted when the tick succeeds."""
    inc_calls: list[tuple] = []

    async def capture_sleep(seconds):
        raise asyncio.CancelledError()

    def fake_inc(name, labels=None):
        inc_calls.append((name, labels))

    with (
        patch("asyncio.sleep", capture_sleep),
        patch("backend.utils.retention_purge._tick", AsyncMock()),
        patch("backend.utils.retention_purge._metric_gauge", MagicMock()),
        patch("backend.utils.retention_purge._metric_inc", fake_inc),
    ):
        try:
            await retention_purge_loop()
        except asyncio.CancelledError:
            pass

    assert len(inc_calls) == 0, f"Error counter should not fire on success; got: {inc_calls}"


# ---------------------------------------------------------------------------
# 3. presence_sweeper_loop — initial jitter + loop-body jitter + metrics
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_presence_sweeper_loop_two_sleep_calls():
    """presence_sweeper_loop has an initial random jitter sleep BEFORE the
    while loop and a ±10% jitter sleep INSIDE the while loop.

    Two asyncio.sleep calls are expected per iteration:
      [0] initial: random.uniform(0, SWEEP_INTERVAL_SECONDS)
      [1] loop body: SWEEP_INTERVAL_SECONDS * (0.9 + random.random() * 0.2)
    """
    # Stub module-level imports for presence_sweeper before importing it.
    import backend.utils.presence_sweeper as sweeper_mod  # noqa: F401

    sleep_calls: list[float] = []
    call_count = 0

    async def capture_sleep(seconds):
        nonlocal call_count
        call_count += 1
        sleep_calls.append(seconds)
        if call_count >= 2:
            raise asyncio.CancelledError()

    with (
        patch("asyncio.sleep", capture_sleep),
        patch.object(sweeper_mod, "_sweep_once", AsyncMock(return_value=0)),
        patch.object(sweeper_mod, "_metric_gauge", MagicMock()),
        patch.object(sweeper_mod, "_metric_inc", MagicMock()),
    ):
        try:
            from backend.utils.presence_sweeper import (
                SWEEP_INTERVAL_SECONDS,
                presence_sweeper_loop,
            )

            await presence_sweeper_loop()
        except asyncio.CancelledError:
            pass

    from backend.utils.presence_sweeper import SWEEP_INTERVAL_SECONDS

    assert len(sleep_calls) == 2, f"Expected 2 sleep calls (initial + loop body), got {sleep_calls}"
    # Initial jitter: uniform [0, SWEEP_INTERVAL_SECONDS)
    assert 0 <= sleep_calls[0] <= SWEEP_INTERVAL_SECONDS, (
        f"Initial jitter {sleep_calls[0]} out of [0, {SWEEP_INTERVAL_SECONDS}]"
    )
    # Loop-body jitter: ±10%
    assert SWEEP_INTERVAL_SECONDS * 0.9 <= sleep_calls[1] <= SWEEP_INTERVAL_SECONDS * 1.1, (
        f"Loop-body sleep {sleep_calls[1]} outside ±10% of {SWEEP_INTERVAL_SECONDS}"
    )


@pytest.mark.anyio
async def test_presence_sweeper_loop_emits_metrics():
    """presence_sweeper_loop emits duration gauge and error counter as expected."""
    import backend.utils.presence_sweeper as sweeper_mod

    sleep_calls: list[float] = []
    gauge_calls: list[tuple] = []
    inc_calls: list[tuple] = []
    call_count = 0

    async def capture_sleep(seconds):
        nonlocal call_count
        call_count += 1
        sleep_calls.append(seconds)
        if call_count >= 2:
            raise asyncio.CancelledError()

    def fake_gauge(name, value, labels):
        gauge_calls.append((name, value, labels))

    def fake_inc(name, labels=None):
        inc_calls.append((name, labels))

    with (
        patch("asyncio.sleep", capture_sleep),
        patch.object(sweeper_mod, "_sweep_once", AsyncMock(return_value=0)),
        patch.object(sweeper_mod, "_metric_gauge", fake_gauge),
        patch.object(sweeper_mod, "_metric_inc", fake_inc),
    ):
        try:
            from backend.utils.presence_sweeper import presence_sweeper_loop

            await presence_sweeper_loop()
        except asyncio.CancelledError:
            pass

    assert len(gauge_calls) == 1
    name, value, labels = gauge_calls[0]
    assert name == "spinr_bgloop_duration_ms"
    assert labels == {"loop": "presence_sweeper"}
    assert value >= 0
    # No error on a successful tick
    assert len(inc_calls) == 0


# ---------------------------------------------------------------------------
# 4. subprocessor_audit — first-run baseline initialisation
# ---------------------------------------------------------------------------


def test_subprocessor_audit_first_run_initialises_hashes(tmp_path):
    """On first run (all content_hash == null), the audit records hashes
    without flagging changes, and returns exit code 0.
    """
    baseline = {
        "_schema_version": "1",
        "_last_audit": None,
        "vendors": {
            "vendor_a": {
                "name": "Vendor A",
                "subprocessor_url": "https://example.com/subprocessors",
                "content_hash": None,
                "etag": None,
                "last_reviewed": None,
                "last_changed": None,
            }
        },
    }
    baseline_path = tmp_path / "subprocessors-baseline.json"
    baseline_path.write_text(json.dumps(baseline))

    fake_content = "<html>Vendor A sub-processors: AWS, GCP</html>"

    def fake_fetch(url):
        return fake_content, '"etag-abc123"'

    import backend.utils.subprocessor_audit as audit_mod

    with patch.object(audit_mod, "BASELINE_PATH", baseline_path), patch.object(audit_mod, "_fetch_page", fake_fetch):
        exit_code = audit_mod.run_audit(dry_run=False)

    assert exit_code == 0, "First run with null hashes should return 0 (init, not changed)"

    updated = json.loads(baseline_path.read_text())
    import hashlib

    expected_hash = hashlib.sha256(fake_content.encode()).hexdigest()
    assert updated["vendors"]["vendor_a"]["content_hash"] == expected_hash
    assert updated["vendors"]["vendor_a"]["last_reviewed"] is not None
    assert updated["_last_audit"] is not None


def test_subprocessor_audit_detects_change(tmp_path):
    """When content differs from the stored hash, exit code is 1 and
    last_changed is updated."""
    old_content = "<html>Old sub-processors</html>"
    import hashlib

    old_hash = hashlib.sha256(old_content.encode()).hexdigest()

    baseline = {
        "_schema_version": "1",
        "_last_audit": "2026-01-01T09:00:00Z",
        "vendors": {
            "stripe": {
                "name": "Stripe, Inc.",
                "subprocessor_url": "https://stripe.com/legal/service-providers/global",
                "content_hash": old_hash,
                "etag": '"etag-old"',
                "last_reviewed": "2026-01-01",
                "last_changed": None,
            }
        },
    }
    baseline_path = tmp_path / "subprocessors-baseline.json"
    baseline_path.write_text(json.dumps(baseline))

    new_content = "<html>New sub-processors: AWS, GCP, Azure</html>"

    def fake_fetch(url):
        return new_content, '"etag-new"'

    import backend.utils.subprocessor_audit as audit_mod

    with patch.object(audit_mod, "BASELINE_PATH", baseline_path), patch.object(audit_mod, "_fetch_page", fake_fetch):
        exit_code = audit_mod.run_audit(dry_run=False)

    assert exit_code == 1, "Changed content should return exit code 1"

    updated = json.loads(baseline_path.read_text())
    new_hash = hashlib.sha256(new_content.encode()).hexdigest()
    assert updated["vendors"]["stripe"]["content_hash"] == new_hash
    assert updated["vendors"]["stripe"]["last_changed"] is not None


def test_subprocessor_audit_dry_run_does_not_write(tmp_path):
    """dry_run=True detects changes but does not update the baseline file."""
    old_content = "<html>Old</html>"
    import hashlib

    old_hash = hashlib.sha256(old_content.encode()).hexdigest()

    baseline = {
        "_schema_version": "1",
        "_last_audit": "2026-01-01T09:00:00Z",
        "vendors": {
            "stripe": {
                "name": "Stripe",
                "subprocessor_url": "https://stripe.com/sub",
                "content_hash": old_hash,
                "etag": None,
                "last_reviewed": "2026-01-01",
                "last_changed": None,
            }
        },
    }
    baseline_path = tmp_path / "subprocessors-baseline.json"
    original_text = json.dumps(baseline)
    baseline_path.write_text(original_text)

    def fake_fetch(url):
        return "<html>New</html>", None

    import backend.utils.subprocessor_audit as audit_mod

    with patch.object(audit_mod, "BASELINE_PATH", baseline_path), patch.object(audit_mod, "_fetch_page", fake_fetch):
        exit_code = audit_mod.run_audit(dry_run=True)

    assert exit_code == 1
    # File must not have changed in dry-run mode.
    assert baseline_path.read_text() == original_text


def test_subprocessor_audit_all_fetch_failures_return_2(tmp_path):
    """If every vendor fetch fails, exit code is 2 (fatal network error)."""
    baseline = {
        "_schema_version": "1",
        "_last_audit": None,
        "vendors": {
            "stripe": {
                "name": "Stripe",
                "subprocessor_url": "https://stripe.com/sub",
                "content_hash": "abc",
                "etag": None,
                "last_reviewed": "2026-01-01",
                "last_changed": None,
            }
        },
    }
    baseline_path = tmp_path / "subprocessors-baseline.json"
    baseline_path.write_text(json.dumps(baseline))

    def fake_fetch(url):
        raise ConnectionError("Network unreachable")

    import backend.utils.subprocessor_audit as audit_mod

    with patch.object(audit_mod, "BASELINE_PATH", baseline_path), patch.object(audit_mod, "_fetch_page", fake_fetch):
        exit_code = audit_mod.run_audit(dry_run=True)

    assert exit_code == 2
