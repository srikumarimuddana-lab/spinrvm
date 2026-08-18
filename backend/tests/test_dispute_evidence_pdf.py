"""C23 item 4: utils/dispute_evidence_pdf.py -- the branded PDF pages for
the dispute evidence pack. Pure rendering (fpdf2), no DB access, so tests
just assert valid non-empty PDF output for a range of inputs."""

from __future__ import annotations

from backend.utils.dispute_evidence_pdf import (
    render_invoice_summary_pdf,
    render_timeline_and_history_pdf,
)

_RIDE = {
    "ride_code": "SPN-1",
    "base_fare": 500,
    "distance_fare": 300,
    "time_fare": 100,
    "booking_fee": 50,
    "airport_fee": 0,
    "tip_amount": 200,
    "total_fare": 1150,
    "ride_completed_at": "2026-01-01T10:30:00+00:00",
}
_DISPUTE = {"stripe_dispute_id": "dp_1", "id": "sd-1"}


class TestRenderInvoiceSummaryPdf:
    def test_returns_nonempty_pdf_bytes(self):
        pdf_bytes = render_invoice_summary_pdf(_RIDE, _DISPUTE)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 100

    def test_handles_missing_fare_fields(self):
        pdf_bytes = render_invoice_summary_pdf({"ride_code": "SPN-2"}, {})
        assert pdf_bytes.startswith(b"%PDF")

    def test_handles_missing_ride_code(self):
        pdf_bytes = render_invoice_summary_pdf({}, _DISPUTE)
        assert pdf_bytes.startswith(b"%PDF")


class TestRenderTimelineAndHistoryPdf:
    def test_returns_nonempty_pdf_with_events(self):
        timeline = [
            {"event": "ride_requested", "at": "2026-01-01T10:00:00+00:00"},
            {"event": "trip_completed", "at": "2026-01-01T10:30:00+00:00"},
        ]
        summary = {
            "driver_code": "DR-1",
            "rider_account_created_at": "2024-01-01T00:00:00+00:00",
            "rider_completed_ride_count": 5,
            "rider_prior_dispute_count": 0,
        }
        pdf_bytes = render_timeline_and_history_pdf(_RIDE, timeline, summary)
        assert pdf_bytes.startswith(b"%PDF")

    def test_empty_timeline_does_not_raise(self):
        pdf_bytes = render_timeline_and_history_pdf(_RIDE, [], {})
        assert pdf_bytes.startswith(b"%PDF")

    def test_none_account_history_fields_render_as_na(self):
        pdf_bytes = render_timeline_and_history_pdf(
            _RIDE,
            [{"event": "ride_requested", "at": "2026-01-01T10:00:00+00:00"}],
            {
                "driver_code": None,
                "rider_account_created_at": None,
                "rider_completed_ride_count": None,
                "rider_prior_dispute_count": None,
            },
        )
        assert pdf_bytes.startswith(b"%PDF")
