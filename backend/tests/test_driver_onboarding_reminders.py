from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


class FakeReminderDB:
    def __init__(self, *, duplicate_claims: bool = False, docs: list[dict] | None = None):
        self.duplicate_claims = duplicate_claims
        self.docs = docs or []
        self.claims: list[dict] = []
        self.updates: list[tuple[dict, dict]] = []
        self.driver = {
            "id": "driver-1",
            "user_id": "user-1",
            "status": "pending",
            "deleted_at": None,
            "service_area_id": "area-1",
            "vehicle_type_id": None,
            "vehicle_make": None,
            "vehicle_model": None,
            "license_plate": None,
        }

    async def get_rows(self, table: str, filters: dict | None = None, **kwargs):
        if table == "service_areas":
            return [
                {
                    "id": "area-1",
                    "timezone": "America/Regina",
                    "required_documents": [
                        {"key": "vehicle_registration", "label": "Vehicle Registration", "required": True}
                    ],
                }
            ]
        if table == "document_requirements":
            return [{"id": "fallback-doc", "name": "Vehicle Registration", "is_mandatory": True}]
        if table == "drivers":
            return [self.driver]
        if table == "driver_documents":
            return self.docs
        return []

    async def insert_one(self, table: str, doc: dict):
        assert table == "driver_onboarding_reminder_log"
        if self.duplicate_claims:
            from utils.driver_onboarding_reminders import DuplicateRecordError

            raise DuplicateRecordError("duplicate daily reminder")
        row = {**doc, "id": f"log-{len(self.claims) + 1}"}
        self.claims.append(row)
        return row

    async def update_one(self, table: str, filters: dict, update: dict):
        assert table == "driver_onboarding_reminder_log"
        self.updates.append((filters, update))
        return {**filters, **update}


@pytest.mark.asyncio
async def test_sends_vehicle_details_and_document_reminders_at_8am_local(monkeypatch):
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB()
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    # America/Regina is UTC-6 year-round: 14:05 UTC is 08:05 local.
    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    assert stats == {"drivers_scanned": 1, "claims_attempted": 2, "pushes_delivered": 2}
    assert [claim["reminder_type"] for claim in fake_db.claims] == [
        reminders.VEHICLE_DETAILS,
        reminders.VEHICLE_DOCUMENTS,
    ]
    assert {claim["local_date"] for claim in fake_db.claims} == {"2026-06-09"}
    assert [call.kwargs["target_app"] for call in send_push.await_args_list] == ["driver", "driver"]
    assert [call.args[1] for call in send_push.await_args_list] == [
        "Add your vehicle details",
        "Upload your vehicle documents",
    ]
    assert [update["send_success"] for _, update in fake_db.updates] == [True, True]


@pytest.mark.asyncio
async def test_duplicate_daily_claim_suppresses_duplicate_pushes(monkeypatch):
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB(duplicate_claims=True)
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    assert stats == {"drivers_scanned": 1, "claims_attempted": 2, "pushes_delivered": 0}
    assert fake_db.claims == []
    assert fake_db.updates == []
    send_push.assert_not_awaited()
