"""Quest progress tracker — updates driver quest progress on ride completion.

Called from the ride completion flow to automatically advance quest progress
for any active quests the driver has joined.
"""

import logging
from datetime import datetime

try:
    from ..db import db
except ImportError:
    from db import db

logger = logging.getLogger(__name__)


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

    for progress in progress_rows:
        try:
            quest = await db.find_one("quests", {"id": progress["quest_id"]})
            if not quest or not quest.get("is_active"):
                continue

            # Check if quest has expired
            now = datetime.utcnow().isoformat()
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
                # Count rides during peak hours (7-9 AM, 5-8 PM)
                completed_at = ride.get("completed_at") or ride.get("updated_at")
                if completed_at:
                    if isinstance(completed_at, str):
                        try:
                            completed_at = datetime.fromisoformat(
                                completed_at.replace("Z", "+00:00").replace("+00:00", "")
                            )
                        except ValueError:
                            completed_at = datetime.utcnow()
                    hour = completed_at.hour
                    if 7 <= hour <= 9 or 17 <= hour <= 20:
                        new_value += 1

            # Check completion
            target = quest["target_value"]
            update_data = {
                "current_value": new_value,
                "updated_at": datetime.utcnow().isoformat(),
            }
            if new_value >= target:
                update_data["status"] = "completed"
                update_data["completed_at"] = datetime.utcnow().isoformat()
                logger.info(f"Driver {driver_id} completed quest {quest['id']} ({quest['title']})")

            await db.update_one(
                "quest_progress",
                {"id": progress["id"]},
                {"$set": update_data},
            )

        except Exception as e:
            logger.error(f"Failed to update quest {progress.get('quest_id')} for driver {driver_id}: {e}")
