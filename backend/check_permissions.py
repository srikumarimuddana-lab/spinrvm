
import os
from supabase import create_client, Client
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

print(f"URL: {url}")
print(f"Key (prefix): {key[:15] if key else 'None'}...")

if not url or not key:
    print("Missing credentials")
    exit(1)

supabase: Client = create_client(url, key)

print("\n--- Attempting to read users table ---")
try:
    res = supabase.table('users').select("*").limit(1).execute()
    print("Read success:", res)
except Exception as e:
    print("Read failed:", e)

print("\n--- Attempting to create a test table (DDL) via RPC (unlikely to work without specific setup) ---")
# Supabase client doesn't support raw SQL unless an RPC 'exec_sql' exists.
# We will try to call a common rpc name just in case, or list extensions.
try:
    res = supabase.rpc('version', {}).execute()
    print("RPC 'version' success:", res)
except Exception as e:
    print("RPC 'version' failed:", e)

print("\n--- Checking for PostGIS functions ---")
try:
    # Try to call a postgis function via rpc if it was wrapped? No, we call standard rpc.
    # We'll try to insert a row into 'drivers' with a dummy location to see if column exists
    # This checks if the schema was ALREADY applied.
    res = supabase.table('drivers').select('location').limit(1).execute()
    print("Column 'location' in 'drivers' exists:", res)
except Exception as e:
    print("Column check failed (likely schema not applied):", e)
