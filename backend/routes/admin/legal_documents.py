"""Admin CRUD for per-audience legal documents (Terms of Service, Privacy
Policy, and the broader set of standalone policy pages under docs/legal/).

Authoring model: the admin dashboard reads every (audience, doc_type)
combination at once and pushes any edited blob back via PUT. We upsert on
(audience, doc_type) so the admin UI doesn't need to track row IDs.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_AUDIENCES = {"rider", "driver"}
# Keep in sync with routes/legal_documents.py's ALLOWED_TYPES — this is the
# admin-authoring side of the same set.
ALLOWED_TYPES = {
    "tos",
    "privacy",
    "community-guidelines",
    "non-discrimination",
    "accessibility",
    "cancellation-fees",
    "promotions-referral",
    "insurance-periods",
    "deactivation-appeals",
    "background-check-consent",
}


@router.get("/legal-documents")
async def admin_list_legal_documents():
    """Return every per-audience legal document row."""
    rows = await db_supabase.get_rows("legal_documents", {}, limit=50)
    return rows or []


@router.put("/legal-documents")
async def admin_upsert_legal_document(payload: Dict[str, Any], admin: dict = Depends(get_admin_user)):
    """Create or update an (audience, doc_type) row in one shot.

    Body: { audience: 'rider'|'driver', type: 'tos'|'privacy', content: str }
    """
    audience = (payload.get("audience") or "").strip()
    doc_type = (payload.get("type") or payload.get("doc_type") or "").strip()
    content = payload.get("content") or ""

    if audience not in ALLOWED_AUDIENCES:
        raise HTTPException(status_code=400, detail="audience must be 'rider' or 'driver'")
    if doc_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"type must be one of: {', '.join(sorted(ALLOWED_TYPES))}",
        )

    existing = await db_supabase.find_one(
        "legal_documents",
        {"audience": audience, "doc_type": doc_type},
    )

    now = datetime.now(timezone.utc).isoformat()
    if existing:
        next_version = int(existing.get("version") or 1) + 1
        await db_supabase.update_one(
            "legal_documents",
            {"id": existing["id"]},
            {"content": content, "version": next_version, "updated_at": now},
        )
        await log_admin_action(
            admin,
            "legal_document_updated",
            "legal_documents",
            existing["id"],
            {"audience": audience, "doc_type": doc_type, "version": next_version},
        )
        return {"audience": audience, "type": doc_type, "version": next_version}

    row = await db_supabase.insert_one(
        "legal_documents",
        {
            "audience": audience,
            "doc_type": doc_type,
            "content": content,
            "version": 1,
            "updated_at": now,
        },
    )
    doc_id = (row or {}).get("id")
    await log_admin_action(
        admin,
        "legal_document_created",
        "legal_documents",
        str(doc_id or f"{audience}:{doc_type}"),
        {"audience": audience, "doc_type": doc_type, "version": 1},
    )
    return {
        "audience": audience,
        "type": doc_type,
        "version": 1,
        "id": doc_id,
    }
