"""
Signed estimate-token helpers.

Surge pricing is read at two points: when the rider asks for an estimate
(``POST /rides/estimate``) and when they confirm (``POST /rides``). If the
service area's ``surge_multiplier`` changes between those two calls, the
rider is bait-and-switched on price — they saw $18.50, they paid $27.75.

The estimate-token locks the surge that was shown to the rider:

    1. /estimate computes surge from service_area
    2. /estimate returns, per vehicle_type, an estimate_token whose
       payload is {pickup, dropoff, vehicle_type_id, surge_multiplier,
       expires_at, rider_id}, signed with JWT_SECRET via HMAC-SHA256
    3. /rides accepts an optional estimate_token; when present and valid,
       the handler reuses the locked surge instead of re-querying.

The token is short-lived (5 minutes by default) — long enough to confirm,
short enough that stale surge can't be replayed later. Binding to
``rider_id`` prevents one rider replaying another rider's low-surge token.

Pure stdlib — no JWT dep needed. Tokens are:

    base64url( json_payload ).base64url( hmac_sha256( json_payload ) )

The shape is unambiguous (``.`` separator) and trivially parseable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

try:
    from ..core.config import settings
except ImportError:
    from core.config import settings


# 5-minute lock window. Short enough that surge drift on the platform
# side can't be replayed hours later; long enough that a rider thumbing
# through payment methods won't see their estimate expire.
DEFAULT_TTL_SECONDS = 300


class EstimateTokenError(ValueError):
    """Raised when a token is malformed, signature-invalid, or expired."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(payload_bytes: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()


def sign_estimate_token(
    *,
    rider_id: str,
    vehicle_type_id: str,
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    surge_multiplier: float,
    total_fare: float,
    distance_km: Optional[float] = None,
    distance_basis: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> str:
    """Return a signed token locking the surge + fare shown at estimate time.

    Includes ``total_fare`` so the create-ride handler can sanity-check
    the quoted amount against what the token promised and reject replays
    that tweak one field (e.g. change vehicle_type_id) while keeping the
    same signature.

    ``distance_km`` / ``distance_basis`` carry the *quoted road distance* (and
    how it was derived — ``road_route`` / ``haversine_fallback`` / ``haversine``)
    from /estimate to /rides, so booking charges the exact distance the rider
    was shown instead of recomputing straight-line haversine. Optional for
    back-compat: older clients omit them and booking falls back to haversine.
    """
    issued = int(now if now is not None else time.time())
    payload: Dict[str, Any] = {
        "v": 1,
        "rid": rider_id,
        "vt": vehicle_type_id,
        "pu": [round(pickup_lat, 5), round(pickup_lng, 5)],
        "do": [round(dropoff_lat, 5), round(dropoff_lng, 5)],
        "sm": round(float(surge_multiplier), 4),
        "tf": round(float(total_fare), 2),
        "iat": issued,
        "exp": issued + ttl_seconds,
    }
    # Only stamp the distance fields when provided so tokens from callers that
    # don't price on road distance stay byte-identical to the pre-change shape.
    if distance_km is not None:
        payload["dk"] = round(float(distance_km), 3)
    if distance_basis is not None:
        payload["db"] = str(distance_basis)
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = _sign(payload_bytes, settings.JWT_SECRET)
    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(sig)}"


def verify_estimate_token(
    token: str,
    *,
    rider_id: str,
    vehicle_type_id: str,
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Return the decoded payload iff the token is valid, unexpired, and
    bound to exactly this rider + route + vehicle type.

    Caller should:
        payload = verify_estimate_token(token, rider_id=..., ...)
        locked_surge = payload["sm"]
        locked_total = payload["tf"]
    ...then use ``locked_surge`` instead of re-reading service_area.

    Raises ``EstimateTokenError`` on any failure so the caller can decide
    whether to 400 (reject) or silently fall back to current surge.
    """
    if not token or token.count(".") != 1:
        raise EstimateTokenError("malformed token")

    payload_b64, sig_b64 = token.split(".", 1)

    try:
        payload_bytes = _b64url_decode(payload_b64)
        provided_sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError) as e:
        raise EstimateTokenError(f"base64 decode failed: {e}") from e

    expected_sig = _sign(payload_bytes, settings.JWT_SECRET)
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise EstimateTokenError("signature mismatch")

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError as e:
        raise EstimateTokenError(f"payload is not JSON: {e}") from e

    if payload.get("v") != 1:
        raise EstimateTokenError(f"unsupported token version: {payload.get('v')}")

    current = int(now if now is not None else time.time())
    exp = payload.get("exp", 0)
    if current >= exp:
        raise EstimateTokenError("token expired")

    if payload.get("rid") != rider_id:
        raise EstimateTokenError("token rider mismatch")

    if payload.get("vt") != vehicle_type_id:
        raise EstimateTokenError("token vehicle_type mismatch")

    # Coordinates rounded to 5 decimals (~1m precision) — tolerates GPS jitter
    # while still catching a rider who materially moved their pin after the
    # estimate was generated.
    pu = payload.get("pu") or []
    do = payload.get("do") or []
    if len(pu) != 2 or len(do) != 2:
        raise EstimateTokenError("token coords missing")
    if pu[0] != round(pickup_lat, 5) or pu[1] != round(pickup_lng, 5):
        raise EstimateTokenError("token pickup mismatch")
    if do[0] != round(dropoff_lat, 5) or do[1] != round(dropoff_lng, 5):
        raise EstimateTokenError("token dropoff mismatch")

    return payload
