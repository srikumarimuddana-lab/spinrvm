"""Background-loop heartbeat exposed as a Prometheus gauge (ADR-010 §3).

The in-app ``loop_watchdog`` already pages via ``ALERT_WEBHOOK_URL`` on stale
heartbeats and stays the primary path — it does not depend on the metrics
pipeline being healthy. This gauge is the second, independent path: it gives
dashboard visibility and still works if the watchdog itself dies.

The subtle part these tests pin: ``record_heartbeat`` tracks
``time.monotonic()`` for staleness (immune to NTP steps/DST), but a gauge
compared against PromQL's ``time()`` must be **epoch** seconds. Exporting the
monotonic value would look like decades of staleness on Linux, where it counts
from boot.
"""

from __future__ import annotations

import time

import pytest

from backend.utils import loop_monitor, metrics


@pytest.fixture(autouse=True)
def _clean_heartbeats():
    with loop_monitor._lock:
        loop_monitor._heartbeats.clear()
        loop_monitor._wall_heartbeats.clear()
    yield
    with loop_monitor._lock:
        loop_monitor._heartbeats.clear()
        loop_monitor._wall_heartbeats.clear()


class TestHeartbeatEpochs:
    def test_records_wall_clock_not_monotonic(self):
        """The exported value must be comparable to PromQL time()."""
        loop_monitor.record_heartbeat("surge_engine (2min)")

        epoch = loop_monitor.get_heartbeat_epochs()["surge_engine (2min)"]

        # Within a minute of real wall-clock now.
        assert abs(epoch - time.time()) < 60
        # And emphatically NOT the monotonic clock, which on Linux counts from
        # boot and would render as a ~56-year-stale loop.
        assert abs(epoch - time.monotonic()) > 1000

    def test_monotonic_staleness_tracking_is_preserved(self):
        """Adding the epoch map must not disturb the existing staleness path."""
        loop_monitor.record_heartbeat("surge_engine (2min)")

        with loop_monitor._lock:
            mono = loop_monitor._heartbeats["surge_engine (2min)"]
        assert abs(mono - time.monotonic()) < 60

        status = loop_monitor.get_loop_status(["surge_engine (2min)"])
        assert status["healthy"] is True
        assert status["loops"]["surge_engine (2min)"]["status"] == "ok"

    def test_never_ticked_loop_is_absent_not_zero(self):
        """A 0 gauge would read as 'last ticked in 1970' — permanently stale.

        stripe_reconcile legitimately waits until 02:00 UTC for its first tick,
        so exporting 0 at startup would false-alarm every deploy. Absence is the
        honest representation; PromQL absent() covers it if an alert needs to.
        """
        loop_monitor.record_heartbeat("surge_engine (2min)")

        epochs = loop_monitor.get_heartbeat_epochs()
        assert "surge_engine (2min)" in epochs
        assert "stripe_reconcile (24h)" not in epochs
        assert 0 not in epochs.values()

    def test_returns_a_copy_not_live_internal_state(self):
        loop_monitor.record_heartbeat("surge_engine (2min)")

        epochs = loop_monitor.get_heartbeat_epochs()
        epochs["injected"] = 1.0

        assert "injected" not in loop_monitor.get_heartbeat_epochs()

    def test_latest_tick_wins(self):
        loop_monitor.record_heartbeat("surge_engine (2min)")
        first = loop_monitor.get_heartbeat_epochs()["surge_engine (2min)"]

        loop_monitor.record_heartbeat("surge_engine (2min)")
        second = loop_monitor.get_heartbeat_epochs()["surge_engine (2min)"]

        assert second >= first


class TestGaugeExposition:
    def test_renders_one_labelled_series_per_loop(self):
        with metrics._lock:
            metrics._gauges.pop("spinr_loop_heartbeat_timestamp_seconds", None)

        loop_monitor.record_heartbeat("surge_engine (2min)")
        loop_monitor.record_heartbeat("payment_retry (5min)")

        for name, epoch in loop_monitor.get_heartbeat_epochs().items():
            metrics.set_gauge("spinr_loop_heartbeat_timestamp_seconds", epoch, {"loop": name})

        out = metrics.render_prometheus()
        assert "# TYPE spinr_loop_heartbeat_timestamp_seconds gauge" in out
        assert 'spinr_loop_heartbeat_timestamp_seconds{loop="surge_engine (2min)"}' in out
        assert 'spinr_loop_heartbeat_timestamp_seconds{loop="payment_retry (5min)"}' in out

        with metrics._lock:
            metrics._gauges.pop("spinr_loop_heartbeat_timestamp_seconds", None)
