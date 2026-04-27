"""Compatibility shim for the removed ``db`` module.

Upstream commit ``33f252b4`` removed ``backend/db.py`` and migrated most
routes to call ``db_supabase`` directly. A number of call sites were
missed and still reference ``db``, ``serialize_doc``, and ``settings``
without importing them — which manifests as F821 lint failures and real
runtime errors at import time.

Rather than revert #290, we re-expose the minimal surface used by
legacy callers. Downstream code is expected to migrate to
``db_supabase`` and ``core.config.settings`` directly over time; at that
point this shim can be removed.

The ``MockCursor`` and ``Collection`` classes are provided for test
compatibility — the old MongoDB-style test suite expected these to live
in ``backend.db``.  They are thin stubs that delegate to the flat
Supabase helpers exposed on the module.
"""

import db_supabase as db  # noqa: F401 (re-export)
from core.config import settings  # noqa: F401 (re-export)


def serialize_doc(doc):
    """Identity passthrough kept for legacy callers.

    The original implementation coerced MongoDB ``ObjectId`` values into
    strings. Supabase already returns JSON-serialisable dicts, so the
    function is a no-op; we keep it so callers don't have to change.
    """
    if doc is None:
        return None
    if isinstance(doc, list):
        return [serialize_doc(d) for d in doc]
    return doc


# ── MongoDB-compat stubs (used by legacy tests only) ──────────────────────────


class MockCursor:
    """Minimal cursor stub that mirrors the old MongoDB cursor interface.

    Tests create ``MockCursor("table", filter)`` and call ``to_list`` or
    chain ``sort``/``skip``/``limit`` on the result.
    """

    def __init__(self, collection_name: str, filter: dict | None = None):  # noqa: A002
        self.collection_name = collection_name
        self.filter = filter
        self._items: list = []

    # Chainable modifiers — return self so existing chaining doesn't crash.
    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length: int | None = None, limit: int | None = None, **kwargs) -> list:
        # Accept both ``length`` (Motor-style) and ``limit`` (test-compat) kwargs.
        return list(self._items)


class Collection:
    """Thin collection proxy so ``db.users``, ``db.rides``, etc. work in tests.

    Delegates the actual DB calls to the flat ``db_supabase`` helpers
    exposed on the module-level ``db`` alias above.
    """

    def __init__(self, name: str):
        self.name = name

    def find(self, filter: dict | None = None, **kwargs) -> MockCursor:  # noqa: A002
        return MockCursor(self.name, filter)

    async def find_one(self, filter: dict | None = None, **kwargs):  # noqa: A002
        return await db.find_one(self.name, filter or {})

    async def insert_one(self, document: dict, **kwargs):
        return await db.insert_one(self.name, document)

    async def update_one(self, filter: dict, update: dict, **kwargs):  # noqa: A002
        return await db.update_one(self.name, filter, update)

    async def delete_one(self, filter: dict, **kwargs):  # noqa: A002
        return await db.delete_one(self.name, filter)

    async def count_documents(self, filter: dict | None = None, **kwargs):  # noqa: A002
        rows = await db.get_rows(self.name, filter or {}, limit=1000)
        return len(rows)


# ── Collection attributes on the module-level db alias ────────────────────────
# These let legacy code do ``db.users.find_one(...)`` even though the
# underlying object is db_supabase (which has no such attributes).

_COLLECTIONS = [
    "users",
    "drivers",
    "rides",
    "otp_records",
    "otps",  # kept for historical tests that reference db.otps
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
    "promo_applications",
    "driver_documents",
    "document_requirements",
    "document_files",
    "driver_location_history",
    "corporate_accounts",
    "ride_messages",
    "emergency_contacts",
    "emergencies",
    "notification_preferences",
]

for _col in _COLLECTIONS:
    if not hasattr(db, _col):
        setattr(db, _col, Collection(_col))
