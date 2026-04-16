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
