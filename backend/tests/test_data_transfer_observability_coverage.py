"""
Coverage-gap tests for services/data_transfer/observability.py (A1c
Sub-tier C, Batch 10 pick).

The Data Transfer route/job test files already exercise
`record_export_result`/`record_import_result` indirectly through the code
paths that call them, which is how this file started at ~70%. This file
tests the module directly and closes the remaining gaps:

  - `record_sgi_form_result` (never called from any currently-covered path)
  - `record_export_result`'s optional-duration branch (both with and
    without `duration_ms`)
  - `capture_failure`'s happy path: tags/contexts shape sent to
    `sentry_sdk.capture_message` must match CLAUDE.md's Sentry-tag
    convention (`domain`, `surface`) plus the module's own `spinr_alert`
    tag and `data_transfer` context key
  - `capture_failure`'s best-effort contract: must never raise, whether
    `sentry_sdk` itself is unimportable (SENTRY_DSN-unset-equivalent) or
    `capture_message` itself blows up
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import services.data_transfer.observability as obs
from utils import metrics


def _counter_total(name: str, labels: dict | None = None) -> int:
    key = metrics._labels_to_key(labels)
    return metrics._counters.get(name, {}).get(key, 0)


def test_record_export_result_increments_counter_with_format_and_status():
    before = _counter_total("spinr_data_transfer_export_total", {"format": "csv", "status": "success"})
    obs.record_export_result("success", "csv")
    after = _counter_total("spinr_data_transfer_export_total", {"format": "csv", "status": "success"})
    assert after == before + 1


def test_record_export_result_without_duration_skips_histogram():
    key = metrics._labels_to_key({"format": "xlsx-no-duration"})
    obs.record_export_result("success", "xlsx-no-duration", duration_ms=None)
    # duration_ms=None must short-circuit before the observe() call, so no
    # histogram bucket is ever created for this format.
    assert key not in metrics._histograms.get("spinr_data_transfer_export_duration_ms", {})


def test_record_export_result_with_duration_observes_histogram():
    obs.record_export_result("success", "pdf", duration_ms=42.5)
    key = metrics._labels_to_key({"format": "pdf"})
    bucket = metrics._histograms.get("spinr_data_transfer_export_duration_ms", {}).get(key)
    assert bucket is not None
    assert bucket["count"] >= 1


def test_record_import_result_increments_counter_with_status():
    before = _counter_total("spinr_data_transfer_import_total", {"status": "failed"})
    obs.record_import_result("failed")
    after = _counter_total("spinr_data_transfer_import_total", {"status": "failed"})
    assert after == before + 1


def test_record_sgi_form_result_increments_counter_with_form_type_and_status():
    before = _counter_total("spinr_data_transfer_sgi_form_total", {"form_type": "sgi1", "status": "success"})
    obs.record_sgi_form_result("sgi1", "success")
    after = _counter_total("spinr_data_transfer_sgi_form_total", {"form_type": "sgi1", "status": "success"})
    assert after == before + 1


def test_capture_failure_sends_tagged_sentry_event(monkeypatch):
    fake_sentry = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    obs.capture_failure("export failed", "sgi_export_failed", {"job_id": "j1", "format": "csv"})

    fake_sentry.capture_message.assert_called_once()
    args, kwargs = fake_sentry.capture_message.call_args
    assert args[0] == "export failed"
    assert kwargs["level"] == "error"
    assert kwargs["tags"] == {"spinr_alert": "sgi_export_failed", "domain": "admin", "surface": "backend"}
    assert kwargs["contexts"] == {"data_transfer": {"job_id": "j1", "format": "csv"}}


def test_capture_failure_never_raises_when_sentry_sdk_unimportable(monkeypatch):
    # Simulate SENTRY_DSN-unset-equivalent / package absent: `import sentry_sdk`
    # raises ImportError when the module is registered as None in sys.modules.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)

    # Must not raise -- best-effort contract from the module docstring.
    obs.capture_failure("import failed", "sgi_import_failed", {"job_id": "j2"})


def test_capture_failure_never_raises_when_capture_message_blows_up(monkeypatch):
    fake_sentry = MagicMock()
    fake_sentry.capture_message.side_effect = RuntimeError("sentry transport down")
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    # Must not raise even though capture_message itself errors.
    obs.capture_failure("boom", "sgi_export_failed", {"job_id": "j3"})
    fake_sentry.capture_message.assert_called_once()
