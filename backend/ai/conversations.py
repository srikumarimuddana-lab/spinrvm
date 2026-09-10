"""Persistence for AI assistant conversations (ai_conversations / ai_messages).

PIPEDA contract (see migration 140): only user text and assistant replies
are written, and both are PII-scrubbed by the orchestrator (scrub_pii)
before they ever reach append_message — the model can echo tool-result data
verbatim (e.g. a phone number), so the assistant side is scrubbed the same
as the user side, not treated as trusted first-party text. Tool arguments
and tool results never reach these tables — an assistant row records tool
*names* only. Every read and delete is owner-scoped; a foreign
conversation_id behaves exactly like a missing one.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
    from ..utils.redis_client import redis_delete
except ImportError:
    import db_supabase
    from utils.redis_client import redis_delete

logger = logging.getLogger(__name__)

# Mirrors tools_booking.py's `_quote_pin_key` format. Not imported from there
# to avoid coupling this module to the booking-tools module for one string;
# the format is a stable, tested contract (see AI17/F5 change-log).
_QUOTE_PIN_KEY_FMT = "ai:quote:{conversation_id}"

_TITLE_MAX = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_or_create_conversation(
    user_id: str,
    conversation_id: Optional[str],
    audience: str = "rider",
    admin_actor_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve an owned conversation, or create a fresh one.

    Returns None when conversation_id is supplied but does not belong to
    this user (caller turns that into a 404 — indistinguishable from a
    missing conversation). ``admin_actor_id`` stamps threads started from
    the super-admin AI console on the user's behalf (migration 144).
    """
    if conversation_id:
        row = await db_supabase.find_one("ai_conversations", {"id": conversation_id, "user_id": user_id})
        return row
    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "audience": audience,
        "title": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    if admin_actor_id:
        row["admin_actor_id"] = admin_actor_id
    await db_supabase.insert_one("ai_conversations", row)
    return row


async def load_history(conversation_id: str, max_messages: int = 12) -> List[Dict[str, Any]]:
    """Last N user/assistant messages in chronological order, shaped as
    canonical ChatMessages for the adapters."""
    rows = await db_supabase.get_rows(
        "ai_messages",
        {"conversation_id": conversation_id},
        order="created_at",
        desc=True,
        limit=max_messages,
    )
    history = [{"role": r["role"], "content": r.get("content", "")} for r in reversed(rows or [])]
    return history


async def append_message(
    conversation: Dict[str, Any],
    role: str,
    content: str,
    *,
    tool_names: Optional[List[str]] = None,
    usage: Optional[Dict[str, int]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert one message row and bump the conversation's updated_at.

    The first user message also becomes the conversation title (truncated)
    so the conversation list has something human-readable to show.
    """
    row: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "conversation_id": conversation["id"],
        "role": role,
        "content": content,
        "created_at": _now(),
    }
    if tool_names:
        row["tool_names"] = tool_names
    if usage:
        row["input_tokens"] = usage.get("input_tokens")
        row["output_tokens"] = usage.get("output_tokens")
    if provider:
        row["provider"] = provider
    if model:
        row["model"] = model
    await db_supabase.insert_one("ai_messages", row)

    updates: Dict[str, Any] = {"updated_at": _now()}
    if role == "user" and not conversation.get("title"):
        updates["title"] = content[:_TITLE_MAX]
        conversation["title"] = updates["title"]
    await db_supabase.update_one("ai_conversations", {"id": conversation["id"]}, updates)
    return row


async def list_conversations(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    rows = await db_supabase.get_rows(
        "ai_conversations",
        {"user_id": user_id},
        order="updated_at",
        desc=True,
        limit=limit,
    )
    return [
        {
            "id": r["id"],
            "title": r.get("title") or "New conversation",
            "updated_at": r.get("updated_at"),
        }
        for r in rows or []
    ]


async def get_messages(conversation_id: str, user_id: str) -> Optional[List[Dict[str, Any]]]:
    """Owner-checked full message list for one conversation (oldest first).
    None = not found / not owned."""
    conv = await db_supabase.find_one("ai_conversations", {"id": conversation_id, "user_id": user_id})
    if not conv:
        return None
    rows = await db_supabase.get_rows(
        "ai_messages",
        {"conversation_id": conversation_id},
        order="created_at",
        limit=200,
    )
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r.get("content", ""),
            "created_at": r.get("created_at"),
        }
        for r in rows or []
    ]


async def delete_conversation(conversation_id: str, user_id: str) -> bool:
    """Owner-checked delete (PIPEDA right-to-delete). ai_messages cascade.
    False = not found / not owned."""
    conv = await db_supabase.find_one("ai_conversations", {"id": conversation_id, "user_id": user_id})
    if not conv:
        return False
    # Messages first: supabase-py deletes don't run in one transaction with
    # the FK cascade when RLS service-role applies, so be explicit.
    await db_supabase.delete_many("ai_messages", {"conversation_id": conversation_id})
    await db_supabase.delete_one("ai_conversations", {"id": conversation_id})
    # AI17/F5: a priced-quote pin (backend/ai/tools_booking.py's _pin_quote)
    # can outlive the conversation it was quoted in by up to its 15-minute
    # TTL. If this conversation_id is ever reused, load_pinned_quote() would
    # hand the orchestrator a stale, already-deleted trip as bookable
    # "LAST QUOTE" context. Best-effort, matching _pin_quote's own contract:
    # a failed cleanup must never fail the delete itself.
    try:
        await redis_delete(_QUOTE_PIN_KEY_FMT.format(conversation_id=conversation_id))
    except Exception:
        logger.error("ai quote pin cleanup failed", exc_info=True, extra={"conversation_id": conversation_id})
    return True
