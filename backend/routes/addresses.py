from fastapi import APIRouter, Depends, HTTPException

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..schemas import SavedAddress, SavedAddressCreate
except ImportError:
    import db_supabase
    from dependencies import get_current_user
    from schemas import SavedAddress, SavedAddressCreate

api_router = APIRouter(prefix="/addresses", tags=["Addresses"])


def serialize_doc(doc):
    return doc


@api_router.get("")
async def get_saved_addresses(current_user: dict = Depends(get_current_user)):
    addresses = await db_supabase.get_rows("saved_addresses", {"user_id": current_user["id"]}, limit=100)
    return serialize_doc(addresses)


@api_router.post("")
async def create_saved_address(request: SavedAddressCreate, current_user: dict = Depends(get_current_user)):
    address = SavedAddress(
        user_id=current_user["id"],
        name=request.name,
        address=request.address,
        lat=request.lat,
        lng=request.lng,
        icon=request.icon,
    )
    await db_supabase.insert_one("saved_addresses", address.dict())
    return address.dict()


@api_router.delete("/{address_id}")
async def delete_saved_address(address_id: str, current_user: dict = Depends(get_current_user)):
    result = await db_supabase.delete_one("saved_addresses", {"id": address_id, "user_id": current_user["id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Address not found")
    return {"success": True}
