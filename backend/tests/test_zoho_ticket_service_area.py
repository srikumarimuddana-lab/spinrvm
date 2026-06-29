"""
Unit tests for Zoho Desk ticket -> Spinr service-area resolution
(services/zoho_ticket_service_area.py).

Covers the "remedial in DB" rule: a ticket's contact email resolving to a known
user whose most recent ride has a service area -> auto-assign; otherwise the
ticket is flagged for (optional) manual assignment.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import services.zoho_ticket_service_area as tsa

pytestmark = pytest.mark.anyio


def _patch(monkeypatch, *, user=None, rides=None, area=None, driver=None, update_returns=None):
    """Wire db_supabase helpers used by the module to async stand-ins.

    find_one dispatches by table: users / service_areas / zoho_desk_tickets.
    """
    async def _find_one(table, filters=None):
        if table == "users":
            return user
        if table == "service_areas":
            return area
        if table == "zoho_desk_tickets":
            return update_returns
        return None

    monkeypatch.setattr(tsa.db_supabase, "find_one", AsyncMock(side_effect=_find_one))
    monkeypatch.setattr(tsa.db_supabase, "get_rides_for_user", AsyncMock(return_value=rides or []))
    monkeypatch.setattr(tsa.db_supabase, "get_driver_by_user_id_cached", AsyncMock(return_value=driver))
    monkeypatch.setattr(tsa.db_supabase, "update_one", AsyncMock(return_value=None))


async def test_no_email_no_suggestion(monkeypatch):
    _patch(monkeypatch)
    assert await tsa.resolve_suggested_area({"subject": "hi"}) is None


async def test_unknown_contact_no_suggestion(monkeypatch):
    _patch(monkeypatch, user=None)
    assert await tsa.resolve_suggested_area({"contact": {"email": "ghost@x.com"}}) is None


async def test_user_without_area_no_suggestion(monkeypatch):
    _patch(monkeypatch, user={"id": "u1"}, rides=[{"id": "r1", "service_area_id": None}])
    assert await tsa.resolve_suggested_area({"email": "rider@x.com"}) is None


async def test_user_with_recent_ride_area_suggested(monkeypatch):
    _patch(
        monkeypatch,
        user={"id": "u1"},
        rides=[{"id": "r1", "service_area_id": None}, {"id": "r2", "service_area_id": "sa_regina"}],
        area={"id": "sa_regina", "name": "Regina"},
    )
    out = await tsa.resolve_suggested_area({"contact": {"email": "Rider@X.com"}})
    assert out == {
        "service_area_id": "sa_regina",
        "service_area_name": "Regina",
        "matched_user_id": "u1",
    }


async def test_driver_contact_falls_back_to_driver_home_area(monkeypatch):
    # Driver with no rider rides -> use their driver profile's service area.
    _patch(
        monkeypatch,
        user={"id": "u9"},
        rides=[],
        driver={"id": "d9", "service_area_id": "sa_saskatoon"},
        area={"id": "sa_saskatoon", "name": "Saskatoon"},
    )
    out = await tsa.resolve_suggested_area({"contact": {"email": "driver@x.com"}})
    assert out == {
        "service_area_id": "sa_saskatoon",
        "service_area_name": "Saskatoon",
        "matched_user_id": "u9",
    }


async def test_resolution_swallows_db_error(monkeypatch):
    monkeypatch.setattr(tsa.db_supabase, "find_one", AsyncMock(side_effect=RuntimeError("db down")))
    # Best-effort: a DB failure yields no suggestion rather than propagating.
    assert await tsa.resolve_suggested_area({"email": "rider@x.com"}) is None


async def test_assignment_view_auto_assigns_and_persists(monkeypatch):
    # Mirror row starts unassigned, then reflects the area after the auto write.
    async def _find_one(table, filters=None):
        if table == "users":
            return {"id": "u1"}
        if table == "service_areas":
            return {"id": "sa_regina", "name": "Regina"}
        if table == "zoho_desk_tickets":
            # First call (current) -> no area; later calls -> assigned.
            return _find_one.row
        return None

    _find_one.row = {"zoho_id": "T1", "service_area_id": None}
    monkeypatch.setattr(tsa.db_supabase, "find_one", AsyncMock(side_effect=_find_one))
    monkeypatch.setattr(
        tsa.db_supabase,
        "get_rides_for_user",
        AsyncMock(return_value=[{"id": "r2", "service_area_id": "sa_regina"}]),
    )

    async def _update_one(table, filters, updates, upsert=False):
        # Reflect the write so the post-write get_ticket_area sees the area.
        _find_one.row = {"zoho_id": "T1", **updates}

    monkeypatch.setattr(tsa.db_supabase, "update_one", AsyncMock(side_effect=_update_one))

    view = await tsa.assignment_view("T1", {"contact": {"email": "rider@x.com"}})
    assert view["needs_assignment"] is False
    assert view["service_area_id"] == "sa_regina"
    assert view["service_area_source"] == "auto"
    assert view["service_area_assigned_by"] == "system"


async def test_assignment_view_highlights_when_no_match(monkeypatch):
    _patch(monkeypatch, user=None, update_returns={"zoho_id": "T2", "service_area_id": None})
    view = await tsa.assignment_view("T2", {"contact": {"email": "ghost@x.com"}})
    assert view["needs_assignment"] is True
    assert view["suggested"] is None
    assert view.get("service_area_id") is None
