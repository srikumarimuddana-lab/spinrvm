"""Lightweight Prometheus-style in-process counters.

Scope: internal observability for the Supabase/Redis layer — retry rate,
circuit-breaker state, cache hit/miss — without pulling in the full
prometheus_client dependency or a sidecar exporter.

Design notes
------------

- **Per-process only.** Each backend replica keeps its own counters.
  A Prometheus scraper reading /metrics from each replica adds them up
  in the server-side view — we do NOT aggregate across replicas here.
- **Labels are cheap.** Callers pass a dict of labels; we stringify it
  once at the increment site so the hot path stays fast.
- **Exposition in text format**: `/metrics` renders a simple Prometheus
  exposition (no # TYPE / # HELP required for scraping to work, but
  included for clarity).
- **Thread-safe**: protected by a module-level lock because async call
  sites still run on the event loop, but the executor threads from
  run_in_executor touch the same counters.

If we later adopt the real prometheus_client, this module stays a
compat shim and the metric names are already Prometheus-idiomatic
(snake_case_total / _total).
"""

from __future__ import annotations

import functools
import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, Tuple

_lock = threading.Lock()

# counter_name -> (labels_tuple -> int)
_counters: Dict[str, Dict[Tuple[Tuple[str, str], ...], int]] = {}
# gauge_name -> (labels_tuple -> value)
_gauges: Dict[str, Dict[Tuple[Tuple[str, str], ...], float]] = {}
# histogram_name -> (labels_tuple -> {"sum": float, "count": int, "buckets": [int per le]})
_histograms: Dict[str, Dict[Tuple[Tuple[str, str], ...], Dict[str, object]]] = {}

# Default buckets are tuned for millisecond latencies around the SLA table in
# CLAUDE.md (fare calc <300ms, WS fan-out <100ms, dispatch offer→accept <2s).
DEFAULT_MS_BUCKETS: Tuple[float, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)

# Dispatch offer→accept has a hard 2000 ms P95 SLA, which falls *inside* the
# DEFAULT_MS_BUCKETS gap between 1000 and 2500. histogram_quantile() would then
# linearly interpolate across that 1.5 s-wide bucket instead of reading a bound
# that lines up with the SLA, so the alert threshold is only ever approximated.
# Adding an explicit 2000 boundary makes the breach/no-breach decision exact.
# See ADR-010 §2 ("One real (non-blocking) gap worth flagging").
#
# This is deliberately a separate tuple rather than a change to
# DEFAULT_MS_BUCKETS: observe() pins a metric's bucket layout on its first
# observation, so widening the shared default would silently invalidate every
# already-recorded series that uses it.
DISPATCH_MS_BUCKETS: Tuple[float, ...] = (
    5,
    10,
    25,
    50,
    100,
    250,
    500,
    1000,
    2000,
    2500,
    5000,
    10000,
)


def _labels_to_key(labels: Dict[str, str] | None) -> Tuple[Tuple[str, str], ...]:
    if not labels:
        return ()
    # Deterministic order so the same label set always maps to the same key.
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def inc(name: str, labels: Dict[str, str] | None = None, by: int = 1) -> None:
    """Increment a counter by `by` (default 1). Safe to call from any thread."""
    key = _labels_to_key(labels)
    with _lock:
        bucket = _counters.setdefault(name, {})
        bucket[key] = bucket.get(key, 0) + by


def set_gauge(name: str, value: float, labels: Dict[str, str] | None = None) -> None:
    """Set a gauge to an absolute value (for point-in-time things like circuit state)."""
    key = _labels_to_key(labels)
    with _lock:
        _gauges.setdefault(name, {})[key] = value


