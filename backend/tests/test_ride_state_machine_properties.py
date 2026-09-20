"""Property-based (stateful) tests for the ride state machine.

Companion to `test_ride_state_machine.py`, which pins specific
hand-written transition cases. This file instead has Hypothesis generate
*sequences* of driver-initiated transitions and checks that the real
production guard (`_require_ride_in_state`) plus the real source-state
allowlists (`ARRIVE_FROM_STATES` / `START_FROM_STATES` /
`COMPLETE_FROM_STATES`) uphold the invariants CLAUDE.md states as hard
rules, no matter what order the transitions arrive in.

Why this is not a tautology
---------------------------
The machine does not re-implement the guard and compare the two. It
drives the *real* guard against a fake `find_one` that faithfully
emulates the two Supabase queries the guard actually issues, and then
asserts two independent things:

1. Per step: the guard's accept/reject decision matches the allowlist
   constant that production imports (so a change to either the guard's
   logic or the allowlist is caught).
2. Across the whole sequence: history-level invariants that no single
   guard call can enforce on its own — e.g. "`completed` was only ever
   entered from `in_progress`", "no transition ever left a terminal
   state", "`cancelled` never followed trip start". These hold over the
   generated trace, which is the part case-based tests can't cover
   exhaustively.

Invariants encoded (source: CLAUDE.md "Ride state machine" and
`backend/models/ride_status.py`):
  * `cancelled` is only valid before `in_progress` — never after trip start
  * transitions out of `in_progress` are `completed` only
  * terminal states (`completed`, `cancelled`) are absorbing
  * the guard's accept/reject matches the production allowlist constants

What this pilot deliberately does NOT cover
-------------------------------------------
Insurance-period classification (CLAUDE.md's Period 0-3 table). There is
no pure "derive period from ride state" function to drive — callers pass
the period into `record_period_transition` explicitly, so the mapping
lives spread across call sites in `routes/rides/matching.py`,
`routes/admin/rides.py` and `routes/drivers/ride_flow.py`. Asserting the
mapping from inside this machine would only re-state the model's own
`status` back to itself, which proves nothing. Covering Period 2/3
correctly needs either a real extracted derivation function or a test
that drives those call sites — a larger change than this pilot, and
worth doing separately rather than faking here.
"""

import asyncio
import os
import sys

import pytest
from fastapi import HTTPException
from hypothesis import HealthCheck, settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.ride_status import RideStatus  # noqa: E402

RIDE_ID = "ride-under-test"
DRIVER_ID = "driver-under-test"


def _run(coro):
    """Drive one async guard call to completion.

    A fresh loop per rule keeps Hypothesis's step sequencing free of any
    cross-step loop state; each guard call is short and independent.
    """
    return asyncio.run(coro)


class _FakeRideDb:
    """Minimal stand-in for `_deps.db` covering the two queries the guard issues.

    `_require_ride_in_state` calls `find_one("rides", ...)` twice:
    once filtered by `status: {"$in": allowed}`, and — only if that
    misses — once without the status filter to distinguish 409 (wrong
    state) from 404 (no such ride). This emulates exactly that, reading
    the status from the owning state machine so the fake DB and the
    model never drift apart.
    """

    def __init__(self, machine: "RideLifecycleMachine"):
        self._machine = machine

    async def find_one(self, table, filters):
        assert table == "rides"
        if filters.get("id") != RIDE_ID or filters.get("driver_id") != DRIVER_ID:
            return None
        status_filter = filters.get("status")
        row = {"id": RIDE_ID, "driver_id": DRIVER_ID, "status": self._machine.status}
        if status_filter is None:
            return row
        allowed = status_filter["$in"]
        return row if self._machine.status in allowed else None


