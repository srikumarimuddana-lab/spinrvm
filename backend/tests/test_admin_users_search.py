"""Regression test for admin_get_users' id-match search fallback.

codex review r3548013237 (PR #2061): switching the cloud-messaging
"Particular Customer/Driver" picker to the server-side typeahead endpoints
dropped the old client-side exact-id match — a rider/driver UUID pasted from
a support ticket returned no results because admin_get_users only searched
phone/email/first_name/last_name. Fixed by adding `id` to the search $or.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

try:
    from backend.routes.admin import users as admin_users
except ImportError:  # pragma: no cover
    from routes.admin import users as admin_users  # type: ignore


def test_search_matches_user_id_uuid():
    captured_filters = {}

    async def get_rows_side(table, filters=None, **kw):
        captured_filters.update(filters or {})
        return []

    with patch("backend.routes.admin.users.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side)):
        asyncio.run(admin_users.admin_get_users(search="uid-rider-target"))

    or_clauses = captured_filters.get("$or", [])
    assert any(c.get("id", {}).get("$regex") == "uid\\-rider\\-target" for c in or_clauses)
