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

import threading
from typing import Dict, Iterable, Tuple

_lock = threading.Lock()

# counter_name -> (labels_tuple -> int)
_counters: Dict[str, Dict[Tuple[Tuple[str, str], ...], int]] = {}
# gauge_name -> (labels_tuple -> value)
_gauges: Dict[str, Dict[Tuple[Tuple[str, str], ...], float]] = {}


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


def snapshot() -> Dict[str, Dict[Tuple[Tuple[str, str], ...], float]]:
    """Return a shallow copy of current counters + gauges for exposition."""
    with _lock:
        return {
            "counters": {k: dict(v) for k, v in _counters.items()},
            "gauges": {k: dict(v) for k, v in _gauges.items()},
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
    return "\n".join(lines) + "\n"


def iter_counters() -> Iterable[Tuple[str, Dict[Tuple[Tuple[str, str], ...], int]]]:
    """Yield (name, bucket) for internal inspection (tests, debug endpoints)."""
    with _lock:
        return list(_counters.items())
