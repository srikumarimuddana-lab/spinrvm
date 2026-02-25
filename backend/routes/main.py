"""
Main router aggregator
Import all route modules and combine them here
"""
from fastapi import APIRouter
from .auth import router as auth_router
from .rides import router as rides_router
from .drivers import router as drivers_router
from .admin import router as admin_router

# Create the main API router
api_router = APIRouter()

# Include all sub-routers
api_router.include_router(auth_router)
api_router.include_router(rides_router)
api_router.include_router(drivers_router)
api_router.include_router(admin_router)

# Health check and root endpoints
@api_router.get("/")
async def root():
    return {"message": "Spinr API", "version": "1.0.0"}

@api_router.get("/health")
async def health_check():
    return {"status": "healthy"}
