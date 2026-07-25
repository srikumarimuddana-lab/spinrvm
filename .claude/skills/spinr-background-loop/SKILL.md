---
name: spinr-background-loop
description: Recipe and replay-safety contract for adding a new background/startup loop to backend/core/lifespan.py. Use whenever the task is to add a new periodic asyncio loop that runs on every backend replica (e.g. a new reminder job, cleanup sweep, retry loop, or scheduled task).
---

# Background Loop Recipe

The startup loops in `core/lifespan.py` all run on every replica simultaneously. A new loop must satisfy the replay-safety contract or it will cause duplicate writes, charges, or notifications.

Template for a new loop:

```python
# backend/utils/my_loop.py
async def my_loop() -> None:
    """One-line purpose. Interval. What state it reads/writes."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("my_loop tick failed", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)

async def _tick() -> None:
    # 1. Query candidates with a filter that excludes already-processed rows
    # 2. For each candidate, attempt an atomic claim (UPDATE ... WHERE reminder_sent = false RETURNING *)
    # 3. Only act on rows where the claim returned a row (other replicas got zero)
    # 4. Do the side-effect (notify, charge, dispatch)
    # 5. On failure, don't re-queue — idempotency key or claim flag prevents replay
    ...
```

Replay-safety options (pick one):
- **Claim flag column** (`reminder_sent`, `auto_approved_this_period`) — preferred for simple cases
- **Idempotency key** (`stripe_events.event_id`) — for external-system interactions
- **Atomic DB claim** (`UPDATE ... WHERE status='pending' RETURNING *`) — for dispatch-style work queues
- **Redis leader lock** (`SET NX EX`) — only for loops that genuinely must run on one replica

Forbidden: in-process locks, filesystem flags, "this pod is primary" environment logic.
