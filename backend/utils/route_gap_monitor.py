"""GPS capture-gap assessment for active trips.

The monitor deliberately works with timestamps only. It can identify that a
ride stopped reporting location without retaining, logging, or emitting a raw
coordinate outside the durable breadcrumb store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

GapState = Literal["healthy", "gap", "unknown"]


@dataclass(frozen=True)
class GapDecision:
    """Timestamp-only result of one active-trip GPS health assessment."""

    state: GapState
    gap_started_at: Optional[datetime]
    gap_seconds: int


def assess_location_gap(
    *,
    now: datetime,
    trip_started_at: Optional[datetime],
    last_captured_at: Optional[datetime],
    threshold_seconds: int,
) -> GapDecision:
    """Classify the latest location-reporting interval for one in-progress ride.

    When there is no accepted breadcrumb yet, the ride start time is the
    truthful beginning of the missing interval. Clock skew cannot produce a
    negative duration, and a non-positive threshold is clamped to one second
    so a bad setting never disables monitoring silently.
    """
    threshold = max(1, int(threshold_seconds))
    gap_started_at = last_captured_at or trip_started_at
    if gap_started_at is None:
        return GapDecision(state="unknown", gap_started_at=None, gap_seconds=0)

    gap_seconds = max(0, int((now - gap_started_at).total_seconds()))
    return GapDecision(
        state="gap" if gap_seconds >= threshold else "healthy",
        gap_started_at=gap_started_at,
        gap_seconds=gap_seconds,
    )
