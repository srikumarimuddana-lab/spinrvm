"""
Pytest configuration and fixtures for Spinr backend tests.
This file provides shared fixtures for all test modules.
"""

import asyncio
import importlib
import importlib.abc
import importlib.machinery
import inspect
import os
import sys
from typing import Any, Dict, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# Add backend dir and project root to path FIRST so subsequent backend imports work.
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_project_root = os.path.dirname(_backend_dir)
sys.path.insert(0, _backend_dir)
if _project_root not in sys.path:
    sys.path.insert(1, _project_root)

# Set env vars before importing any backend module so core/config.py sees them.
# Use `or` fallback instead of setdefault: GitHub Actions sets missing secrets to ""
# (empty string), which setdefault treats as already-set and leaves unchanged.
os.environ["SUPABASE_URL"] = os.environ.get("SUPABASE_URL") or "https://test.supabase.co"
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "test_key"
os.environ["JWT_SECRET"] = os.environ.get("JWT_SECRET") or "test-secret-key-for-ci-only-32chars!!"
os.environ["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD") or "TestAdminPass123!"
os.environ["ADMIN_EMAIL"] = os.environ.get("ADMIN_EMAIL") or "admin@spinr.ca"
os.environ["ENV"] = os.environ.get("ENV") or "test"

# Pre-import backend.server with real rate-limit dependencies so all route
# module-level decorators bind real types. If that runs inside the slowapi mock
# context below, MagicMock children
# flow into FastAPI's route registration and cause FastAPIError at test setup.
# Since Python caches modules in sys.modules, fixtures that later do
# `from backend.server import app` get the already-built app with correct bindings.
# Force-stub slowapi AFTER route modules are cached. The real package IS
# installed in CI (it's in requirements.txt for the rate-limiter), so the
# `if _m not in sys.modules` guard in some test files leaves the real module
# in place.  The real Limiter class has no .return_value attribute, which
# breaks tests that do  sys.modules["slowapi"].Limiter.return_value.limit = fn.
# Replacing it here (conftest runs before collection) makes the stub visible
# to every test file regardless of collection order.
from slowapi.errors import RateLimitExceeded as _real_RateLimitExceeded  # noqa: E402

import backend.server as _backend_server_preload  # noqa: F401, E402

for _slowapi_mod in ("slowapi", "slowapi.extension", "slowapi.errors", "slowapi.util"):
    sys.modules[_slowapi_mod] = MagicMock()

# Restore RateLimitExceeded as a real Exception subclass so that
# app.add_exception_handler(RateLimitExceeded, ...) passes issubclass() when
# Starlette builds its ExceptionMiddleware during TestClient startup.
sys.modules["slowapi.errors"].RateLimitExceeded = _real_RateLimitExceeded

# server.py inserts backend/ into sys.path and uses bare imports, so route/util
# modules land in sys.modules under bare keys ("routes.admin.auth") rather than
# qualified keys ("backend.routes.admin.auth"). Test files that do qualified
# imports during collection would otherwise trigger a second import of those
# files — this time with slowapi already mocked — turning @limiter.limit
# decorators into MagicMocks.
#
# A one-shot loop that mirrors sys.modules keys present at collection time is
# not enough: some modules (e.g. utils.ws_pubsub) are only bare-imported
# lazily, inside a function body, the first time a request/test exercises
# that code path — well after this file has already run. A later qualified
# import (e.g. Python resolving `unittest.mock.patch("backend.utils.ws_pubsub
# ...")`) then finds no cached "backend.utils.ws_pubsub" entry and does a
# genuine fresh import, producing a second, disconnected module (and a
# disconnected singleton instance) that the mock silently patches while the
# real code keeps calling the original bare module — no exception, just a
# no-op mock.
#
# A sys.meta_path finder handles both the already-imported and the lazy case
# uniformly: for any "backend.X" import, if a bare "X" module already exists,
# reuse it instead of re-executing the module file. Python's import machinery
# then does its normal setattr(parent_module, child_name, module) binding for
# us, so attribute-walking resolvers (monkeypatch.setattr, unittest.mock.patch)
# resolve to the same object the bare-importing code actually uses.
_MIRRORED_BARE_ROOTS = {
    "routes",
    "services",
    "repositories",
    "utils",
    "core",
    "documents",
    "features",
    "dependencies",
    "socket_manager",
    "db_supabase",
    "schemas",
    "validators",
    "sms_service",
    "settings_loader",
    "logging_utils",
    "geo_utils",
}


