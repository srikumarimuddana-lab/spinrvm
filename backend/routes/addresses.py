from fastapi import APIRouter, Depends, HTTPException

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..schemas import SavedAddress, SavedAddressCreate
    from ..utils.address_verification import verify_address_matches_coordinate
    from ..validators import sanitize_string
except ImportError:
    import db_supabase
    from dependencies import get_current_user
    from schemas import SavedAddress, SavedAddressCreate
    from utils.address_verification import verify_address_matches_coordinate
    from validators import sanitize_string

api_router = APIRouter(prefix="/addresses", tags=["Addresses"])


def serialize_doc(doc):
    return doc


@api_router.get("")
async def get_saved_addresses(current_user: dict = Depends(get_current_user)):
    addresses = await db_supabase.get_rows("saved_addresses", {"user_id": current_user["id"]}, limit=100)
    return serialize_doc(addresses)


@api_router.post("")
async def create_saved_address(request: SavedAddressCreate, current_user: dict = Depends(get_current_user)):
    _, sanitized_address = sanitize_string(request.address)

    # B9: best-effort check that the address text and the coordinate agree —
    # fails open on anything ambiguous (see utils/address_verification.py
    # docstring); only rejects a confident mismatch so a saved address can't
    # silently replay a wrong pin forever (Glide Crescent incident).
    ok, mismatch_reason, place_id = await verify_address_matches_coordinate(sanitized_address, request.lat, request.lng)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Address and location don't match: {mismatch_reason}")

    address = SavedAddress(
        user_id=current_user["id"],
        name=sanitize_string(request.name)[1],
        address=sanitized_address,
        lat=request.lat,
        lng=request.lng,
        icon=request.icon,
        place_id=place_id,
    )
    await db_supabase.insert_one("saved_addresses", address.dict())
    return address.dict()


@api_router.delete("/{address_id}")
async def delete_saved_address(address_id: str, current_user: dict = Depends(get_current_user)):
    result = await db_supabase.delete_one("saved_addresses", {"id": address_id, "user_id": current_user["id"]})
    if not result:
        raise HTTPException(status_code=404, detail="Address not found")
    return {"success": True}
