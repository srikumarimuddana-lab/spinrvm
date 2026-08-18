"""C23 items 4-5: utils/dispute_evidence_pack.py -- the shared evidence
assembly consumed by both the zip-download endpoint (item 4) and the
Stripe-submission endpoint (item 5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import dispute_evidence_pack as pack

pytestmark = pytest.mark.anyio


class TestBuildGpsTrailRows:
    def test_filters_to_ride_relevant_phases_only(self):
        ride = {
            "location_trail": [
                {"tracking_phase": "online_idle", "lat": 1, "lng": 1, "timestamp": "t0"},
                {"tracking_phase": "navigating_to_pickup", "lat": 2, "lng": 2, "timestamp": "t1"},
                {"tracking_phase": "trip_in_progress", "lat": 3, "lng": 3, "timestamp": "t2"},
                {"tracking_phase": "returning_to_service_area", "lat": 4, "lng": 4, "timestamp": "t3"},
            ]
        }
        rows = pack.build_gps_trail_rows(ride)
        assert [r["phase"] for r in rows] == ["navigating_to_pickup", "trip_in_progress"]

    def test_drops_points_missing_coordinates(self):
        ride = {
            "location_trail": [
                {"tracking_phase": "trip_in_progress", "lat": None, "lng": 1, "timestamp": "t"},
                {"tracking_phase": "trip_in_progress", "lat": 1, "lng": None, "timestamp": "t"},
            ]
        }
        assert pack.build_gps_trail_rows(ride) == []

    def test_empty_trail_returns_empty_list(self):
        assert pack.build_gps_trail_rows({}) == []


class TestBuildRideTimeline:
    def test_orders_events_chronologically(self):
        ride = {
            "created_at": "2026-01-01T10:00:00+00:00",
            "offers": [
                {
                    "driver_name": "D1",
                    "status": "declined",
                    "offered_at": "2026-01-01T10:01:00+00:00",
                    "responded_at": "2026-01-01T10:01:05+00:00",
                },
                {
                    "driver_name": "D2",
                    "status": "accepted",
                    "offered_at": "2026-01-01T10:02:00+00:00",
                    "responded_at": "2026-01-01T10:02:05+00:00",
                },
            ],
            "driver_accepted_at": "2026-01-01T10:02:05+00:00",
            "driver_arrived_at": "2026-01-01T10:10:00+00:00",
            "ride_started_at": "2026-01-01T10:12:00+00:00",
            "ride_completed_at": "2026-01-01T10:30:00+00:00",
        }
        events = pack.build_ride_timeline(ride)
        timestamps = [e["at"] for e in events]
        assert timestamps == sorted(timestamps)
        assert events[0]["event"] == "ride_requested"
        assert events[-1]["event"] == "trip_completed"

    def test_missing_timestamps_are_skipped_not_raised(self):
        ride = {"created_at": None, "offers": []}
        assert pack.build_ride_timeline(ride) == []

    def test_offer_events_labeled_with_driver_name(self):
        ride = {
            "created_at": "2026-01-01T10:00:00+00:00",
            "offers": [
                {
                    "driver_name": "Alex",
                    "status": "accepted",
                    "offered_at": "2026-01-01T10:01:00+00:00",
                    "responded_at": "2026-01-01T10:01:30+00:00",
                },
            ],
        }
        events = pack.build_ride_timeline(ride)
        labels = [e["event"] for e in events]
        assert any("offer_sent (driver Alex)" == label for label in labels)
        assert any("offer_accepted (driver Alex)" == label for label in labels)


class TestBuildAccountHistorySummary:
    async def test_no_rider_id_returns_defaults(self):
        summary = await pack.build_account_history_summary({"driver_code": "DR-1"})
        assert summary["driver_code"] == "DR-1"
        assert summary["rider_account_created_at"] is None
        assert summary["rider_completed_ride_count"] is None

    async def test_aggregates_rider_history(self):
        ride = {"rider_id": "rider-1", "driver_code": "DR-2", "driver_completed_rides": 42}

        async def _get_rows(table, filters, **kwargs):
            if table == "users":
                return [{"id": "rider-1", "created_at": "2024-01-01T00:00:00+00:00"}]
            if table == "rides":
                return [{"id": "ride-a"}, {"id": "ride-b"}]
            if table == "stripe_disputes":
                return [{"id": "sd-1"}]
            raise AssertionError(table)

        with (
            patch.object(pack, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(pack, "count_documents", AsyncMock(return_value=7)),
        ):
            summary = await pack.build_account_history_summary(ride)

        assert summary["rider_account_created_at"] == "2024-01-01T00:00:00+00:00"
        assert summary["rider_completed_ride_count"] == 7
        assert summary["rider_prior_dispute_count"] == 1
        assert summary["driver_completed_ride_count"] == 42

    async def test_rider_lookup_failure_does_not_raise(self):
        ride = {"rider_id": "rider-2"}
        with (
            patch.object(pack, "get_rows", AsyncMock(side_effect=Exception("db down"))),
            patch.object(pack, "count_documents", AsyncMock(side_effect=Exception("db down"))),
        ):
            summary = await pack.build_account_history_summary(ride)
        assert summary["rider_account_created_at"] is None
        assert summary["rider_completed_ride_count"] is None
        assert summary["rider_prior_dispute_count"] is None

    async def test_no_prior_rides_gives_zero_disputes_not_none(self):
        ride = {"rider_id": "rider-3"}

        async def _get_rows(table, filters, **kwargs):
            if table == "users":
                return []
            if table == "rides":
                return []
            raise AssertionError(table)

        with (
            patch.object(pack, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(pack, "count_documents", AsyncMock(return_value=0)),
        ):
            summary = await pack.build_account_history_summary(ride)
        assert summary["rider_prior_dispute_count"] == 0


class TestBuildCoverLetterText:
    def test_includes_ride_code_amount_and_reason(self):
        ride = {"ride_code": "SPN-999"}
        dispute = {"stripe_dispute_id": "dp_1", "amount_cents": 2550, "reason": "fraudulent"}
        text = pack.build_cover_letter_text(ride, dispute, {})
        assert "SPN-999" in text
        assert "$25.50" in text
        assert "fraudulent" in text

    def test_flags_prior_disputes_when_present(self):
        ride = {"ride_code": "SPN-1"}
        dispute = {"stripe_dispute_id": "dp_1", "amount_cents": 100, "reason": "duplicate"}
        text = pack.build_cover_letter_text(ride, dispute, {"rider_prior_dispute_count": 3})
        assert "3 prior" in text

    def test_no_prior_disputes_note_when_zero(self):
        ride = {"ride_code": "SPN-1"}
        dispute = {"stripe_dispute_id": "dp_1", "amount_cents": 100, "reason": "duplicate"}
        text = pack.build_cover_letter_text(ride, dispute, {"rider_prior_dispute_count": 0})
        assert "prior dispute record" not in text
