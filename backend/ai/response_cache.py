"""Response cache for pure-FAQ AI turns (token / cost optimization).

Common openers ("how does surge work?", "what areas do you cover?") are asked
verbatim by many riders and drivers. Answering them re-runs a full LLM turn
every time even though the answer depends only on (audience, question) and the
`faqs` table. This module lets such turns be replayed from Redis instead.

A turn is cacheable only when it is self-contained AND impersonal:
  - it is the first message in the conversation (no prior history to depend on,
    so no "what about drivers?" follow-up can be miscached), and
  - it is not an admin-console (impersonated) turn, and
  - the model used ONLY read-only, non-personal tools (or none) and emitted no
    client action.

Under those rules the reply carries no per-user data, so it is safe to serve to
the same OR a different user. Keyed on the PII-scrubbed question; TTL-bounded so
FAQ edits propagate. Backed by utils/redis_client — in dev the in-process
fallback applies and the cache simply resets on restart.
"""

import hashlib
import logging
import re
from typing import List, Optional, Set

try:
    from ..utils.redis_client import redis_get, redis_set
except ImportError:  # pragma: no cover — top-level run
    from utils.redis_client import redis_get, redis_set

logger = logging.getLogger(__name__)

# Read-only tools whose results carry NO per-user data. A turn that touched only
# these (or no tool) is safe to replay across users. Keep this allowlist tight:
# adding a personal-data tool here would leak one user's data to another.
CACHEABLE_TOOLS: Set[str] = {"search_faqs", "get_company_info"}

_WS = re.compile(r"\s+")


def _normalize(question: str) -> str:
    """Fold trivial phrasing differences (whitespace, case, trailing
    punctuation) so "How does surge work?" and "how does surge work" collide."""
    return _WS.sub(" ", question.strip().lower()).strip(" \t\r\n?.!")


def cache_key(audience: str, scrubbed_question: str) -> str:
    digest = hashlib.sha256(_normalize(scrubbed_question).encode("utf-8")).hexdigest()
    return f"ai:respcache:{audience}:{digest}"


def is_cacheable(
    *,
    admin_actor_id: Optional[str],
    prior_turns: int,
    used_tool_names: List[str],
    had_client_action: bool,
    text: str,
) -> bool:
    """Whether the just-completed turn is safe to store. See module docstring."""
    if admin_actor_id is not None:
        return False
    if prior_turns > 0:  # only the first, context-free message qualifies
        return False
    if had_client_action:
        return False
    if not text.strip():
        return False
    return set(used_tool_names) <= CACHEABLE_TOOLS


async def get_cached(audience: str, scrubbed_question: str) -> Optional[str]:
    """Return a cached answer or None. Never raises — a cache fault must not
    break the chat; the caller falls through to a live LLM turn."""
    try:
        return await redis_get(cache_key(audience, scrubbed_question))
    except Exception:
        logger.error("ai response-cache read failed — falling through to LLM", exc_info=True)
        return None


async def store_cached(audience: str, scrubbed_question: str, text: str, ttl: int) -> None:
    """Persist an answer under the (audience, question) key. Never raises."""
    try:
        await redis_set(cache_key(audience, scrubbed_question), text, ttl=ttl)
    except Exception:
        logger.error("ai response-cache write failed", exc_info=True)
