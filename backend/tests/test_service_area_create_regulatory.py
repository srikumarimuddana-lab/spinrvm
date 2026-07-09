"""Regression tests for PR #2064 follow-up: ServiceAreaCreateRequest must
accept the province + regulatory_* fields the admin create form sends.

Before the fix, Pydantic silently dropped them (they existed only on
ServiceAreaUpdateRequest), so a newly created area landed with NULL
regulatory metadata despite the admin filling in the form — and the
migration-223 backfill only covered rows that existed at migration time.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.admin.service_areas import (
    ServiceAreaCreateRequest,
    admin_create_service_area,
)

REGULATORY_PAYLOAD = {
    "province": "AB",
    "regulatory_authority": "Alberta TNC / municipal licensing",
    "regulatory_region": "AB",
    "regulatory_requirements_url": "https://example.ca/ab-tnc",
    "regulatory_notes": "Municipal licence required in Calgary.",
}


class TestCreateModelAcceptsRegulatoryFields:
    def test_fields_are_not_silently_dropped(self):
        req = ServiceAreaCreateRequest(name="Calgary", city="Calgary", **REGULATORY_PAYLOAD)
        dumped = req.model_dump()
        for key, value in REGULATORY_PAYLOAD.items():
            assert dumped[key] == value, key

    def test_fields_default_to_none_when_absent(self):
        req = ServiceAreaCreateRequest(name="Regina")
        for key in REGULATORY_PAYLOAD:
            assert getattr(req, key) is None


class TestCreateHandlerPersistsRegulatoryFields:
    @pytest.mark.anyio
    async def test_insert_doc_carries_regulatory_fields(self):
        req = ServiceAreaCreateRequest(
            name="Calgary",
            city="Calgary",
            polygon=[{"lat": 51.05, "lng": -114.07}],
            **REGULATORY_PAYLOAD,
        )
        insert_one = AsyncMock()
        with (
            patch("backend.routes.admin.service_areas.db_supabase.insert_one", insert_one),
            patch(
                "backend.routes.admin.service_areas.db_supabase.get_rows",
                AsyncMock(return_value=[]),
            ),
            patch(
                "backend.routes.admin.service_areas.invalidate_fare_cache",
                AsyncMock(),
            ),
            patch(
                "backend.routes.admin.service_areas.log_admin_action",
                AsyncMock(),
            ),
        ):
            await admin_create_service_area(req, admin={"id": "admin-1", "email": "a@spinr.ca"})

        insert_one.assert_awaited_once()
        table, doc = insert_one.await_args.args
        assert table == "service_areas"
        for key, value in REGULATORY_PAYLOAD.items():
            assert doc[key] == value, key
