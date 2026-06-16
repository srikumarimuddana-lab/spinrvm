"""Record before/after changes to driver vehicle/identity fields.

Driver vehicle/plate edits (self-service or admin) are written in place on the
drivers table; this appends an audit trail to driver_vehicle_history so the
vehicle in effect at any time can be reconstructed for SGI/insurance audits.

Best-effort: a history-write failure must never block the underlying update.
"""

import logging

logger = logging.getLogger(__name__)

# Fields whose changes we track. Keep in sync with the driver-editable vehicle
# fields in routes/drivers.py and routes/admin/drivers.py.
TRACKED_FIELDS = (
    "vehicle_make",
    "vehicle_model",
    "vehicle_color",
    "vehicle_year",
    "vehicle_vin",
    "license_plate",
    "vehicle_type_id",
)


def _norm(v) -> str:
    return "" if v is None else str(v)


async def record_vehicle_changes(
    driver_id: str,
    before: dict,
    after: dict,
    *,
    changed_by_user_id=None,
    role: str = "system",
) -> int:
    """Append one history row per changed tracked field. Returns rows written.

    Args:
        driver_id: the drivers.id being modified.
        before: the driver row (or dict) before the update.
        after: the proposed changes (only changed keys need be present).
        changed_by_user_id: actor user id (driver or admin).
        role: 'driver' | 'admin' | 'system'.
    """
    if not driver_id:
        return 0
    try:
        try:
            from .. import db_supabase
        except ImportError:
            import db_supabase  # type: ignore

        written = 0
        for field in TRACKED_FIELDS:
            if field not in after:
                continue
            old_v = _norm((before or {}).get(field))
            new_v = _norm(after.get(field))
            if old_v == new_v:
                continue
            await db_supabase.insert_one(
                "driver_vehicle_history",
                {
                    "driver_id": driver_id,
                    "changed_by_user_id": changed_by_user_id,
                    "changed_by_role": role if role in ("driver", "admin", "system") else "system",
                    "field": field,
                    "old_value": old_v or None,
                    "new_value": new_v or None,
                },
            )
            written += 1
        return written
    except Exception:
        logger.error("[vehicle_history] failed to record changes for driver %s", driver_id, exc_info=True)
        return 0
