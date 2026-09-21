"""Completeness and length guards for user-facing ride-state copy.

The bug this file exists to prevent: a phrase map hand-keyed by raw status
strings silently degrades to the generic branch when a state is added to the
state machine — no type error, no test failure, just a vaguer message. And a
phrase that is individually reasonable can still push the caller's full
sentence past the client's 140-character toast clamp, which truncates the
actionable half away.
"""

import pytest

from backend.models.ride_status import RideStatus
from backend.utils.ride_state_copy import (
    _DRIVER_PHRASE,
    _RIDER_PHRASE,
    driver_phrase,
    rider_phrase,
    unavailable_message,
)

# Mirror of shared/utils/toastMessage.ts::TOAST_MESSAGE_MAX. The apps clamp at
# this length, cutting at a word boundary and appending an ellipsis, so a
# longer message loses its closing instruction.
TOAST_MESSAGE_MAX = 140


@pytest.mark.unit
@pytest.mark.parametrize("table,name", [(_DRIVER_PHRASE, "driver"), (_RIDER_PHRASE, "rider")])
def test_every_ride_status_has_a_phrase(table, name):
    missing = {s.value for s in RideStatus} - {s.value for s in table}
    assert not missing, f"{name} phrase map is missing: {sorted(missing)}"


@pytest.mark.unit
@pytest.mark.parametrize("status", list(RideStatus))
def test_messages_fit_the_toast_budget(status):
    for fn in (driver_phrase, rider_phrase):
        message = unavailable_message(fn(status))
        assert len(message) <= TOAST_MESSAGE_MAX, f"{status.value}: {len(message)} chars — {message}"


@pytest.mark.unit
@pytest.mark.parametrize("status", list(RideStatus))
def test_no_message_leaks_the_raw_state_name(status):
    """The whole point: the internal value must not reach the user."""
    for fn in (driver_phrase, rider_phrase):
        assert status.value not in unavailable_message(fn(status))


@pytest.mark.unit
def test_unknown_status_falls_back_and_logs_loudly(caplog):
    """CLAUDE.md: an out-of-contract ride.status is a contract violation and
    must surface loudly. 4xx detail is never logged by the error handler, so
    this log line is the only place the offending value is recoverable."""
    with caplog.at_level("ERROR"):
        assert driver_phrase("not_a_real_state") is None
    assert "not_a_real_state" in caplog.text
    assert unavailable_message(None) == (
        "That isn't available for this ride right now. Refresh to see its latest status."
    )
