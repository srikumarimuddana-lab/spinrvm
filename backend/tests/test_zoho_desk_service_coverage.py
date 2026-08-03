"""
Coverage-focused unit tests for services/zoho_desk_service.py.

A1c Sub-tier C — test-only pass, no application code changed. Written purely
by reading backend/services/zoho_desk_service.py; not executed against
pytest in this session (per task instructions), so relies on careful static
reading of the source rather than a red/green loop.

Targets the previously-uncovered branches:
  - _require_connected: missing-keys message (not just "disabled")
  - _token_is_fresh: falsy expiry, non-string expiry, naive-datetime expiry,
    unparsable-string expiry, and a genuinely buggy non-string/non-datetime
    expiry (see test_token_is_fresh_int_expiry_raises_attributeerror_bug)
  - _refresh_access_token: transport error, non-JSON response body
  - _request: transport error, 204 no-content, non-JSON error body,
    non-JSON success body
  - list_tickets / search_tickets: every optional filter param
  - get_default_department_id, create_ticket (all branches), get_ticket_threads,
    get_thread, add_comment, update_ticket (success path), add_tags,
    remove_tags, list_agents, list_departments, ticket_count (bad-count branch)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import services.zoho_desk_service as zoho
from services.zoho_desk_service import ZohoDeskError


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    """Async-context-manager stand-in for httpx.AsyncClient. ``handler`` is a
    callable (method, url, kwargs) -> _FakeResponse."""

    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        return self._handler("POST", url, kwargs)

    async def request(self, method, url, **kwargs):
        return self._handler(method, url, kwargs)


class _ErrorClient:
    """Stand-in whose every call raises an httpx transport error."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        raise httpx.ConnectError("boom")

    async def request(self, method, url, **kwargs):
        raise httpx.ConnectError("boom")


def _connected_config(**overrides):
    cfg = {
        "id": "default",
        "enabled": True,
        "data_center": "ca",
        "org_id": "700123",
        "client_id": "cid",
        "client_secret": "secret",
        "refresh_token": "rtok",
        "access_token": "AT",
        "access_token_expires_at": "2999-01-01T00:00:00+00:00",
    }
    cfg.update(overrides)
    return cfg


def _patch_db(monkeypatch, row):
    fake_db = MagicMock()
    fake_db.find_one = AsyncMock(return_value=row)
    fake_db.update_one = AsyncMock(return_value=None)
    monkeypatch.setattr(zoho, "db_supabase", fake_db)
    return fake_db


def _patch_http(monkeypatch, handler):
    monkeypatch.setattr(zoho.httpx, "AsyncClient", lambda *a, **k: _FakeClient(handler))


def _patch_http_error(monkeypatch):
    monkeypatch.setattr(zoho.httpx, "AsyncClient", lambda *a, **k: _ErrorClient())


# --------------------------------------------------------------------------
# _require_connected — missing-keys branch (line 96)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_missing_config_keys_raises_503_with_field_names(monkeypatch):
    # enabled=True but client_secret blank -> hits the "missing:" message
    # branch, distinct from the plain "disabled" branch already covered in
    # test_zoho_desk.py.
    _patch_db(monkeypatch, _connected_config(client_secret=""))
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 503
    assert "client_secret" in ei.value.message


# --------------------------------------------------------------------------
# _token_is_fresh branches
# --------------------------------------------------------------------------


def test_token_is_fresh_false_when_expiry_missing():
    # token present, no expiry recorded -> line 108
    cfg = {"access_token": "tok", "access_token_expires_at": None}
    assert zoho._token_is_fresh(cfg) is False


def test_token_is_fresh_true_for_non_string_tzaware_datetime():
    from datetime import datetime, timedelta, timezone

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    # access_token_expires_at stored as an actual datetime object (not a
    # string) -> the `else: exp = expires_at` branch, line 113.
    cfg = {"access_token": "tok", "access_token_expires_at": future}
    assert zoho._token_is_fresh(cfg) is True


