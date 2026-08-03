"""Coverage-focused supplement to test_apns_client.py.

Targets the branches that file's happy/sad-path suite doesn't exercise:
config-load exception + malformed .p8 (warn-once branches), the real
_load_templates() disk read plus its missing-file/invalid-json except
branches, _get_client()/aclose() connection-pool lifecycle, the
httpx-not-installed and empty-push-token early returns, the use_sandbox=None
auto-detect-from-ENV branch, a second-attempt-also-fails retry path, a
raised-exception-during-POST path, and _reason()'s own except branch.

Written by reading backend/utils/apns_client.py only — not executed locally
per task instructions; a separate pass runs the full suite.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

try:
    from backend.utils import apns_client
except ImportError:  # pragma: no cover - bare-path test runs
    from utils import apns_client

try:
    from backend.core.config import settings as _core_settings
except ImportError:  # pragma: no cover - bare-path test runs
    from core.config import settings as _core_settings


def _p8_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _settings() -> dict:
    return {
        "apns_key_id": "KEYID12345",
        "apns_team_id": "TEAMID6789",
        "apns_bundle_id": "com.spinr.user",
        "apns_p8_key": _p8_pem(),
    }


def _resp(status: int, reason: str | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value={"reason": reason} if reason else {})
    r.text = reason or ""
    return r


def _patch(monkeypatch, *, settings=None, templates=None, responses=None) -> MagicMock:
    """Mirrors test_apns_client.py's helper so both files behave consistently."""
    monkeypatch.setattr(
        apns_client,
        "get_app_settings",
        AsyncMock(return_value=_settings() if settings is None else settings),
    )
    apns_client._templates = {"driver_accepted:none": "BLOB"} if templates is None else templates
    apns_client._token_cache.clear()
    apns_client._config_warned = False
    apns_client._pem_warned = False
    client = MagicMock()
    client.post = AsyncMock(side_effect=responses or [_resp(200)])
    monkeypatch.setattr(apns_client, "_get_client", lambda: client)
    return client


_CONTENT = {"status": "driver_accepted", "eta_minutes": None}


# --- _load_apns_config: exception + malformed-pem branches ----------------- #


def test_load_apns_config_get_app_settings_raises_returns_none(monkeypatch):
    apns_client._config_warned = False
    apns_client._pem_warned = False
    monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(side_effect=RuntimeError("db down")))
    result = asyncio.run(apns_client._load_apns_config())
    assert result is None


def test_load_apns_config_malformed_pem_returns_none_and_warns_once(monkeypatch):
    apns_client._config_warned = False
    apns_client._pem_warned = False
    bad_settings = {
        "apns_key_id": "K1",
        "apns_team_id": "T1",
        "apns_bundle_id": "B1",
        "apns_p8_key": "this-is-not-a-pem-private-key",
    }
    monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(return_value=bad_settings))
    result = asyncio.run(apns_client._load_apns_config())
    assert result is None
    assert apns_client._pem_warned is True
    # Second call hits the "already warned" guard (still returns None, no re-log).
    result2 = asyncio.run(apns_client._load_apns_config())
    assert result2 is None
    apns_client._pem_warned = False


# --- _load_templates: real disk read + except branches --------------------- #


def test_load_templates_reads_real_file_and_caches():
    apns_client._templates = None
    result = apns_client._load_templates()
    assert isinstance(result, dict)
    # Second call must return the *same* cached object, not re-read the file.
    result2 = apns_client._load_templates()
    assert result2 is result
    apns_client._templates = None


def test_load_templates_missing_file_returns_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(apns_client, "_TEMPLATES_PATH", str(tmp_path / "does_not_exist.json"))
    apns_client._templates = None
    assert apns_client._load_templates() == {}
    apns_client._templates = None


def test_load_templates_invalid_json_returns_empty_dict(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    monkeypatch.setattr(apns_client, "_TEMPLATES_PATH", str(bad))
    apns_client._templates = None
    assert apns_client._load_templates() == {}
    apns_client._templates = None


# --- _get_client / aclose: connection-pool lifecycle ------------------------ #


def test_get_client_creates_once_and_reuses():
    apns_client._client = None
    c1 = apns_client._get_client()
    assert isinstance(c1, apns_client.httpx.AsyncClient)
    c2 = apns_client._get_client()
    assert c1 is c2  # second call must not create a new client
    asyncio.run(c1.aclose())
    apns_client._client = None


def test_aclose_closes_open_client_and_clears_global():
    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()
    apns_client._client = mock_client
    asyncio.run(apns_client.aclose())
    mock_client.aclose.assert_awaited_once()
    assert apns_client._client is None


def test_aclose_is_noop_when_never_opened():
    apns_client._client = None
    asyncio.run(apns_client.aclose())  # must not raise
    assert apns_client._client is None


# --- send_apns_live_activity: early-return guards --------------------------- #


def test_send_returns_false_false_when_httpx_not_installed(monkeypatch):
    monkeypatch.setattr(apns_client, "httpx", None)
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, False)


def test_send_returns_false_false_when_push_token_empty(monkeypatch):
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, False)


# --- send_apns_live_activity: use_sandbox=None auto-detect from ENV -------- #


def test_send_use_sandbox_none_defaults_to_sandbox_outside_production(monkeypatch):
    monkeypatch.setattr(_core_settings, "ENV", "staging")
    client = _patch(monkeypatch, responses=[_resp(200)])
    ok, _dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update"))
    assert ok is True
    url = client.post.await_args.args[0]
    assert "sandbox" in url


def test_send_use_sandbox_none_uses_prod_host_in_production(monkeypatch):
    monkeypatch.setattr(_core_settings, "ENV", "production")
    client = _patch(monkeypatch, responses=[_resp(200)])
    ok, _dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update"))
    assert ok is True
    url = client.post.await_args.args[0]
    assert "sandbox" not in url


# --- send_apns_live_activity: retry-also-fails + POST exception ------------ #


def test_send_403_retry_also_fails_computes_dead_from_new_reason(monkeypatch):
    client = _patch(monkeypatch, responses=[_resp(403, "ExpiredProviderToken"), _resp(400, "BadDeviceToken")])
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, True)
    assert client.post.await_count == 2


def test_send_post_raising_exception_is_swallowed(monkeypatch):
    client = _patch(monkeypatch)
    client.post = AsyncMock(side_effect=RuntimeError("connection reset"))
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, False)


# --- _reason: except branch -------------------------------------------------- #


def test_reason_returns_none_when_response_json_raises():
    resp = MagicMock()
    resp.json = MagicMock(side_effect=ValueError("not json"))
    assert apns_client._reason(resp) is None
