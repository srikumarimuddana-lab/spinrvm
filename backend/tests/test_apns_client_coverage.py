"""Coverage gap-closer for utils/apns_client.py (APNs Live Activity client).

test_apns_client.py already covers the template index/ETA-bucket helpers, the
aps payload shape, ships-dark-when-unconfigured / no-template skips, the happy
send path, and 410/400/403-retry status handling by stubbing `_get_client()`
entirely and monkeypatching `get_app_settings`. This file closes the branches
that stubbing style never reaches: `_load_apns_config`'s exception and
malformed-PEM paths, `_load_templates`'s real file I/O (success + missing +
malformed JSON), the real `_get_client`/`aclose` lifecycle, the early
httpx/jwt-unavailable and empty-token guards in `send_apns_live_activity`, the
use_sandbox=None settings-driven branch, the retry-still-fails branch, the
outer exception handler, and `_reason`'s own json()-raises branch.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

try:
    from backend.utils import apns_client
except ImportError:  # pragma: no cover - bare-path test runs
    from utils import apns_client


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


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Every test gets a clean slate on the module-level warn-once flags,
    token cache, template cache, and shared httpx client so tests don't leak
    into each other."""
    apns_client._config_warned = False
    apns_client._pem_warned = False
    apns_client._token_cache.clear()
    apns_client._templates = None
    apns_client._client = None
    yield
    apns_client._client = None
    apns_client._templates = None


# --------------------------------------------------------------------------- #
# _load_apns_config
# --------------------------------------------------------------------------- #


class TestLoadApnsConfig:
    def test_settings_load_raises_returns_none(self, monkeypatch):
        monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(side_effect=RuntimeError("db down")))
        cfg = asyncio.run(apns_client._load_apns_config())
        assert cfg is None

    def test_malformed_pem_returns_none_and_warns_once(self, monkeypatch):
        bad = _settings()
        bad["apns_p8_key"] = "not-a-real-key"
        monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(return_value=bad))
        assert apns_client._pem_warned is False
        cfg = asyncio.run(apns_client._load_apns_config())
        assert cfg is None
        assert apns_client._pem_warned is True

    def test_valid_config_returns_dict(self, monkeypatch):
        monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(return_value=_settings()))
        cfg = asyncio.run(apns_client._load_apns_config())
        assert cfg["key_id"] == "KEYID12345"
        assert "BEGIN PRIVATE KEY" in cfg["p8"]

    def test_missing_keys_returns_none_and_warns_once(self, monkeypatch):
        monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(return_value={}))
        assert apns_client._config_warned is False
        cfg = asyncio.run(apns_client._load_apns_config())
        assert cfg is None
        assert apns_client._config_warned is True


# --------------------------------------------------------------------------- #
# _load_templates — real file I/O
# --------------------------------------------------------------------------- #


