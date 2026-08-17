"""Resolution of driver-heatmap tuning knobs.

Every value the demand-heatmap endpoint depends on is resolved through one
chain, in this order:

    per-service-area override  →  global app_settings  →  code default

and clamped to a hard range at the end regardless of where it came from.

Why the clamp is unconditional
------------------------------
Two of the three sources are writable out-of-band. ``app_settings`` and
``service_areas.heatmap_config`` are ordinary rows: an operator with database
access, a migration, or a bulk script can put anything in them without passing
through the admin API's validation. Several of these values are not cosmetic —
``k_floor`` is a PIPEDA k-anonymity control, and ``refresh_seconds`` multiplies
across every online driver — so the read site clamps as its own last line of
defence rather than trusting the write side.

Why per-area at all
-------------------
The windows were hardcoded for a single mid-size market. They do not transfer:
a dense downtown core wants a shorter live window and smaller cells than a
rural area, and a low-volume region needs a *longer* baseline window to clear
the same k-anonymity floor that a busy one clears in a day. Forcing one set of
numbers on every region means either starving small areas of visible cells or
loosening the privacy floor globally to compensate.

Adding a key
------------
Add it to ``HEATMAP_SPEC`` and it is automatically resolvable per area,
globally settable, clamped, included in the config hash (so caches invalidate
when it changes), and covered by the bounds tests. Nothing else needs editing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping, Optional


class _Key:
    """One tunable knob: its type, bounds, default, and global settings name."""

    __slots__ = ("name", "kind", "lo", "hi", "default", "global_key")

    def __init__(self, name: str, kind: str, lo: float, hi: float, default: Any, global_key: Optional[str] = None):
        self.name = name
        self.kind = kind  # "int" | "float"
        self.lo = lo
        self.hi = hi
        self.default = default
        # The matching column in the global `settings` row, when one exists.
        # Keys added later (the time windows) are per-area + default only,
        # deliberately: see the module docstring on why they vary by market.
        self.global_key = global_key


# Bounds are enforced identically here, in the admin API's Pydantic model, and
# (for the global keys) in migration 311's CHECK constraint. Three layers is
# intentional — each covers a path the others do not.
HEATMAP_SPEC: Dict[str, _Key] = {
    k.name: k
    for k in [
        # ── Privacy and grid ────────────────────────────────────────────
        _Key("k_floor", "int", 1, 50, 3, "heatmap_k_floor"),
        _Key("cell_lat_deg", "float", 0.0005, 0.05, 0.004, "heatmap_cell_lat_deg"),
        _Key("cell_lng_deg", "float", 0.0005, 0.05, 0.006, "heatmap_cell_lng_deg"),
        _Key("decay_half_life_days", "float", 0.5, 30.0, 3.0, "heatmap_decay_half_life_days"),
        _Key("refresh_seconds", "int", 30, 600, 90, "heatmap_refresh_seconds"),
        # ── Aggregation windows (previously hardcoded in the endpoint) ──
        # Lookback for the decayed v1 point cloud.
        _Key("live_window_days", "int", 1, 30, 7),
        # "Busy right now" — matches the surge engine's own demand window.
        _Key("now_window_minutes", "int", 5, 120, 10),
        # "Usually busy at this hour" — a low-volume area needs a longer
        # window than a busy one to clear the same k-anonymity floor.
        _Key("baseline_window_days", "int", 7, 90, 28),
        # How far ahead scheduled pickups count as demand.
        _Key("scheduled_lookahead_hours", "int", 1, 24, 2),
        # Driver-facing forecast horizon and the history it is built from.
        _Key("forecast_hours_ahead", "int", 1, 24, 6),
        _Key("forecast_lookback_days", "int", 7, 90, 28),
    ]
}


def _coerce(spec: _Key, value: Any) -> Optional[Any]:
    """Cast and clamp one value, or return None if it isn't usable at all."""
    if value is None:
        return None
    try:
        n = int(value) if spec.kind == "int" else float(value)
    except (TypeError, ValueError):
        # Garbage ("abc", {}, []) falls through to the next source in the
        # chain rather than raising: a bad row must not 500 every driver poll.
        return None
    if n != n:  # NaN
        return None
    return max(spec.lo, min(spec.hi, n))


def resolve_heatmap_config(
    area: Optional[Mapping[str, Any]] = None,
    app_settings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve every knob for one service area.

    ``area`` is the ``service_areas`` row (its ``heatmap_config`` JSONB column
    holds the overrides); ``app_settings`` is the global settings dict. Both
    are optional — with neither, this returns the code defaults.

    Always returns a complete config: every key in :data:`HEATMAP_SPEC` is
    present, correctly typed, and within bounds.
    """
    overrides: Mapping[str, Any] = {}
    if area:
        raw = area.get("heatmap_config")
        if isinstance(raw, str):
            # Some drivers hand back JSONB as a string; tolerate both.
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = None
        if isinstance(raw, Mapping):
            overrides = raw

    settings: Mapping[str, Any] = app_settings or {}

    resolved: Dict[str, Any] = {}
    for name, spec in HEATMAP_SPEC.items():
        value = _coerce(spec, overrides.get(name))
        if value is None and spec.global_key:
            value = _coerce(spec, settings.get(spec.global_key))
        if value is None:
            value = spec.default
        resolved[name] = int(value) if spec.kind == "int" else float(value)
    return resolved


def config_fingerprint(config: Mapping[str, Any]) -> str:
    """Short stable hash of a resolved config, for cache keys.

    The cached payload is only valid for the config that produced it. Without
    this in the key, a tuning change would keep serving cells built with the
    old cell size or — worse — the old k-anonymity floor until the TTL expired.
    """
    canonical = json.dumps({k: config[k] for k in sorted(config)}, separators=(",", ":"))
    # usedforsecurity=False: this is a cache-key fingerprint, not a security
    # hash (no password, token, or signature involved) -- silences Bandit's
    # B324 false positive (CR-2026-(assign), see .github/ISSUE_TEMPLATE
    # ci_change_request.yml issue) without changing the digest at all.
    return hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def describe_overrides(area: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return only the keys this area actually overrides, cleaned and clamped.

    Used by the admin API so the UI can distinguish "inherits the global
    value" from "explicitly set to the same number as the global value" —
    a distinction that matters when the global later changes.
    """
    if not area:
        return {}
    raw = area.get("heatmap_config")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw, Mapping):
        return {}
    out: Dict[str, Any] = {}
    for name, spec in HEATMAP_SPEC.items():
        value = _coerce(spec, raw.get(name))
        if value is not None:
            out[name] = int(value) if spec.kind == "int" else float(value)
    return out
