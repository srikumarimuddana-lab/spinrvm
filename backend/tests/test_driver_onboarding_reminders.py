from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _reset_completed_windows(monkeypatch):
    """Each test starts with a fresh once-per-window memo."""
    from utils import driver_onboarding_reminders as reminders

    monkeypatch.setattr(reminders, "_completed_windows", set())


@pytest.fixture(autouse=True)
def _default_app_settings(monkeypatch):
    """No overrides configured → the built-in defaults apply.

    Patched so the loop's app_settings read never reaches the real settings
    table (or its in-process TTL cache) from a unit test.
    """
    from utils import driver_onboarding_reminders as reminders

    monkeypatch.setattr(reminders, "_get_app_settings", AsyncMock(return_value={}))


def _stats(scanned=1, claims=2, delivered=2, capped=0):
    return {
        "drivers_scanned": scanned,
        "claims_attempted": claims,
        "pushes_delivered": delivered,
        "capped_skips": capped,
    }


class FakeReminderDB:
    def __init__(
        self,
        *,
        duplicate_claims: bool = False,
        raw_duplicate_claims: bool = False,
        service_unavailable_claims: bool = False,
        docs: list[dict] | None = None,
        preexisting_log: list[dict] | None = None,
        driver_status: str = "pending",
    ):
        # Claim rows already in driver_onboarding_reminder_log from earlier
        # days — what the repeat cap counts against.
        self.preexisting_log = preexisting_log or []
        self.duplicate_claims = duplicate_claims
        # Simulate the duplicate surfacing as a generic exception whose text
        # carries the unique-violation (not the typed DuplicateRecordError) —
        # the dual-import-class-mismatch case the text fallback must still skip.
        self.raw_duplicate_claims = raw_duplicate_claims
        # Simulate the DB circuit breaker rejecting the claim insert.
        self.service_unavailable_claims = service_unavailable_claims
        self.docs = docs or []
        self.claims: list[dict] = []
        self.updates: list[tuple[dict, dict]] = []
        self.tables_read: list[str] = []
        self.failing_tables: set[str] = set()
        self.failing_claims = False
        self.claim_attempts = 0
        self.driver = {
            "id": "driver-1",
            "user_id": "user-1",
            "status": driver_status,
            "deleted_at": None,
            "service_area_id": "area-1",
            "vehicle_type_id": None,
            "vehicle_make": None,
            "vehicle_model": None,
            "license_plate": None,
        }

    async def get_rows(self, table: str, filters: dict | None = None, **kwargs):
        self.tables_read.append(table)
        if table in self.failing_tables:
            raise RuntimeError(f"simulated DB failure reading {table}")
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
        if table == "driver_onboarding_reminder_log":
            # Rows claimed on earlier days plus anything claimed this run —
            # exactly what the repeat cap counts.
            return self.preexisting_log + self.claims
        return []

    async def insert_one(self, table: str, doc: dict):
        assert table == "driver_onboarding_reminder_log"
        self.claim_attempts += 1
        if self.service_unavailable_claims:
            from utils.driver_onboarding_reminders import ServiceUnavailableException

            raise ServiceUnavailableException("database")
        if self.failing_claims:
            raise RuntimeError("simulated claim insert outage")
        if self.duplicate_claims:
            from utils.driver_onboarding_reminders import DuplicateRecordError

            raise DuplicateRecordError("duplicate daily reminder")
        if self.raw_duplicate_claims:
            raise RuntimeError(
                'duplicate key value violates unique constraint "driver_onboarding_reminder_log_daily_uidx"'
            )
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

    assert stats == _stats(claims=2, delivered=2)
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

    assert stats == _stats(claims=2, delivered=0)
    assert fake_db.claims == []
    assert fake_db.updates == []
    send_push.assert_not_awaited()


