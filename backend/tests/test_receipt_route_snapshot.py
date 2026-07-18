"""Receipt rendering contract for revisioned, truthful route snapshots."""

import asyncio
import io
import re
import zlib
from decimal import Decimal
from unittest.mock import AsyncMock

from PIL import Image

from backend.utils.email_receipt import generate_receipt_html
from backend.utils.receipt_pdf import generate_receipt_pdf

RIDER = {"id": "rider_1", "first_name": "Rae", "last_name": "Rider"}
RIDE = {
    "id": "ride_1",
    "ride_code": "SPIN-1",
    "status": "completed",
    "base_fare": "5.00",
    "distance_fare": "3.00",
    "time_fare": "2.00",
    "booking_fee": "1.00",
    "grand_total": "11.00",
    "distance_km": "4.04",
    "duration_minutes": 13,
    "pickup_address": "Pickup",
    "dropoff_address": "Planned dropoff",
}


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 16), "white").save(output, format="PNG")
    return output.getvalue()


def _pdf_text(pdf_bytes: bytes) -> str:
    """Read FPDF's compressed content stream without adding a test dependency."""
    streams = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, flags=re.DOTALL)
    return "\n".join(zlib.decompress(stream).decode("latin-1") for stream in streams)


def test_html_labels_a_revision_matched_v2_snapshot_as_actual_route() -> None:
    html = generate_receipt_html(
        {
            **RIDE,
            "route_schema_version": 2,
            "route_revision": 4,
            "snapshot_revision": 4,
            "processing_status": "complete",
            "route_snapshot_url": "https://maps.example/route-v4.png",
            "route_quality": {"coverage_ratio": 0.91, "missing_tail": False},
        },
        RIDER,
    )

    assert "https://maps.example/route-v4.png" in html
    # Contract B: a complete route is exactly "Actual route" (revision may be
    # shown), never dressed up with a coverage % or the word "verified".
    assert "Actual route · revision 4" in html
    assert "verified" not in html.lower()
    assert "GPS coverage" not in html
    assert "incomplete" not in html.lower()


def test_html_uses_incomplete_fallback_copy_without_a_stale_snapshot() -> None:
    # route_revision 5 but snapshot_revision 4: the latest revision's image is
    # not published yet, so no stale image may render — but the note must still
    # state the recording is incomplete (never imply a complete route).
    html = generate_receipt_html(
        {
            **RIDE,
            "route_schema_version": 2,
            "route_revision": 5,
            "snapshot_revision": 4,
            "processing_status": "incomplete",
            "route_snapshot_url": "https://maps.example/route-v4.png",
            "route_quality": {"coverage_ratio": 0.54, "missing_tail": True},
        },
        RIDER,
    )

    assert "https://maps.example/route-v4.png" not in html
    assert "Route recording incomplete — 54% GPS coverage" in html
    assert "verified" not in html.lower()
    assert "Actual route" not in html


def test_html_never_calls_a_legacy_planned_snapshot_an_actual_route() -> None:
    html = generate_receipt_html({**RIDE, "route_snapshot_url": "https://maps.example/planned.png"}, RIDER)

    assert "https://maps.example/planned.png" in html
    assert "Planned route" in html
    assert "Actual route" not in html


def test_completed_receipt_waits_for_initial_route_finalization(monkeypatch) -> None:
    from backend import db_supabase
    from backend.utils import email_receipt

    rows = iter(
        [
            [{"processing_status": "processing", "route_schema_version": 2}],
            [
                {
                    "processing_status": "complete",
                    "route_schema_version": 2,
                    "route_revision": 4,
                    "snapshot_revision": 4,
                    "snapshot_object_path": "ride_1/route-v4.png",
                    "route_quality": {"coverage_ratio": 0.91},
                }
            ],
        ]
    )

    async def _get_rows(*_args, **_kwargs):
        return next(rows)

    async def _sleep(_seconds):
        return None

    monkeypatch.setattr(db_supabase, "get_rows", _get_rows)
    monkeypatch.setattr(email_receipt.asyncio, "sleep", _sleep)
    monkeypatch.setattr(
        email_receipt,
        "create_route_snapshot_signed_url",
        AsyncMock(return_value="https://storage.example/signed/route-v4.png"),
    )

    resolved = asyncio.run(email_receipt._await_route_receipt_projection(RIDE))

    assert resolved["route_revision"] == 4
    assert resolved["route_snapshot_url"].endswith("route-v4.png")


