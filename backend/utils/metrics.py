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
import os
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


def _with_worker_pid(labels_tuple: Tuple[Tuple[str, str], ...], worker_pid: str) -> Tuple[Tuple[str, str], ...]:
    # Drop any caller-supplied "worker_pid" first: two entries with the same
    # key would render as an invalid Prometheus line (duplicate label in one
    # `{...}` block), risking a scrape-wide parse failure instead of one bad
    # series. No current call site passes this key, but nothing stops one
    # from doing so by accident later.
    without_pid = tuple(kv for kv in labels_tuple if kv[0] != "worker_pid")
    return tuple(sorted(without_pid + (("worker_pid", worker_pid),)))


def render_prometheus() -> str:
    """Render all counters + gauges in Prometheus text exposition format.

    Every series carries a `worker_pid` label (read live via `os.getpid()`,
    same convention as `routes/admin/monitoring.py`'s per-worker fan-out
    stats) so a multi-process Uvicorn deployment (`fly.toml`'s
    UVICORN_WORKERS) doesn't smear one worker's counters into another's on
    each scrape: without it, a scrape landing on a different worker than the
    previous one looks like the counter dropped, which breaks Prometheus's
    rate()/increase() (a real decrease reads as a process restart).
    Downstream aggregation across workers: counters and histograms are
    additive, safe to `sum by (...)`. Gauges are NOT additive across
    workers — each worker independently reports its own read of what's
    conceptually one shared value (e.g. Redis memory) — so query them with
    `max by (...)` (grouping out worker_pid), never `sum by (...)`.
    """
    worker_pid = str(os.getpid())
    lines: list[str] = []
    snap = snapshot()
    for name, bucket in sorted(snap["counters"].items()):
        lines.append(f"# TYPE {name} counter")
        for labels_tuple, value in sorted(bucket.items()):
            lines.append(f"{name}{_format_labels(_with_worker_pid(labels_tuple, worker_pid))} {value}")
    for name, bucket in sorted(snap["gauges"].items()):
        lines.append(f"# TYPE {name} gauge")
        for labels_tuple, value in sorted(bucket.items()):
            lines.append(f"{name}{_format_labels(_with_worker_pid(labels_tuple, worker_pid))} {value}")
    for name, series in sorted(snap.get("histograms", {}).items()):
        lines.append(f"# TYPE {name} histogram")
        for labels_tuple, cell in sorted(series.items()):
            labels_tuple = _with_worker_pid(labels_tuple, worker_pid)
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
