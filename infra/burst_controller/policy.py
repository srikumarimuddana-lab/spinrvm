"""Pure decisions: no network, writes, restarts, or scale-in."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Machine:
    id: str
    group: str
    state: str
    region: str = "yyz"


@dataclass(frozen=True)
class Sample:
    memory: float
    cpu: float
    timestamp: float


@dataclass(frozen=True)
class Decision:
    reason: str
    target: str | None = None


class Policy:
    def __init__(self):
        self.high_since = None
        self.last_sample = None

    def decide(self, machines, samples, ready, state, now):
        ids = [m.id for m in machines]
        groups = [m.group for m in machines]
        if (
            len(ids) != 8
            or len(set(ids)) != 8
            or groups.count("app") != 2
            or groups.count("burst") != 6
            or any(m.region != "yyz" for m in machines)
        ):
            self.high_since = None
            return Decision("invalid_inventory")
        if any(m.state not in ("started", "stopped", "suspended") for m in machines):
            self.high_since = None
            return Decision("machine_transition")
        running = {m.id for m in machines if m.state == "started"}
        if not running or set(samples) != running:
            self.high_since = None
            return Decision("missing_metrics")
        if any(
            not all(math.isfinite(v) for v in (s.memory, s.cpu, s.timestamp))
            or not (0 <= s.memory <= 1 and 0 <= s.cpu <= 1)
            or not 0 <= now - s.timestamp <= 90
            for s in samples.values()
        ):
            self.high_since = None
            return Decision("invalid_metrics")
        stamp = min(s.timestamp for s in samples.values())
        if self.last_sample is not None and stamp <= self.last_sample:
            return Decision("awaiting_fresh_sample")
        if self.last_sample is not None and stamp - self.last_sample > 30:
            self.high_since = None
        self.last_sample = stamp
        if not (running & ready):
            self.high_since = None
            return Decision("dependency_unready")
        high = any(s.memory >= 0.70 or s.cpu >= 0.80 for s in samples.values())
        critical = any(s.memory >= 0.85 for s in samples.values())
        self.high_since = (
            (self.high_since if self.high_since is not None else stamp)
            if high
            else None
        )
        if state.get("pending"):
            return Decision("awaiting_readiness")
        attempts = state.get("attempts", [])
        recent = [a for a in attempts if now - a["at"] < 3600]
        if recent and now - recent[-1]["at"] < 120:
            return Decision("cooldown")
        if len(recent) >= 8:
            return Decision("attempt_budget")
        warm_missing = [m for m in machines if m.group == "app" and m.id not in running]
        if (
            not warm_missing
            and not critical
            and (not high or stamp - self.high_since < 30)
        ):
            return Decision("sustaining_pressure" if high else "normal")
        candidates = warm_missing or [
            m for m in machines if m.group == "burst" and m.id not in running
        ]
        if not candidates:
            return Decision("capacity_exhausted" if high else "normal")
        # Rotate past a failed candidate; the durable attempt journal survives restarts.
        last_tried = {a["id"]: a["at"] for a in attempts}
        target = min(candidates, key=lambda m: (last_tried.get(m.id, 0), m.id))
        return Decision("restore_warm" if warm_missing else "pressure", target.id)
