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


def test_html_silently_omits_map_when_gps_capture_incomplete() -> None:
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
    assert "Route snapshot unavailable" not in html
    assert "GPS capture was incomplete" not in html
    assert "Actual route (revision" not in html
    assert "Pickup" in html
    assert "Dropoff" in html


def test_html_never_calls_a_legacy_planned_snapshot_an_actual_route() -> None:
    html = generate_receipt_html({**RIDE, "route_snapshot_url": "https://maps.example/planned.png"}, RIDER)

    assert "https://maps.example/planned.png" in html
    assert "Planned route" in html
    assert "Actual route" not in html


def test_html_cid_src_overrides_the_hotlinked_snapshot_url() -> None:
    html = generate_receipt_html(
        {
            **RIDE,
            "route_schema_version": 2,
            "route_revision": 4,
            "snapshot_revision": 4,
            "route_snapshot_url": "https://maps.example/route-v4.png",
            "route_quality": {"coverage_ratio": 0.91},
        },
        RIDER,
        include_route_snapshot=False,
        route_snapshot_src="cid:spinr-route-snapshot",
    )

    assert "https://maps.example/route-v4.png" not in html
    assert 'src="cid:spinr-route-snapshot"' in html
    assert "Actual route (revision 4)" in html
    assert "A permanent map copy is attached" not in html


def test_html_rejects_non_cid_route_snapshot_src() -> None:
    html = generate_receipt_html(
        {
            **RIDE,
            "route_schema_version": 2,
            "route_revision": 4,
            "snapshot_revision": 4,
            "route_snapshot_url": "https://maps.example/route-v4.png",
            "route_quality": {"coverage_ratio": 0.91},
        },
        RIDER,
        include_route_snapshot=False,
        route_snapshot_src='https://evil.example/x.png" onerror="alert(1)',
    )

    assert "evil.example" not in html
    assert "onerror" not in html
    assert "https://maps.example/route-v4.png" not in html
    assert "cid:spinr-route-snapshot" not in html
    assert "Actual route (revision 4)" in html


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


def test_email_receipt_embeds_private_snapshot_via_cid_without_an_expiring_html_url(monkeypatch) -> None:
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
    send = AsyncMock(
        return_value=email_provider.EmailDeliveryResult(status=email_provider.EmailDeliveryStatus.accepted)
    )
    pdf_kwargs = {}

    def _fake_pdf(*_args, **kwargs):
        pdf_kwargs.update(kwargs)
        return b"pdf"

    monkeypatch.setattr(email_receipt, "_await_route_receipt_projection", AsyncMock(return_value=route))
    monkeypatch.setattr(email_receipt, "_download_route_snapshot", AsyncMock(return_value=_png_bytes()))
    monkeypatch.setattr(receipt_pdf, "generate_receipt_pdf", _fake_pdf)
    monkeypatch.setattr(email_receipt, "send_transactional_email_result", send)

    assert asyncio.run(email_receipt.send_receipt_email(RIDE, RIDER, recipient_email="rider@example.test"))

    payload = send.await_args.kwargs
    assert signed_url not in payload["html"]
    assert 'src="cid:spinr-route-snapshot"' in payload["html"]
    assert "Actual route (revision 4)" in payload["html"]
    assert "A permanent map copy is attached" not in payload["html"]
    assert pdf_kwargs["route_snapshot_bytes"] == _png_bytes()
    assert payload["attachments"] == [
        {"filename": "Spinr-receipt-SPIN-1.pdf", "content": b"pdf", "mime": "application/pdf"},
        {
            "filename": "Spinr-route-SPIN-1.png",
            "content": _png_bytes(),
            "mime": "image/png",
            "content_id": "spinr-route-snapshot",
        },
    ]


def test_email_receipt_omits_cid_when_snapshot_download_fails(monkeypatch) -> None:
    """A failed snapshot fetch must not hotlink the signed URL or attach a PNG."""
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
    send = AsyncMock(
        return_value=email_provider.EmailDeliveryResult(status=email_provider.EmailDeliveryStatus.accepted)
    )
    monkeypatch.setattr(email_receipt, "_await_route_receipt_projection", AsyncMock(return_value=route))
    monkeypatch.setattr(email_receipt, "_download_route_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(receipt_pdf, "generate_receipt_pdf", lambda *_args, **_kwargs: b"pdf")
    monkeypatch.setattr(email_receipt, "send_transactional_email_result", send)

    assert asyncio.run(email_receipt.send_receipt_email(RIDE, RIDER, recipient_email="rider@example.test"))

    payload = send.await_args.kwargs
    assert signed_url not in payload["html"]
    assert "cid:spinr-route-snapshot" not in payload["html"]
    assert 'width="472"' not in payload["html"]
    assert "Actual route (revision 4)" in payload["html"]
    assert "91% GPS coverage" in payload["html"]
    assert payload["attachments"] == [
        {"filename": "Spinr-receipt-SPIN-1.pdf", "content": b"pdf", "mime": "application/pdf"},
    ]


def test_build_receipt_pdf_bytes_reuses_the_same_snapshot_and_generator_wiring(monkeypatch) -> None:
    """R9 (docs/audit/ride-experience/ROADMAP.md): the rider-app download
    endpoint (routes/rides/receipts.py::get_ride_receipt_pdf) calls this
    helper for the exact PDF bytes the emailed receipt already uses - same
    route-snapshot resolution, same generate_receipt_pdf call, same branded
    company - not a second, independently-implemented renderer."""
    from backend.utils import email_receipt, receipt_pdf

    route = {**RIDE, "route_schema_version": 2, "route_revision": 1, "snapshot_revision": 1}
    monkeypatch.setattr(email_receipt, "_await_route_receipt_projection", AsyncMock(return_value=route))
    monkeypatch.setattr(email_receipt, "_download_route_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(email_receipt, "_branded_company", AsyncMock(return_value=None))
    captured = {}

    def _fake_generate(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return b"%PDF-from-shared-generator"

    monkeypatch.setattr(receipt_pdf, "generate_receipt_pdf", _fake_generate)

    result = asyncio.run(email_receipt.build_receipt_pdf_bytes(RIDE, RIDER, None, Decimal("0")))

    assert result == b"%PDF-from-shared-generator"
    # Same positional (ride, rider, driver, tip) contract generate_receipt_pdf
    # already has for the email-attachment path.
    assert captured["args"][:2] == (route, RIDER)
    assert "route_snapshot_bytes" in captured["kwargs"]
    assert "company" in captured["kwargs"]


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


def test_pdf_omits_diagnostic_note_when_snapshot_unavailable() -> None:
    pdf = generate_receipt_pdf(
        RIDE,
        RIDER,
        route_snapshot_note="",
        route_snapshot_is_actual=False,
    )

    text = _pdf_text(pdf)
    assert "Route snapshot unavailable" not in text
    assert "Actual route" not in text
