"""Public per-audience legal document endpoint.

Riders and drivers fetch Terms of Service / Privacy Policy text filtered
by their audience. Admin CRUD lives at /admin/legal-documents.

Falls through to the legacy /settings/legal text when no per-audience row
exists — keeps the rider/driver legal screens rendering during rollout.
"""

import logging

from fastapi import APIRouter, Query

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
except ImportError:
    import db_supabase
    from settings_loader import get_app_settings

logger = logging.getLogger(__name__)

api_router = APIRouter(tags=["LegalDocuments"])

ALLOWED_AUDIENCES = {"rider", "driver"}
ALLOWED_TYPES = {"tos", "privacy"}


@api_router.get("/legal-documents")
async def get_legal_document(
    audience: str = Query(..., pattern="^(rider|driver)$"),
    doc_type: str = Query(..., alias="type", pattern="^(tos|privacy)$"),
):
    """Return the active legal document for an (audience, doc_type) pair.

    Falls back to the legacy single-blob `/settings/legal` text if no
    per-audience row has been published yet.
    """
    row = await db_supabase.find_one(
        "legal_documents",
        {"audience": audience, "doc_type": doc_type},
    )

    if row and (row.get("content") or "").strip():
        return {
            "audience": audience,
            "type": doc_type,
            "content": row["content"],
            "version": row.get("version", 1),
            "updated_at": row.get("updated_at"),
        }

    # Legacy fallback — single blob shared across both apps.
    settings = await get_app_settings()
    legacy_key = "terms_of_service_text" if doc_type == "tos" else "privacy_policy_text"
    legacy = (settings.get(legacy_key) or "").strip()

    return {
        "audience": audience,
        "type": doc_type,
        "content": legacy,
        "version": 0,
        "updated_at": None,
    }
