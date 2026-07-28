"""Domain-level Meta conversion events.

This layer knows about Spinr rows (users, rides, drivers) and turns them into
the event dictionary agreed with the Meta Ads analyst. ``utils/meta_capi``
below it knows only about HTTP and hashing.

Event ownership — which side fires what, and who mints the event_id:

    Event                 App SDK   CAPI   event_id minted by
    ────────────────────────────────────────────────────────────────
    CompleteRegistration     yes     yes   backend (returned to client)
    Purchase                 no      yes   backend
    FirstRide                no      yes   backend
    DriverApproved           no      yes   backend (persisted)
    DriverActivated          no      yes   backend (persisted)

Purchase is deliberately server-only. The client cannot see the authoritative
"money moved" moment: settlement has three payment methods, a background retry
sweep, and a guest-corporate auto-settle path that never touches a device. A
client-fired Purchase would either double-count against this one or miss rides
entirely, and there is no third option that stays correct under retry.

CompleteRegistration is the one event fired on both sides, so it is the one
that needs de-duplication. The backend mints the event_id during signup and
returns it in the auth response; the app fires its SDK event with that exact
id. Meta then collapses the pair on (event_name, event_id). If the client
never fires — offline, old build, SDK unavailable — the server copy still
lands, which is why the server side is not conditional on the client.

Advanced Matching is server-side only by design (see META_EVENTS.md). Hashed
email / phone / external_id are attached here, on every event, without
requiring App Tracking Transparency consent on iOS or changing the apps'
``NSPrivacyTracking: false`` declaration.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

try:
    from .. import db_supabase
    from ..utils.meta_capi import build_user_data, get_config, send_events, send_meta_event
except ImportError:  # pragma: no cover
    import db_supabase  # type: ignore
    from utils.meta_capi import build_user_data, get_config, send_events, send_meta_event  # type: ignore

logger = logging.getLogger(__name__)

CURRENCY = "CAD"

# Standard events (Meta-defined names — these must match verbatim or Events
# Manager treats them as custom events and the standard optimisation goals
# stop working).
EVENT_COMPLETE_REGISTRATION = "CompleteRegistration"
EVENT_PURCHASE = "Purchase"
# Custom events (Spinr-defined). A custom conversion in Events Manager is
# built on these names, so renaming one silently breaks the analyst's
# reporting — treat them as a published contract, not an implementation
# detail.
EVENT_FIRST_RIDE = "FirstRide"
EVENT_DRIVER_APPROVED = "DriverApproved"
EVENT_DRIVER_ACTIVATED = "DriverActivated"


def new_event_id() -> str:
    """Mint an event_id. Shared verbatim with the client for de-duplication."""
    return str(uuid.uuid4())


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _tracking_enabled(config: Any, dataset_id: str) -> bool:
    """Whether a send can actually reach Meta right now.

    Checked BEFORE any once-per-subject claim is taken. Order matters: the
    claims here (users.first_ride_completed_at, meta_capi_events.dedup_key)
    are permanent and single-use, so claiming while tracking is disabled would
    consume them against a send that never happened. Every rider who took
    their first ride before the Meta credentials were pasted into app_settings
    would be permanently ineligible for a FirstRide event, and every driver
    approved in that window would never fire DriverApproved — silently, with
    no row left to show it should have.

    So: no token configured ⇒ nothing is claimed and nothing is spent.
    """
    return bool(dataset_id and getattr(config, "access_token", ""))


def _money(value: Any) -> float:
    """Decimal-safe money → float for the JSON payload.

    Money arithmetic in this repo is Decimal-only (CLAUDE.md). Nothing is
    *computed* here — the charged amount arrives already settled — but the
    string round-trip via Decimal still avoids the float artefacts that would
    otherwise put `29.989999999999998` on a conversion value.
    """
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01")))


# ── Backend-only event idempotency ──────────────────────────────────────────


async def _claim_backend_event(
    dedup_key: str,
    event_name: str,
    dataset_id: str,
) -> Optional[str]:
    """Reserve an event_id for a backend-only event, or None to skip.

    Backend-only events (DriverApproved, DriverActivated) have no client
    counterpart to inherit an id from, so it is generated and persisted here
    BEFORE the send. Three outcomes:

      * no row yet      → insert, return the fresh id (send it)
      * row, not sent   → return the STORED id (retry — Meta de-duplicates the
                          retry against the original attempt)
      * row, sent       → return None (already counted, do not re-send)

    The UNIQUE constraint on dedup_key is what makes this safe under the
    concurrent replicas this backend runs on: two replicas both handling the
    same approval race to insert, one wins, the loser reads back the winner's
    event_id rather than minting a second one.
    """
    event_id = new_event_id()
    try:
        await db_supabase.insert_one(
            "meta_capi_events",
            {
                "dedup_key": dedup_key,
                "event_id": event_id,
                "event_name": event_name,
                "dataset_id": dataset_id,
                "status": "pending",
                "attempts": 0,
            },
        )
        return event_id
    except Exception as exc:
        # DuplicateRecordError (23505) is the expected, non-exceptional path:
        # a retry or a concurrent replica. Anything else is a real DB fault.
        if "duplicate" not in type(exc).__name__.lower() and "23505" not in str(exc):
            logger.error(
                "meta_conversions: could not claim %s (%s) — skipping send",
                event_name,
                dedup_key,
                exc_info=True,
            )
            return None

    try:
        rows = await db_supabase.get_rows("meta_capi_events", {"dedup_key": dedup_key}, limit=1)
    except Exception:
        logger.error(
            "meta_conversions: claim conflict on %s but re-read failed — skipping send",
            dedup_key,
            exc_info=True,
        )
        return None

    existing = rows[0] if rows else None
    if not existing:
        # Conflicted on insert but the row isn't readable. Refuse to mint a
        # second id — that would double-count in Meta, which is worse than a
        # missed conversion.
        logger.error("meta_conversions: claim conflict on %s but row not found — skipping send", dedup_key)
        return None
    if existing.get("status") == "sent":
        logger.debug("meta_conversions: %s already sent — skipping", dedup_key)
        return None
    return str(existing.get("event_id"))


async def _mark_backend_event(dedup_key: str, *, sent: bool, error: str = "") -> None:
    """Record the outcome of a backend-only send. Best-effort by definition."""
    update: Dict[str, Any] = {
        "status": "sent" if sent else "failed",
        "attempts": 1,
    }
    if sent:
        update["sent_at"] = datetime.now(timezone.utc).isoformat()
    elif error:
        update["last_error"] = error[:500]
    try:
        await db_supabase.update_one("meta_capi_events", {"dedup_key": dedup_key}, update)
    except Exception:
        logger.error("meta_conversions: failed to record outcome for %s", dedup_key, exc_info=True)


# ── Rider events ────────────────────────────────────────────────────────────


async def send_rider_registration(
    user: Dict[str, Any],
    *,
    event_id: str,
    registration_method: str = "phone",
    client_ip: Optional[str] = None,
    client_user_agent: Optional[str] = None,
) -> None:
    """CompleteRegistration for a newly-created rider account.

    ``event_id`` is minted by the caller (auth.py) and handed to the client in
    the same response, so both copies of this event carry it.
    """
    config = await get_config()
    dataset_id = config.dataset_for("rider")

    await send_meta_event(
        dataset_id=dataset_id,
        event_name=EVENT_COMPLETE_REGISTRATION,
        event_id=event_id,
        event_time=_now_ts(),
        user_data=build_user_data(
            email=user.get("email"),
            phone=user.get("phone"),
            user_id=user.get("id"),
            client_ip=client_ip,
            client_user_agent=client_user_agent,
        ),
        custom_data={
            "registration_method": registration_method,
            # Second safeguard against a driver application being reported as
            # a rider signup. The datasets already separate the two funnels;
            # this makes the distinction visible inside a single dataset's
            # reporting too.
            "content_category": "rider",
            "status": True,
        },
        action_source="app",
        access_token=config.access_token,
        test_event_code=config.test_event_code,
    )


async def send_ride_purchase(
    ride: Dict[str, Any],
    user: Dict[str, Any],
    charged_amount: Any,
) -> None:
    """Purchase for a completed paid ride, plus FirstRide on the user's first.

    ``charged_amount`` is what the rider actually paid — post-promo, tip
    included. It comes from the settlement result, not from a fare field, so
    it cannot drift from the real charge.

    The FirstRide gate is an atomic conditional UPDATE against
    users.first_ride_completed_at. Exactly one caller wins it, ever: a retry,
    a concurrent replica, a reinstalled app, and a second device all lose.
    """
    config = await get_config()
    dataset_id = config.dataset_for("rider")
    # Before the first-ride claim below — see _tracking_enabled.
    if not _tracking_enabled(config, dataset_id):
        logger.debug("meta_conversions: tracking disabled — not claiming first ride for this settlement")
        return

    rider_id = user.get("id") or ride.get("rider_id")
    value = _money(charged_amount)
    promo_code = (ride.get("promo_code") or "").strip()

    user_data = build_user_data(
        email=user.get("email"),
        phone=user.get("phone"),
        user_id=rider_id,
    )
    # promo_code is present on EVERY Purchase — empty string when none was
    # used. A custom conversion in Events Manager filtering promo_code =
    # SPINR75 only works if the property exists on the events it filters, so
    # it must not be omitted when blank.
    custom_data: Dict[str, Any] = {
        "value": value,
        "currency": CURRENCY,
        "promo_code": promo_code,
        "ride_id": ride.get("id"),
        "content_category": "ride",
    }

    event_time = _now_ts()
    events = [
        {
            "event_name": EVENT_PURCHASE,
            "event_time": event_time,
            "event_id": new_event_id(),
            "action_source": "app",
            "user_data": user_data,
            "custom_data": custom_data,
        }
    ]

    if await _claim_first_ride(rider_id):
        events.append(
            {
                "event_name": EVENT_FIRST_RIDE,
                "event_time": event_time,
                "event_id": new_event_id(),
                "action_source": "app",
                "user_data": user_data,
                "custom_data": {
                    "value": value,
                    "currency": CURRENCY,
                    "promo_code": promo_code,
                },
            }
        )

    await send_events(
        dataset_id=dataset_id,
        events=events,
        access_token=config.access_token,
        test_event_code=config.test_event_code,
    )


async def _claim_first_ride(rider_id: Optional[str]) -> bool:
    """True exactly once per user, on their first completed paid ride.

    ``{"first_ride_completed_at": None}`` maps to PostgREST ``is.null``, so
    the UPDATE only matches a user who has never converted. update_one returns
    None when zero rows matched — that's every subsequent call.

    A DB failure returns False (don't fire). Missing a conversion is
    recoverable; double-counting one corrupts campaign optimisation and cannot
    be walked back on Meta's side.

    KNOWN LIMITATION, accepted deliberately: the claim is taken before the
    send, so if the send then fails after its retries, that user's FirstRide
    is lost permanently — the column stays set and never claims again. The
    alternative (release the claim on send failure) is worse in two ways.
    First, a send that succeeded but whose response was lost would re-fire and
    double-count. Second, and more importantly, first_ride_completed_at is
    business truth ("this rider has completed a paid ride"), not tracking
    truth; releasing it on a Meta outage would redefine the column as "Meta
    acknowledged our event", which would quietly mislead every future reader
    of it. Purchase still fires for that ride either way, so the revenue
    conversion is not lost — only the first-ride segmentation.
    """
    if not rider_id:
        return False
    try:
        claimed = await db_supabase.update_one(
            "users",
            {"id": rider_id, "first_ride_completed_at": None},
            {"first_ride_completed_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        logger.error("meta_conversions: first-ride claim failed for user — not firing FirstRide", exc_info=True)
        return False
    return claimed is not None


# ── Driver events ───────────────────────────────────────────────────────────


async def send_driver_registration(
    user: Dict[str, Any],
    *,
    event_id: str,
    registration_method: str = "phone",
    client_ip: Optional[str] = None,
    client_user_agent: Optional[str] = None,
) -> None:
    """CompleteRegistration for a submitted driver application.

    Goes to the DRIVER dataset, so it can never blend into rider reporting.
    """
    config = await get_config()
    dataset_id = config.dataset_for("driver")

    await send_meta_event(
        dataset_id=dataset_id,
        event_name=EVENT_COMPLETE_REGISTRATION,
        event_id=event_id,
        event_time=_now_ts(),
        user_data=build_user_data(
            email=user.get("email"),
            phone=user.get("phone"),
            user_id=user.get("id"),
            client_ip=client_ip,
            client_user_agent=client_user_agent,
        ),
        custom_data={
            "content_category": "driver_application",
            "registration_method": registration_method,
            "status": True,
        },
        action_source="app",
        access_token=config.access_token,
        test_event_code=config.test_event_code,
    )


async def send_driver_approved(driver: Dict[str, Any], user: Dict[str, Any]) -> None:
    """DriverApproved — documents accepted by an admin. CAPI-only.

    Happens in the admin dashboard, never on the driver's device, so there is
    no client event and no shared id. ``action_source="system_generated"``
    tells Meta this conversion originated from a business workflow rather than
    a user action in the app.
    """
    driver_id = driver.get("id")
    if not driver_id:
        logger.error("meta_conversions: DriverApproved called with no driver id — skipping")
        return

    config = await get_config()
    dataset_id = config.dataset_for("driver")
    if not _tracking_enabled(config, dataset_id):
        logger.debug("meta_conversions: tracking disabled — not claiming DriverApproved")
        return

    dedup_key = f"{EVENT_DRIVER_APPROVED}:{driver_id}"
    event_id = await _claim_backend_event(dedup_key, EVENT_DRIVER_APPROVED, dataset_id)
    if not event_id:
        return

    sent = await send_meta_event(
        dataset_id=dataset_id,
        event_name=EVENT_DRIVER_APPROVED,
        event_id=event_id,
        event_time=_now_ts(),
        user_data=build_user_data(
            email=user.get("email") or driver.get("email"),
            phone=user.get("phone") or driver.get("phone"),
            user_id=user.get("id") or driver.get("user_id"),
        ),
        custom_data={"driver_id": driver_id},
        action_source="system_generated",
        access_token=config.access_token,
        test_event_code=config.test_event_code,
    )
    await _mark_backend_event(dedup_key, sent=sent)


async def send_driver_activated(
    driver: Dict[str, Any],
    user: Dict[str, Any],
    ride: Dict[str, Any],
) -> None:
    """DriverActivated — driver completed their first trip. CAPI-only.

    Idempotent on driver_id, so the caller does not have to work out whether
    this really was the first trip: a second call for the same driver finds a
    'sent' row and returns without sending.
    """
    driver_id = driver.get("id")
    if not driver_id:
        logger.error("meta_conversions: DriverActivated called with no driver id — skipping")
        return

    config = await get_config()
    dataset_id = config.dataset_for("driver")
    if not _tracking_enabled(config, dataset_id):
        logger.debug("meta_conversions: tracking disabled — not claiming DriverActivated")
        return

    dedup_key = f"{EVENT_DRIVER_ACTIVATED}:{driver_id}"
    event_id = await _claim_backend_event(dedup_key, EVENT_DRIVER_ACTIVATED, dataset_id)
    if not event_id:
        return

    # Driver-side value is the fare on the trip they completed. grand_total is
    # the rider-facing total; fall back to total_fare for legacy rides written
    # before the column existed (same fallback process_payment uses).
    fare = ride.get("grand_total")
    if fare is None:
        fare = ride.get("total_fare", 0)

    sent = await send_meta_event(
        dataset_id=dataset_id,
        event_name=EVENT_DRIVER_ACTIVATED,
        event_id=event_id,
        event_time=_now_ts(),
        user_data=build_user_data(
            email=user.get("email") or driver.get("email"),
            phone=user.get("phone") or driver.get("phone"),
            user_id=user.get("id") or driver.get("user_id"),
        ),
        custom_data={
            "driver_id": driver_id,
            "value": _money(fare),
            "currency": CURRENCY,
        },
        action_source="system_generated",
        access_token=config.access_token,
        test_event_code=config.test_event_code,
    )
    await _mark_backend_event(dedup_key, sent=sent)
