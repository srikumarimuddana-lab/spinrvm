"""Additional coverage for services/zoho_desk_service.py.

Complements tests/test_zoho_desk.py (which covers not-configured/disabled,
unknown data center, token refresh + persistence, the 401-retry-once
contract, and a handful of Codex-flagged param-shape regressions) with the
remaining endpoint wrappers and error branches that weren't previously
exercised directly:

  - _require_connected: enabled but missing required fields -> 503
  - _token_is_fresh: every branch (no expiry, non-string expiry, naive
    datetime, unparseable expiry)
  - _refresh_access_token: transport error, non-JSON response body
  - _request: transport error on the actual API call (distinct from the
    token-refresh transport error already covered), a 204 response, a
    non-JSON 4xx/5xx error body, a non-JSON 2xx body
  - search_tickets, get_default_department_id, create_ticket,
    get_ticket_threads, get_thread, add_comment, update_ticket (success
    path), add_tags, remove_tags, list_agents, list_departments,
    ticket_count (the int()-conversion-failure branch)
  - list_tickets: status / assignee_id / priority / channel filters
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.zoho_desk_service as zoho
from services.zoho_desk_service import ZohoDeskError


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", json_raises=False):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self._json_raises = json_raises

    def json(self):
        if self._json_raises or self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
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


class _RaisingClient:
    """Stands in for httpx.AsyncClient when the transport itself should
    blow up (connect timeout, DNS failure, etc.)."""

    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        raise self._exc

    async def request(self, method, url, **kwargs):
        raise self._exc


def _connected_config(**overrides):
    cfg = {
        "id": "default",
        "enabled": True,
        "data_center": "ca",
        "org_id": "700123",
        "client_id": "cid",
        "client_secret": "secret",
        "refresh_token": "rtok",
        "access_token": "",
        "access_token_expires_at": None,
    }
    cfg.update(overrides)
    return cfg


def _fresh_config(**overrides):
    """A config whose access token is already fresh, so calls skip the
    refresh round-trip and go straight to the API request."""
    return _connected_config(
        access_token="AT",
        access_token_expires_at="2999-01-01T00:00:00+00:00",
        **overrides,
    )


def _patch_db(monkeypatch, row):
    fake_db = MagicMock()
    fake_db.find_one = AsyncMock(return_value=row)
    fake_db.update_one = AsyncMock(return_value=None)
    monkeypatch.setattr(zoho, "db_supabase", fake_db)
    return fake_db


def _patch_http(monkeypatch, handler):
    monkeypatch.setattr(zoho.httpx, "AsyncClient", lambda *a, **k: _FakeClient(handler))


def _patch_http_raises(monkeypatch, exc):
    monkeypatch.setattr(zoho.httpx, "AsyncClient", lambda *a, **k: _RaisingClient(exc))


# --- _require_connected: enabled but missing fields -------------------------


@pytest.mark.anyio
async def test_enabled_but_missing_fields_raises_503(monkeypatch):
    _patch_db(monkeypatch, _connected_config(client_secret="", refresh_token=""))
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 503
    assert "client_secret" in ei.value.message
    assert "refresh_token" in ei.value.message


# --- _token_is_fresh: direct unit tests over every branch -------------------


def test_token_is_fresh_false_without_token():
    assert zoho._token_is_fresh({"access_token": "", "access_token_expires_at": "2999-01-01T00:00:00+00:00"}) is False


def test_token_is_fresh_false_without_expiry():
    assert zoho._token_is_fresh({"access_token": "AT", "access_token_expires_at": None}) is False


def test_token_is_fresh_handles_non_string_expiry():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert zoho._token_is_fresh({"access_token": "AT", "access_token_expires_at": future}) is True


def test_token_is_fresh_treats_naive_datetime_as_utc():
    # A naive datetime (no tzinfo) in the future must still be treated as
    # fresh -- the function assumes UTC rather than raising or mis-comparing.
    future_naive = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    assert zoho._token_is_fresh({"access_token": "AT", "access_token_expires_at": future_naive}) is True


def test_token_is_fresh_false_on_unparseable_expiry():
    assert zoho._token_is_fresh({"access_token": "AT", "access_token_expires_at": "not-a-timestamp"}) is False


def test_token_is_fresh_false_when_expired():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert zoho._token_is_fresh({"access_token": "AT", "access_token_expires_at": past}) is False


# --- _refresh_access_token: transport + malformed-body branches -------------


@pytest.mark.anyio
async def test_refresh_transport_error_raises_502(monkeypatch):
    _patch_db(monkeypatch, _connected_config())
    _patch_http_raises(monkeypatch, zoho.httpx.ConnectTimeout("timed out"))
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 502


@pytest.mark.anyio
async def test_refresh_response_non_json_body_treated_as_failure(monkeypatch):
    _patch_db(monkeypatch, _connected_config())

    def handler(method, url, kwargs):
        # 200 status but a body that isn't valid JSON at all -> data stays
        # {} -> no access_token -> refresh failure path.
        return _FakeResponse(200, json_raises=True, text="not json")

    _patch_http(monkeypatch, handler)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.list_tickets()
    assert ei.value.status == 502


# --- _request: transport error on the API call itself -----------------------


@pytest.mark.anyio
async def test_request_transport_error_after_valid_token_raises_502(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    _patch_http_raises(monkeypatch, zoho.httpx.ConnectError("unreachable"))
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.get_ticket("123")
    assert ei.value.status == 502


@pytest.mark.anyio
async def test_request_204_returns_none(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())

    def handler(method, url, kwargs):
        return _FakeResponse(204)

    _patch_http(monkeypatch, handler)
    result = await zoho.get_thread("t1", "th1")
    assert result is None


@pytest.mark.anyio
async def test_request_error_body_non_json_is_swallowed_into_text(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())

    def handler(method, url, kwargs):
        return _FakeResponse(500, json_raises=True, text="<html>boom</html>")

    _patch_http(monkeypatch, handler)
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.get_ticket("123")
    assert ei.value.status == 502
    assert ei.value.details == "<html>boom</html>"


@pytest.mark.anyio
async def test_request_success_non_json_body_returns_none(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())

    def handler(method, url, kwargs):
        return _FakeResponse(200, json_raises=True, text="")

    _patch_http(monkeypatch, handler)
    result = await zoho.get_thread("t1", "th1")
    assert result is None


# --- list_tickets: remaining filter params -----------------------------------


@pytest.mark.anyio
async def test_list_tickets_all_filters(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, {"data": []})

    _patch_http(monkeypatch, handler)
    await zoho.list_tickets(status="Open", assignee_id="agent1", priority="High", channel="Email")
    assert seen["params"]["status"] == "Open"
    assert seen["params"]["assignee"] == "agent1"
    assert seen["params"]["priority"] == "High"
    assert seen["params"]["channel"] == "Email"


# --- search_tickets -----------------------------------------------------------


@pytest.mark.anyio
async def test_search_tickets_numeric_query_uses_ticket_number(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, {"data": [{"id": "1"}]})

    _patch_http(monkeypatch, handler)
    out = await zoho.search_tickets(query="12345")
    assert out["data"][0]["id"] == "1"
    assert "tickets/search" in seen["url"]
    assert seen["params"]["ticketNumber"] == "12345"
    assert "_all" not in seen["params"]


@pytest.mark.anyio
async def test_search_tickets_keyword_query_and_all_filters(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, None)  # data=None -> falls back to {"data": []}

    _patch_http(monkeypatch, handler)
    out = await zoho.search_tickets(
        query="overcharged rider",
        department_id="dep1",
        status="Open",
        priority="High",
        assignee_id="agent1",
    )
    assert out == {"data": []}
    assert seen["params"]["_all"] == "*overcharged rider*"
    assert seen["params"]["departmentId"] == "dep1"
    assert seen["params"]["status"] == "Open"
    assert seen["params"]["priority"] == "High"
    assert seen["params"]["assigneeId"] == "agent1"


@pytest.mark.anyio
async def test_search_tickets_empty_query_omits_all_param(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, {"data": []})

    _patch_http(monkeypatch, handler)
    await zoho.search_tickets(query="   ")
    assert "_all" not in seen["params"]
    assert "ticketNumber" not in seen["params"]


# --- get_default_department_id -----------------------------------------------


@pytest.mark.anyio
async def test_get_default_department_id_returns_value(monkeypatch):
    _patch_db(monkeypatch, _connected_config(default_department_id=" dep-9 "))
    result = await zoho.get_default_department_id()
    assert result == "dep-9"


@pytest.mark.anyio
async def test_get_default_department_id_none_when_blank(monkeypatch):
    _patch_db(monkeypatch, _connected_config(default_department_id=""))
    result = await zoho.get_default_department_id()
    assert result is None


# --- create_ticket -------------------------------------------------------------


@pytest.mark.anyio
async def test_create_ticket_no_department_raises_400(monkeypatch):
    _patch_db(monkeypatch, _connected_config(default_department_id=""))
    with pytest.raises(ZohoDeskError) as ei:
        await zoho.create_ticket(subject="Help")
    assert ei.value.status == 400


@pytest.mark.anyio
async def test_create_ticket_with_contact_id_uses_department_override(monkeypatch):
    _patch_db(monkeypatch, _fresh_config(default_department_id="dep-default"))
    seen = {}

    def handler(method, url, kwargs):
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "zt1"})

    _patch_http(monkeypatch, handler)
    out = await zoho.create_ticket(
        subject="Lost item",
        description="desc",
        department_id="dep-explicit",
        priority="High",
        category="Lost & Found",
        contact_id="contact-1",
        extra={"customField": "x"},
    )
    assert out["id"] == "zt1"
    assert seen["body"]["departmentId"] == "dep-explicit"
    assert seen["body"]["contactId"] == "contact-1"
    assert "contact" not in seen["body"]
    assert seen["body"]["priority"] == "High"
    assert seen["body"]["category"] == "Lost & Found"
    assert seen["body"]["customField"] == "x"


@pytest.mark.anyio
async def test_create_ticket_inline_contact_synthesises_last_name(monkeypatch):
    _patch_db(monkeypatch, _fresh_config(default_department_id="dep-default"))
    seen = {}

    def handler(method, url, kwargs):
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "zt2"})

    _patch_http(monkeypatch, handler)
    await zoho.create_ticket(subject="Question", email="rider@example.ca")
    assert seen["body"]["contact"]["email"] == "rider@example.ca"
    assert seen["body"]["contact"]["lastName"] == "rider"  # from the email local-part
    assert seen["body"]["subject"] == "Question"


@pytest.mark.anyio
async def test_create_ticket_blank_subject_defaults(monkeypatch):
    _patch_db(monkeypatch, _fresh_config(default_department_id="dep-default"))
    seen = {}

    def handler(method, url, kwargs):
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "zt3"})

    _patch_http(monkeypatch, handler)
    await zoho.create_ticket(subject="")
    assert seen["body"]["subject"] == "(no subject)"
    assert seen["body"]["contact"]["lastName"] == "Customer"


# --- get_ticket_threads / get_thread -------------------------------------------


@pytest.mark.anyio
async def test_get_ticket_threads_returns_data(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())

    def handler(method, url, kwargs):
        return _FakeResponse(200, {"data": [{"id": "th1"}]})

    _patch_http(monkeypatch, handler)
    out = await zoho.get_ticket_threads("t1")
    assert out["data"][0]["id"] == "th1"


@pytest.mark.anyio
async def test_get_ticket_threads_falls_back_to_empty_list(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())

    def handler(method, url, kwargs):
        return _FakeResponse(200, None)

    _patch_http(monkeypatch, handler)
    out = await zoho.get_ticket_threads("t1")
    assert out == {"data": []}


@pytest.mark.anyio
async def test_get_thread_returns_full_content(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())

    def handler(method, url, kwargs):
        assert "/tickets/t1/threads/th1" in url
        return _FakeResponse(200, {"id": "th1", "content": "full body"})

    _patch_http(monkeypatch, handler)
    out = await zoho.get_thread("t1", "th1")
    assert out["content"] == "full body"


# --- add_comment ----------------------------------------------------------------


@pytest.mark.anyio
async def test_add_comment_sends_expected_body(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["method"] = method
        seen["url"] = url
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "c1"})

    _patch_http(monkeypatch, handler)
    out = await zoho.add_comment("t1", content="internal note", is_public=True)
    assert out["id"] == "c1"
    assert seen["method"] == "POST"
    assert "/tickets/t1/comments" in seen["url"]
    assert seen["body"] == {"content": "internal note", "contentType": "html", "isPublic": True}


# --- update_ticket: success path ------------------------------------------------


@pytest.mark.anyio
async def test_update_ticket_success_filters_to_allowed_fields(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["method"] = method
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {"id": "t1", "status": "Closed"})

    _patch_http(monkeypatch, handler)
    out = await zoho.update_ticket("t1", {"status": "Closed", "unknown_field": "x", "priority": None})
    assert out["status"] == "Closed"
    assert seen["method"] == "PATCH"
    # unknown_field is dropped; priority=None is dropped (falsy filter).
    assert seen["body"] == {"status": "Closed"}


# --- add_tags / remove_tags -----------------------------------------------------


@pytest.mark.anyio
async def test_add_tags_filters_blank_names(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["url"] = url
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {})

    _patch_http(monkeypatch, handler)
    await zoho.add_tags("t1", ["vip", "", None, "urgent"])
    assert "associateTag" in seen["url"]
    assert seen["body"]["tags"] == ["vip", "urgent"]


@pytest.mark.anyio
async def test_remove_tags_filters_blank_names(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["url"] = url
        seen["body"] = kwargs.get("json") or {}
        return _FakeResponse(200, {})

    _patch_http(monkeypatch, handler)
    await zoho.remove_tags("t1", ["stale", ""])
    assert "disassociateTag" in seen["url"]
    assert seen["body"]["tags"] == ["stale"]


# --- list_agents / list_departments ---------------------------------------------


@pytest.mark.anyio
async def test_list_agents_clamps_limit_and_returns_data(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, {"data": [{"id": "a1"}]})

    _patch_http(monkeypatch, handler)
    out = await zoho.list_agents(limit=999)
    assert out["data"][0]["id"] == "a1"
    assert seen["params"]["limit"] == 200  # clamped to the endpoint's max


@pytest.mark.anyio
async def test_list_departments_filters_enabled_only(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())
    seen = {}

    def handler(method, url, kwargs):
        seen["params"] = kwargs.get("params") or {}
        return _FakeResponse(200, None)

    _patch_http(monkeypatch, handler)
    out = await zoho.list_departments()
    assert out == {"data": []}
    assert seen["params"]["isEnabled"] == "true"


# --- ticket_count: the int()-conversion failure branch -------------------------


@pytest.mark.anyio
async def test_ticket_count_returns_zero_on_unparseable_count(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())

    def handler(method, url, kwargs):
        return _FakeResponse(200, {"count": "not-a-number"})

    _patch_http(monkeypatch, handler)
    out = await zoho.ticket_count()
    assert out == 0


@pytest.mark.anyio
async def test_ticket_count_returns_zero_on_missing_count(monkeypatch):
    _patch_db(monkeypatch, _fresh_config())

    def handler(method, url, kwargs):
        return _FakeResponse(200, None)

    _patch_http(monkeypatch, handler)
    out = await zoho.ticket_count()
    assert out == 0
