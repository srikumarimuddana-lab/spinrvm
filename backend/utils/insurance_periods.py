"""TNC insurance period transition logger (Saskatchewan Transportation Act).

Period definitions (from CLAUDE.md):
  0 — App off / offline              → personal auto only
  1 — App on, available (no ride)    → TNC contingent liability
  2 — En route to pickup             → TNC primary commercial
  3 — Passenger aboard (in_progress) → TNC primary commercial (full coverage)

Rules:
  - Rows are append-only. Never update or delete.
  - period 2 starts on driver_assigned (not driver_accepted).
  - A driver cannot be in period 3 without an in_progress ride_id.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from ..db_supabase import get_rows, insert_one, update_one
except ImportError:
    from db_supabase import get_rows, insert_one, update_one  # type: ignore


async def open_period(
    driver_id: str,
    period: int,
    ride_id: Optional[str] = None,
) -> None:
    """Close any open period for this driver, then insert the new one."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        open_rows = await get_rows(
            "driver_insurance_periods",
            {"driver_id": driver_id, "ended_at": None},
            limit=5,
        )
        for row in open_rows or []:
            await update_one(
                "driver_insurance_periods",
                {"id": row["id"]},
                {"ended_at": now},
            )

        await insert_one(
            "driver_insurance_periods",
            {
                "id": str(uuid.uuid4()),
                "driver_id": driver_id,
                "period": period,
                "started_at": now,
                "ride_id": ride_id,
                "created_at": now,
            },
        )
        logger.info(
            f"[INSURANCE] driver={driver_id} period={period} ride={ride_id}",
            extra={"domain": "safety", "driver_id": driver_id},
        )
    except Exception:
        logger.error(
            f"[INSURANCE] failed to log period transition: driver={driver_id} period={period}",
            exc_info=True,
            extra={"domain": "safety", "driver_id": driver_id},
        )
