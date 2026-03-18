from fastapi import APIRouter, Depends, HTTPException, UploadFile, File  # type: ignore
try:
    from ..dependencies import get_current_user  # type: ignore
    from ..schemas import UserProfile, CreateProfileRequest  # type: ignore
    from ..db import db  # type: ignore
except ImportError:
    from dependencies import get_current_user  # type: ignore
    from schemas import UserProfile, CreateProfileRequest  # type: ignore
    from db import db  # type: ignore
import base64
import uuid
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/users", tags=["Users"])

@api_router.get("/profile", response_model=UserProfile)
async def get_profile(current_user: dict = Depends(get_current_user)):
    """Get the current user's profile."""
    user = await db.users.find_one({'id': current_user['id']})
    if not user:
        raise HTTPException(status_code=404, detail="User profile not found")
    return UserProfile(**user)

@api_router.post("/profile", response_model=UserProfile)
async def create_profile(request: CreateProfileRequest, current_user: dict = Depends(get_current_user)):
    valid_genders = ['Male', 'Female', 'Other']
    if request.gender not in valid_genders:
        raise HTTPException(status_code=400, detail=f'Gender must be one of: {", ".join(valid_genders)}')
    
    # GAP FIX: Check for duplicate email across users
    email_lower = request.email.strip().lower()
    existing_email_user = await db.users.find_one({
        'email': email_lower,
        'id': {'$ne': current_user['id']}
    })
    if existing_email_user:
        raise HTTPException(status_code=400, detail='This email address is already in use by another account')
    
    update_data = {
        'first_name': request.first_name.strip(),
        'last_name': request.last_name.strip(),
        'email': email_lower,
        'gender': request.gender,
        'profile_complete': True
    }
    
    await db.users.update_one({'id': current_user['id']}, {'$set': update_data})
    updated_user = await db.users.find_one({'id': current_user['id']})
    
    if not updated_user:
        raise HTTPException(status_code=500, detail="Database error: Could not retrieve updated user profile. Check server logs for DB connection issues.")

    return UserProfile(**updated_user)

from pydantic import BaseModel  # type: ignore

class UpdatePhoneRequest(BaseModel):
    phone: str

@api_router.patch("/profile/phone", response_model=UserProfile)
async def update_phone(request: UpdatePhoneRequest, current_user: dict = Depends(get_current_user)):
    """Update the current user's phone number."""
    phone = request.phone.strip()
    if len(phone) < 10:
        raise HTTPException(status_code=400, detail='Invalid phone number')
    
    # Check if phone is already in use by another user
    existing = await db.users.find_one({'phone': phone, 'id': {'$ne': current_user['id']}})
    if existing:
        raise HTTPException(status_code=400, detail='Phone number already in use')
    
    await db.users.update_one(
        {'id': current_user['id']}, 
        {'$set': {'phone': phone}}
    )
    updated_user = await db.users.find_one({'id': current_user['id']})
    
    if not updated_user:
        raise HTTPException(status_code=500, detail="Database error: Could not retrieve updated user profile.")

    return UserProfile(**updated_user)

@api_router.put("/profile-image", response_model=UserProfile)
async def upload_profile_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Upload a profile image for the current user (stored as base64 in database)."""
    # Validate file type
    allowed_types = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail='File must be an image (JPEG, PNG, WebP, or GIF)')

    # Validate file size (max 5MB)
    content = await file.read()
    if not isinstance(content, bytes):
        content = bytes(content) if hasattr(content, '__bytes__') else str(content).encode('utf-8')
    
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail='Image must be smaller than 5MB')
    
    # Convert to base64
    base64_image = base64.b64encode(content).decode('utf-8')
    # Store as data URI
    data_uri = f"data:{file.content_type};base64,{base64_image}"
    
    await db.users.update_one(
        {'id': current_user['id']},
        {'$set': {'profile_image': data_uri}}
    )
    updated_user = await db.users.find_one({'id': current_user['id']})
    
    if not updated_user:
        raise HTTPException(status_code=500, detail="Database error: Could not retrieve updated user profile.")

    return UserProfile(**updated_user)


class LinkCorporateRequest(BaseModel):
    corporate_account_id: Optional[str] = None
    
@api_router.patch("/profile/corporate", response_model=UserProfile)
async def link_corporate_account(request: LinkCorporateRequest, current_user: dict = Depends(get_current_user)):
    """Link or unlink a corporate account to the user profile."""
    if request.corporate_account_id:
        account = await db.corporate_accounts.find_one({'id': request.corporate_account_id})
        if not account:
            raise HTTPException(status_code=404, detail="Corporate account not found")
            
    await db.users.update_one(
        {'id': current_user['id']},
        {'$set': {'corporate_account_id': request.corporate_account_id}}
    )
    
    updated_user = await db.users.find_one({'id': current_user['id']})
    if not updated_user:
         raise HTTPException(status_code=500, detail="Could not retrieve updated profile.")
         
    return UserProfile(**updated_user)


# ============================================================
# GAP FIX: Emergency Contacts (Uber/Lyft/Grab standard feature)
# ============================================================

class EmergencyContactCreate(BaseModel):
    name: str
    phone: str
    relationship: str = "Friend"  # Friend, Family, Spouse, Other

class EmergencyContactResponse(BaseModel):
    id: str
    user_id: str
    name: str
    phone: str
    relationship: str


@api_router.get("/emergency-contacts")
async def get_emergency_contacts(current_user: dict = Depends(get_current_user)):
    """Get the user's emergency contacts."""
    try:
        contacts_cursor = db.emergency_contacts.find({'user_id': current_user['id']})
        contacts = await contacts_cursor.to_list(length=10) if hasattr(contacts_cursor, 'to_list') else list(contacts_cursor)
    except Exception as e:
        logger.warning(f"Could not fetch emergency contacts: {e}")
        contacts = []
    return {'contacts': contacts}

@api_router.post("/emergency-contacts")
async def add_emergency_contact(
    contact: EmergencyContactCreate,
    current_user: dict = Depends(get_current_user)
):
    """Add an emergency contact (max 3 contacts per user, matching Uber/Lyft)."""
    try:
        existing_cursor = db.emergency_contacts.find({'user_id': current_user['id']})
        existing = await existing_cursor.to_list(length=10) if hasattr(existing_cursor, 'to_list') else list(existing_cursor)
    except Exception:
        existing = []
    
    MAX_EMERGENCY_CONTACTS = 3
    if len(existing) >= MAX_EMERGENCY_CONTACTS:
        raise HTTPException(
            status_code=400,
            detail=f'Maximum {MAX_EMERGENCY_CONTACTS} emergency contacts allowed. Remove one before adding another.'
        )
    
    phone = contact.phone.strip()
    if len(phone) < 10:
        raise HTTPException(status_code=400, detail='Invalid phone number for emergency contact')
    
    contact_doc = {
        'id': str(uuid.uuid4()),
        'user_id': current_user['id'],
        'name': contact.name.strip(),
        'phone': phone,
        'relationship': contact.relationship,
    }
    
    await db.emergency_contacts.insert_one(contact_doc)
    return {'success': True, 'contact': contact_doc}

@api_router.delete("/emergency-contacts/{contact_id}")
async def delete_emergency_contact(
    contact_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Remove an emergency contact."""
    contact = await db.emergency_contacts.find_one({
        'id': contact_id,
        'user_id': current_user['id']
    })
    if not contact:
        raise HTTPException(status_code=404, detail="Emergency contact not found")
    
    await db.emergency_contacts.delete_one({'id': contact_id})
    return {'success': True}

