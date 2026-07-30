"""Pre-match driver map visibility policy (PIPEDA / launch gate).

A driver's live position is personal information about a contractor's whereabouts.
Before a ride is assigned there is no relationship that justifies exact
coordinates, so riders get a coarsened position; exact coordinates are reserved
for the assigned-ride tracking path.

This module exists because the policy has **two** call sites that must not drift:
``GET /drivers/nearby`` and the ``get_nearby_drivers`` WebSocket message. They were
written independently and had already diverged (the WS handler did not geo-bound
its query and rejected a driver legitimately at lat=0). Any change to what a
pre-match rider may see belongs here, not in either route.

Launch-gate hard no-go this addresses: "Any client can enumerate precise driver
locations without an assigned ride."
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from .pii import coarsen_coord
except ImportError:  # pragma: no cover - dual import
    from utils.pii import coarsen_coord  # type: ignore

# Never let a misconfigured setting re-expose exact positions. coarsen_coord()
# treats cell_m<=0 as "exact passthrough", which is correct for the assigned-ride
# caller but must be unreachable from a pre-match path — so clamp to a floor
# rather than trusting the settings row.
MIN_CELL_M = 100

# Fields a pre-match rider may see. Allowlist, not denylist: a new column on
# `drivers` must not become rider-visible just because someone added it.
#
# Deliberately excluded, and why:
#   lat/lng          -> replaced by the coarsened pair below
#   vehicle_make     -> with make+model+heading a specific car is re-identifiable
#   vehicle_model    -> same
#   name/phone/email -> never rider-visible pre-match
#   user_id          -> internal join key
_PREMATCH_FIELDS = (
    "vehicle_type_id",
    "vehicle_type_name",
    "marker_variant",
    "heading",
)


def map_settings(app_settings: Optional[Dict[str, Any]]) -> Tuple[bool, int, float]:
    """Resolve the three visibility knobs from an app_settings dict.

    Returns ``(show_locations, cell_m, max_radius_km)``. Tolerates a missing or
    malformed settings row by falling back to the safe values — a settings outage
    must not fail *open* into exact coordinates.
    """
    settings = app_settings or {}

    show = settings.get("driver_map_show_locations", True)
    show = True if show is None else bool(show)

    try:
        cell_m = int(settings.get("driver_map_cell_m") or 500)
    except (TypeError, ValueError):
        cell_m = 500
    cell_m = max(MIN_CELL_M, cell_m)

    try:
        max_radius = float(settings.get("driver_map_max_radius_km") or 15.0)
    except (TypeError, ValueError):
        max_radius = 15.0
    if max_radius <= 0:
        max_radius = 15.0

    return show, cell_m, max_radius


def clamp_radius(requested: Any, max_radius_km: float, default_km: float) -> float:
    """Bound a caller-supplied radius.

    The radius arrives from the client on both call sites. Unbounded, a single
    request with ``radius=1000`` enumerates every online driver in the province,
    which is the launch-gate no-go. A non-numeric or non-positive value falls back
    to the configured default rather than erroring — this is a map read, not a
    money path, and failing closed to the default keeps the map working.
    """
    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = default_km
    if value <= 0:
        value = default_km
    return min(value, max_radius_km)


def prematch_driver_payload(driver: Dict[str, Any], cell_m: int) -> Optional[Dict[str, Any]]:
    """Project one driver row into what a pre-match rider may see.

    Returns ``None`` when the driver has no usable position, so callers can simply
    skip a falsy result. Note this preserves the existing ``(0, 0)`` behaviour:
    that is the registration default meaning "no GPS yet", and surfacing it would
    put a ghost car in the Gulf of Guinea.
    """
    coarse = coarsen_coord(driver.get("lat"), driver.get("lng"), cell_m=max(MIN_CELL_M, cell_m))
    if coarse is None:
        return None

    payload: Dict[str, Any] = {
        # The driver row id is retained: the rider app uses it only as a map
        # marker key, and rotating it would remount markers mid-session. It does
        # still allow following one (coarsened) vehicle over time, which is
        # hardening beyond this gate — tracked separately, not silently assumed
        # to be covered here.
        "id": driver.get("id"),
        "lat": coarse[0],
        "lng": coarse[1],
        # Tell the client this is deliberately approximate, so it can render
        # accordingly instead of implying GPS precision it does not have.
        "precision": "approximate",
        "precision_m": max(MIN_CELL_M, cell_m),
    }
    for field in _PREMATCH_FIELDS:
        if field in driver:
            payload[field] = driver.get(field)
    return payload


def prematch_driver_list(drivers: List[Dict[str, Any]], cell_m: int) -> List[Dict[str, Any]]:
    """Project a list, dropping drivers without a usable position."""
    out = []
    for driver in drivers:
        payload = prematch_driver_payload(driver, cell_m)
        if payload is not None:
            out.append(payload)
    return out
