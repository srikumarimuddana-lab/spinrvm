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
        # postgrest 2.x stores its httpx.Client as `.session` (the older `._client`
        # attribute does not exist — using it silently left the client on HTTP/2,
        # which is NOT thread-safe across our _DB_EXECUTOR pool and caused
        # "RuntimeError: deque mutated during iteration" in h2's hpack table).
        _old = _pg.session
        _pg.session = httpx.Client(
            base_url=str(_old.base_url),
            headers=dict(_old.headers),
            http2=False,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            limits=httpx.Limits(keepalive_expiry=15),
            transport=httpx.HTTPTransport(retries=1),
            verify=True,
        )
    except Exception as _patch_exc:
        # Loud, not swallowed: if this fails the client falls back to HTTP/2 and
        # will intermittently crash background loops under concurrency. A future
        # postgrest attribute rename must scream here, not hide (CLAUDE.md: never
        # warning-and-continue on a DB error).
        import logging as _logging

        _logging.getLogger(__name__).error(
            "Failed to patch Supabase postgrest client to HTTP/1.1 — HTTP/2 still "
            "active, expect 'deque mutated during iteration' under thread concurrency: %s",
            _patch_exc,
            exc_info=True,
        )
else:
    # Supabase not configured; code should handle supabase being None
    supabase = None
