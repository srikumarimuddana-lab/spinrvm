#!/usr/bin/env python3
"""
Test script to check corporate accounts database connection and API
"""

import asyncio
import json
import os
import sys

# Add the backend directory to Python path
sys.path.insert(0, os.path.dirname(__file__))

from db_supabase import get_all_corporate_accounts


async def test_corporate_accounts():
    """Test the corporate accounts database functions"""
    try:
        print("Testing corporate accounts database connection...")

        # Test getting all corporate accounts
        accounts = await get_all_corporate_accounts()
        print(f"✓ Success: Found {len(accounts)} corporate accounts")

        if accounts:
            print(f"✓ First account: {json.dumps(accounts[0], indent=2, default=str)}")
        else:
            print("ℹ No corporate accounts found (this is normal if table is empty)")

        return True

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_api_route():
    """Test the API route directly"""
    try:
        print("\nTesting corporate accounts API route...")

        # Import the route function

        from routes.corporate_accounts import get_corporate_accounts

        # Create a mock request
        class MockRequest:
            def __init__(self):
                self.headers = {}

        # Create a mock admin user
        mock_admin = {"id": "test-admin", "role": "admin"}

        # Test the API route
        accounts = await get_corporate_accounts(MockRequest(), current_admin=mock_admin)
        print(f"✓ API route successful: Found {len(accounts)} corporate accounts")

        return True

    except Exception as e:
        print(f"✗ API route error: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    """Run all tests"""
    print("=" * 60)
    print("CORPORATE ACCOUNTS TEST SUITE")
    print("=" * 60)

    # Test database connection
    db_success = await test_corporate_accounts()

    # Test API route
    api_success = await test_api_route()

    print("\n" + "=" * 60)
    print("TEST RESULTS:")
    print(f"Database Connection: {'✓ PASS' if db_success else '✗ FAIL'}")
    print(f"API Route: {'✓ PASS' if api_success else '✗ FAIL'}")

    if db_success and api_success:
        print("\n🎉 All tests passed! The corporate accounts API should now work.")
        return True
    else:
        print("\n❌ Some tests failed. Check the errors above.")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
