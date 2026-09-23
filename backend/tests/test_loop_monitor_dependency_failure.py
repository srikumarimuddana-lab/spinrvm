"""Tests for dependency failures reported by background loops."""

from backend.utils import loop_monitor


def test_dependency_failure_is_unhealthy_until_next_heartbeat(monkeypatch):
    name = "payment_retry (5min)"
    with loop_monitor._lock:
        loop_monitor._heartbeats.pop(name, None)
        loop_monitor._failures.pop(name, None)

    try:
        loop_monitor.record_dependency_failure(name)
        failed = loop_monitor.get_loop_status([name])
        assert failed["healthy"] is False
        assert failed["loops"][name]["status"] == "unhealthy"

        # A normal heartbeat is also how a replica reports successful lock
        # contention or a recovered Redis connection.
        loop_monitor.record_heartbeat(name)
        recovered = loop_monitor.get_loop_status([name])
        assert recovered["healthy"] is True
        assert recovered["loops"][name]["status"] == "ok"

        # Failures are included even when callers rely on the implicit set of
        # registered loops and this loop has never sent a heartbeat.
        with loop_monitor._lock:
            loop_monitor._heartbeats.pop(name, None)
        loop_monitor.record_dependency_failure(name)
        implicit = loop_monitor.get_loop_status()
        assert implicit["healthy"] is False
        assert implicit["loops"][name]["status"] == "unhealthy"
    finally:
        with loop_monitor._lock:
            loop_monitor._heartbeats.pop(name, None)
            loop_monitor._failures.pop(name, None)
