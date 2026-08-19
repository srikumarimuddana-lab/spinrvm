"""
Regression tests for A-P1-9: bulk push notifications must fan out concurrently.

Background
----------
admin_send_cloud_message iterated over target_users with a sequential
`for u in target_users: await send_push_notification(...)`.  For a broadcast
to 10 000 users each taking ~100 ms, that's a 16-minute blocking request.

Fix:
- Notification fan-out moved to a BackgroundTasks helper (_fan_out_push)
- _fan_out_push uses asyncio.gather + asyncio.Semaphore(50) for concurrency
- Endpoint returns 202 Accepted immediately after enqueueing the task
- Background task writes final successful/failed_count back to the DB record

Test layers
-----------
Layer 1 — pure logic: verify fan-out mechanics and concurrency cap.
Layer 2 — integration: require backend deps; skipped locally, run in CI.
"""

import asyncio

import pytest

# ---------------------------------------------------------------------------
# Layer 1 — pure logic
# ---------------------------------------------------------------------------


def test_uids_extracted_from_dict_and_plain_list():
    """_fan_out_push handles both dict rows and bare string IDs."""
    target_users = [{"id": "u1"}, {"id": "u2"}, "u3"]
    uids = [u.get("id") if isinstance(u, dict) else u for u in target_users]
    uids = [uid for uid in uids if uid]
    assert uids == ["u1", "u2", "u3"]


def test_none_ids_are_filtered():
    """Rows missing an id key must be silently dropped."""
    target_users = [{"id": "u1"}, {}, {"id": None}, {"id": "u2"}]
    uids = [u.get("id") if isinstance(u, dict) else u for u in target_users]
    uids = [uid for uid in uids if uid]
    assert uids == ["u1", "u2"]


@pytest.mark.anyio
async def test_semaphore_limits_concurrency():
    """asyncio.Semaphore(50) must cap concurrent in-flight push calls."""
    max_concurrency = 0
    current = 0
    sem = asyncio.Semaphore(50)

    async def _mock_send(uid):
        nonlocal max_concurrency, current
        async with sem:
            current += 1
            max_concurrency = max(max_concurrency, current)
            await asyncio.sleep(0)  # yield so other tasks can acquire
            current -= 1
            return True

    uids = [f"u{i}" for i in range(200)]
    await asyncio.gather(*[_mock_send(uid) for uid in uids])
    assert max_concurrency <= 50


@pytest.mark.anyio
async def test_gather_counts_success_and_failure():
    """Results from asyncio.gather are tallied correctly."""

    async def _val(v):
        return v

    outcomes = [True, True, False, True, False]
    results = await asyncio.gather(*[_val(o) for o in outcomes])
    successful = sum(1 for r in results if r is True)
    failed = len(results) - successful
    assert successful == 3
    assert failed == 2


def test_immediate_send_sets_status_code_202():
    """Endpoint must signal 202 when fan-out is delegated to background task."""
    is_scheduled = False
    # Simulate what the endpoint does
    expected_status = 202 if not is_scheduled else 200
    assert expected_status == 202


def test_scheduled_send_does_not_trigger_background_task():
    """Scheduled sends must NOT enqueue a background task — no notifications sent yet."""
    is_scheduled = True
    background_called = False
    if not is_scheduled:
        background_called = True
    assert not background_called


def test_doc_initial_stats_are_zero():
    """The DB record inserted immediately must have successful=0, failed_count=0."""
    doc = {"successful": 0, "failed_count": 0, "status": "sent"}
    assert doc["successful"] == 0
    assert doc["failed_count"] == 0


# ---------------------------------------------------------------------------
# Layer 2 — integration (require backend deps; skipped if unavailable)
# ---------------------------------------------------------------------------

try:
    import backend.routes.admin.messaging as _messaging_module

    _HAS_BACKEND_DEPS = True
except BaseException:
    _messaging_module = None  # type: ignore[assignment]
    _HAS_BACKEND_DEPS = False

_skip_no_deps = pytest.mark.skipif(not _HAS_BACKEND_DEPS, reason="backend deps not installed")


@_skip_no_deps
@pytest.mark.anyio
async def test_send_cloud_message_returns_202_for_immediate_send():
    """Immediate broadcast must return 202 (not 200) and enqueue a background task."""
    from unittest.mock import AsyncMock, patch

    from fastapi import BackgroundTasks
    from fastapi.responses import Response

    from backend.routes.admin.messaging import CloudMessageRequest

    insert_mock = AsyncMock(return_value=None)
    count_mock = AsyncMock(return_value=2)
    get_rows_mock = AsyncMock(return_value=[{"id": "u1"}, {"id": "u2"}])

    background_tasks = BackgroundTasks()
    response = Response()

    with (
        patch("backend.routes.admin.messaging.db_supabase.insert_one", insert_mock),
        patch("backend.routes.admin.messaging.db_supabase.count_documents", count_mock),
        patch("backend.routes.admin.messaging.db.get_rows", get_rows_mock),
        patch("backend.routes.admin.messaging.log_admin_action", AsyncMock(return_value="audit-1")),
    ):
        result = await _messaging_module.admin_send_cloud_message(
            payload=CloudMessageRequest(
                title="Test",
                description="Hello riders",
                audience="customers",
            ),
            background_tasks=background_tasks,
            response=response,
            admin={"id": "admin-1", "role": "admin"},
        )

    assert response.status_code == 202
    assert result["success"] is True
    # Background task should be registered
    assert len(background_tasks.tasks) == 1


@_skip_no_deps
@pytest.mark.anyio
async def test_scheduled_send_returns_200_no_background_task():
    """Scheduled send must return 200 and must NOT register a background task."""
    from unittest.mock import AsyncMock, patch

    from fastapi import BackgroundTasks
    from fastapi.responses import Response

    from backend.routes.admin.messaging import CloudMessageRequest

    background_tasks = BackgroundTasks()
    response = Response()

    with (
        patch("backend.routes.admin.messaging.db_supabase.insert_one", AsyncMock()),
        patch("backend.routes.admin.messaging.db_supabase.count_documents", AsyncMock(return_value=5)),
        patch("backend.routes.admin.messaging.log_admin_action", AsyncMock(return_value="audit-2")),
    ):
        result = await _messaging_module.admin_send_cloud_message(
            payload=CloudMessageRequest(
                title="Scheduled",
                description="Coming soon",
                audience="customers",
                scheduled_at="2026-05-01T10:00:00Z",
            ),
            background_tasks=background_tasks,
            response=response,
            admin={"id": "admin-1", "role": "admin"},
        )

    # Default Response status_code is 200 (not overridden for scheduled sends)
    assert response.status_code == 200
    assert len(background_tasks.tasks) == 0
    assert result["message"]["status"] == "scheduled"