@pytest.mark.asyncio
async def test_raw_unique_violation_treated_as_already_claimed(monkeypatch):
    """A duplicate that surfaces as a generic exception (not the typed
    DuplicateRecordError — e.g. dual-import class mismatch) must still be a
    silent skip, not an error-logged claim failure that re-sends next tick."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB(raw_duplicate_claims=True)
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    # Same observable outcome as the typed-duplicate case: no push, no claim
    # row, no update — and crucially the window still completes (no _CLAIM_ERROR).
    assert stats == _stats(claims=2, delivered=0)
    assert fake_db.claims == []
    assert fake_db.updates == []
    send_push.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_unavailable_aborts_scan_on_first_claim(monkeypatch):
    """Breaker-open / DB-down is systemic: the scan must abort on the FIRST
    failing claim with one log line — not attempt every remaining driver and
    kind (2026-07-04: one traceback per driver, hundreds per tick) — and the
    window must stay incomplete so the next tick retries."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB(service_unavailable_claims=True)
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    # The driver needs BOTH reminders, but only the first claim is attempted.
    assert fake_db.claim_attempts == 1
    assert stats["claims_attempted"] == 1
    assert stats["pushes_delivered"] == 0
    send_push.assert_not_awaited()

    # Window incomplete → a later tick in the same hour rescans and delivers.
    fake_db.service_unavailable_claims = False
    second = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 20, tzinfo=timezone.utc))
    assert second == _stats(claims=2, delivered=2)


@pytest.mark.asyncio
async def test_outside_send_window_skips_drivers_scan(monkeypatch):
    """Outside the 08:00 local hour, a tick reads only service_areas — the
    drivers/documents scan (the egress-heavy part) must not run."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB()
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    # 18:05 UTC is 12:05 in America/Regina — no window open.
    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 18, 5, tzinfo=timezone.utc))

    assert stats == _stats(scanned=0, claims=0, delivered=0)
    assert fake_db.tables_read == ["service_areas"]
    send_push.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_runs_once_per_send_window(monkeypatch):
    """A second tick inside the same 08:00 window skips the drivers scan
    (in-process memo); the next day's window scans again."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB()
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    first = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))
    assert first["drivers_scanned"] == 1

    fake_db.tables_read.clear()
    second = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 20, tzinfo=timezone.utc))
    assert second == _stats(scanned=0, claims=0, delivered=0)
    assert fake_db.tables_read == ["service_areas"]

    next_day = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 10, 14, 5, tzinfo=timezone.utc))
    assert next_day["drivers_scanned"] == 1


@pytest.mark.asyncio
async def test_failed_scan_does_not_complete_window(monkeypatch):
    """A DB failure mid-scan must leave the send window incomplete so the
    next tick in the same hour retries — not silently skip the day."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB()
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    # First tick: the drivers read fails inside the 08:00 window.
    fake_db.failing_tables = {"drivers"}
    failed = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))
    assert failed == _stats(scanned=0, claims=0, delivered=0)
    assert reminders._completed_windows == set()
    send_push.assert_not_awaited()

    # Next tick, DB recovered: the scan runs and the reminders go out.
    fake_db.failing_tables = set()
    retried = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 20, tzinfo=timezone.utc))
    assert retried["drivers_scanned"] == 1
    assert retried["pushes_delivered"] == 2


@pytest.mark.asyncio
async def test_failed_claim_insert_does_not_complete_window(monkeypatch):
    """A claim-log insert outage leaves no dedupe row, so the window must
    stay incomplete and the next tick must retry the sends (only duplicate
    claims are a definitive 'already sent today')."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB()
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    fake_db.failing_claims = True
    failed = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))
    assert failed["pushes_delivered"] == 0
    assert reminders._completed_windows == set()
    send_push.assert_not_awaited()

    fake_db.failing_claims = False
    retried = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 20, tzinfo=timezone.utc))
    assert retried["pushes_delivered"] == 2
    assert reminders._completed_windows != set()