class _BareModuleAliasLoader(importlib.abc.Loader):
    """Loader that hands back an already-imported bare module unmodified."""

    def __init__(self, bare_name: str) -> None:
        self._bare_name = bare_name

    def create_module(self, spec):  # noqa: D102
        return sys.modules[self._bare_name]

    def exec_module(self, module) -> None:  # noqa: D102
        pass


class _BareModuleAliasFinder(importlib.abc.MetaPathFinder):
    """Aliases "backend.<bare>" imports to the existing bare "<bare>" module.

    See the comment above this class for why a static, one-time mirror isn't
    sufficient on its own.
    """

    def find_spec(self, fullname, path, target=None):  # noqa: D102
        if not fullname.startswith("backend."):
            return None
        _bare_name = fullname[len("backend.") :]
        if _bare_name.split(".")[0] not in _MIRRORED_BARE_ROOTS:
            return None
        if fullname in sys.modules:
            return None
        if _bare_name not in sys.modules:
            # Whichever spelling is imported first becomes the canonical
            # instance. Import the bare name ourselves so "backend.X" never
            # wins that race and ends up as a second, disconnected module.
            try:
                importlib.import_module(_bare_name)
            except ImportError:
                return None
        return importlib.machinery.ModuleSpec(fullname, _BareModuleAliasLoader(_bare_name))


sys.meta_path.insert(0, _BareModuleAliasFinder())

# Mirror what's already in sys.modules at collection time too, so code that
# reaches straight into sys.modules (bypassing the import system, e.g. `import
# backend.server` above having already cached these) sees the same modules.
for _bare_key in sorted(
    (k for k in sys.modules if k.split(".")[0] in _MIRRORED_BARE_ROOTS),
    key=lambda k: k.count("."),
):
    _qualified_key = "backend." + _bare_key
    if _qualified_key not in sys.modules:
        sys.modules[_qualified_key] = sys.modules[_bare_key]
    _parts = _qualified_key.split(".")
    for _i in range(1, len(_parts)):
        _parent_name = ".".join(_parts[:_i])
        _parent_mod = sys.modules.get(_parent_name)
        if _parent_mod is not None:
            setattr(_parent_mod, _parts[_i], sys.modules[".".join(_parts[: _i + 1])])

# TASK 9-11: explicitly load anyio plugin so @pytest.mark.anyio is available
# alongside the asyncio_mode=auto setting in pytest.ini.
pytest_plugins = ["anyio"]