def test_token_is_fresh_true_for_naive_iso_string():
    # No timezone offset in the string -> fromisoformat gives a naive
    # datetime -> the `exp.tzinfo is None` branch, lines 115-116.
    cfg = {"access_token": "tok", "access_token_expires_at": "2099-01-01T00:00:00"}
    assert zoho._token_is_fresh(cfg) is True


def test_token_is_fresh_false_for_unparsable_string():
    # fromisoformat raises ValueError -> caught -> line 117 `return False`.
    cfg = {"access_token": "tok", "access_token_expires_at": "not-a-date"}
    assert zoho._token_is_fresh(cfg) is False


def test_token_is_fresh_int_expiry_raises_attributeerror_bug():
    """FOUND NOT FIXED (not fixed here, per task instructions):

    services/zoho_desk_service.py `_token_is_fresh`, ~lines 109-117.

    If `access_token_expires_at` is anything other than a `str` or a
    `datetime` (e.g. an int/epoch value, which is a plausible shape if the
    config row is ever populated by a different writer than this module),
    the code takes the `else: exp = expires_at` branch and then evaluates
    `exp.tzinfo`. An `int` has no `.tzinfo` attribute, so this raises
    `AttributeError`, which is *not* one of the types caught by
    `except (ValueError, TypeError)`. The exception propagates uncaught out
    of `_token_is_fresh` (and in turn `_get_access_token`/`_request`)
    instead of being treated as "not fresh, go refresh". This test pins the
    actual (buggy) behavior rather than the presumably-intended one.
    """
    cfg = {"access_token": "tok", "access_token_expires_at": 1893456000}
    with pytest.raises(AttributeError):
        zoho._token_is_fresh(cfg)


# --------------------------------------------------------------------------
# _refresh_access_token — transport error + non-JSON body
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_refresh_transport_error_raises_502(monkeypatch):
    _patch_db(monkeypatch, _connected_config(access_token="", access_token_expires_at=None))
    _patch_http_error(monkeypatch)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 502


@pytest.mark.anyio
async def test_refresh_non_json_body_still_raises_502(monkeypatch):
    # resp.json() raises ValueError -> `data` stays {} -> access_token is
    # None -> falls into the "refresh failed" branch using resp.text.
    _patch_db(monkeypatch, _connected_config(access_token="", access_token_expires_at=None))

    def handler(method, url, kwargs):
        return _FakeResponse(200, json_data=None, text="not json")

    _patch_http(monkeypatch, handler)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 502
    assert ei.value.details == "not json"


