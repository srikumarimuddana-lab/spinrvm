"""APNs client (iOS Live Activity push) — Phase 3.

Covers the template index + ETA buckets, the aps payload shape, the
ships-dark-when-unconfigured / no-template skips, the success path (headers +
topic + body + real ES256 JWT), APNs status handling (410/400 dead token, 403
expired-token bust+retry), and provider-JWT caching. Network is mocked; the JWT
is signed for real against an ephemeral P-256 key (shape of a real .p8).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

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


def _patch(monkeypatch, *, settings=None, templates=None, responses=None) -> MagicMock:
    monkeypatch.setattr(
        apns_client,
        "get_app_settings",
        AsyncMock(return_value=_settings() if settings is None else settings),
    )
    apns_client._templates = {"driver_accepted:none": "BLOB"} if templates is None else templates
    apns_client._token_cache.clear()
    apns_client._config_warned = False
    client = MagicMock()
    client.post = AsyncMock(side_effect=responses or [_resp(200)])
    monkeypatch.setattr(apns_client, "_get_client", lambda: client)
    return client


_CONTENT = {"status": "driver_accepted", "eta_minutes": None}


# --- pure helpers ---------------------------------------------------------- #


def test_eta_bucket():
    assert apns_client._eta_bucket(None) == "none"
    assert apns_client._eta_bucket(1) == "lt2"
    assert apns_client._eta_bucket(4) == "lt5"
    assert apns_client._eta_bucket(9) == "lt10"
    assert apns_client._eta_bucket(19) == "lt20"
    assert apns_client._eta_bucket(30) == "gte20"
    assert apns_client._eta_bucket("nope") == "none"


def test_render_indexes_templates_with_none_fallback():
    apns_client._templates = {"driver_accepted:none": "BLOB", "in_progress:lt5": "B2"}
    assert apns_client._render_ui_json({"status": "driver_accepted", "eta_minutes": None}, "update") == "BLOB"
    assert apns_client._render_ui_json({"status": "in_progress", "eta_minutes": 3}, "update") == "B2"
    # bucket-specific missing → fall back to the status' :none template
    apns_client._templates = {"in_progress:none": "B3"}
    assert apns_client._render_ui_json({"status": "in_progress", "eta_minutes": 3}, "update") == "B3"
    assert apns_client._render_ui_json({"status": "unknown", "eta_minutes": None}, "update") is None


def test_build_aps_update_and_end():
    u = apns_client._build_aps("BLOB", "update")["aps"]
    assert u["event"] == "update"
    assert u["content-state"] == {"uiJsonData": "BLOB"}
    assert "dismissal-date" not in u
    e = apns_client._build_aps("BLOB", "end")["aps"]
    assert e["event"] == "end"
    assert "dismissal-date" in e


# --- send ------------------------------------------------------------------ #


def test_send_skips_when_unconfigured(monkeypatch):
    client = _patch(monkeypatch, settings={})  # no apns_* keys
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, False)
    client.post.assert_not_awaited()


def test_send_skips_when_no_template(monkeypatch):
    client = _patch(monkeypatch, templates={})
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, False)
    client.post.assert_not_awaited()


def test_send_success_headers_topic_body(monkeypatch):
    client = _patch(monkeypatch, responses=[_resp(200)])
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("DEVTOK", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (True, False)
    client.post.assert_awaited_once()
    url = client.post.await_args.args[0]
    kwargs = client.post.await_args.kwargs
    assert url.endswith("/3/device/DEVTOK")
    assert "sandbox" in url  # use_sandbox routed to the sandbox host
    h = kwargs["headers"]
    assert h["apns-push-type"] == "liveactivity"
    assert h["apns-topic"] == "com.spinr.user.push-type.liveactivity"
    assert h["authorization"].startswith("bearer ")
    assert kwargs["json"]["aps"]["content-state"] == {"uiJsonData": "BLOB"}


def test_send_rejects_path_chars_in_token(monkeypatch):
    client = _patch(monkeypatch, responses=[_resp(200)])
    # A token with path/structural chars must never reach the APNs URL path.
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("../../evil", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, False)
    client.post.assert_not_awaited()


def test_send_410_marks_dead(monkeypatch):
    _patch(monkeypatch, responses=[_resp(410, "Unregistered")])
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, True)


def test_send_400_bad_device_token_marks_dead(monkeypatch):
    _patch(monkeypatch, responses=[_resp(400, "BadDeviceToken")])
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
    assert (ok, dead) == (False, True)


def test_send_403_expired_token_busts_and_retries(monkeypatch):
    client = _patch(monkeypatch, responses=[_resp(403, "ExpiredProviderToken"), _resp(200)])
    ok, dead = asyncio.run(apns_client.send_apns_live_activity("tok", _CONTENT, "update", use_sandbox=True))
    assert ok is True
    assert client.post.await_count == 2


def test_provider_jwt_cached_across_sends(monkeypatch):
    _patch(monkeypatch, responses=[_resp(200), _resp(200)])
    asyncio.run(apns_client.send_apns_live_activity("t1", _CONTENT, "update", use_sandbox=True))
    asyncio.run(apns_client.send_apns_live_activity("t2", _CONTENT, "update", use_sandbox=True))
    # One key_id → one cached provider JWT, reused for the second push.
    assert len(apns_client._token_cache) == 1
