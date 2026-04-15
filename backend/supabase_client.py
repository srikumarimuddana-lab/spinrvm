import os

import httpx
from dotenv import load_dotenv
from supabase import create_client

# Load .env file from backend directory
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    # postgrest-py 2.x creates its internal httpx.Client with http2=True by
    # default. Supabase's PostgREST server sends HTTP/2 GOAWAY frames (error
    # code 9 = COMPRESSION_ERROR) when the stream limit is reached on a
    # long-lived connection, which surfaces as h2.ConnectionTerminated errors
    # in our sync thread pool. Replacing the internal client with an HTTP/1.1
    # one eliminates the GOAWAY issue entirely at the cost of slightly higher
    # latency per request (acceptable for an admin backend).
    try:
        _pg = supabase.postgrest
        _old = _pg._client
        _pg._client = httpx.Client(
            base_url=str(_old.base_url),
            headers=dict(_old.headers),
            http2=False,
            timeout=_old.timeout,
            verify=True,
        )
    except Exception:  # noqa: S110
        # If the internal API changes, fall back gracefully — the retry
        # in run_sync still handles the transient error.
        pass
else:
    # Supabase not configured; code should handle supabase being None
    supabase = None
