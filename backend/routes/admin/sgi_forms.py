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
    from ...services.data_transfer import sgi_field_maps, sgi_form_filler
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from services.data_transfer import sgi_field_maps, sgi_form_filler
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter()


class SgiFormRequest(BaseModel):
    form_type: str = Field(..., pattern="^(driver_details|vehicle_details)$")
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

    driver_rows = await db_supabase.get_rows("drivers", {"id": {"$in": body.driver_ids}})
    if not driver_rows:
        raise HTTPException(status_code=404, detail="None of the requested drivers could be found")

    if body.form_type == "driver_details":
        row_dicts = [sgi_field_maps.driver_to_driver_details_row(d, action=body.action) for d in driver_rows]
        pdf_bytes = sgi_form_filler.fill_driver_details_form(row_dicts)
        filename = "SGI_D00032_Driver_Details.pdf"
    else:
        row_dicts = [sgi_field_maps.driver_to_vehicle_details_row(d, action=body.action) for d in driver_rows]
        pdf_bytes = sgi_form_filler.fill_vehicle_details_form(row_dicts)
        filename = "SGI_D00033_Vehicle_Details.pdf"

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
