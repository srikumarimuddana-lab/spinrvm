"""Public, signed-URL endpoint that renders the ride-offer fare banner.

The driver-app ride-offer notification uses this image as its Android
``BigPicture``. Notifee fetches it over a plain HTTP GET from the OS — with no
JWT and no Firebase App Check header — so this endpoint is intentionally
unauthenticated *at the middleware layer* (it's in ``_APP_CHECK_EXEMPT_PREFIXES``)
and instead authorises each request with the HMAC-signed, ride+driver-bound,
short-TTL token minted at dispatch time (see ``utils/offer_card_token``).

Defence in depth / PIPEDA:
  - token expires ~90s after the offer is sent, so a leaked URL is useless
    almost immediately;
  - the banner only renders for a ride still in an *offer* state — a completed
    or cancelled ride returns 404, so the URL can never surface a past trip;
  - the image itself carries only minimised PII (rider first name + coarse
    area), enforced in ``utils/offer_card``.

The banner is rendered on demand here — never on the dispatch hot path — so it
adds zero latency to offer delivery (Performance SLA: offer→phone < 2s).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

try:
    from .. import db_supabase
    from ..utils.offer_card import coarsen_address, render_offer_card
    from ..utils.offer_card_token import OfferCardTokenError, verify_offer_card_token
    from ..utils.rate_limiter import default_limiter
except ImportError:
    import db_supabase
    from utils.offer_card import coarsen_address, render_offer_card
    from utils.offer_card_token import OfferCardTokenError, verify_offer_card_token
    from utils.rate_limiter import default_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/offer-cards", tags=["offer-cards"])

# A ride only has a banner while it's actually being offered. Outside these
# states the URL must not render — it would expose details of a trip the driver
# is no longer being offered.
_OFFER_STATES = {"searching", "driver_assigned", "driver_accepted"}

_NOT_FOUND = Response(status_code=404)


@router.get("/{ride_id}.png")
@default_limiter.limit("60/minute")
async def offer_card_png(
    request: Request,  # noqa: ARG001 — required by SlowAPI's limiter
    ride_id: str,
    t: str = Query(..., description="Signed offer-card token"),
) -> Response:
    """Render the branded fare banner for an in-flight ride offer."""
    return await _build_offer_card_response(ride_id, t)


async def _build_offer_card_response(ride_id: str, t: str) -> Response:
    """Verify the token, load the offer, render the banner. Testable without
    the SlowAPI/request plumbing the decorated route carries."""
    try:
        verify_offer_card_token(t, ride_id=ride_id)
    except OfferCardTokenError:
        # Flat 403 — never leak which check failed.
        return Response(status_code=403)

    try:
        ride = await db_supabase.get_ride(ride_id)
    except Exception:
        logger.error("offer-card: ride load failed for %s", ride_id, exc_info=True)
        return Response(status_code=503)

    if not ride or ride.get("status") not in _OFFER_STATES:
        return _NOT_FOUND

    rider = None
    rider_id = ride.get("rider_id")
    if rider_id:
        try:
            rider = await db_supabase.get_user_by_id(rider_id)
        except Exception:
            logger.warning("offer-card: rider load failed for %s", ride_id)

    def _num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    surge = _num(ride.get("surge_multiplier"))
    png = render_offer_card(
        fare=_num(ride.get("driver_earnings")) or 0.0,
        distance_km=_num(ride.get("distance_km")),
        duration_minutes=_num(ride.get("duration_minutes")),
        rider_first_name=(rider or {}).get("first_name") or (rider or {}).get("name"),
        rider_rating=_num((rider or {}).get("rating")),
        pickup_area=coarsen_address(ride.get("pickup_address")),
        dropoff_area=coarsen_address(ride.get("dropoff_address")),
        surge_multiplier=surge if (surge and surge > 1) else None,
    )

    if not png:
        # Renderer unavailable (e.g. Pillow missing) — let the notification
        # fall back to its text card instead of a broken image.
        return _NOT_FOUND

    return Response(
        content=png,
        media_type="image/png",
        headers={
            # Match the token lifetime; the offer is gone shortly after.
            "Cache-Control": "private, max-age=60",
        },
    )