def observe(
    name: str,
    value: float,
    labels: Dict[str, str] | None = None,
    buckets: Tuple[float, ...] = DEFAULT_MS_BUCKETS,
) -> None:
    """Record one observation in a histogram (cumulative buckets + sum + count).

    `buckets` must stay constant per metric name — the first observation pins
    the bucket layout for that name. Safe to call from any thread.
    """
    key = _labels_to_key(labels)
    with _lock:
        series = _histograms.setdefault(name, {})
        cell = series.get(key)
        if cell is None:
            cell = {"sum": 0.0, "count": 0, "le": tuple(buckets), "buckets": [0] * len(buckets)}
            series[key] = cell
        cell["sum"] = float(cell["sum"]) + float(value)  # type: ignore[arg-type]
        cell["count"] = int(cell["count"]) + 1  # type: ignore[arg-type]
        bucket_counts: list = cell["buckets"]  # type: ignore[assignment]
        for i, le in enumerate(cell["le"]):  # type: ignore[arg-type]
            if value <= le:
                bucket_counts[i] += 1


@contextmanager
def time_ms(name: str, labels: Dict[str, str] | None = None) -> Iterator[None]:
    """Time the wrapped block and observe the elapsed milliseconds.

    Records even when the block raises — a slow failure is still latency
    the SLA dashboards must see.
    """
    t0 = time.monotonic()
    try:
        yield
    finally:
        observe(name, (time.monotonic() - t0) * 1000.0, labels)


def timed(name: str, labels: Dict[str, str] | None = None):
    """Decorator form of time_ms for async functions (e.g. route handlers).

    functools.wraps preserves the signature so FastAPI dependency
    injection keeps working on decorated endpoints.
    """

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            with time_ms(name, labels):
                return await fn(*args, **kwargs)

        return wrapper

    return deco


def snapshot() -> Dict[str, Dict[Tuple[Tuple[str, str], ...], float]]:
    """Return a shallow copy of current counters + gauges + histograms for exposition."""
    with _lock:
        return {
            "counters": {k: dict(v) for k, v in _counters.items()},
            "gauges": {k: dict(v) for k, v in _gauges.items()},
            "histograms": {
                k: {lk: {**cell, "buckets": list(cell["buckets"])} for lk, cell in v.items()}
                for k, v in _histograms.items()
            },
        }


def _format_labels(labels_tuple: Tuple[Tuple[str, str], ...]) -> str:
    if not labels_tuple:
        return ""
    inner = ",".join(f'{k}="{_escape_label_value(v)}"' for k, v in labels_tuple)
    return "{" + inner + "}"


def _escape_label_value(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus() -> str:
    """Render all counters + gauges in Prometheus text exposition format."""
    lines: list[str] = []
    snap = snapshot()
    for name, bucket in sorted(snap["counters"].items()):
        lines.append(f"# TYPE {name} counter")
        for labels_tuple, value in sorted(bucket.items()):
            lines.append(f"{name}{_format_labels(labels_tuple)} {value}")
    for name, bucket in sorted(snap["gauges"].items()):
        lines.append(f"# TYPE {name} gauge")
        for labels_tuple, value in sorted(bucket.items()):
            lines.append(f"{name}{_format_labels(labels_tuple)} {value}")
    for name, series in sorted(snap.get("histograms", {}).items()):
        lines.append(f"# TYPE {name} histogram")
        for labels_tuple, cell in sorted(series.items()):
            for le, count in zip(cell["le"], cell["buckets"]):
                le_labels = tuple(sorted(labels_tuple + (("le", _format_le(le)),)))
                lines.append(f"{name}_bucket{_format_labels(le_labels)} {count}")
            inf_labels = tuple(sorted(labels_tuple + (("le", "+Inf"),)))
            lines.append(f"{name}_bucket{_format_labels(inf_labels)} {cell['count']}")
            lines.append(f"{name}_sum{_format_labels(labels_tuple)} {cell['sum']}")
            lines.append(f"{name}_count{_format_labels(labels_tuple)} {cell['count']}")
    return "\n".join(lines) + "\n"


def _format_le(le: float) -> str:
    """Render bucket bounds the Prometheus way: integral values without .0."""
    return str(int(le)) if float(le).is_integer() else str(le)


def iter_counters() -> Iterable[Tuple[str, Dict[Tuple[Tuple[str, str], ...], int]]]:
    """Yield (name, bucket) for internal inspection (tests, debug endpoints)."""
    with _lock:
        return list(_counters.items())