@pytest.fixture
def mock_supabase_client() -> MagicMock:
    """Create a mock Supabase client for testing."""
    mock_client = MagicMock()

    # Mock table method with chainable responses
    mock_table = MagicMock()
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.neq.return_value = mock_table
    mock_table.gt.return_value = mock_table
    mock_table.lt.return_value = mock_table
    mock_table.gte.return_value = mock_table
    mock_table.lte.return_value = mock_table
    mock_table.like.return_value = mock_table
    mock_table.ilike.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.in_.return_value = mock_table
    mock_table.contains.return_value = mock_table
    mock_table.overlap.return_value = mock_table
    mock_table.match.return_value = mock_table
    mock_table.text_search.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.offset.return_value = mock_table
    mock_table.single.return_value = mock_table

    # execute()/rpc() are synchronous in the real supabase-py client --
    # production code always wraps the whole chain in
    # `run_sync(lambda: supabase.table(...).execute())` (see
    # repositories/_base.py, ride_repo.py, core/lifespan.py) and never
    # awaits execute()/rpc() directly. An AsyncMock here produced a
    # coroutine that synchronous code creates and then never awaits --
    # silently leaking and failing an arbitrary other test on GC (A8).
    def mock_execute():
        response = MagicMock()
        response.data = []
        response.count = 0
        return response

    mock_table.execute = MagicMock(side_effect=mock_execute)
    mock_client.table.return_value = mock_table

    # Mock RPC method -- real supabase-py's client.rpc(name, params) returns a
    # filter-request builder, not the response itself; .execute() on that
    # builder returns the response (mirrors .table()'s chain above). Most
    # tests already assume this two-step shape when they locally override
    # `mock.rpc.return_value.execute.return_value` -- the base fixture must
    # match it too, or a test that reaches this default (rather than
    # overriding it) gets an unconfigured auto-mock in place of `res.data`.
    mock_rpc_builder = MagicMock()

    def mock_rpc_execute():
        response = MagicMock()
        response.data = None
        return response

    mock_rpc_builder.execute = MagicMock(side_effect=mock_rpc_execute)
    mock_client.rpc = MagicMock(return_value=mock_rpc_builder)

    # Mock auth methods
    mock_client.auth = MagicMock()
    mock_client.auth.sign_in_with_password = AsyncMock(return_value=MagicMock())
    mock_client.auth.sign_up = AsyncMock(return_value=MagicMock())
    mock_client.auth.refresh_session = AsyncMock(return_value=MagicMock())
    mock_client.auth.get_user = AsyncMock(return_value=MagicMock())
    mock_client.auth.admin_get_user = AsyncMock(return_value=MagicMock())

    return mock_client


@pytest.fixture
def mock_db_collections() -> Dict[str, MagicMock]:
    """Create mock database collections for testing."""
    collections = {}

    for collection_name in [
        "users",
        "drivers",
        "rides",
        "otps",
        "otp_records",
        "vehicle_types",
        "fare_configs",
        "service_areas",
        "settings",
        "saved_addresses",
        "support_tickets",
        "faqs",
        "area_fees",
        "surge_pricing",
        "notifications",
        "disputes",
        "payouts",
        "bank_accounts",
        "promo_codes",
    ]:
        mock_collection = MagicMock()
        mock_collection.find = MagicMock(return_value=MagicMock(to_list=AsyncMock(return_value=[])))
        mock_collection.find_one = AsyncMock(return_value=None)
        mock_collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
        mock_collection.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=[]))
        mock_collection.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
        mock_collection.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        mock_collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))
        mock_collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
        mock_collection.count_documents = AsyncMock(return_value=0)
        collections[collection_name] = mock_collection

    return collections


@pytest.fixture
def mock_firebase_admin() -> MagicMock:
    """Mock Firebase Admin SDK."""
    mock_firebase = MagicMock()
    mock_firebase.credentials = MagicMock()
    mock_firebase.cert = MagicMock()
    mock_firebase.initialize_app = MagicMock()

    mock_auth = MagicMock()
    mock_auth.create_user = AsyncMock(return_value=MagicMock(uid="test_uid"))
    mock_auth.get_user = AsyncMock(return_value=MagicMock(uid="test_uid", phone_number="+1234567890"))
    mock_auth.update_user = AsyncMock(return_value=MagicMock(uid="test_uid"))
    mock_auth.delete_user = AsyncMock(return_value=MagicMock())
    mock_auth.get_user_by_phone_number = AsyncMock(return_value=MagicMock(uid="test_uid"))
    mock_auth.set_custom_user_claims = AsyncMock(return_value=None)

    mock_firebase.auth = mock_auth
    return mock_firebase


@pytest.fixture
def mock_sms_service() -> MagicMock:
    """Mock SMS service for testing."""
    mock_service = MagicMock()
    mock_service.send = AsyncMock(return_value=True)
    mock_service.send_otp = AsyncMock(return_value={"success": True})
    return mock_service