def test_completed_receipt_signs_a_private_v2_snapshot(monkeypatch) -> None:
    from backend import db_supabase
    from backend.utils import email_receipt

    async def _get_rows(*_args, **_kwargs):
        return [
            {
                "processing_status": "complete",
                "route_schema_version": 2,
                "route_revision": 4,
                "snapshot_revision": 4,
                "snapshot_object_path": "ride_1/route-v4.png",
                "route_quality": {"coverage_ratio": 0.91},
            }
        ]

    signed_url = "https://storage.example/signed/route-v4.png"
    signer = AsyncMock(return_value=signed_url)
    monkeypatch.setattr(db_supabase, "get_rows", _get_rows)
    monkeypatch.setattr(email_receipt, "create_route_snapshot_signed_url", signer)

    resolved = asyncio.run(email_receipt._await_route_receipt_projection(RIDE))

    signer.assert_awaited_once_with("ride_1/route-v4.png")
    assert resolved["route_snapshot_url"] == signed_url
    assert "snapshot_object_path" not in resolved


def test_email_receipt_attaches_private_snapshot_without_an_expiring_html_url(monkeypatch) -> None:
    """The receipt remains viewable after a temporary storage URL expires."""
    from backend.utils import email_provider, email_receipt, receipt_pdf

    signed_url = "https://storage.example/signed/route-v4.png?expires=900"
    route = {
        **RIDE,
        "route_schema_version": 2,
        "route_revision": 4,
        "snapshot_revision": 4,
        "route_snapshot_url": signed_url,
        "route_quality": {"coverage_ratio": 0.91},
    }
    route["processing_status"] = "complete"
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(email_receipt, "_await_route_receipt_projection", AsyncMock(return_value=route))
    monkeypatch.setattr(email_receipt, "_download_route_snapshot", AsyncMock(return_value=_png_bytes()))
    monkeypatch.setattr(receipt_pdf, "generate_receipt_pdf", lambda *_args, **_kwargs: b"pdf")
    monkeypatch.setattr(email_provider, "send_transactional_email", send)

    assert asyncio.run(email_receipt.send_receipt_email(RIDE, RIDER, recipient_email="rider@example.test"))

    payload = send.await_args.kwargs
    assert signed_url not in payload["html"]
    assert "Actual route · revision 4" in payload["html"]
    # The bytes were attached, so the receipt may say so.
    assert "A permanent map copy is attached to this receipt." in payload["html"]
    assert payload["attachments"] == [
        {"filename": "Spinr-receipt-SPIN-1.pdf", "content": b"pdf", "mime": "application/pdf"},
        {"filename": "Spinr-route-SPIN-1.png", "content": _png_bytes(), "mime": "image/png"},
    ]


def test_pdf_embeds_snapshot_bytes_and_prints_truthful_quality_note() -> None:
    pdf = generate_receipt_pdf(
        RIDE,
        RIDER,
        tip=Decimal("0"),
        route_snapshot_bytes=_png_bytes(),
        route_snapshot_note="Actual route · revision 4",
        route_snapshot_is_actual=True,
    )

    assert pdf.startswith(b"%PDF")
    assert b"/Image" in pdf
    text = _pdf_text(pdf)
    assert "Actual route" in text
    assert "revision 4" in text
    assert "verified" not in text.lower()


def test_pdf_uses_incomplete_note_without_calling_a_planned_snapshot_actual() -> None:
    # No bytes: the standalone-note PDF path must still state incompleteness.
    pdf = generate_receipt_pdf(
        RIDE,
        RIDER,
        route_snapshot_note="Route recording incomplete — 54% GPS coverage",
        route_snapshot_is_actual=False,
    )

    text = _pdf_text(pdf)
    # fpdf core fonts are latin-1, so the em dash renders as a hyphen; assert
    # the halves that straddle it rather than the raw dash.
    assert "Route recording incomplete" in text
    assert "54% GPS coverage" in text
    assert "Actual route" not in text


def test_incomplete_route_note_is_identical_in_email_and_both_pdf_paths() -> None:
    """M-H: an incomplete route says so — identically — everywhere (Contract B)."""
    incomplete_ride = {
        **RIDE,
        "route_schema_version": 2,
        "route_revision": 4,
        "snapshot_revision": 4,
        "processing_status": "incomplete",
        "route_snapshot_url": "https://maps.example/route-v4.png",
        "route_quality": {"coverage_ratio": 0.26, "missing_tail": True},
    }

    note = "Route recording incomplete — 26% GPS coverage"

    # HTML email — note derived by the presentation layer, not hand-fed.
    html = generate_receipt_html(incomplete_ride, RIDER)
    assert note in html
    assert "Actual route" not in html
    assert "verified" not in html.lower()

    # PDF caption path (revision-matched snapshot bytes present).
    pdf_with_map = generate_receipt_pdf(
        incomplete_ride,
        RIDER,
        route_snapshot_bytes=_png_bytes(),
        route_snapshot_note=note,
        route_snapshot_is_actual=True,
    )
    assert b"/Image" in pdf_with_map
    caption_text = _pdf_text(pdf_with_map)
    assert "Route recording incomplete" in caption_text
    assert "26% GPS coverage" in caption_text
    assert "Actual route" not in caption_text

    # PDF standalone-note path (no bytes available).
    pdf_no_map = generate_receipt_pdf(
        incomplete_ride,
        RIDER,
        route_snapshot_note=note,
        route_snapshot_is_actual=False,
    )
    note_text = _pdf_text(pdf_no_map)
    assert "Route recording incomplete" in note_text
    assert "26% GPS coverage" in note_text
    assert "Actual route" not in note_text


