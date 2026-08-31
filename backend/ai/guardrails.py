"""Bounded, process-local fallback cap for AI daily-usage guardrails.

Context (GitHub issue #3742, decision AI1b): both `orchestrator._over_daily_cap`
and `mcp_server._over_mcp_daily_cap` enforce a per-user daily cap on AI usage
via a Redis INCR, and previously fell back to "uncapped" (fail open) whenever
Redis raised. That is a different failure mode from the ~26 background loops
in `core/lifespan.py` that also fail open on Redis errors — those hold
best-effort leader locks, and the worst case of a lock failing open is
duplicate benign work (e.g. two replicas running the same idempotent sweep).
Here, every allowed call proceeds to a real AI provider request with real
per-request cost and no other ceiling — an open-ended Redis outage would mean
open-ended spend.

This module provides a bounded, process-local floor that both call sites fall
back to only on the Redis-exception path (never on the normal, healthy path).
It is intentionally *not* shared across replicas — there is no cross-process
counter to keep consistent during the very outage that would make one
unreliable to depend on. That means worst-case exposure during an outage is
bounded by `_FALLBACK_DAILY_CAP * replica_count`, not unbounded, which is the
guarantee this exists to provide. It is a floor of last resort: Redis being
healthy is still the normal, accurate, cross-replica enforcement path.
"""

import logging
from datetime import datetime, timezone
from typing import Dict

try:
    from ..utils.metrics import inc as _metric_inc
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Deliberately conservative and well below any realistic admin-configured
# per-user daily cap (`ai_daily_message_cap`, `ai_mcp_daily_tool_cap` are
# typically in the tens-to-hundreds range). This is not meant to match the
# real cap — it exists purely to bound cost exposure per-process while Redis
# is unreachable, at the price of being stricter than intended during that
# window. Tune only with an explicit product/eng decision, same bar as
# SURGE_CAP.
_FALLBACK_DAILY_CAP = 20

# {f"{user_id}:{YYYYMMDD}": count} — process-local only, never persisted or
# shared across replicas. Pruned lazily (see fallback_over_cap) so it doesn't
# grow unbounded across day boundaries; a process restart also clears it.
_fallback_counts: Dict[str, int] = {}


def _today_key(user_id: str) -> str:
    return f"{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"


def fallback_over_cap(user_id: str, cap: int) -> bool:
    """Increment and check the process-local fallback counter for `user_id`.

    Called only from the Redis-exception branch of `_over_daily_cap` /
    `_over_mcp_daily_cap` — never on the normal healthy-Redis path, so this
    counter only ever accrues during an actual outage. Effective ceiling is
    `min(cap, _FALLBACK_DAILY_CAP)`: never looser than the real admin-
    configured cap, and never looser than the fixed floor either.

    Lazily prunes any key not for today's UTC date on every call, so the
    dict stays bounded by (today's active user count) rather than growing
    across days.
    """
    today_suffix = datetime.now(timezone.utc).strftime("%Y%m%d")
    stale_keys = [k for k in _fallback_counts if not k.endswith(f":{today_suffix}")]
    for k in stale_keys:
        del _fallback_counts[k]

    key = _today_key(user_id)
    count = _fallback_counts.get(key, 0) + 1
    _fallback_counts[key] = count

    effective_cap = min(cap, _FALLBACK_DAILY_CAP)
    over = count > effective_cap
    _metric_inc("spinr_ai_fallback_cap_total")
    return over
