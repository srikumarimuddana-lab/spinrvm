from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

try:
    from .. import db_supabase
    from ..dependencies import get_current_user
    from ..schemas import SavedAddress, SavedAddressCreate, SavedAddressUpdate
    from ..utils.address_verification import verify_address_matches_coordinate
    from ..validators import sanitize_string
except ImportError:
    import db_supabase
    from dependencies import get_current_user
    from schemas import SavedAddress, SavedAddressCreate, SavedAddressUpdate
    from utils.address_verification import verify_address_matches_coordinate
    from validators import sanitize_string

api_router = APIRouter(prefix="/addresses", tags=["Addresses"])

# Fixed text on purpose: the verifier's own reason string quotes the full
# address back, and a 400 detail travels into client logs/error reporting.
_MISMATCH_DETAIL = "Address and location don't match. Please search for the address again and re-select it."

_SINGLETON_TYPES = ("home", "work")
# Icons that name a type of their own, so the label is never consulted.
_OTHER_TYPED_ICONS = ("gym", "school", "other")


def serialize_doc(doc):
    return doc


def _singleton_type(name: Optional[str], icon: Optional[str]) -> Optional[str]:
    """Return "home"/"work" when this place is the rider's Home/Work, else None.

    Mirrors rider-app/utils/savedPlaceIcon.ts isHomePlace/isWorkPlace: the
    type the rider picked (stored in ``icon``) wins; only an untyped row
    (legacy "location" icon, or none) falls back to an exact label match.
    """
    icon_l = (icon or "").strip().lower()
    if icon_l in _SINGLETON_TYPES:
        return icon_l
    if icon_l in _OTHER_TYPED_ICONS:
        return None
    label = (name or "").strip().lower()
    return label if label in _SINGLETON_TYPES else None


async def _drop_other_singletons(user_id: str, place_type: str, keep_id: str) -> None:
    # One filtered delete scoped to this rider — no read-then-write. Also
    # collapses any pre-existing duplicate Home/Work rows onto keep_id.
    await db_supabase.delete_many(
        "saved_addresses",
        {"user_id": user_id, "icon": place_type, "id": {"$ne": keep_id}},
    )


@api_router.get("")
async def get_saved_addresses(current_user: dict = Depends(get_current_user)):
    addresses = await db_supabase.get_rows(
        "saved_addresses", {"user_id": current_user["id"]}, order="created_at", limit=100
    )
    return serialize_doc(addresses)


@api_router.post("")
async def create_saved_address(request: SavedAddressCreate, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    _, sanitized_address = sanitize_string(request.address)

    # B9: best-effort check that the address text and the coordinate agree —
    # fails open on anything ambiguous (see utils/address_verification.py
    # docstring); only rejects a confident mismatch so a saved address can't
    # silently replay a wrong pin forever (Glide Crescent incident).
    ok, _mismatch_reason, place_id = await verify_address_matches_coordinate(
        sanitized_address, request.lat, request.lng
    )
    if not ok:
        raise HTTPException(status_code=400, detail=_MISMATCH_DETAIL)

    name = sanitize_string(request.name)[1]
    place_type = _singleton_type(name, request.icon)
    address = SavedAddress(
        user_id=user_id,
        name=name,
        address=sanitized_address,
        lat=request.lat,
        lng=request.lng,
        # A Home/Work row always carries its type in `icon`, so the replace
        # filter below (and the next save) can find it by icon alone.
        icon=place_type or request.icon,
        # The server's own geocode place_id wins; the client's is a fallback.
        place_id=place_id or request.place_id,
    )
    doc = address.model_dump()

    if place_type:
        # Owner decision 2026-09-25: a rider keeps exactly one Home and one
        # Work — saving a second one replaces the first, in place (same id).
        fields = {k: doc[k] for k in ("name", "address", "lat", "lng", "icon", "place_id")}
        updated = await db_supabase.update_one("saved_addresses", {"user_id": user_id, "icon": place_type}, fields)
        if updated:
            await _drop_other_singletons(user_id, place_type, updated["id"])
            return updated

    await db_supabase.insert_one("saved_addresses", doc)
    return doc


@api_router.patch("/{address_id}")
async def update_saved_address(
    address_id: str, request: SavedAddressUpdate, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["id"]
    existing = await db_supabase.find_one("saved_addresses", {"id": address_id, "user_id": user_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Address not found")

    sent = request.model_dump(exclude_unset=True)
    sent = {k: v for k, v in sent.items() if v is not None}
    if not sent:
        raise HTTPException(status_code=422, detail="No fields to update")

    update: dict = {}
    if "name" in sent:
        update["name"] = sanitize_string(sent["name"])[1]
    if "icon" in sent:
        update["icon"] = sent["icon"]

    location_keys = {"address", "lat", "lng"} & sent.keys()
    if location_keys:
        if location_keys != {"address", "lat", "lng"}:
            raise HTTPException(status_code=422, detail="address, lat and lng must be updated together")
        _, sanitized_address = sanitize_string(sent["address"])
        ok, _mismatch_reason, verified_place_id = await verify_address_matches_coordinate(
            sanitized_address, sent["lat"], sent["lng"]
        )
        if not ok:
            raise HTTPException(status_code=400, detail=_MISMATCH_DETAIL)
        update.update(
            address=sanitized_address,
            lat=sent["lat"],
            lng=sent["lng"],
            place_id=verified_place_id or sent.get("place_id"),
        )
    elif "place_id" in sent:
        update["place_id"] = sent["place_id"]

    place_type = _singleton_type(update.get("name", existing.get("name")), update.get("icon", existing.get("icon")))
    if place_type:
        update["icon"] = place_type

    row = await db_supabase.update_one("saved_addresses", {"id": address_id, "user_id": user_id}, update)
    if not row:
        raise HTTPException(status_code=404, detail="Address not found")
    if place_type:
        await _drop_other_singletons(user_id, place_type, address_id)
    return row


@api_router.delete("/{address_id}")
async def delete_saved_address(address_id: str, current_user: dict = Depends(get_current_user)):
    result = await db_supabase.delete_one("saved_addresses", {"id": address_id, "user_id": current_user["id"]})
    if not result:
        raise HTTPException(status_code=404, detail="Address not found")
    return {"success": True}
