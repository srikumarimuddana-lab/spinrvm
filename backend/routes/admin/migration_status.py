"""Admin route for the legacy-migration checklist status panel.

One read-only endpoint -- no file upload, no writes. Answers "what's
already run, and what's still pending" across all 16 migration/import/
backfill tools tracked by services/migration_status_service.py, in their
verified dependency order (docs/runbooks/migration-tool-order.md).

super_admin only, matching every other Bulk Operations tool's posture --
gated at the router-mount level in routes/admin/__init__.py, same pattern
as pre_launch_flag_router/wallet_import_router.
"""

import logging

from fastapi import APIRouter, Depends

try:
    from ...dependencies import get_admin_user
    from ...services import migration_status_service as svc
except ImportError:
    from dependencies import get_admin_user  # type: ignore
    from services import migration_status_service as svc  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/migration-status")
async def admin_get_migration_status(admin: dict = Depends(get_admin_user)):
    """Read-only. Returns all 16 tool statuses in dependency order."""
    report = svc.get_migration_status()
    return {
        "tools": [
            {
                "order": t.order,
                "id": t.id,
                "name": t.name,
                "state": t.state,
                "detail": t.detail,
                "admin_path": t.admin_path,
                "warning": t.warning,
            }
            for t in report.tools
        ]
    }
