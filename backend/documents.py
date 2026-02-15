from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid
import logging
import os
import shutil
from pathlib import Path

try:
    from .db import db
    from .dependencies import get_current_user
except ImportError:
    from db import db
    from dependencies import get_current_user

logger = logging.getLogger(__name__)

# Routers
documents_router = APIRouter(prefix="/api/drivers")
admin_documents_router = APIRouter(prefix="/api/admin/documents")

# --- Models ---

class DocumentRequirement(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    is_mandatory: bool
    requires_back_side: bool
    created_at: datetime

class CreateRequirementRequest(BaseModel):
    name: str
    description: Optional[str] = None
    is_mandatory: bool = True
    requires_back_side: bool = False

class UpdateRequirementRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_mandatory: Optional[bool] = None
    requires_back_side: Optional[bool] = None

class LinkDocumentRequest(BaseModel):
    requirement_id: str
    document_url: str
    document_type: str = "image/jpeg"
    side: Optional[str] = "front"

class DriverDocument(BaseModel):
    id: str
    driver_id: str
    requirement_id: Optional[str] = None
    document_type: str  # Kept for backward compatibility or display
    document_url: str
    side: Optional[str] = None
    status: str
    rejection_reason: Optional[str] = None
    uploaded_at: datetime


# --- Helper: Upload Directory ---
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def save_upload(file: UploadFile) -> str:
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save file: {e}")
        
    return f"/uploads/{filename}"

# --- Public/Driver Endpoints ---

@documents_router.get("/requirements")
async def get_document_requirements():
    """Get all document requirements for drivers."""
    # Fetch all active requirements
    # In future, filter by country/city if needed
    requirements = await db.document_requirements.find().sort('created_at', 1).to_list(100)
    return requirements

@documents_router.get("/documents")
async def get_driver_documents(current_user: dict = Depends(get_current_user)):
    """Get all documents uploaded by the current driver."""
    if not current_user.get('is_driver'):
        raise HTTPException(status_code=403, detail="User is not a driver")
        
    # Get driver profile to ensure we have the correct driver_id
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")
        
    documents = await db.driver_documents.find({'driver_id': driver['id']}).sort('uploaded_at', -1).to_list(100)
    return documents

@documents_router.post("/documents")
async def link_driver_document(
    doc_data: LinkDocumentRequest,
    current_user: dict = Depends(get_current_user)
):
    """Link an uploaded document to the current driver."""
    if not current_user.get('is_driver'):
        raise HTTPException(status_code=403, detail="User is not a driver")
        
    driver = await db.drivers.find_one({'user_id': current_user['id']})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver profile not found")

    # Validate requirement exists
    req = await db.document_requirements.find_one({'id': doc_data.requirement_id})
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")

    # Create document record
    doc_record = {
        'id': str(uuid.uuid4()),
        'driver_id': driver['id'],
        'requirement_id': doc_data.requirement_id,
        'document_type': doc_data.document_type,
        'document_url': doc_data.document_url,
        'side': doc_data.side,
        'status': 'pending', 
        'uploaded_at': datetime.utcnow(),
        'updated_at': datetime.utcnow()
    }

    # Archive previous documents for this requirement/side?? 
    # For now, just insert. The latest ONE is what we show.
    
    await db.driver_documents.insert_one(doc_record)
    return doc_record

@documents_router.post("/documents/upload")
async def upload_driver_document(
    file: UploadFile = File(...),
    driver_id: str = Form(...),
    requirement_id: str = Form(...),
    side: Optional[str] = Form(None)  # 'front' or 'back'
):
    """Upload a specific document linked to a requirement."""
    # storage logic
    url = await save_upload(file)

    # Validate requirement exists
    req = await db.document_requirements.find_one({'id': requirement_id})
    if not req:
        raise HTTPException(status_code=404, detail="Requirement not found")

    # Create document record
    doc_record = {
        'id': str(uuid.uuid4()),
        'driver_id': driver_id,
        'requirement_id': requirement_id,
        'document_type': req.get('name'), # Denormalize name for easy display
        'document_url': url,
        'side': side,
        'status': 'pending',
        'uploaded_at': datetime.utcnow(),
        'updated_at': datetime.utcnow()
    }

    # Check if existing doc for this requirement+side exists, if so, archive/delete it?
    # For now, just insert new one. The latest one is considered active.
    # Optionally: update old ones to 'archived'
    
    await db.driver_documents.insert_one(doc_record)
    
    return doc_record


# --- Admin Endpoints ---

@admin_documents_router.get("/requirements")
async def admin_get_requirements():
    """Get all document requirements."""
    requirements = await db.document_requirements.find().sort('created_at', 1).to_list(100)
    return requirements

@admin_documents_router.post("/requirements")
async def admin_create_requirement(req: CreateRequirementRequest):
    """Create a new document requirement."""
    new_req = {
        'id': str(uuid.uuid4()),
        'name': req.name,
        'description': req.description,
        'is_mandatory': req.is_mandatory,
        'requires_back_side': req.requires_back_side,
        'created_at': datetime.utcnow()
    }
    await db.document_requirements.insert_one(new_req)
    return new_req

@admin_documents_router.put("/requirements/{req_id}")
async def admin_update_requirement(req_id: str, req: UpdateRequirementRequest):
    """Update a document requirement."""
    update_data = {}
    if req.name is not None: update_data['name'] = req.name
    if req.description is not None: update_data['description'] = req.description
    if req.is_mandatory is not None: update_data['is_mandatory'] = req.is_mandatory
    if req.requires_back_side is not None: update_data['requires_back_side'] = req.requires_back_side
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
        
    result = await db.document_requirements.update_one({'id': req_id}, {'$set': update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Requirement not found")
        
    return await db.document_requirements.find_one({'id': req_id})

@admin_documents_router.delete("/requirements/{req_id}")
async def admin_delete_requirement(req_id: str):
    """Delete a document requirement."""
    # Check if used?
    # For now, allow delete.
    result = await db.document_requirements.delete_one({'id': req_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return {'deleted': True}

@admin_documents_router.get("/drivers/{driver_id}")
async def admin_get_driver_documents(driver_id: str):
    """Get all documents uploaded by a specific driver."""
    documents = await db.driver_documents.find({'driver_id': driver_id}).sort('uploaded_at', -1).to_list(100)
    return documents

class ReviewDocumentRequest(BaseModel):
    status: str
    rejection_reason: Optional[str] = None

@admin_documents_router.post("/{doc_id}/review")
async def admin_review_document(doc_id: str, req: ReviewDocumentRequest):
    """Approve or reject a driver document."""
    if req.status not in ['approved', 'rejected']:
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")
    
    update_data = {
        'status': req.status,
        'updated_at': datetime.utcnow()
    }
    if req.rejection_reason is not None:
        update_data['rejection_reason'] = req.rejection_reason

    result = await db.driver_documents.update_one({'id': doc_id}, {'$set': update_data})
    
    if result.matched_count == 0:
         raise HTTPException(status_code=404, detail="Document not found")
         
    return await db.driver_documents.find_one({'id': doc_id})