# --------------------------------------------------------------------------
# _request — transport error, 204, non-JSON error body, non-JSON success body
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_request_transport_error_raises_502(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    _patch_http_error(monkeypatch)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.get_ticket("t1")
    assert ei.value.status == 502


@pytest.mark.anyio
async def test_request_204_returns_none(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        return _FakeResponse(204)

    _patch_http(monkeypatch, handler)
    out = await zoho.get_ticket("t1")
    assert out is None


@pytest.mark.anyio
async def test_request_error_body_falls_back_to_text(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        return _FakeResponse(500, json_data=None, text="upstream blew up")

    _patch_http(monkeypatch, handler)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.get_ticket("t1")
    assert ei.value.status == 502
    assert ei.value.details == "upstream blew up"


@pytest.mark.anyio
async def test_request_success_non_json_body_returns_none(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        return _FakeResponse(200, json_data=None, text="")

    _patch_http(monkeypatch, handler)
    out = await zoho.get_ticket("t1")
    assert out is None


# --------------------------------------------------------------------------
# list_tickets — remaining optional filters
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tickets_all_optional_filters(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, {"data": []})

    _patch_http(monkeypatch, handler)
    await zoho.list_tickets(status="Open", assignee_id="a1", priority="High", channel="Email")
    p = seen["params"]
    assert p["status"] == "Open"
    assert p["assignee"] == "a1"
    assert p["priority"] == "High"
    assert p["channel"] == "Email"


# --------------------------------------------------------------------------
# search_tickets
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_tickets_numeric_query_with_all_filters(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, {"data": [{"id": "9"}]})

    _patch_http(monkeypatch, handler)
    out = await zoho.search_tickets(
        query="12345",
        department_id="dep1",
        status="Open",
        priority="Low",
        assignee_id="ag1",
    )
    assert out["data"][0]["id"] == "9"
    p = seen["params"]
    assert p["ticketNumber"] == "12345"
    assert "_all" not in p
    assert p["departmentId"] == "dep1"
    assert p["status"] == "Open"
    assert p["priority"] == "Low"
    assert p["assigneeId"] == "ag1"
    assert "search" in seen["url"]


@pytest.mark.anyio
async def test_search_tickets_keyword_query_wildcards_and_defaults_empty(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(204)  # -> None -> falls back to {"data": []}

    _patch_http(monkeypatch, handler)
    out = await zoho.search_tickets(query="wallet issue")
    assert out == {"data": []}
    p = seen["params"]
    assert p["_all"] == "*wallet issue*"
    assert "ticketNumber" not in p
    assert "departmentId" not in p


# --------------------------------------------------------------------------
# get_default_department_id
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_default_department_id_present_and_absent(monkeypatch):
    db = _patch_db(monkeypatch, {"id": "default", "default_department_id": "  dep9  "})
    out = await zoho.get_default_department_id()
    assert out == "dep9"

    db.find_one = AsyncMock(return_value={"id": "default", "default_department_id": ""})
    out2 = await zoho.get_default_department_id()
    assert out2 is None


# --------------------------------------------------------------------------
# create_ticket — no-department error, contact_id path, inline-contact path,
# extra merge
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_ticket_no_department_raises_400(monkeypatch):
    _patch_db(monkeypatch, {"id": "default", "default_department_id": ""})
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.create_ticket(subject="Help")
    assert ei.value.status == 400


@pytest.mark.anyio
async def test_create_ticket_contact_id_priority_category_extra(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "zt1"})

    _patch_http(monkeypatch, handler)
    out = await zoho.create_ticket(
        subject="Card issue",
        description="details",
        department_id="dep1",
        priority="High",
        category="Payment",
        contact_id="c123",
        extra={"cf": {"custom": "x"}},
    )
    assert out["id"] == "zt1"
    body = seen["body"]
    assert body["departmentId"] == "dep1"
    assert body["priority"] == "High"
    assert body["category"] == "Payment"
    assert body["contactId"] == "c123"
    assert "contact" not in body
    assert body["cf"] == {"custom": "x"}


@pytest.mark.anyio
async def test_create_ticket_inline_contact_derives_lastname_from_email(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "zt2"})

    _patch_http(monkeypatch, handler)
    await zoho.create_ticket(
        subject="Lost item",
        department_id="dep1",
        email="jane.doe@example.ca",
    )
    contact = seen["body"]["contact"]
    assert contact["lastName"] == "jane.doe"
    assert contact["firstName"] == ""
    assert contact["email"] == "jane.doe@example.ca"
    assert contact["phone"] == ""


@pytest.mark.anyio
async def test_create_ticket_inline_contact_no_email_defaults_customer(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "zt3"})

    _patch_http(monkeypatch, handler)
    await zoho.create_ticket(subject="Anon", department_id="dep1")
    contact = seen["body"]["contact"]
    assert contact["lastName"] == "Customer"
    assert seen["body"]["subject"] == "Anon"


# --------------------------------------------------------------------------
# get_ticket_threads / get_thread / add_comment / update_ticket / add_tags /
# remove_tags / list_agents / list_departments
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_ticket_threads(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        assert "/conversations" in url
        return _FakeResponse(200, {"data": [{"id": "th1"}]})

    _patch_http(monkeypatch, handler)
    out = await zoho.get_ticket_threads("t1")
    assert out["data"][0]["id"] == "th1"


@pytest.mark.anyio
async def test_get_ticket_threads_empty_fallback(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        return _FakeResponse(204)

    _patch_http(monkeypatch, handler)
    out = await zoho.get_ticket_threads("t1")
    assert out == {"data": []}


@pytest.mark.anyio
async def test_get_thread(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        assert "/threads/th1" in url
        return _FakeResponse(200, {"id": "th1", "plainText": "full body"})

    _patch_http(monkeypatch, handler)
    out = await zoho.get_thread("t1", "th1")
    assert out["plainText"] == "full body"


@pytest.mark.anyio
async def test_add_comment(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["url"] = url
        seen["body"] = kwargs.get("json")
        return _FakeResponse(200, {"id": "cm1"})

    _patch_http(monkeypatch, handler)
    out = await zoho.add_comment("t1", content="internal note", is_public=True)
    assert out["id"] == "cm1"
    assert "/comments" in seen["url"]
    assert seen["body"] == {"content": "internal note", "contentType": "html", "isPublic": True}


@pytest.mark.anyio
async def test_update_ticket_success(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["method"] = method
        seen["body"] = kwargs.get("json")
        return _FakeResponse(200, {"id": "t1", "status": "Closed"})

    _patch_http(monkeypatch, handler)
    out = await zoho.update_ticket("t1", {"status": "Closed", "unknown_field": "ignored"})
    assert out["status"] == "Closed"
    assert seen["method"] == "PATCH"
    assert seen["body"] == {"status": "Closed"}


@pytest.mark.anyio
async def test_add_tags(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["url"] = url
        seen["body"] = kwargs.get("json")
        return _FakeResponse(200, {"ok": True})

    _patch_http(monkeypatch, handler)
    out = await zoho.add_tags("t1", ["vip", "", "urgent"])
    assert out == {"ok": True}
    assert "/associateTag" in seen["url"]
    assert seen["body"] == {"tags": ["vip", "urgent"]}


@pytest.mark.anyio
async def test_remove_tags(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["url"] = url
        seen["body"] = kwargs.get("json")
        return _FakeResponse(200, {"ok": True})

    _patch_http(monkeypatch, handler)
    out = await zoho.remove_tags("t1", ["vip"])
    assert out == {"ok": True}
    assert "/disassociateTag" in seen["url"]
    assert seen["body"] == {"tags": ["vip"]}


@pytest.mark.anyio
async def test_list_agents(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params")
        return _FakeResponse(200, {"data": [{"id": "ag1"}]})

    _patch_http(monkeypatch, handler)
    out = await zoho.list_agents(limit=500)
    assert out["data"][0]["id"] == "ag1"
    assert seen["params"]["limit"] == 200  # clamped to the endpoint max


@pytest.mark.anyio
async def test_list_agents_empty_fallback(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        return _FakeResponse(204)

    _patch_http(monkeypatch, handler)
    out = await zoho.list_agents()
    assert out == {"data": []}


@pytest.mark.anyio
async def test_list_departments(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params")
        return _FakeResponse(200, {"data": [{"id": "d1"}]})

    _patch_http(monkeypatch, handler)
    out = await zoho.list_departments()
    assert out["data"][0]["id"] == "d1"
    assert seen["params"] == {"isEnabled": "true"}


@pytest.mark.anyio
async def test_list_departments_empty_fallback(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        return _FakeResponse(204)

    _patch_http(monkeypatch, handler)
    out = await zoho.list_departments()
    assert out == {"data": []}


# --------------------------------------------------------------------------
# ticket_count — non-numeric count falls back to 0
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ticket_count_non_numeric_returns_zero(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        return _FakeResponse(200, {"count": "not-a-number"})

    _patch_http(monkeypatch, handler)
    out = await zoho.ticket_count()
    assert out == 0


@pytest.mark.anyio
async def test_ticket_count_missing_count_key_returns_zero(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        return _FakeResponse(204)  # -> None -> (data or {}) -> {} -> .get("count", 0) -> 0

    _patch_http(monkeypatch, handler)
    out = await zoho.ticket_count()
    assert out == 0
