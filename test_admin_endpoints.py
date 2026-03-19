#!/usr/bin/env python3
"""
Simple test script to verify admin panel endpoints are working
"""
import sys
import os
import asyncio
import httpx

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

async def test_admin_endpoints():
    """Test the admin endpoints we just implemented"""
    base_url = "http://localhost:8000"
    
    # Test endpoints that don't require authentication
    test_endpoints = [
        "/api/v1/admin/stats",
        "/api/v1/admin/service-areas", 
        "/api/v1/admin/vehicle-types",
        "/api/v1/admin/fare-configs",
        "/api/v1/admin/rides",
        "/api/v1/admin/drivers",
        "/api/v1/admin/earnings",
        "/api/v1/admin/documents/requirements",
        "/api/v1/admin/corporate-accounts",
        "/api/v1/admin/users",
        "/api/v1/admin/promotions",
        "/api/v1/admin/disputes",
        "/api/v1/admin/tickets",
        "/api/v1/admin/faqs",
        "/api/v1/admin/notifications/send",
    ]
    
    async with httpx.AsyncClient() as client:
        print("Testing admin endpoints...")
        for endpoint in test_endpoints:
            try:
                response = await client.get(f"{base_url}{endpoint}")
                if response.status_code == 200:
                    print(f"✅ {endpoint} - OK")
                elif response.status_code == 401:
                    print(f"🔒 {endpoint} - Requires authentication (expected)")
                else:
                    print(f"❌ {endpoint} - Error {response.status_code}")
            except Exception as e:
                print(f"❌ {endpoint} - Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_admin_endpoints())