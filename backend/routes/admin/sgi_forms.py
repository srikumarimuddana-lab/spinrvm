"""Generate the real SGI compliance PDFs (D00032 Driver Details, D00033
Vehicle Details) for selected drivers, for submission to SGI per the
Saskatchewan Transportation Act reporting obligations (CLAUDE.md's
Saskatchewan Regulatory section).

Row-value mapping (driver/user row -> filler dict) lives in
``services/data_transfer/sgi_field_maps.py``, kept separate from the
row-slot mechanics in ``sgi_form_filler.py`` so a future SGI form revision
only touches the mapping, not the PDF-filling code.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...routes.drivers._shared import _decrypt_driver_pii
    from ...services.data_transfer import observability, sgi_field_maps, sgi_form_filler
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from routes.drivers._shared import _decrypt_driver_pii
    from services.data_transfer import observability, sgi_field_maps, sgi_form_filler
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter()


class SgiFormRequest(BaseModel):
    form_type: str = Field(..., pattern="^(driver_details|vehicle_details)$")
    # `users.id` — the canonical entity_id used across the whole Data
    # Transfer module (search, export, SGI forms). NOT `drivers.id`; see the
    # driver lookup below, which resolves via `drivers.user_id`.
    driver_ids: list[str]
    action: str = Field("add", pattern="^(add|remove|change)$")


@router.post("/data-transfer/sgi-forms/generate")
async def generate_sgi_form(
    body: SgiFormRequest,
    admin: dict = Depends(get_admin_user),
):
    """Fill the requested SGI form for the given drivers and return the PDF
    directly (small file, single request/response — no Storage upload needed
    unlike the ZIP/CSV export, which can be much larger)."""
    max_rows = sgi_form_filler.FORM_MAX_ROWS[body.form_type]
    if not body.driver_ids:
        raise HTTPException(status_code=400, detail="No drivers selected")
    if len(body.driver_ids) > max_rows:
        raise HTTPException(
            status_code=422,
            detail=f"{len(body.driver_ids)} drivers requested; the {body.form_type} form has {max_rows} rows",
        )

    # Regression: this previously looked up `drivers.id`, but every other
    # part of the Data Transfer module (search's driver-scoped variant vs.
    # its default/mixed variant, and entity_export_service.gather_entity_bundle,
    # which is explicitly documented as taking `users.id`) disagreed on
    # which ID `driver_ids` actually meant -- selecting a driver from one
    # search mode made export work and SGI forms silently return nothing;
    # selecting from the other mode did the reverse. Standardized on
    # `users.id` everywhere; resolve the driver row via the FK instead of PK.
    driver_rows = await db_supabase.get_rows("drivers", {"user_id": {"$in": body.driver_ids}})
    if not driver_rows:
        raise HTTPException(status_code=404, detail="None of the requested drivers could be found")

    # license_number is vault-encrypted at rest (a vault.secrets UUID, not the
    # real value — see routes/drivers/_shared.py's _VAULT_PII_FIELDS). D00032
    # needs the real licence number on the form, not the encrypted token.
    driver_rows = [await _decrypt_driver_pii(d) for d in driver_rows]

    try:
        if body.form_type == "driver_details":
            row_dicts = [sgi_field_maps.driver_to_driver_details_row(d, action=body.action) for d in driver_rows]
            pdf_bytes = sgi_form_filler.fill_driver_details_form(row_dicts)
            filename = "SGI_D00032_Driver_Details.pdf"
        else:
            row_dicts = [sgi_field_maps.driver_to_vehicle_details_row(d, action=body.action) for d in driver_rows]
            pdf_bytes = sgi_form_filler.fill_vehicle_details_form(row_dicts)
            filename = "SGI_D00033_Vehicle_Details.pdf"
    except Exception as e:
        logger.error("data-transfer sgi-forms: fill failed for form_type=%s", body.form_type, exc_info=True)
        observability.record_sgi_form_result(body.form_type, "failed")
        observability.capture_failure(
            "SGI form generation failed",
            "data_transfer_sgi_form_failed",
            {
                "admin_id": admin.get("id"),
                "form_type": body.form_type,
                "driver_count": len(driver_rows),
                "error": str(e),
            },
        )
        raise HTTPException(status_code=502, detail="Could not generate the SGI form") from e

    observability.record_sgi_form_result(body.form_type, "completed")
    await log_admin_action(
        admin,
        "sgi_form_generated",
        "drivers",
        ",".join(body.driver_ids),
        {"form_type": body.form_type, "driver_count": len(driver_rows), "action": body.action},
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
