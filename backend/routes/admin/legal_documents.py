"""Admin CRUD for per-audience legal documents (Terms of Service + Privacy Policy).

Authoring model: the admin dashboard reads all four docs (rider/tos,
rider/privacy, driver/tos, driver/privacy) at once and pushes any
edited blob back via PUT. We upsert on (audience, doc_type) so the
admin UI doesn't need to track row IDs.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

try:
    from ... import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_AUDIENCES = {"rider", "driver"}
ALLOWED_TYPES = {"tos", "privacy"}


@router.get("/legal-documents")
async def admin_list_legal_documents():
    """Return every per-audience legal document row."""
    rows = await db_supabase.get_rows("legal_documents", {}, limit=50)
    return rows or []


@router.put("/legal-documents")
async def admin_upsert_legal_document(payload: Dict[str, Any]):
    """Create or update an (audience, doc_type) row in one shot.

    Body: { audience: 'rider'|'driver', type: 'tos'|'privacy', content: str }
    """
    audience = (payload.get("audience") or "").strip()
    doc_type = (payload.get("type") or payload.get("doc_type") or "").strip()
    content = payload.get("content") or ""

    if audience not in ALLOWED_AUDIENCES:
        raise HTTPException(
            status_code=400, detail="audience must be 'rider' or 'driver'"
        )
    if doc_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="type must be 'tos' or 'privacy'")

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
    return {
        "audience": audience,
        "type": doc_type,
        "version": 1,
        "id": (row or {}).get("id"),
    }
