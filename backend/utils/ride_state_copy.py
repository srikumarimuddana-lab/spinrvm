"""User-facing phrasing for ride states, for riders and for drivers.

Why this module exists
----------------------
Ride guards used to interpolate the state machine's own vocabulary straight
into a 4xx the app prints verbatim, so a driver mid-shift read::

    Ride is in status 'driver_arrived'; cannot perform this action from that
    state (allowed: ['driver_accepted']).

The first pass at fixing that added a phrase map inside
``routes/drivers/_shared.py`` only, and the rider twin in
``routes/rides/_shared.py`` — the same sentence, on the rider surface — was
left untouched for a full review cycle. That is the fork failure CLAUDE.md's
gate 10 warns about, so the vocabulary lives in exactly one module now.

The two audiences need different words for the same state: ``driver_accepted``
is "you haven't marked yourself as arrived yet" to the driver and "your driver
is on the way" to the rider. Hence two maps, one source file, one completeness
test (``backend/tests/test_ride_state_copy.py``) that fails if either map
misses a ``RideStatus`` member.

Both phrases are written to slot into "This ride is {phrase}, so ..." and are
kept short on purpose: the caller's full sentence has to fit the client's
140-character toast clamp (``shared/utils/toastMessage.ts``).
"""

import logging

try:
    from ..models.ride_status import RideStatus
except ImportError:  # pragma: no cover - direct-module execution path
    from models.ride_status import RideStatus  # type: ignore

logger = logging.getLogger(__name__)

# Driver's point of view — every caller of the driver guards is a driver action.
_DRIVER_PHRASE: dict[RideStatus, str] = {
    RideStatus.SCHEDULED: "scheduled for later",
    RideStatus.SEARCHING: "still looking for a driver",
    RideStatus.DRIVER_ASSIGNED: "still waiting for you to accept",
    RideStatus.DRIVER_ACCEPTED: "accepted but not marked arrived",
    RideStatus.DRIVER_ARRIVED: "waiting for you to start the trip",
    RideStatus.IN_PROGRESS: "already in progress",
    RideStatus.COMPLETED: "already finished",
    RideStatus.CANCELLED: "cancelled",
}

# Rider's point of view — same states, the rider's half of the story.
# Wording reconciled with the parallel fix in commit 06abbd8, which landed a
# local copy of this table on the rider guard; test_ride_state_machine.py's
# TestCancelStateGuardRider pins "already in progress" and "already finished".
_RIDER_PHRASE: dict[RideStatus, str] = {
    RideStatus.SCHEDULED: "scheduled for later",
    RideStatus.SEARCHING: "still looking for a driver",
    RideStatus.DRIVER_ASSIGNED: "matched with a driver, but not yet accepted",
    RideStatus.DRIVER_ACCEPTED: "on its way — your driver has accepted",
    RideStatus.DRIVER_ARRIVED: "waiting for you at pickup",
    RideStatus.IN_PROGRESS: "already in progress",
    RideStatus.COMPLETED: "already finished",
    RideStatus.CANCELLED: "already cancelled",
}


def _phrase(table: dict[RideStatus, str], status: object, audience: str) -> str | None:
    """Look up *status* in *table*, logging loudly when it isn't a known state.

    CLAUDE.md: "When writing code that reads ``ride.status``, treat any value
    not in the set above as a contract violation — surface loudly." The old
    message at least carried the offending value in the response body; the
    phrase-map version cannot, and ``http_exception_handler`` deliberately
    never logs un-redacted 4xx detail, so without this the value would be
    unrecoverable from both the response and the logs.
    """
    try:
        return table[RideStatus(status)]
    except (ValueError, KeyError):
        logger.error(
            "Ride status outside the state machine contract: %r (audience=%s)",
            status,
            audience,
            extra={"domain": "rides", "surface": "backend", "ride_status": str(status)},
        )
        return None


def driver_phrase(status: object) -> str | None:
    """Driver-facing phrase for *status*, or None if it isn't a known state."""
    return _phrase(_DRIVER_PHRASE, status, "driver")


def rider_phrase(status: object) -> str | None:
    """Rider-facing phrase for *status*, or None if it isn't a known state."""
    return _phrase(_RIDER_PHRASE, status, "rider")


def unavailable_message(phrase: str | None) -> str:
    """The full sentence a wrong-state guard should return.

    Kept here so the driver and rider guards cannot drift in wording, and so
    the length is checked in one place by the module's test.
    """
    if phrase:
        return f"This ride is {phrase}, so that isn't available right now. Refresh to see its latest status."
    return "That isn't available for this ride right now. Refresh to see its latest status."
