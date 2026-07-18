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
            "route_snapshot_url": "https://maps.example/route-v4.png",
            "route_quality": {"coverage_ratio": 0.91, "missing_tail": False},
        },
        RIDER,
    )

    assert "https://maps.example/route-v4.png" in html
    assert "Actual route (revision 4)" in html
    assert "91% GPS coverage" in html


def test_html_uses_incomplete_fallback_copy_without_a_stale_snapshot() -> None:
    html = generate_receipt_html(
        {
            **RIDE,
            "route_schema_version": 2,
            "route_revision": 5,
            "snapshot_revision": 4,
            "route_snapshot_url": "https://maps.example/route-v4.png",
            "route_quality": {"coverage_ratio": 0.54, "missing_tail": True},
        },
        RIDER,
    )

    assert "https://maps.example/route-v4.png" not in html
    assert "Route snapshot unavailable" in html
    assert "GPS capture was incomplete (54% coverage)" in html
    assert "Actual route (revision" not in html


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


def test_pdf_embeds_snapshot_bytes_and_prints_truthful_quality_note() -> None:
    pdf = generate_receipt_pdf(
        RIDE,
        RIDER,
        tip=Decimal("0"),
        route_snapshot_bytes=_png_bytes(),
        route_snapshot_note="Actual route (revision 4) — 91% GPS coverage.",
        route_snapshot_is_actual=True,
    )

    assert pdf.startswith(b"%PDF")
    assert b"/Image" in pdf
    text = _pdf_text(pdf)
    assert "Actual route" in text
    assert "91% GPS coverage" in text


def test_pdf_uses_incomplete_note_without_calling_a_planned_snapshot_actual() -> None:
    pdf = generate_receipt_pdf(
        RIDE,
        RIDER,
        route_snapshot_note="Route snapshot unavailable — GPS capture was incomplete (54% coverage).",
        route_snapshot_is_actual=False,
    )

    text = _pdf_text(pdf)
    assert "Route snapshot unavailable" in text
    assert "Actual route" not in text
