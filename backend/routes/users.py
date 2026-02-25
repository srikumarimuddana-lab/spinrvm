from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
try:
    from ..dependencies import get_current_user
    from ..schemas import UserProfile, CreateProfileRequest
    from ..db import db
except ImportError:
    from dependencies import get_current_user
    from schemas import UserProfile, CreateProfileRequest
    from db import db
import base64

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
    
    update_data = {
        'first_name': request.first_name.strip(),
        'last_name': request.last_name.strip(),
        'email': request.email.strip().lower(),
        'gender': request.gender,
        'profile_complete': True
    }
    
    await db.users.update_one({'id': current_user['id']}, {'$set': update_data})
    updated_user = await db.users.find_one({'id': current_user['id']})
    
    if not updated_user:
        raise HTTPException(status_code=500, detail="Database error: Could not retrieve updated user profile. Check server logs for DB connection issues.")

    return UserProfile(**updated_user)

from pydantic import BaseModel
from typing import Optional

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
