"""Canonical ride status enum.

Encodes the state machine documented in CLAUDE.md. Because RideStatus
inherits from str, instances compare equal to their string values and
can be used directly in Supabase filter dicts and JSON serialization
without any conversion.

Valid transitions (simplified):
    scheduled → searching → driver_assigned → driver_accepted
        → driver_arrived → in_progress → completed
    searching|driver_assigned|driver_accepted|driver_arrived → cancelled (pre-trip)
"""

from enum import Enum


class RideStatus(str, Enum):
    SCHEDULED = "scheduled"
    SEARCHING = "searching"
    DRIVER_ASSIGNED = "driver_assigned"
    DRIVER_ACCEPTED = "driver_accepted"
    DRIVER_ARRIVED = "driver_arrived"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    # Convenience sets used by state-machine guards.
    @classmethod
    def active_statuses(cls) -> frozenset["RideStatus"]:
        return frozenset(
            {
                cls.SEARCHING,
                cls.DRIVER_ASSIGNED,
                cls.DRIVER_ACCEPTED,
                cls.DRIVER_ARRIVED,
                cls.IN_PROGRESS,
            }
        )

    @classmethod
    def terminal_statuses(cls) -> frozenset["RideStatus"]:
        return frozenset({cls.COMPLETED, cls.CANCELLED})
