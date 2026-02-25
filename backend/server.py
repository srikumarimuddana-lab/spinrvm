from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
import asyncio
import uuid
import base64
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parent
# Try loading .env from backend dir, or fallback to current dir
env_path = ROOT_DIR / '.env'
if not env_path.exists():
    env_path = Path.cwd() / '.env'

load_dotenv(env_path)

# Import DB
try:
    from .db import db
except ImportError:
    from db import db

# Import Shared Modules
try:
    from .utils import calculate_distance
    from .socket_manager import manager
except ImportError:
    from utils import calculate_distance
    from socket_manager import manager


# Import Routers
try:
    from .routes import (
        auth, users, rides, drivers, payments, webhooks, 
        settings, addresses, fares, websocket, admin
    )
    # Import existing features if they still exist and are needed
    from .features import support_router, admin_support_router, pricing_router, check_scheduled_rides, calculate_airport_fee, send_push_notification
    from .documents import documents_router, admin_documents_router, files_router
except ImportError:
    from routes import (
        auth, users, rides, drivers, payments, webhooks, 
        settings, addresses, fares, websocket, admin
    )
    from features import support_router, admin_support_router, pricing_router, check_scheduled_rides, calculate_airport_fee, send_push_notification
    from documents import documents_router, admin_documents_router, files_router

# Import Rate Limiter
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded



# Initialize Firebase (Keep existing initialization logic as it might be intricate)
import firebase_admin
from firebase_admin import credentials as firebase_credentials
import json

FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
if FIREBASE_SERVICE_ACCOUNT_JSON:
    try:
        sa_info = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
        cred = firebase_credentials.Certificate(sa_info)
        try:
            firebase_admin.initialize_app(cred)
        except ValueError:
            pass
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to initialize Firebase Admin from JSON: {e}")
else:
    try:
        firebase_admin.initialize_app()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start scheduled ride checker background task
    scheduler_task = asyncio.create_task(check_scheduled_rides())

    # Check for Supabase credentials
    if not os.environ.get('SUPABASE_URL') or not os.environ.get('SUPABASE_SERVICE_ROLE_KEY'):
        logging.error("CRITICAL: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is missing from environment variables. Database operations will fail.")

    # Create indexes for location history (idempotent)
    # Indexes are managed via Supabase dashboard/SQL, not application code.
    # The 'create_index' method is not supported by the Supabase wrapper.
    yield
    # Shutdown: cancel the scheduler
    scheduler_task.cancel()

app = FastAPI(title="Spinr API", version="1.0.0", lifespan=lifespan)

# CORS Middleware
origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:8081",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:8000",
    "https://spinr-admin.vercel.app",
    "https://spinr-admin-git-main-mkkreddys-projects.vercel.app",
]

env_origins = os.environ.get("ALLOWED_ORIGINS")
if env_origins:
    origins.extend([origin.strip() for origin in env_origins.split(",")])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Create main routers
api_router = APIRouter(prefix="/api")
admin_router = APIRouter(prefix="/api/admin")

# Include Refactored Routers
api_router.include_router(auth.api_router)
api_router.include_router(users.api_router)
api_router.include_router(rides.api_router)
api_router.include_router(documents_router) # Include before drivers to avoid strict slash conflict or shadowing
api_router.include_router(files_router)
api_router.include_router(drivers.api_router)
api_router.include_router(payments.api_router)
api_router.include_router(addresses.api_router)
api_router.include_router(fares.api_router)
api_router.include_router(settings.api_router)
api_router.include_router(admin.admin_router)

# Include Webhook Router (Mount separately or under /webhooks handling)
app.include_router(webhooks.api_router)

# Include WebSocket Router
app.include_router(websocket.router)

# Include Existing Feature/Document Routers
# (Using api_router or admin_router as appropriate based on original usages)
api_router.include_router(support_router)
# pricing_router is admin only
admin_router.include_router(pricing_router)

admin_router.include_router(admin_support_router)
admin_router.include_router(admin_documents_router)

# Mount main routers
app.include_router(api_router)
app.include_router(admin_router)


# File Upload Handling (Legacy direct function in server.py - keeping simple version if needed or relying on documents router)
# The `upload_file` endpoint in original server.py was generic. 
# `documents.py` likely handles specific document uploads.
# Re-implementing the generic upload here for backward compatibility if it wasn't moved to documents.py
# (Inspecting original: it was @api_router.post("/upload"))

@api_router.post("/upload")
async def upload_file_endpoint(file: UploadFile = File(...)):
    """Upload a file to Supabase Storage or DB (Fallback)"""
    try:
        from supabase import create_client
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        
        file_content = await file.read()
        file_ext = os.path.splitext(file.filename or '.jpg')[1]
        content_type = file.content_type or 'application/octet-stream'
        
        # Supabase Storage
        if supabase_url and supabase_key:
            try:
                supabase = create_client(supabase_url, supabase_key)
                unique_filename = f"{uuid.uuid4()}{file_ext}"
                bucket_name = 'driver-documents'
                
                try:
                    buckets = supabase.storage.list_buckets()
                    if not any(b.name == bucket_name for b in buckets):
                        supabase.storage.create_bucket(bucket_name, {'public': True})
                except Exception:
                    pass

                bucket = supabase.storage.from_(bucket_name)
                bucket.upload(
                    unique_filename,
                    file_content,
                    file_options={"content-type": content_type, "upsert": "false"}
                )
                
                public_url = f"{supabase_url}/storage/v1/object/public/{bucket_name}/{unique_filename}"
                return {"url": public_url, "filename": unique_filename}
                
            except Exception as e:
                print(f"Storage failed, attempting DB fallback: {e}")
        
        # DB Fallback
        file_id = str(uuid.uuid4())
        base64_content = base64.b64encode(file_content).decode('utf-8')
        doc_record = {
            'id': file_id,
            'filename': file.filename or 'document',
            'content_type': content_type,
            'data': base64_content,
            'created_at': datetime.utcnow().isoformat()
        }
        await db.document_files.insert_one(doc_record)
        return {
            "url": f"/api/documents/{file_id}", 
            "filename": file.filename or 'document',
            "storage_type": "database"
        }
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Upload error: {str(e)}')


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
