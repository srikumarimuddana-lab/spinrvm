"""Quest progress tracker — updates driver quest progress on ride completion.

Called from the ride completion flow to automatically advance quest progress
for any active quests the driver has joined.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

try:
    from ..db import db
    from .datetime_utils import parse_iso_utc
except ImportError:
    from db import db
    from utils.datetime_utils import parse_iso_utc

logger = logging.getLogger(__name__)

# Saskatchewan is UTC-6 year-round (no DST). Peak-hour windows are advertised in
# the driver's local time, so completions must be evaluated in the service-area
# timezone — not UTC, which would shift the windows by six hours.
_DEFAULT_TZ = "America/Regina"

# Sentinel distinguishing "local hour not yet computed" from "no timestamp" (None).
_UNSET = object()


async def _ride_local_completion_hour(ride: dict):
    """Hour-of-day (0-23) the ride completed, in its service-area local timezone.

    Returns None when no usable completion timestamp is present. Falls back to
    America/Regina when the ride has no service area or the area has no timezone.
    """
    completed_at = (
        ride.get("ride_completed_at")
        or ride.get("completed_at")
        or ride.get("updated_at")
    )
    if not completed_at:
        return None
    if isinstance(completed_at, str):
        completed_at = parse_iso_utc(completed_at)
    if completed_at is None:
        return None
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)

    tz_name = _DEFAULT_TZ
    area_id = ride.get("service_area_id")
    if area_id:
        try:
            area = await db.find_one("service_areas", {"id": area_id})
            if area and area.get("timezone"):
                tz_name = area["timezone"]
        except Exception as e:
            logger.warning(f"peak_rides: could not resolve timezone for area {area_id}: {e}")
    try:
        return completed_at.astimezone(ZoneInfo(tz_name)).hour
    except Exception:
        return completed_at.astimezone(ZoneInfo(_DEFAULT_TZ)).hour


async def update_quest_progress_on_ride_complete(driver_id: str, ride: dict):
    """Update all active quest progress for a driver after a ride completes.

    Handles quest types: ride_count, earnings_target, peak_rides.
    """
    try:
        progress_rows = await db.get_rows(
            "quest_progress",
            {"driver_id": driver_id, "status": "active"},
            limit=20,
        )
    except Exception as e:
        logger.error(f"Failed to fetch quest progress for driver {driver_id}: {e}")
        return

    # Resolved lazily on the first peak_rides quest and reused for the rest.
    local_hour = _UNSET

    for progress in progress_rows:
        try:
            quest = await db.find_one("quests", {"id": progress["quest_id"]})
            if not quest or not quest.get("is_active"):
                continue

            # Check if quest has expired
            now = datetime.now(timezone.utc).isoformat()
            if quest.get("end_date", "") < now:
                await db.update_one(
                    "quest_progress",
                    {"id": progress["id"]},
                    {"$set": {"status": "expired", "updated_at": now}},
                )
                continue

            new_value = progress["current_value"]

            if quest["type"] == "ride_count":
                new_value += 1

            elif quest["type"] == "earnings_target":
                earnings = ride.get("driver_earnings", 0) or 0
                new_value += earnings

            elif quest["type"] == "peak_rides":
                # Count rides completed during local peak hours (7-9 AM, 5-8 PM).
                if local_hour is _UNSET:
                    local_hour = await _ride_local_completion_hour(ride)
                if local_hour is not None and (7 <= local_hour <= 9 or 17 <= local_hour <= 20):
                    new_value += 1

            # Check completion
            target = quest["target_value"]
            update_data = {
                "current_value": new_value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if new_value >= target:
                update_data["status"] = "completed"
                update_data["completed_at"] = datetime.now(timezone.utc).isoformat()
                logger.info(f"Driver {driver_id} completed quest {quest['id']} ({quest['title']})")

            await db.update_one(
                "quest_progress",
                {"id": progress["id"]},
                {"$set": update_data},
            )

        except Exception as e:
            logger.error(f"Failed to update quest {progress.get('quest_id')} for driver {driver_id}: {e}")
