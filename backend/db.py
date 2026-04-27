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
"""

try:
    from backend import db_supabase
    from backend.core.config import settings  # noqa: F401 (re-export)
except ImportError:
    import db_supabase  # type: ignore[no-redef]
    from core.config import settings  # noqa: F401 (re-export)


def serialize_doc(doc):
    """Identity passthrough kept for legacy callers."""
    if doc is None:
        return None
    if isinstance(doc, list):
        return [serialize_doc(d) for d in doc]
    return doc


class MockCursor:
    """Iterable cursor that wraps a list of documents (test helper)."""

    def __init__(self, collection_name, filter_dict=None):
        self.collection_name = collection_name
        self.filter = filter_dict
        self._rows = []

    def sort(self, *args, **kwargs):
        return self

    def skip(self, n):
        return self

    def limit(self, n):
        return self

    async def to_list(self, limit=None, length=None):
        try:
            rows = await db_supabase.get_rows(self.collection_name, self.filter)
            return rows
        except Exception:
            return self._rows

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class Collection:
    """MongoDB-style collection shim backed by db_supabase helpers."""

    def __init__(self, table_name):
        self.name = table_name

    def find(self, filter_dict=None):
        return MockCursor(self.name, filter_dict)

    async def find_one(self, filter_dict=None, **kwargs):
        return await db_supabase.find_one(self.name, filter_dict or {})

    async def insert_one(self, doc, **kwargs):
        return await db_supabase.insert_one(self.name, doc)

    async def update_one(self, filter_dict, update=None, **kwargs):
        if isinstance(update, dict) and "$set" in update:
            set_data = update["$set"]
        else:
            set_data = update or {}
        return await db_supabase.update_one(self.name, filter_dict or {}, set_data)

    async def count_documents(self, filter_dict=None, **kwargs):
        return await db_supabase.count_documents(self.name, filter_dict or {})

    async def delete_one(self, filter_dict=None, **kwargs):
        return await db_supabase.delete_many(self.name, filter_dict or {})

    async def delete_many(self, filter_dict=None, **kwargs):
        return await db_supabase.delete_many(self.name, filter_dict or {})


class _DBWrapper:
    """Unified db object: flat async methods + collection attribute access."""

    async def find_one(self, table, filters=None, **kwargs):
        return await db_supabase.find_one(table, filters)

    async def insert_one(self, table, doc, **kwargs):
        return await db_supabase.insert_one(table, doc)

    async def update_one(self, table, filters, update=None, **kwargs):
        if isinstance(update, dict) and "$set" in update:
            set_data = update["$set"]
        else:
            set_data = update or {}
        return await db_supabase.update_one(table, filters, set_data)

    async def get_rows(self, table, filters=None, **kwargs):
        return await db_supabase.get_rows(table, filters, **kwargs)

    async def count_documents(self, table, filters=None, **kwargs):
        return await db_supabase.count_documents(table, filters)

    async def delete_many(self, table, filters=None, **kwargs):
        return await db_supabase.delete_many(table, filters)

    def __getattr__(self, name):
        # First check if db_supabase has an explicit attribute (functions like
        # wallet_increment_balance, fare_split_pay_share, etc.)
        attr = getattr(db_supabase, name, None)
        if attr is not None:
            return attr
        # Otherwise return a Collection proxy for table-style access (db.users, db.rides, etc.)
        return Collection(name)


db = _DBWrapper()