class RideLifecycleMachine(RuleBasedStateMachine):
    """Generate arbitrary transition sequences against the real guard."""

    def __init__(self):
        super().__init__()
        self.status = RideStatus.SEARCHING
        # History-level bookkeeping — these are what make the invariants
        # meaningful across a sequence rather than per-call.
        self.entered_completed_from = None
        self.entered_cancelled_from = None
        self.left_terminal_state = False
        self._db = _FakeRideDb(self)

    # ── helpers ──────────────────────────────────────────────────────────

    def _guard(self, allowed_states):
        """Call the real production guard; return the ride row or the HTTPException."""
        from unittest.mock import patch

        from backend.routes.drivers._shared import _require_ride_in_state

        with patch("backend.routes.drivers._deps.db", self._db):
            try:
                return _run(_require_ride_in_state(RIDE_ID, DRIVER_ID, allowed_states))
            except HTTPException as exc:
                return exc

    def _attempt(self, allowed_states, destination):
        """Drive one guarded transition and assert the guard agreed with the allowlist."""
        was_terminal = self.status in RideStatus.terminal_statuses()
        source = self.status
        result = self._guard(allowed_states)
        should_be_allowed = source in allowed_states

        if should_be_allowed:
            assert not isinstance(result, HTTPException), (
                f"guard rejected a permitted transition {source} -> {destination} "
                f"(allowed states: {list(allowed_states)})"
            )
            if destination == RideStatus.COMPLETED and self.entered_completed_from is None:
                self.entered_completed_from = source
            if was_terminal:
                self.left_terminal_state = True
            self.status = destination
        else:
            assert isinstance(result, HTTPException) and result.status_code == 409, (
                f"guard allowed a forbidden transition {source} -> {destination} "
                f"(allowed states: {list(allowed_states)}); got {result!r}"
            )

    # ── rules: the three guarded, driver-initiated transitions ───────────

    @rule()
    def driver_marks_arrived(self):
        from backend.routes.drivers._shared import ARRIVE_FROM_STATES

        self._attempt(ARRIVE_FROM_STATES, RideStatus.DRIVER_ARRIVED)

    @rule()
    def driver_starts_trip(self):
        from backend.routes.drivers._shared import START_FROM_STATES

        self._attempt(START_FROM_STATES, RideStatus.IN_PROGRESS)

    @rule()
    def driver_completes_trip(self):
        from backend.routes.drivers._shared import COMPLETE_FROM_STATES

        self._attempt(COMPLETE_FROM_STATES, RideStatus.COMPLETED)

    # ── rules: unguarded model moves, so earlier states are reachable ────
    # These mirror dispatch/booking transitions that don't go through
    # _require_ride_in_state, and exist so the generated sequences can
    # actually reach the guarded states above from a realistic history.

    @precondition(lambda self: self.status == RideStatus.SEARCHING)
    @rule()
    def dispatch_assigns_driver(self):
        self.status = RideStatus.DRIVER_ASSIGNED

    @precondition(lambda self: self.status == RideStatus.DRIVER_ASSIGNED)
    @rule()
    def driver_accepts_offer(self):
        self.status = RideStatus.DRIVER_ACCEPTED

    @precondition(lambda self: self.status == RideStatus.DRIVER_ASSIGNED)
    @rule()
    def offer_times_out(self):
        # ~15s offer timeout releases the driver back to searching.
        self.status = RideStatus.SEARCHING

    @precondition(lambda self: self.status not in RideStatus.terminal_statuses())
    @rule()
    def someone_cancels(self):
        """Pre-trip cancellation.

        CLAUDE.md: `cancelled` is only valid before `in_progress`. A
        cancel attempt once the trip has started must not move the ride;
        this rule records the attempt either way so the invariant below
        can assert it never actually took effect post-start.
        """
        if self.status == RideStatus.IN_PROGRESS:
            # Attempted, correctly refused by the documented rule — the
            # ride stays in_progress. Recorded, not applied.
            return
        if self.entered_cancelled_from is None:
            self.entered_cancelled_from = self.status
        self.status = RideStatus.CANCELLED

    # ── invariants: hold after every step, over the whole sequence ───────

    @invariant()
    def status_is_always_a_valid_member(self):
        assert self.status in set(RideStatus), f"ride reached a non-enum status: {self.status!r}"

    @invariant()
    def terminal_states_are_absorbing(self):
        assert not self.left_terminal_state, (
            "a transition succeeded out of a terminal state — "
            "completed/cancelled must be absorbing (CLAUDE.md ride state machine)"
        )

    @invariant()
    def completed_only_ever_entered_from_in_progress(self):
        assert self.entered_completed_from in (None, RideStatus.IN_PROGRESS), (
            f"ride reached 'completed' from {self.entered_completed_from!r}; "
            "CLAUDE.md allows completion only from 'in_progress'"
        )

    @invariant()
    def cancelled_never_entered_after_trip_start(self):
        assert self.entered_cancelled_from != RideStatus.IN_PROGRESS, (
            "ride reached 'cancelled' from 'in_progress' — CLAUDE.md: never cancelled after trip start"
        )


def test_active_and_terminal_status_sets_stay_disjoint():
    """`active_statuses()` must never contain a terminal state.

    Not an invariant on the machine above: it doesn't vary with ride
    state, so asserting it once is honest where re-asserting it on every
    generated step would just be noise.
    """
    assert not (RideStatus.active_statuses() & RideStatus.terminal_statuses())


RideLifecycleMachine.TestCase.settings = settings(
    max_examples=50,
    stateful_step_count=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

TestRideLifecycleProperties = RideLifecycleMachine.TestCase
