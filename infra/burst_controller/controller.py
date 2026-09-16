"""Scale-out coordinator: shared journal, anchor lock, target fencing, no retries."""

from contextlib import contextmanager
import logging
import math
import time
from policy import Decision, Machine, Policy

logger = logging.getLogger(__name__)


def inventory(raw):
    machines = [
        Machine(
            m["id"],
            m["config"]["metadata"]["fly_process_group"],
            m["state"],
            m["region"],
        )
        for m in raw
    ]
    if (
        len(machines) != 8
        or len({m.id for m in machines}) != 8
        or sum(m.group == "app" for m in machines) != 2
        or sum(m.group == "burst" for m in machines) != 6
        or any(m.region != "yyz" for m in machines)
    ):
        raise ValueError("Unexpected fleet inventory")
    return machines


def fingerprint(raw):
    return sorted(
        (
            m["id"],
            m["region"],
            m["config"]["metadata"]["fly_process_group"],
            m["config"].get("guest", {}).get("memory_mb"),
            m["config"].get("guest", {}).get("cpus"),
            m["state"],
            m.get("instance_id"),
            m.get("private_ip"),
            m["config"].get("image"),
            m["config"]["metadata"].get("fly_release_id"),
        )
        for m in raw
    )


def checked_journal(state, now):
    if not isinstance(state, dict) or not isinstance(state.get("attempts", []), list):
        raise ValueError("Invalid action journal")
    attempts = state.get("attempts", [])
    if len(attempts) > 32:
        raise ValueError("Oversized action journal")
    previous = 0
    for action in attempts:
        if (
            not isinstance(action.get("id"), str)
            or not math.isfinite(action["at"])
            or not previous <= action["at"] <= now
            or action.get("outcome") not in ("pending", "ready", "failed")
        ):
            raise ValueError("Invalid action journal entry")
        previous = action["at"]
    pending = state.get("pending")
    if pending and (
        not attempts
        or pending != attempts[-1]["id"]
        or attempts[-1]["outcome"] != "pending"
    ):
        raise ValueError("Invalid pending action")
    if not pending and attempts and attempts[-1]["outcome"] == "pending":
        raise ValueError("Untracked pending action")
    return state


class Controller:
    def __init__(self, fly, anchor, clock=time.time):
        self.fly, self.anchor, self.clock = fly, anchor, clock
        self.policy = Policy()

    def fence(self, lease):
        if (
            not isinstance(lease.get("nonce"), str)
            or not lease["nonce"]
            or not math.isfinite(lease["expires_at"])
            or lease["expires_at"] - self.clock() < 20
            or lease["deadline"] - time.monotonic() < 20
        ):
            raise ValueError("Lease lacks safe mutation window")

    @contextmanager
    def leased(self, machine_id):
        lease = self.fly.lease(machine_id)
        lease["deadline"] = time.monotonic() + min(
            60, lease["expires_at"] - self.clock()
        )
        try:
            self.fence(lease)
            yield lease
        finally:
            if isinstance(lease.get("nonce"), str) and lease["nonce"]:
                try:
                    self.fly.release(machine_id, lease["nonce"])
                except Exception as error:
                    logger.error(
                        "Lease release failed (%s); lease expires automatically",
                        type(error).__name__,
                    )

    def fresh_observation(self, samples):
        if not samples or any(
            not 0 <= self.clock() - s.timestamp <= 90 for s in samples.values()
        ):
            self.policy = Policy()
            raise ValueError("Observation expired before mutation")

    def tick(self, raw, samples, ready, dry_run=True):
        machines = inventory(raw)
        if self.anchor not in {m.id for m in machines if m.group == "app"}:
            raise ValueError("Fixed warm anchor missing")
        running = {m.id for m in machines if m.state == "started"}
        samples = {key: value for key, value in samples.items() if key in running}
        if dry_run:
            state = checked_journal(self.fly.journal(self.anchor), self.clock())
            return self.policy.decide(machines, samples, ready, state, self.clock())
        with self.leased(self.anchor) as anchor:
            fresh = self.fly.machines()
            inventory(fresh)
            if fingerprint(raw) != fingerprint(fresh):
                self.policy = Policy()
                raise ValueError("Fleet changed during observation")
            state = checked_journal(self.fly.journal(self.anchor), self.clock())
            pending = state.get("pending")
            if pending:
                succeeded = pending in running and pending in ready
                expired = self.clock() - state["attempts"][-1]["at"] >= 180
                if succeeded or expired:
                    state["pending"] = None
                    state["attempts"][-1]["outcome"] = (
                        "ready" if succeeded else "failed"
                    )
                    self.fence(anchor)
                    self.fly.save(self.anchor, state, anchor["nonce"])
                    if not succeeded:
                        return Decision("start_failed")
            decision = self.policy.decide(machines, samples, ready, state, self.clock())
            if not decision.target:
                return decision
            # Separate target lease fences the actual start, even if anchor expires.
            with (
                self.leased(decision.target)
                if decision.target != self.anchor
                else _existing(anchor) as target
            ):
                if fingerprint(fresh) != fingerprint(self.fly.machines()):
                    raise ValueError("Fleet changed before mutation")
                attempts = [
                    a
                    for a in state.get("attempts", [])
                    if self.clock() - a["at"] < 3600
                ]
                state = {
                    "pending": decision.target,
                    "attempts": attempts
                    + [
                        {
                            "id": decision.target,
                            "at": self.clock(),
                            "outcome": "pending",
                        }
                    ],
                }
                self.fence(anchor)
                self.fence(target)
                self.fresh_observation(samples)
                self.fly.save(self.anchor, state, anchor["nonce"])
                self.fence(anchor)
                self.fence(target)
                self.fresh_observation(samples)
                self.fly.start(decision.target, target["nonce"])
            return decision


@contextmanager
def _existing(lease):
    yield lease
