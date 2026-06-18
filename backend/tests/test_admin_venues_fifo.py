"""Admin venues FIFO fields + validation (routes/admin/venues.py, ADR-008).

Covers the server-side guards that turn the venues_fifo_lot_exclusive DB CHECK
and the queue_for_venue_id FK into clean 400s:
  * a venue cannot be both a terminal (fifo_enabled) and a feeder lot.
  * a feeder lot cannot queue for itself, a missing venue, or a non-terminal.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.routes.admin import venues as v


def test_fifo_terminal_is_valid():
    body = v.VenueRequest(name="YXE Terminal", center_lat=52.17, center_lng=-106.7, fifo_enabled=True)
    assert body.fifo_enabled is True and body.queue_for_venue_id is None


def test_feeder_lot_is_valid():
    body = v.VenueRequest(name="YXE Cell Lot", center_lat=52.19, center_lng=-106.7, queue_for_venue_id="term-1")
    assert body.fifo_enabled is False and body.queue_for_venue_id == "term-1"


def test_terminal_and_lot_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        v.VenueRequest(
            name="bad",
            center_lat=52.17,
            center_lng=-106.7,
            fifo_enabled=True,
            queue_for_venue_id="term-1",
        )


def test_payload_includes_fifo_fields():
    body = v.VenueRequest(name="lot", center_lat=52.19, center_lng=-106.7, queue_for_venue_id="term-1")
    p = v._payload(body)
    assert p["fifo_enabled"] is False and p["queue_for_venue_id"] == "term-1"


pytestmark = pytest.mark.anyio


async def test_validate_queue_target_noop_without_link():
    body = v.VenueRequest(name="t", center_lat=52.17, center_lng=-106.7, fifo_enabled=True)
    with patch.object(v.db_supabase, "find_one", AsyncMock()) as find:
        await v._validate_queue_target(body)  # should not raise or query
    find.assert_not_called()


async def test_validate_queue_target_rejects_self_reference():
    body = v.VenueRequest(name="lot", center_lat=52.19, center_lng=-106.7, queue_for_venue_id="venue-1")
    with pytest.raises(HTTPException) as exc:
        await v._validate_queue_target(body, venue_id="venue-1")
    assert exc.value.status_code == 400


async def test_validate_queue_target_rejects_missing_terminal():
    body = v.VenueRequest(name="lot", center_lat=52.19, center_lng=-106.7, queue_for_venue_id="ghost")
    with patch.object(v.db_supabase, "find_one", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await v._validate_queue_target(body)
    assert exc.value.status_code == 400


async def test_validate_queue_target_rejects_non_terminal():
    body = v.VenueRequest(name="lot", center_lat=52.19, center_lng=-106.7, queue_for_venue_id="plain")
    with patch.object(v.db_supabase, "find_one", AsyncMock(return_value={"id": "plain", "fifo_enabled": False})):
        with pytest.raises(HTTPException) as exc:
            await v._validate_queue_target(body)
    assert exc.value.status_code == 400


async def test_validate_queue_target_accepts_real_terminal():
    body = v.VenueRequest(name="lot", center_lat=52.19, center_lng=-106.7, queue_for_venue_id="term-1")
    with patch.object(v.db_supabase, "find_one", AsyncMock(return_value={"id": "term-1", "fifo_enabled": True})):
        await v._validate_queue_target(body)  # no raise
