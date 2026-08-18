"""Public per-audience legal document endpoint.

Riders and drivers fetch legal/policy text filtered by their audience and
document type. Admin CRUD lives at /admin/legal-documents.

'tos' and 'privacy' fall through to the legacy /settings/legal text when no
per-audience row exists — keeps the rider/driver legal screens rendering
during rollout. Every other doc_type has no legacy source, so it simply
returns empty content (rendered as a "not yet available" placeholder by the
app) until an admin publishes it — see docs/legal/legal-text-publication-checklist.md
for which of these are still draft-only pending counsel review.
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

# 'tos'/'privacy' have a legacy fallback (see below). The rest map 1:1 to a
# docs/legal/*.md draft and have no legacy source — new rows only.
LEGACY_TYPES = {"tos", "privacy"}
ALLOWED_TYPES = LEGACY_TYPES | {
    "community-guidelines",
    "non-discrimination",
    "accessibility",
    "cancellation-fees",
    "promotions-referral",
    "insurance-periods",
    "deactivation-appeals",
    "background-check-consent",
}
_TYPE_PATTERN = "^(" + "|".join(sorted(ALLOWED_TYPES)) + ")$"


@api_router.get("/legal-documents")
async def get_legal_document(
    audience: str = Query(..., pattern="^(rider|driver)$"),
    doc_type: str = Query(..., alias="type", pattern=_TYPE_PATTERN),
):
    """Return the active legal document for an (audience, doc_type) pair.

    'tos'/'privacy' fall back to the legacy single-blob `/settings/legal`
    text if no per-audience row has been published yet. Every other
    doc_type returns empty content when unpublished — there is no legacy
    source for it to fall back to.
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

    legacy = ""
    if doc_type in LEGACY_TYPES:
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
