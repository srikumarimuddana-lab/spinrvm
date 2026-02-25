from fastapi import APIRouter, Depends, HTTPException
try:
    from ..dependencies import get_current_user
    from ..schemas import SavedAddress, SavedAddressCreate
    from ..db import db
except ImportError:
    from dependencies import get_current_user
    from schemas import SavedAddress, SavedAddressCreate
    from db import db

api_router = APIRouter(prefix="/addresses", tags=["Addresses"])

def serialize_doc(doc):
    return doc

@api_router.get("")
async def get_saved_addresses(current_user: dict = Depends(get_current_user)):
    addresses = await db.saved_addresses.find({'user_id': current_user['id']}).to_list(100)
    return serialize_doc(addresses)

@api_router.post("")
async def create_saved_address(request: SavedAddressCreate, current_user: dict = Depends(get_current_user)):
    address = SavedAddress(
        user_id=current_user['id'],
        name=request.name,
        address=request.address,
        lat=request.lat,
        lng=request.lng,
        icon=request.icon
    )
    await db.saved_addresses.insert_one(address.dict())
    return address.dict()

@api_router.delete("/{address_id}")
async def delete_saved_address(address_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.saved_addresses.delete_one({'id': address_id, 'user_id': current_user['id']})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail='Address not found')
    return {'success': True}