def test_resend_after_finalization_re_reads_and_embeds_the_latest_revision(monkeypatch) -> None:
    """M-E: a receipt resent after finalization picks up the newest revision + map."""
    from backend import db_supabase
    from backend.utils import email_provider, email_receipt, receipt_pdf

    # The ride handed to the resend still carries a STALE revision + URL.
    stale_ride = {
        **RIDE,
        "route_schema_version": 2,
        "route_revision": 4,
        "snapshot_revision": 4,
        "route_snapshot_url": "https://maps.example/STALE-route-v4.png",
        "route_quality": {"coverage_ratio": 0.55, "missing_tail": True},
    }

    # ride_routes now holds a newer, complete revision 6 with a published object.
    async def _get_rows(*_args, **_kwargs):
        return [
            {
                "processing_status": "complete",
                "route_schema_version": 2,
                "route_revision": 6,
                "snapshot_revision": 6,
                "snapshot_object_path": "ride_1/route-v6.png",
                "route_quality": {"coverage_ratio": 0.98, "missing_tail": False},
            }
        ]

    async def _sleep(_seconds):
        return None

    send = AsyncMock(return_value=True)
    monkeypatch.setattr(db_supabase, "get_rows", _get_rows)
    monkeypatch.setattr(email_receipt.asyncio, "sleep", _sleep)
    monkeypatch.setattr(
        email_receipt,
        "create_route_snapshot_signed_url",
        AsyncMock(return_value="https://storage.example/signed/route-v6.png"),
    )
    monkeypatch.setattr(email_receipt, "_download_route_snapshot", AsyncMock(return_value=_png_bytes()))
    monkeypatch.setattr(receipt_pdf, "generate_receipt_pdf", lambda *_args, **_kwargs: b"pdf")
    monkeypatch.setattr(email_provider, "send_transactional_email", send)

    assert asyncio.run(email_receipt.send_receipt_email(stale_ride, RIDER, recipient_email="rider@example.test"))

    payload = send.await_args.kwargs
    # The resend used revision 6, not the stale revision 4 it was handed.
    assert "Actual route · revision 6" in payload["html"]
    assert "STALE-route-v4.png" not in payload["html"]
    assert "A permanent map copy is attached to this receipt." in payload["html"]
    assert {"filename": "Spinr-route-SPIN-1.png", "content": _png_bytes(), "mime": "image/png"} in payload[
        "attachments"
    ]


def test_map_attached_claim_is_absent_when_snapshot_bytes_cannot_be_downloaded(monkeypatch) -> None:
    """M-H guard: never claim a map is attached when the download failed."""
    from backend.utils import email_provider, email_receipt, receipt_pdf

    route = {
        **RIDE,
        "route_schema_version": 2,
        "route_revision": 4,
        "snapshot_revision": 4,
        "processing_status": "complete",
        "route_snapshot_url": "https://storage.example/signed/route-v4.png",
        "route_quality": {"coverage_ratio": 0.91, "missing_tail": False},
    }

    send = AsyncMock(return_value=True)
    monkeypatch.setattr(email_receipt, "_await_route_receipt_projection", AsyncMock(return_value=route))
    # Download fails → no bytes to attach.
    monkeypatch.setattr(email_receipt, "_download_route_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(receipt_pdf, "generate_receipt_pdf", lambda *_args, **_kwargs: b"pdf")
    monkeypatch.setattr(email_provider, "send_transactional_email", send)

    assert asyncio.run(email_receipt.send_receipt_email(route, RIDER, recipient_email="rider@example.test"))

    payload = send.await_args.kwargs
    assert "Actual route · revision 4" in payload["html"]
    # The claim must not appear, and only the PDF (no PNG) is attached.
    assert "A permanent map copy is attached" not in payload["html"]
    assert payload["attachments"] == [
        {"filename": "Spinr-receipt-SPIN-1.pdf", "content": b"pdf", "mime": "application/pdf"},
    ]
