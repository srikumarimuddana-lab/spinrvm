"""Live Ride Activity dispatch — Phase 1 (storage + log-only).

After a driver accepts, the rider app registers a push token for the ride
(``ride_live_activities``). On each ride-state transition the backend builds the
shared *content-state* both surfaces render and would push an update per
platform (iOS Live Activity via APNs, Android ongoing notification via FCM).

Android (Phase 2) dispatches a **data-only FCM** message so the rider app's
Notifee handler builds/updates/cancels an ongoing notification. iOS stays
log-only until the Phase 3 ActivityKit APNs (``.p8``) path. On a terminal event
it marks the activity rows ended so a future replica stops dispatching.

Privacy: a Live Activity renders on a **locked** phone, so the content-state is
**area-only** — never an exact address, never coordinates, only a first name and
a vehicle label. This is stricter than the in-app rider view by design. Nothing
PII-bearing (full name, address, coordinates, push token) is ever logged.

See docs/live-ride-activity-plan.md §2–§4. Best-effort like ``_push_in_background``:
failures are logged (``error``) and never raised into the ride request path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - top-level execution fallback
    import db_supabase  # type: ignore

logger = logging.getLogger(__name__)

# Lifecycle events the ride transitions map to.
EVENT_START = "start"
EVENT_UPDATE = "update"
EVENT_END = "end"

_TERMINAL_STATUSES = frozenset({"completed", "cancelled"})


# Area-coarsening (city-only) logic lives in utils.pii.area_only — shared with
# the payment-ledger redaction path. Keep the ``_area_label`` name: callers and
# tests in this module predate the move.
try:
    from .pii import area_only as _area_label
except ImportError:  # pragma: no cover - top-level execution fallback
    from utils.pii import area_only as _area_label  # type: ignore


def _driver_attr(driver: Any, key: str) -> Any:
    """Read a field from a driver that may be a dict or a DriverPublicView model."""
    if driver is None:
        return None
    if isinstance(driver, dict):
        return driver.get(key)
    return getattr(driver, key, None)


def _first_name(name: Optional[str]) -> Optional[str]:
    """First name only — lock-screen minimisation (matches Uber/Lyft convention)."""
    if not name:
        return None
    return name.strip().split(" ")[0] or None


def _vehicle_label(driver: Any) -> Optional[str]:
    """Human "Silver Honda Civic · ABC 1234" so the rider can spot the car."""
    color = _driver_attr(driver, "vehicle_color")
    make = _driver_attr(driver, "vehicle_make")
    model = _driver_attr(driver, "vehicle_model")
    plate = _driver_attr(driver, "license_plate")
    desc = " ".join(str(p) for p in (color, make, model) if p).strip()
    if plate:
        return f"{desc} · {plate}".strip(" ·") or None
    return desc or None


def _headline(status: Optional[str], *, dropoff_area: Optional[str]) -> str:
    if status == "driver_accepted":
        return "Driver on the way"
    if status == "driver_arrived":
        return "Your driver has arrived"
    if status == "in_progress":
        return f"On your way to {dropoff_area}" if dropoff_area else "On your way"
    if status == "completed":
        return "Trip complete"
    if status == "cancelled":
        return "Ride cancelled"
    return "Ride update"


def build_content_state(
    ride: dict,
    *,
    driver: Any = None,
    eta_minutes: Optional[int] = None,
) -> dict:
    """The single struct both platforms render. Area-only, first-name-only — no
    exact addresses, coordinates, or full names (lock-screen safe)."""
    status = ride.get("status")
    pickup_area = _area_label(ride.get("pickup_address"))
    dropoff_area = _area_label(ride.get("dropoff_address"))
    # Prefer an explicitly-passed driver; fall back to a joined DriverPublicView.
    driver = driver if driver is not None else ride.get("driver")
    return {
        "status": status,
        "headline": _headline(status, dropoff_area=dropoff_area),
        "eta_minutes": eta_minutes,
        "driver_name": _first_name(_driver_attr(driver, "name")),
        "vehicle_label": _vehicle_label(driver),
        "pickup_area": pickup_area,
        "dropoff_area": dropoff_area,
        "ride_code": ride.get("code"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _active_activities(ride_id: str) -> list[dict]:
    """Registered, not-yet-ended live activities for a ride (one per platform).

    Selects id/platform/ended_at/rider_id/push_token. Android dispatch reuses
    the user's FCM token via send_push_notification(rider_id) and ignores the
    stored token; iOS reads push_token here for the ActivityKit APNs push. The
    token is used only to send — never logged.
    """
    rows = await db_supabase.get_rows(
        "ride_live_activities",
        {"ride_id": ride_id},
        columns="id,platform,ended_at,rider_id,push_token",
    )
    return [r for r in (rows or []) if not r.get("ended_at")]


def _fcm_data(content: dict, event: str, ride_id: str) -> dict:
    """Flatten the content-state into an FCM data payload (all string values,
    None fields omitted). The rider app's Notifee handler renders/updates the
    ongoing notification from this; ``event`` is start/update/end."""
    data = {"type": "live_activity", "event": event, "ride_id": str(ride_id)}
    for key in (
        "status",
        "headline",
        "eta_minutes",
        "driver_name",
        "vehicle_label",
        "pickup_area",
        "dropoff_area",
        "ride_code",
    ):
        val = content.get(key)
        if val is not None:
            data[key] = str(val)
    return data


async def _send_android(rider_id: Optional[str], content: dict, fcm_data: dict) -> None:
    """Data-only FCM to the rider so the Notifee handler builds/updates/cancels
    the ongoing notification. Self-contained best-effort — never raises."""
    if not rider_id:
        return
    try:
        try:
            from ..features import send_push_notification
        except ImportError:  # pragma: no cover - top-level execution fallback
            from features import send_push_notification  # type: ignore

        # features.py treats type=='live_activity' as data-only, so title/body
        # are ignored by FCM — Notifee renders the notification from the data.
        await send_push_notification(
            rider_id,
            content["headline"],
            content.get("dropoff_area") or "",
            data=fcm_data,
            priority="normal",
            target_app="rider",
        )
    except Exception:
        logger.error("live_activity android FCM send failed", exc_info=True)


def _live_metric(event: str, outcome: str) -> None:
    """Best-effort counter; a metrics hiccup must never break dispatch."""
    try:
        try:
            from .metrics import inc as _inc
        except ImportError:  # pragma: no cover - top-level execution fallback
            from utils.metrics import inc as _inc  # type: ignore
        _inc("spinr_rides_live_activity_apns_sent_total", labels={"event": event, "outcome": outcome})
    except Exception:
        logger.debug("live_activity metric emit failed", exc_info=True)


async def _send_ios(act: dict, content: dict, event: str) -> None:
    """ActivityKit APNs push (.p8 token auth, HTTP/2) so the rider's Voltra Live
    Activity updates/ends while the app is backgrounded/killed. On a dead token
    (APNs 410 / BadDeviceToken) the activity row is ended so no replica retries
    it. Best-effort — never raises."""
    push_token = act.get("push_token")
    if not push_token:
        return
    try:
        try:
            from .apns_client import send_apns_live_activity
        except ImportError:  # pragma: no cover - top-level execution fallback
            from utils.apns_client import send_apns_live_activity  # type: ignore

        ok, dead = await send_apns_live_activity(push_token, content, event)
        _live_metric(event, "success" if ok else "failed")
        if dead and act.get("id"):
            now = datetime.now(timezone.utc).isoformat()
            await db_supabase.update_one(
                "ride_live_activities", {"id": act["id"]}, {"ended_at": now, "updated_at": now}
            )
    except Exception:
        logger.error("live_activity ios APNs send failed", exc_info=True)


async def send_live_activity_update(
    ride: dict,
    event: str,
    *,
    driver: Any = None,
    eta_minutes: Optional[int] = None,
) -> None:
    """Build the content-state and dispatch a live-activity update per platform.

    Android (Phase 2): a data-only FCM message drives the Notifee ongoing
    notification. iOS (Phase 3): a direct ActivityKit APNs push drives the Voltra
    Live Activity. On a terminal event the activity rows are marked ended. The
    end push is sent BEFORE the rows are marked ended (the dispatch loop runs
    first). Best-effort: never raises into the ride request path (mirrors
    ``routes.rides._push_in_background``).
    """
    try:
        ride_id = ride.get("id")
        if not ride_id:
            return
        activities = await _active_activities(ride_id)
        if not activities:
            # No rider registered a live activity for this ride — nothing to do.
            return

        content = build_content_state(ride, driver=driver, eta_minutes=eta_minutes)
        fcm_data = _fcm_data(content, event, ride_id)
        for act in activities:
            platform = act.get("platform")
            # PII-safe log: no driver name, address, coordinates, or token.
            logger.info(
                "live_activity dispatch",
                extra={
                    "event": event,
                    "platform": platform,
                    "ride_id": ride_id,
                    "ride_status": content["status"],
                    "headline": content["headline"],
                    "eta_minutes": content["eta_minutes"],
                    "pickup_area": content["pickup_area"],
                    "dropoff_area": content["dropoff_area"],
                    "has_driver": content["driver_name"] is not None,
                },
            )
            if platform == "android":
                await _send_android(act.get("rider_id"), content, fcm_data)
            elif platform == "ios":
                await _send_ios(act, content, event)

        if event == EVENT_END or ride.get("status") in _TERMINAL_STATUSES:
            now = datetime.now(timezone.utc).isoformat()
            for act in activities:
                await db_supabase.update_one(
                    "ride_live_activities",
                    {"id": act["id"]},
                    {"ended_at": now, "updated_at": now},
                )
    except Exception:
        logger.error("send_live_activity_update failed", exc_info=True)
