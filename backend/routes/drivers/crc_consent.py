"""Driver CRC/VSC background-check consent (self-service).

Purpose-built PIPEDA consent capture, separate from the general Privacy
Policy acceptance — see services/driver_crc_consent.py and migration 319
for why this has its own table/audit trail. The consent text itself is
served through the existing legal_documents mechanism
(audience='driver', doc_type='background-check-consent') so it can be
authored/versioned the same way as every other policy page.
"""

from ._deps import (  # noqa: F401
    APIRouter,
    Depends,
    Dict,
    HTTPException,
    db_supabase,
    get_current_user,
    logger,
)

try:
    from ...services import driver_crc_consent as consent_service
except ImportError:  # pragma: no cover - direct module imports in tests
    from services import driver_crc_consent as consent_service  # type: ignore

router = APIRouter()


async def _get_own_driver_row(current_user: Dict) -> Dict:
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return driver


@router.get("/crc-consent")
async def get_crc_consent_status(current_user: Dict = Depends(get_current_user)):
    """Current driver's CRC/VSC consent state, plus the doc version
    currently being served, so the app can tell whether an existing
    consent is still current or needs re-confirmation."""
    driver = await _get_own_driver_row(current_user)
    doc = await db_supabase.find_one(
        "legal_documents",
        {"audience": "driver", "doc_type": "background-check-consent"},
    )
    current_version = doc.get("version") if doc else None
    status = await consent_service.get_consent_status(driver["id"])
    return {
        **status,
        "current_consent_version": current_version,
        "is_current": bool(status.get("consented")) and status.get("consent_version") == current_version,
    }


@router.post("/crc-consent")
async def submit_crc_consent(current_user: Dict = Depends(get_current_user)):
    """Record the current driver's consent to the CRC/VSC check at the
    consent_version currently being served. Idempotent — calling this again
    with the same served version just re-records the same state."""
    driver = await _get_own_driver_row(current_user)
    doc = await db_supabase.find_one(
        "legal_documents",
        {"audience": "driver", "doc_type": "background-check-consent"},
    )
    current_version = doc.get("version") if doc else None
    await consent_service.record_consent(
        driver["id"],
        consent_version=current_version,
        source="driver_app",
    )
    return {"consented": True, "consent_version": current_version}