@pytest.fixture
def sample_user_data() -> Dict[str, Any]:
    """Sample user data for testing."""
    return {
        "id": "user_123",
        "phone": "+1234567890",
        "email": "test@example.com",
        "first_name": "Test",
        "last_name": "User",
        "created_at": "2024-01-01T00:00:00Z",
        "is_admin": False,
    }


@pytest.fixture
def sample_driver_data() -> Dict[str, Any]:
    """Sample driver data for testing."""
    return {
        "id": "driver_123",
        "user_id": "user_123",
        "phone": "+1234567890",
        "first_name": "Test",
        "last_name": "Driver",
        "is_available": True,
        "is_online": False,
        "lat": 52.1333,
        "lng": -106.6667,
        "vehicle_type": "sedan",
        "license_plate": "ABC123",
        "rating": 4.8,
        "total_rides": 100,
        "created_at": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def sample_ride_data() -> Dict[str, Any]:
    """Sample ride data for testing."""
    return {
        "id": "ride_123",
        "rider_id": "user_123",
        "driver_id": "driver_123",
        "pickup_lat": 52.1333,
        "pickup_lng": -106.6667,
        "dropoff_lat": 52.1500,
        "dropoff_lng": -106.6500,
        "pickup_address": "123 Test St",
        "dropoff_address": "456 Main Ave",
        "status": "requested",
        "fare_amount": 15.50,
        "distance_km": 2.5,
        "duration_minutes": 10,
        "vehicle_type": "sedan",
        "created_at": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def sample_otp_data() -> Dict[str, Any]:
    """Sample OTP data for testing."""
    return {
        "id": "otp_123",
        "phone": "+1234567890",
        "code": "1234",
        "verified": False,
        "expires_at": "2024-01-01T00:10:00Z",
        "created_at": "2024-01-01T00:00:00Z",
    }


@pytest.fixture
def mock_jwt_token() -> str:
    """Return a mock JWT token for testing."""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzEyMyIsInBob25lIjoiKzEyMzQ1Njc4OTAiLCJleHAiOjk5OTk5OTk5OTl9.mock_signature"


@pytest.fixture
def auth_headers(mock_jwt_token: str) -> Dict[str, str]:
    """Return authorization headers with mock JWT token."""
    return {"Authorization": f"Bearer {mock_jwt_token}", "Content-Type": "application/json"}


@pytest.fixture
def mock_rate_limiter() -> MagicMock:
    """Mock rate limiter for testing."""
    mock_limiter = MagicMock()
    mock_limiter._rate_limit_exceeded_handler = MagicMock()
    return mock_limiter


@pytest.fixture
def mock_redis(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Provide a fresh, isolated in-process Redis store for each test.

    Replaces backend.utils.redis_client._local with an empty dict so that
    rate-limit counters, OTP records, and any other redis_client state written
    by one test never bleed into the next.  monkeypatch restores the original
    dict automatically after the test finishes.
    """
    from backend.utils import redis_client as rc

    clean: dict = {}
    monkeypatch.setattr(rc, "_local", clean)
    return clean


@pytest.fixture(autouse=True)
def _ensure_main_thread_event_loop() -> Generator[None, None, None]:
    """Heal the main-thread event loop before every test.

    pytest-asyncio 0.23 (asyncio_mode=auto) resolves some async tests —
    notably class-based ones — through a legacy wrapper that calls
    ``asyncio.get_event_loop()``. Any earlier test that closes the ambient
    loop without setting a new one (``asyncio.run`` does exactly this)
    makes that raise ``RuntimeError: There is no current event loop``,
    failing later tests order-dependently. Setting a fresh loop only when
    none exists mirrors what pytest-asyncio's own ``event_loop`` fixture
    would do, without touching a healthy loop.
    """
    policy = asyncio.get_event_loop_policy()
    try:
        policy.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(policy.new_event_loop())
    yield


@pytest.fixture(autouse=True)
def patch_external_dependencies(
    mock_supabase_client: MagicMock, mock_firebase_admin: MagicMock, mock_sms_service: MagicMock
) -> None:
    """Automatically patch external dependencies for all tests.

    The patch targets are attempted with both the fully-qualified package path
    (``backend.*``, required when the project root is on sys.path, e.g. in CI)
    and the bare module path (required when pytest runs from inside backend/ so
    that backend/ itself is the sys.path root). importlib is used to import the
    module before patching so mock.patch can resolve submodule attributes even
    when the parent package has not yet loaded them via getattr.
    """
    import importlib

    _specs = [
        ("backend.db_supabase", "db_supabase", "supabase", mock_supabase_client),
        ("backend.repositories._base", "repositories._base", "supabase", mock_supabase_client),
        ("backend.repositories.auth_repo", "repositories.auth_repo", "supabase", mock_supabase_client),
        ("backend.repositories.corporate_repo", "repositories.corporate_repo", "supabase", mock_supabase_client),
        ("backend.repositories.driver_repo", "repositories.driver_repo", "supabase", mock_supabase_client),
        ("backend.repositories.ride_repo", "repositories.ride_repo", "supabase", mock_supabase_client),
        ("backend.repositories.wallet_repo", "repositories.wallet_repo", "supabase", mock_supabase_client),
        ("backend.core.lifespan", "core.lifespan", "supabase", mock_supabase_client),
        ("backend.core.security", "core.security", "firebase_admin", mock_firebase_admin),
        ("backend.sms_service", "sms_service", "send_sms", mock_sms_service.send),
        ("backend.sms_service", "sms_service", "send_otp_sms", mock_sms_service.send_otp),
        ("backend.routes.auth", "routes.auth", "send_otp_sms", mock_sms_service.send_otp),
    ]

    patches = []
    for qualified_mod, bare_mod, attr, mock_obj in _specs:
        # Patch every importable path — both the qualified package path
        # (backend.*) and the bare path (used when backend/ is on sys.path).
        # They are often different module objects in sys.modules, so we must
        # patch both; breaking after the first leaves the other uncovered.
        for mod_path in (qualified_mod, bare_mod):
            try:
                mod = importlib.import_module(mod_path)
            except (ImportError, ModuleNotFoundError):
                continue
            if not hasattr(mod, attr):
                continue
            p = patch.object(mod, attr, mock_obj)
            p.start()
            patches.append(p)

    yield

    for p in patches:
        p.stop()


@pytest.fixture(autouse=True)
def reset_db_circuit_breaker() -> None:
    """Reset the db_supabase circuit breaker to closed state before each test.

    The circuit breaker is a module-level singleton whose failure count
    accumulates across the test session. Tests that call real Supabase URLs
    (which fail in CI) can open the breaker and cause subsequent tests to
    receive ServiceUnavailableException even when they mock the client.
    Resetting it here prevents cross-test contamination.
    """
    import importlib

    for mod_path in ("backend.db_supabase", "db_supabase"):
        try:
            mod = importlib.import_module(mod_path)
            breaker = getattr(mod, "_breaker", None)
            if breaker is not None:
                breaker._state = "closed"
                breaker._failure_times = []
                breaker._opened_at = None
                breaker._probe_in_flight = False
        except (ImportError, ModuleNotFoundError):
            continue


def _reset_limiter_storage(limiter_obj) -> None:
    """Reset sync SlowAPI or async limits storage before a test."""
    inner = getattr(limiter_obj, "_limiter", None)
    storage = getattr(inner, "storage", None) if inner is not None else None
    if storage is not None and callable(getattr(storage, "reset", None)):
        result = storage.reset()
        if inspect.isawaitable(result):
            asyncio.run(result)


@pytest.fixture(autouse=True)
def reset_rate_limiters() -> None:
    """Reset in-process rate-limiter storage before each test.

    The real SlowAPI Limiter is created at module import time (before conftest
    mocks slowapi) and uses MemoryStorage whose hit counts persist across tests
    in the same process.  After the limit threshold is reached (e.g. 3 hits to
    /api/admin/auth/login in 30 min), all subsequent tests that hit the same
    endpoint receive 429 instead of the expected 4xx/5xx — masking the real
    auth logic under test.
    """
    import importlib

    # Disable and reset the shared default_limiter used by all pre-configured limits
    # (payment_action_limit, ride_action_limit, ride_request_limit, etc.).
    # Setting enabled=False makes SlowAPI skip the starlette.Request lookup entirely,
    # which prevents IndexError / "request must be Request" errors when tests call
    # route handlers directly without an HTTP request object.
    for rl_mod_path in ("backend.utils.rate_limiter", "utils.rate_limiter"):
        try:
            rl_mod = importlib.import_module(rl_mod_path)
            limiter = getattr(rl_mod, "default_limiter", None)
            if limiter is not None:
                limiter.enabled = False
            _reset_limiter_storage(limiter)
        except (ImportError, ModuleNotFoundError):
            continue

    for mod_path in ("backend.routes.admin.auth", "routes.admin.auth"):
        try:
            mod = importlib.import_module(mod_path)
            _reset_limiter_storage(getattr(mod, "limiter", None))
        except (ImportError, ModuleNotFoundError):
            continue


@pytest.fixture
def test_client() -> TestClient:
    """Create a test client for the FastAPI app."""
    from backend.server import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def admin_override():
    """Override get_admin_user so authenticated admin routes see a fake admin.

    FastAPI binds `Depends(get_admin_user)` at import time, so patching the
    module attribute has no effect — the canonical pattern for admin-route
    tests is to install an override on the app.
    """
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


@pytest.fixture
def async_http_client() -> httpx.AsyncClient:
    """Create an async HTTP client for testing."""
    transport = httpx.AsyncHTTPTransport(app=MagicMock())
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield client
    client.aclose()


# ---------------------------------------------------------------------------
# Stale test-class skip registry
# ---------------------------------------------------------------------------
#
# These test classes were authored against pre-Supabase API shapes (MongoDB
# `count_documents`/`find().to_list()`, old JWT payload fields, old function
# return types, etc.) and currently fail because the production code has
# moved on. Rewriting them against the current Supabase-backed code path is
# a multi-day project tracked as a P1 item — see
# docs/audit/production-readiness-2026-04/09_ROADMAP_CHECKLIST.md
# § Testing / test-suite repair.
#
# Skipping at collection time keeps the `backend-test` CI job green without
# silently hiding the debt — `grep _STALE_TEST_CLASSES` lists every single
# item. As each class is rewritten its entry should be removed here.
#
# Nothing outside this list is affected; sibling classes in the same file
# continue to run normally.
_STALE_TEST_CLASSES: frozenset[str] = frozenset(
    {
        # test_admin_routes_auth.py — 0/6 stale (all repaired)
        # test_auth.py — 0/27 stale (all repaired)
        # test_db.py — 0/31 stale (all repaired)
        # test_documents.py — 0/25 stale (all repaired)
        # test_drivers.py — 0/19 stale (all repaired)
        # test_features.py — 0/24 stale (all repaired)
        # test_rides.py — 0/28 stale (all repaired)
    }
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip collected items belonging to known-stale test classes.

    Matching is by substring on nodeid (e.g.
    `tests/test_rides.py::TestRideCreation::test_foo`) so that renaming a
    test method still keeps the skip applied until the class is removed
    from `_STALE_TEST_CLASSES`.

    Set ``SPINR_RUN_STALE=1`` in the environment to disable the skip for
    local triage runs while repairing classes — never used in CI.
    """
    if os.environ.get("SPINR_RUN_STALE") == "1":
        return
    skip_marker = pytest.mark.skip(reason="Stale — pre-Supabase API shape. Rewrite tracked in P1 test-suite repair.")
    for item in items:
        if any(stale in item.nodeid for stale in _STALE_TEST_CLASSES):
            item.add_marker(skip_marker)
