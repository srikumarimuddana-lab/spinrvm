from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

NOW = datetime.now(timezone.utc)


def _driver(did="driver-1", uid="user-1", status="pending", **extra):
    return {
        "id": did,
        "user_id": uid,
        "status": status,
        "service_area_id": "area-1",
        "created_at": (NOW - timedelta(days=3)).isoformat(),
        **extra,
    }


def _user(uid="user-1", photo_status="approved", **extra):
    return {
        "id": uid,
        "first_name": "Alex",
        "last_name": "Driver",
        "email": "alex@example.test",
        "profile_image": "https://cdn.example.test/photo.jpg",
        "profile_image_status": photo_status,
        "updated_at": (NOW - timedelta(hours=2)).isoformat(),
        **extra,
    }


def _make_fake_get_rows(*, drivers=(), users=(), pending_docs=(), areas=()):
    """Dispatch get_rows calls, applying the filters the handler sends."""
    drivers = list(drivers)
    users = list(users)
    pending_docs = list(pending_docs)
    areas = list(areas)

    async def fake_get_rows(table, filters=None, **kwargs):
        filters = filters or {}
        if table == "drivers":
            rows = drivers
            if "status" in filters and isinstance(filters["status"], str):
                rows = [d for d in rows if d["status"] == filters["status"]]
            if "$nin" in (filters.get("status") or {} if isinstance(filters.get("status"), dict) else {}):
                rows = [d for d in rows if d["status"] not in filters["status"]["$nin"]]
            if "id" in filters:
                rows = [d for d in rows if d["id"] in filters["id"]["$in"]]
            if "user_id" in filters:
                rows = [d for d in rows if d["user_id"] in filters["user_id"]["$in"]]
            if "service_area_id" in filters:
                rows = [d for d in rows if d["service_area_id"] == filters["service_area_id"]]
            return rows
        if table == "users":
            if "profile_image_status" in filters:
                return [
                    {"id": u["id"]} for u in users if u.get("profile_image_status") == filters["profile_image_status"]
                ]
            return [u for u in users if u["id"] in filters["id"]["$in"]]
        if table == "driver_documents":
            if filters.get("status") == "pending":
                return pending_docs
            return [d for d in pending_docs if d["driver_id"] in filters["driver_id"]["$in"]]
        if table == "service_areas":
            return areas
        if table == "vehicle_types":
            return []
        raise AssertionError(f"unexpected table: {table}")

    return fake_get_rows


async def _run_queue(fake_get_rows, **kwargs):
    from routes.admin import drivers as admin_drivers

    kwargs.setdefault("limit", 200)  # bypass the FastAPI Query default when calling directly
    with patch.object(admin_drivers.db_supabase, "get_rows", new=AsyncMock(side_effect=fake_get_rows)) as get_rows:
        result = await admin_drivers.admin_get_approval_queue(**kwargs)
    return result, get_rows


@pytest.mark.asyncio
async def test_new_applicant_flags_and_counts():
    fake = _make_fake_get_rows(drivers=[_driver()], users=[_user()])
    result, _ = await _run_queue(fake)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["is_new_applicant"] is True
    assert item["is_resubmission"] is False
    assert item["has_pending_photo"] is False
    assert item["profile_image_status"] == "approved"
    assert result["stats"]["new_applicants"] == 1
    assert result["stats"]["resubmissions"] == 0
    assert result["stats"]["photo_review"] == 0


@pytest.mark.asyncio
async def test_resubmission_uses_earliest_pending_doc_timestamp():
    doc_ts = (NOW - timedelta(hours=5)).isoformat()
    fake = _make_fake_get_rows(
        drivers=[_driver(status="needs_review")],
        users=[_user()],
        pending_docs=[{"id": "doc-1", "driver_id": "driver-1", "status": "pending", "uploaded_at": doc_ts}],
    )
    result, _ = await _run_queue(fake)

    item = result["items"][0]
    assert item["is_resubmission"] is True
    assert item["is_new_applicant"] is False
    assert item["queue_started_at"] == doc_ts
    assert item["pending_docs_count"] == 1
    assert result["stats"]["resubmissions"] == 1
    assert result["stats"]["new_applicants"] == 0


@pytest.mark.asyncio
async def test_photo_only_driver_is_included():
    """Active driver with only a pending photo now enters the queue."""
    fake = _make_fake_get_rows(
        drivers=[_driver(status="active")],
        users=[_user(photo_status="pending_review")],
    )
    result, get_rows = await _run_queue(fake)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["has_pending_photo"] is True
    assert item["is_new_applicant"] is False
    assert item["is_resubmission"] is False
    assert result["stats"]["photo_review"] == 1
    # Wait time approximated from users.updated_at, not drivers.created_at.
    assert item["queue_started_at"] == _user()["updated_at"]

    photo_call = next(c for c in get_rows.await_args_list if c.args[0] == "drivers" and "user_id" in (c.args[1] or {}))
    assert photo_call.args[1]["status"] == {"$nin": ["banned", "rejected"]}


@pytest.mark.asyncio
async def test_rider_with_pending_photo_is_excluded():
    """pending_review user without a drivers row never enters the queue."""
    fake = _make_fake_get_rows(users=[_user(uid="rider-1", photo_status="pending_review")])
    result, _ = await _run_queue(fake)

    assert result["items"] == []
    assert result["stats"]["photo_review"] == 0


@pytest.mark.asyncio
async def test_combined_new_applicant_with_pending_photo_counted_once():
    fake = _make_fake_get_rows(
        drivers=[_driver()],
        users=[_user(photo_status="pending_review")],
    )
    result, _ = await _run_queue(fake)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["is_new_applicant"] is True
    assert item["has_pending_photo"] is True
    assert result["stats"]["new_applicants"] == 1
    assert result["stats"]["photo_review"] == 1
    assert result["stats"]["total_pending"] == 1


@pytest.mark.asyncio
async def test_banned_driver_with_pending_photo_is_excluded():
    fake = _make_fake_get_rows(
        drivers=[_driver(status="banned")],
        users=[_user(photo_status="pending_review")],
    )
    result, _ = await _run_queue(fake)

    assert result["items"] == []
    assert result["stats"]["photo_review"] == 0


@pytest.mark.asyncio
async def test_empty_queue_returns_zeroed_segment_stats():
    fake = _make_fake_get_rows()
    result, _ = await _run_queue(fake)

    assert result["items"] == []
    assert result["stats"]["new_applicants"] == 0
    assert result["stats"]["resubmissions"] == 0
    assert result["stats"]["photo_review"] == 0


@pytest.mark.asyncio
async def test_service_area_filter_propagates_to_photo_fetch():
    fake = _make_fake_get_rows(
        drivers=[
            _driver(status="active"),
            _driver(did="driver-2", uid="user-2", status="active", service_area_id="area-2"),
        ],
        users=[
            _user(photo_status="pending_review"),
            _user(uid="user-2", photo_status="pending_review"),
        ],
    )
    result, get_rows = await _run_queue(fake, service_area_id="area-1")

    assert [it["driver_id"] for it in result["items"]] == ["driver-1"]
    photo_call = next(c for c in get_rows.await_args_list if c.args[0] == "drivers" and "user_id" in (c.args[1] or {}))
    assert photo_call.args[1]["service_area_id"] == "area-1"