# ── Eligibility gating + repeat cap ─────────────────────────────────
# Before these, the loop skipped only `banned` drivers and had no terminal
# condition: an approved driver missing one vehicle field, or a rejected
# applicant who could not act at all, was pushed every morning forever.


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "needs_review", "rejected", "suspended", "banned"])
async def test_non_pending_driver_is_never_reminded(monkeypatch, status):
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB(driver_status=status)
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    assert stats == _stats(scanned=1, claims=0, delivered=0)
    assert fake_db.claims == []
    send_push.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_driver_with_blank_status_is_reminded(monkeypatch):
    """drivers.status is NOT NULL DEFAULT 'pending'; a legacy blank row
    predates that default and must be treated as pending, not silenced."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB()
    fake_db.driver["status"] = None
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    assert stats == _stats(claims=2, delivered=2)


@pytest.mark.asyncio
async def test_reminders_stop_after_the_cap(monkeypatch):
    from utils import driver_onboarding_reminders as reminders
    from utils.driver_onboarding_reminder_rules import DEFAULT_MAX_REMINDERS_PER_TYPE as CAP

    # Exactly CAP prior daily claims for the vehicle-details reminder; the
    # documents reminder is one short of the cap.
    prior = [
        {"driver_id": "driver-1", "reminder_type": reminders.VEHICLE_DETAILS, "local_date": f"2026-06-{d:02d}"}
        for d in range(1, CAP + 1)
    ] + [
        {"driver_id": "driver-1", "reminder_type": reminders.VEHICLE_DOCUMENTS, "local_date": f"2026-06-{d:02d}"}
        for d in range(1, CAP)
    ]
    fake_db = FakeReminderDB(preexisting_log=prior)
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    # Vehicle-details is exhausted; documents gets its final reminder.
    assert stats == _stats(claims=1, delivered=1, capped=1)
    assert [c["reminder_type"] for c in fake_db.claims] == [reminders.VEHICLE_DOCUMENTS]
    assert [call.args[1] for call in send_push.await_args_list] == ["Upload your vehicle documents"]


@pytest.mark.asyncio
async def test_cap_can_be_disabled_via_settings(monkeypatch):
    """max_days <= 0 restores the uncapped pre-fix behaviour — the
    config-only rollback path."""
    from utils import driver_onboarding_reminders as reminders
    from utils.driver_onboarding_reminder_rules import DEFAULT_MAX_REMINDERS_PER_TYPE as CAP

    prior = [
        {"driver_id": "driver-1", "reminder_type": reminders.VEHICLE_DETAILS, "local_date": f"2026-05-{d:02d}"}
        for d in range(1, CAP + 5)
    ]
    fake_db = FakeReminderDB(preexisting_log=prior)
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)
    monkeypatch.setattr(
        reminders, "_get_app_settings", AsyncMock(return_value={"driver_onboarding_reminder_max_days": 0})
    )

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    assert stats == _stats(claims=2, delivered=2)


@pytest.mark.asyncio
async def test_status_allowlist_can_be_widened_via_settings(monkeypatch):
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB(driver_status="active")
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)
    monkeypatch.setattr(
        reminders,
        "_get_app_settings",
        AsyncMock(return_value={"driver_onboarding_reminder_statuses": "pending, active"}),
    )

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    assert stats == _stats(claims=2, delivered=2)


@pytest.mark.asyncio
async def test_failed_reminder_log_read_does_not_complete_window(monkeypatch):
    """Without the claim log we can't tell a first reminder from a 40th, so
    the page must not be processed and the window must stay incomplete."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB()
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)

    fake_db.failing_tables = {"driver_onboarding_reminder_log"}
    failed = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))
    assert failed == _stats(scanned=0, claims=0, delivered=0)
    assert reminders._completed_windows == set()
    send_push.assert_not_awaited()

    fake_db.failing_tables = set()
    retried = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 20, tzinfo=timezone.utc))
    assert retried["pushes_delivered"] == 2


@pytest.mark.asyncio
async def test_settings_read_failure_falls_back_to_safe_defaults(monkeypatch):
    """A settings outage must not re-open the send-to-everyone behaviour."""
    from utils import driver_onboarding_reminders as reminders

    fake_db = FakeReminderDB(driver_status="active")
    send_push = AsyncMock(return_value=True)
    monkeypatch.setattr(reminders, "db", fake_db)
    monkeypatch.setattr(reminders, "send_push_notification", send_push)
    monkeypatch.setattr(reminders, "_get_app_settings", AsyncMock(side_effect=RuntimeError("settings down")))

    stats = await reminders.check_driver_onboarding_reminders(datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc))

    assert stats == _stats(scanned=1, claims=0, delivered=0)
    send_push.assert_not_awaited()