class TestLoadTemplates:
    def test_loads_real_bundled_templates_file(self):
        # No monkeypatched path: exercises the real voltra_templates.json
        # shipped alongside this module.
        result = apns_client._load_templates()
        assert isinstance(result, dict)
        # Cached on the module global for subsequent calls.
        assert apns_client._templates is result

    def test_missing_file_yields_empty_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr(apns_client, "_TEMPLATES_PATH", str(tmp_path / "does_not_exist.json"))
        result = apns_client._load_templates()
        assert result == {}

    def test_malformed_json_yields_empty_dict(self, monkeypatch, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid json")
        monkeypatch.setattr(apns_client, "_TEMPLATES_PATH", str(bad_file))
        result = apns_client._load_templates()
        assert result == {}

    def test_valid_json_file_is_loaded_and_cached(self, monkeypatch, tmp_path):
        good_file = tmp_path / "good.json"
        good_file.write_text(json.dumps({"driver_accepted:none": "BLOB"}))
        monkeypatch.setattr(apns_client, "_TEMPLATES_PATH", str(good_file))
        result = apns_client._load_templates()
        assert result == {"driver_accepted:none": "BLOB"}
        # Second call hits the cache, not the file, but should return the same dict.
        assert apns_client._load_templates() is result


# --------------------------------------------------------------------------- #
# _get_client / aclose — real lifecycle
# --------------------------------------------------------------------------- #


class TestClientLifecycle:
    def test_get_client_lazily_creates_and_reuses_instance(self):
        c1 = apns_client._get_client()
        c2 = apns_client._get_client()
        assert c1 is c2
        assert apns_client._client is c1

    def test_aclose_closes_and_clears_shared_client(self):
        apns_client._get_client()
        assert apns_client._client is not None
        asyncio.run(apns_client.aclose())
        assert apns_client._client is None

    def test_aclose_is_a_safe_noop_when_never_opened(self):
        assert apns_client._client is None
        asyncio.run(apns_client.aclose())  # must not raise
        assert apns_client._client is None


# --------------------------------------------------------------------------- #
# send_apns_live_activity — early guards, sandbox resolution, retry/exception
# --------------------------------------------------------------------------- #


_CONTENT = {"status": "driver_accepted", "eta_minutes": None}


class TestSendEarlyGuards:
    def test_returns_false_false_when_httpx_unavailable(self, monkeypatch):
        monkeypatch.setattr(apns_client, "httpx", None)
        ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update"))
        assert (ok, dead) == (False, False)

    def test_returns_false_false_when_jwt_unavailable(self, monkeypatch):
        monkeypatch.setattr(apns_client, "jwt", None)
        ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update"))
        assert (ok, dead) == (False, False)

    def test_returns_false_false_for_empty_push_token(self):
        ok, dead = asyncio.run(apns_client.send_apns_live_activity("", _CONTENT, "update"))
        assert (ok, dead) == (False, False)


class TestSendSandboxResolution:
    def test_use_sandbox_none_reads_env_setting_non_production(self, monkeypatch):
        """use_sandbox omitted (None): resolved from core.config.settings.ENV.
        Non-production -> sandbox host."""
        monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(return_value=_settings()))
        apns_client._templates = {"driver_accepted:none": "BLOB"}
        client = MagicMock()
        client.post = AsyncMock(return_value=_resp(200))
        monkeypatch.setattr(apns_client, "_get_client", lambda: client)

        try:
            from backend.core.config import settings as real_settings
        except ImportError:  # pragma: no cover
            from core.config import settings as real_settings
        monkeypatch.setattr(real_settings, "ENV", "development")

        ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update"))
        assert (ok, dead) == (True, False)
        url = client.post.await_args.args[0]
        assert "sandbox" in url

    def test_use_sandbox_none_reads_env_setting_production(self, monkeypatch):
        monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(return_value=_settings()))
        apns_client._templates = {"driver_accepted:none": "BLOB"}
        client = MagicMock()
        client.post = AsyncMock(return_value=_resp(200))
        monkeypatch.setattr(apns_client, "_get_client", lambda: client)

        try:
            from backend.core.config import settings as real_settings
        except ImportError:  # pragma: no cover
            from core.config import settings as real_settings
        monkeypatch.setattr(real_settings, "ENV", "production")

        ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update"))
        assert (ok, dead) == (True, False)
        url = client.post.await_args.args[0]
        assert "sandbox" not in url
        assert url.startswith(apns_client._PROD_HOST)


class TestSendRetryAndExceptionPaths:
    def test_retry_after_expired_token_still_fails(self, monkeypatch):
        """403 ExpiredProviderToken busts the cache and retries once; if the
        retry ALSO fails (not 200), the second reason must be recomputed and
        surfaced (not the stale first-attempt reason)."""
        monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(return_value=_settings()))
        apns_client._templates = {"driver_accepted:none": "BLOB"}
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_resp(403, "ExpiredProviderToken"), _resp(500, "ServerError")])
        monkeypatch.setattr(apns_client, "_get_client", lambda: client)

        ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
        assert (ok, dead) == (False, False)
        assert client.post.await_count == 2

    def test_post_raising_is_caught_and_returns_false_false(self, monkeypatch):
        monkeypatch.setattr(apns_client, "get_app_settings", AsyncMock(return_value=_settings()))
        apns_client._templates = {"driver_accepted:none": "BLOB"}
        client = MagicMock()
        client.post = AsyncMock(side_effect=RuntimeError("connection reset"))
        monkeypatch.setattr(apns_client, "_get_client", lambda: client)

        ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
        assert (ok, dead) == (False, False)


# --------------------------------------------------------------------------- #
# _reason
# --------------------------------------------------------------------------- #


class TestReason:
    def test_reason_returns_value_from_json_body(self):
        assert apns_client._reason(_resp(400, "BadDeviceToken")) == "BadDeviceToken"

    def test_reason_returns_none_when_json_parsing_raises(self):
        r = MagicMock()
        r.json = MagicMock(side_effect=ValueError("not json"))
        assert apns_client._reason(r) is None
