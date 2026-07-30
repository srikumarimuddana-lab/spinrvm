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

import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from .pii import coarsen_coord
except ImportError:  # pragma: no cover - dual import
    from utils.pii import coarsen_coord  # type: ignore

logger = logging.getLogger(__name__)

# ── Pseudonymous marker ids ─────────────────────────────────────────
#
# Coarsening the position was not sufficient on its own. A stable `drivers.id` in
# the pre-match payload lets any authenticated rider poll GET /drivers/nearby on a
# timer and stitch one driver's coarse positions into a movement trace. At 500 m
# granularity a single sample says little, but a week of samples for a known id
# reveals where that contractor starts and ends their day — which is precisely the
# inference PIPEDA data-minimisation exists to prevent, and it needs no exact
# coordinates at all.
#
# So the pre-match payload carries a rotating HMAC pseudonym instead of the row id.
# Three properties, each load-bearing:
#
#   * **Rotating** — the token changes every PSEUDONYM_PERIOD_S, which caps any
#     trace at one period regardless of how long a client polls.
#   * **Per-viewer** — the viewer's id is in the HMAC input, so two riders (or two
#     accounts held by one person) see different tokens for the same driver and
#     cannot pool observations.
#   * **Staggered** — the rotation boundary is offset per driver, derived from the
#     driver id, so markers do not all churn on the same tick. This is what makes
#     rotation affordable: the rider app uses this value as a React marker key
#     (`key={driver.id}` in ride-options.tsx and (tabs)/index.tsx), so a
#     synchronised rotation would remount every marker at once.
#
# The token is display-only. Verified before adopting it: the rider app uses the id
# solely as a marker key/identifier, and no rider-facing endpoint accepts a
# client-supplied driver id, so nothing round-trips it back to the server.
PSEUDONYM_PERIOD_S = 15 * 60

# Length of the hex token. 16 hex chars = 64 bits: far beyond collision range for
# the number of drivers online in one service area, while staying short enough to
# be an unremarkable map key.
_PSEUDONYM_LEN = 16

_PSEUDONYM_INFO = b"spinr-prematch-driver-pseudonym-v1"


def _pseudonym_key() -> bytes:
    """Derive a dedicated subkey rather than using JWT_SECRET directly.

    Same secret material, separate purpose: a token forged from this key must not be
    interchangeable with anything on the auth path, and vice versa.
    """
    try:
        from core.config import settings  # local import: avoids a config import cycle
    except ImportError:  # pragma: no cover - dual import
        from ..core.config import settings  # type: ignore

    secret = getattr(settings, "JWT_SECRET", None) or getattr(settings, "jwt_secret", None) or ""
    return hmac.new(str(secret).encode(), _PSEUDONYM_INFO, hashlib.sha256).digest()


def prematch_driver_pseudonym(
    driver_id: Any,
    viewer_id: Any,
    *,
    now: Optional[float] = None,
    period_s: int = PSEUDONYM_PERIOD_S,
) -> str:
    """Stable-within-a-period, per-viewer pseudonym for a driver's map marker.

    ``viewer_id`` is the requesting rider's user id. It is deliberately part of the
    HMAC input, not just a salt on the driver id: without it, colluding accounts
    could merge traces and the rotation would only slow them down.
    """
    key = _pseudonym_key()
    did = str(driver_id or "")
    # Per-driver rotation offset so all markers do not rotate simultaneously.
    offset = int.from_bytes(hmac.new(key, b"offset:" + did.encode(), hashlib.sha256).digest()[:4], "big")
    bucket = (int(now if now is not None else time.time()) + (offset % max(1, period_s))) // max(1, period_s)
    msg = f"{did}:{viewer_id or ''}:{bucket}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:_PSEUDONYM_LEN]

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


def prematch_driver_payload(driver: Dict[str, Any], cell_m: int, viewer_id: Any = None) -> Optional[Dict[str, Any]]:
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
        # A rotating per-viewer pseudonym, NOT the driver row id. The row id was
        # retained in the first version of this projection, which left a rider able
        # to poll on a timer and stitch one driver's coarse positions into a
        # movement trace — no exact coordinates required. See
        # prematch_driver_pseudonym above for why it rotates, why the viewer is in
        # the HMAC input, and why the rotation boundary is staggered per driver.
        "id": prematch_driver_pseudonym(driver.get("id"), viewer_id),
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


def prematch_driver_list(
    drivers: List[Dict[str, Any]], cell_m: int, viewer_id: Any = None
) -> List[Dict[str, Any]]:
    """Project a list, dropping drivers without a usable position.

    ``viewer_id`` is the requesting rider's user id, which scopes the marker
    pseudonyms to that viewer. It defaults to ``None`` so an omission degrades to a
    single shared pseudonym space (still rotating) rather than leaking the real row
    id — but every real caller must pass it.
    """
    out = []
    for driver in drivers:
        payload = prematch_driver_payload(driver, cell_m, viewer_id)
        if payload is not None:
            out.append(payload)
    return out
