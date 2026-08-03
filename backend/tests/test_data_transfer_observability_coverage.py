"""Coverage-only tests for backend/services/data_transfer/observability.py.

A1c Sub-tier C — test-only, no application code changed. This file exists
purely to close the coverage gap (20 stmts, 5 missing at lines 46-56, the
``capture_failure`` try/except around ``sentry_sdk.capture_message``) by
exercising every branch directly, since the two existing call sites
(test_admin_sgi_forms_coverage.py, test_data_transfer_export_route.py) only
ever mock ``capture_failure`` itself and never touch its internals.

No pytest run was performed while authoring this file per task instructions;
behavior was verified by careful reading of the source and of the
sibling pattern in test_refresh_token_reuse_detection.py
(test_cascade_swallows_sentry_capture_failure), which patches
``sentry_sdk.capture_message`` directly rather than mocking the whole
`sentry_sdk` module, since sentry_sdk is a real installed dependency here
(imported unconditionally in server.py) — not an optional/absent one.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.data_transfer.observability import (
    capture_failure,
    record_export_result,
    record_import_result,
    record_sgi_form_result,
)


def test_record_export_result_without_duration():
    """duration_ms=None (the default) must skip the histogram observation
    entirely — covers the `if duration_ms is not None` false branch."""
    with (
        patch("backend.services.data_transfer.observability.metrics.inc") as inc,
        patch("backend.services.data_transfer.observability.metrics.observe") as observe,
    ):
        record_export_result("success", "csv")

    inc.assert_called_once_with("spinr_data_transfer_export_total", {"format": "csv", "status": "success"})
    observe.assert_not_called()


def test_record_export_result_with_duration():
    """duration_ms provided must feed both the counter and the histogram."""
    with (
        patch("backend.services.data_transfer.observability.metrics.inc") as inc,
        patch("backend.services.data_transfer.observability.metrics.observe") as observe,
    ):
        record_export_result("failed", "pdf", duration_ms=123.4)

    inc.assert_called_once_with("spinr_data_transfer_export_total", {"format": "pdf", "status": "failed"})
    observe.assert_called_once_with("spinr_data_transfer_export_duration_ms", 123.4, {"format": "pdf"})


def test_record_export_result_zero_duration_still_observes():
    """duration_ms=0.0 is falsy but not None — must still be recorded.
    Guards against a future `if duration_ms:` regression that would silently
    drop legitimate zero-duration observations."""
    with (
        patch("backend.services.data_transfer.observability.metrics.inc"),
        patch("backend.services.data_transfer.observability.metrics.observe") as observe,
    ):
        record_export_result("success", "csv", duration_ms=0.0)

    observe.assert_called_once_with("spinr_data_transfer_export_duration_ms", 0.0, {"format": "csv"})


def test_record_import_result():
    with patch("backend.services.data_transfer.observability.metrics.inc") as inc:
        record_import_result("partial")

    inc.assert_called_once_with("spinr_data_transfer_import_total", {"status": "partial"})


def test_record_sgi_form_result():
    with patch("backend.services.data_transfer.observability.metrics.inc") as inc:
        record_sgi_form_result("SGI-1234", "generated")

    inc.assert_called_once_with(
        "spinr_data_transfer_sgi_form_total",
        {"form_type": "SGI-1234", "status": "generated"},
    )


def test_capture_failure_sends_tagged_sentry_event_on_success():
    """Happy path: sentry_sdk import succeeds and capture_message succeeds —
    covers lines 46-54 (the try body)."""
    with patch("sentry_sdk.capture_message") as capture_message:
        capture_failure(
            "SGI form generation failed",
            alert="sgi_form_failure",
            contexts={"entity_id": "d1", "form_type": "SGI-1234"},
        )

    capture_message.assert_called_once_with(
        "SGI form generation failed",
        level="error",
        tags={
            "spinr_alert": "sgi_form_failure",
            "domain": "admin",
            "surface": "backend",
        },
        contexts={"data_transfer": {"entity_id": "d1", "form_type": "SGI-1234"}},
    )


def test_capture_failure_swallows_sentry_exception():
    """capture_message raising (misconfigured DSN, network blip, whatever)
    must be swallowed, not propagated — covers the except branch, lines
    55-56. This is the module's core promise: 'best-effort, never raises'."""
    with patch("sentry_sdk.capture_message", side_effect=RuntimeError("sentry unreachable")):
        # MUST NOT raise.
        capture_failure(
            "export job failed",
            alert="export_failure",
            contexts={"entity_id": "d1"},
        )


def test_capture_failure_swallows_import_error():
    """If sentry_sdk itself can't be imported (optional dependency missing
    in some deployment), the broad `except Exception` must still swallow
    it silently rather than raise — covers the except branch via the
    `import sentry_sdk` statement itself failing rather than the
    capture_message call failing."""
    with patch.dict("sys.modules", {"sentry_sdk": None}):
        # With sys.modules['sentry_sdk'] = None, `import sentry_sdk` raises
        # ImportError (Python's import system treats a None entry as a
        # cached "this module failed to import" sentinel).
        capture_failure(
            "import job failed",
            alert="import_failure",
            contexts={"entity_id": "d1"},
        )


def test_capture_failure_context_shape_is_nested_under_data_transfer_key():
    """Regression guard: the caller-supplied `contexts` dict must be nested
    under a single 'data_transfer' Sentry context key, not spread at the
    top level of the `contexts=` kwarg (which would silently collide with
    any other context Sentry attaches, e.g. 'runtime' or 'os')."""
    fake_sentry = MagicMock()
    with patch.dict("sys.modules", {"sentry_sdk": fake_sentry}):
        capture_failure("msg", alert="a", contexts={"foo": "bar"})

    _, kwargs = fake_sentry.capture_message.call_args
    assert set(kwargs["contexts"].keys()) == {"data_transfer"}
    assert kwargs["contexts"]["data_transfer"] == {"foo": "bar"}
