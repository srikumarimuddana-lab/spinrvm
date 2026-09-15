"""Validated service-area scheduling controls and booked-pickup timing guards."""
from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field

try:
    from .datetime_utils import parse_iso_utc
except ImportError:
    from utils.datetime_utils import parse_iso_utc


class ScheduledRideConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    dispatch_lead_minutes: int = Field(10, ge=0, le=30)
    driver_reminder_minutes: int = Field(10, ge=1, le=60)
    rider_reminder_minutes: int = Field(10, ge=1, le=60)


def pickup_wait_start(ride, arrived_at):
    pickup = parse_iso_utc(ride.get("scheduled_time")) if ride.get("is_scheduled") else None
    return max(arrived_at, pickup) if pickup else arrived_at


def scheduled_search_deadline(ride, grace_seconds=300):
    pickup = parse_iso_utc(ride.get("scheduled_time")) if ride.get("is_scheduled") else None
    if not pickup:
        return None
    requested = parse_iso_utc(ride.get("ride_requested_at")) or pickup
    return max(pickup, requested) + timedelta(seconds=grace_seconds)
