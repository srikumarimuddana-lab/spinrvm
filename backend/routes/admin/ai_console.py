"""Super-admin AI console — test the assistant as a specific rider/driver.

POST /admin/ai/chat                                     — send a message AS the user
GET  /admin/ai/users/{user_id}/conversations            — what the user sees
GET  /admin/ai/users/{user_id}/conversations/{id}/messages

The conversation is REAL: it persists under the target user's account (the
rider sees it in their app), runs the same orchestrator/tool path with the
target user's data scoping, and counts against their daily cap — a true
end-to-end test. Strictly super_admin (admin module grants are not enough:
this both impersonates a user and reads their chat history), and every
action writes an audit_logs row.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...ai import conversations
    from ...ai.orchestrator import run_chat_turn
    from ...dependencies import get_admin_user
except ImportError:
    import db_supabase
    from ai import conversations
    from ai.orchestrator import run_chat_turn
    from dependencies import get_admin_user

logger = logging.getLogger(__name__)

router = APIRouter()

_ERROR_STATUS = {
    "ai_disabled": 503,
    "daily_cap": 429,
    "not_found": 404,
    "ai_misconfigured": 503,
    "provider_error": 502,
}


class AdminAiChatRequest(BaseModel):
    user_id: str = Field(..., max_length=64)
    message: str = Field(..., min_length=1, max_length=1000)
    conversation_id: Optional[str] = Field(None, max_length=64)
    # Optional override so dual-role (rider+driver) users can be tested on
    # either tool surface; defaults to the user row's primary role.
    audience: Optional[str] = Field(None, pattern="^(rider|driver)$")


def _require_super_admin(admin: Dict[str, Any]) -> None:
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="AI console is restricted to super admins")


async def _audit(admin: Dict[str, Any], action: str, target_user_id: str, details: Dict[str, Any]) -> None:
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": action,
            "entity_type": "ai_console",
            "entity_id": target_user_id,
            "details": details,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _target_user(user_id: str) -> Dict[str, Any]:
    user = await db_supabase.get_user_by_id(user_id)
    if not user or user.get("role") == "admin":
        # Admin accounts are not impersonable test subjects.
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/ai/chat")
async def admin_ai_chat(body: AdminAiChatRequest, admin: dict = Depends(get_admin_user)):
    """Run one assistant turn as the target user (non-streaming)."""
    _require_super_admin(admin)
    target = await _target_user(body.user_id)
    audience = body.audience or ("driver" if target.get("is_driver") else "rider")

    await _audit(
        admin,
        "admin_ai_chat_as_user",
        body.user_id,
        {"audience": audience, "conversation_id": body.conversation_id},
    )

    reply_parts: list = []
    actions: list = []
    meta: dict = {}
    done: dict = {}
    async for name, payload in run_chat_turn(
        user=target,
        conversation_id=body.conversation_id,
        user_message=body.message,
        audience=audience,
        admin_actor_id=admin["id"],
    ):
        if name == "token":
            reply_parts.append(payload.get("text", ""))
        elif name == "action":
            actions.append(payload)
        elif name == "meta":
            meta = payload
        elif name == "done":
            done = payload
        elif name == "error":
            raise HTTPException(
                status_code=_ERROR_STATUS.get(payload.get("code"), 502),
                detail={"code": payload.get("code"), "message": payload.get("message")},
            )
    return {
        "conversation_id": meta.get("conversation_id"),
        "message_id": done.get("message_id"),
        "reply": "".join(reply_parts),
        "actions": actions,
        "audience": audience,
        "usage": done.get("usage"),
    }


@router.get("/ai/users/{user_id}/conversations")
async def admin_ai_user_conversations(user_id: str, admin: dict = Depends(get_admin_user)):
    """List the target user's AI threads — exactly what they see in-app."""
    _require_super_admin(admin)
    await _target_user(user_id)
    await _audit(admin, "admin_ai_conversations_viewed", user_id, {})
    return {"conversations": await conversations.list_conversations(user_id)}


@router.get("/ai/users/{user_id}/conversations/{conversation_id}/messages")
async def admin_ai_user_messages(user_id: str, conversation_id: str, admin: dict = Depends(get_admin_user)):
    _require_super_admin(admin)
    await _target_user(user_id)
    messages = await conversations.get_messages(conversation_id, user_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _audit(admin, "admin_ai_messages_viewed", user_id, {"conversation_id": conversation_id})
    return {"messages": messages}
