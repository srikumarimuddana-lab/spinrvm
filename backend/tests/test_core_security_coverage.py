"""Coverage for backend/core/security.py (A1c, Sub-tier B — 57.89% coverage,
no dedicated test file).

The module is small: a single function, `init_firebase()`, that wraps
Firebase Admin SDK initialization in defensive try/except so a bad or
missing service-account config degrades to "FCM pushes silently dropped"
(per its own docstring/comment) rather than crashing app startup. This file
exercises every branch:

- Service-account JSON configured, happy path (`Certificate` + `initialize_app`
  both succeed).
- Service-account JSON configured, `initialize_app` raises `ValueError`
  (Firebase's "app already initialized" signal) — caught by the inner
  `except ValueError: pass`, no error logged.
- Service-account JSON configured, `initialize_app` raises a *non*-ValueError
  exception — NOT caught by the inner handler (which only catches
  `ValueError`), propagates up and is caught by the outer `except Exception`,
  which logs via `logger.error(..., exc_info=True)`.
- Service-account JSON configured but not valid JSON — `json.loads` raises,
  caught by the outer handler.
- Service-account JSON configured but `credentials.Certificate(...)` raises
  (e.g. malformed service-account dict) — caught by the outer handler.
- No service-account JSON configured (falsy `settings.FIREBASE_SERVICE_ACCOUNT_JSON`)
  — takes the `firebase_admin.initialize_app()` (no-cred, ADC) branch.
  - Happy path.
  - `initialize_app()` raises — caught by the module's own bare
    `except Exception: pass` (`# noqa: S110`) at that specific call site, so
    NO error is logged in this sub-branch (distinct from the JSON-configured
    failure paths above, which do log). Verified explicitly below.

Dual-import note (CLAUDE.md): loaded as `backend.core.security`, the relative
`from .config import settings` wins, so `settings` and `firebase_admin` are
patched as bound *in this module* (`backend.core.security.settings` /
`backend.core.security.firebase_admin`), matching the session's established
pattern — not the source modules they were imported from. conftest.py's
autouse `patch_external_dependencies` fixture already replaces
`backend.core.security.firebase_admin` with `mock_firebase_admin` for every
test; this file overrides that per-test with its own MagicMock (per the task
instructions) for full, deterministic control of `initialize_app`'s
success/failure behavior in each branch.

No auth/security-bypass bugs found while reading this module — it is a
narrow initialization wrapper with no request-time authorization logic.

Test-only change — no application code modified.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from backend.core import security

pytestmark = pytest.mark.unit


FAKE_SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "spinr-test",
    "private_key": "fake-key",
    "client_email": "fake@spinr-test.iam.gserviceaccount.com",
}


@pytest.fixture
def fake_firebase_admin(monkeypatch):
    """Fresh MagicMock firebase_admin, bound as imported in security.py."""
    fake = MagicMock()
    fake.initialize_app = MagicMock()
    monkeypatch.setattr(security, "firebase_admin", fake)
    return fake


def _set_service_account_json(monkeypatch, value):
    monkeypatch.setattr(security.settings, "FIREBASE_SERVICE_ACCOUNT_JSON", value, raising=False)


# ── Service-account-JSON-configured branch ──────────────────────────────────


def test_configured_happy_path_uses_certificate_credentials(monkeypatch, fake_firebase_admin):
    _set_service_account_json(monkeypatch, json.dumps(FAKE_SERVICE_ACCOUNT))

    fake_cert = MagicMock(name="cert-instance")
    fake_credentials_cls = MagicMock()
    fake_credentials_cls.Certificate = MagicMock(return_value=fake_cert)
    monkeypatch.setattr(security, "firebase_credentials", fake_credentials_cls)

    security.init_firebase()

    fake_credentials_cls.Certificate.assert_called_once_with(FAKE_SERVICE_ACCOUNT)
    fake_firebase_admin.initialize_app.assert_called_once_with(fake_cert)


def test_configured_initialize_app_value_error_is_swallowed_silently(monkeypatch, fake_firebase_admin, caplog):
    """Firebase raises ValueError when the default app is already
    initialized — the inner `except ValueError: pass` swallows it with no
    logging at all (not even a warning)."""
    _set_service_account_json(monkeypatch, json.dumps(FAKE_SERVICE_ACCOUNT))

    fake_credentials_cls = MagicMock()
    fake_credentials_cls.Certificate = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(security, "firebase_credentials", fake_credentials_cls)
    fake_firebase_admin.initialize_app.side_effect = ValueError("The default Firebase app already exists.")

    with caplog.at_level(logging.ERROR, logger="backend.core.security"):
        security.init_firebase()  # must not raise

    assert "Firebase initialization failed" not in caplog.text


def test_configured_initialize_app_non_value_error_propagates_to_outer_handler(
    monkeypatch, fake_firebase_admin, caplog
):
    """A non-ValueError from initialize_app() is NOT caught by the inner
    `except ValueError` — it propagates and is caught by the outer
    `except Exception`, which logs an error with exc_info."""
    _set_service_account_json(monkeypatch, json.dumps(FAKE_SERVICE_ACCOUNT))

    fake_credentials_cls = MagicMock()
    fake_credentials_cls.Certificate = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(security, "firebase_credentials", fake_credentials_cls)
    fake_firebase_admin.initialize_app.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="backend.core.security"):
        security.init_firebase()  # must not raise — outer handler catches it

    assert "Firebase initialization failed" in caplog.text


def test_configured_invalid_json_is_caught_by_outer_handler(monkeypatch, fake_firebase_admin, caplog):
    _set_service_account_json(monkeypatch, "{not valid json")

    with caplog.at_level(logging.ERROR, logger="backend.core.security"):
        security.init_firebase()  # must not raise

    assert "Firebase initialization failed" in caplog.text
    fake_firebase_admin.initialize_app.assert_not_called()


def test_configured_certificate_construction_failure_is_caught_by_outer_handler(
    monkeypatch, fake_firebase_admin, caplog
):
    _set_service_account_json(monkeypatch, json.dumps(FAKE_SERVICE_ACCOUNT))

    fake_credentials_cls = MagicMock()
    fake_credentials_cls.Certificate = MagicMock(side_effect=ValueError("malformed service account"))
    monkeypatch.setattr(security, "firebase_credentials", fake_credentials_cls)

    with caplog.at_level(logging.ERROR, logger="backend.core.security"):
        security.init_firebase()  # must not raise

    assert "Firebase initialization failed" in caplog.text
    fake_firebase_admin.initialize_app.assert_not_called()


# ── No-service-account-JSON branch (ADC / default credentials) ─────────────


def test_unconfigured_happy_path_uses_default_credentials(monkeypatch, fake_firebase_admin):
    _set_service_account_json(monkeypatch, None)

    security.init_firebase()

    fake_firebase_admin.initialize_app.assert_called_once_with()


def test_unconfigured_empty_string_also_takes_default_credentials_branch(monkeypatch, fake_firebase_admin):
    """Empty string is falsy, so it takes the same branch as None/unset."""
    _set_service_account_json(monkeypatch, "")

    security.init_firebase()

    fake_firebase_admin.initialize_app.assert_called_once_with()


def test_unconfigured_initialize_app_failure_is_swallowed_by_local_bare_except(
    monkeypatch, fake_firebase_admin, caplog
):
    """Unlike the JSON-configured failure paths, this specific call site has
    its own local `except Exception: pass  # noqa: S110` — so a failure here
    is swallowed with NO error logged at all, not even by the outer handler
    (the outer handler never gets a chance to see it)."""
    _set_service_account_json(monkeypatch, None)
    fake_firebase_admin.initialize_app.side_effect = RuntimeError("no default credentials found")

    with caplog.at_level(logging.ERROR, logger="backend.core.security"):
        security.init_firebase()  # must not raise

    assert "Firebase initialization failed" not in caplog.text
