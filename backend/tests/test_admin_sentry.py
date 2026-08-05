"""Unit coverage for backend/routes/admin/sentry.py.

The Sentry viewer is a stateless proxy over the Sentry Web API, so these tests
mock the HTTP layer (``_sentry_request`` / ``_fetch_project_issues``) and the
config gate rather than hitting Sentry. They lock in: the issue/stacktrace
shaping contract the dashboard depends on, the configured-vs-not gating, the
surface tagging + cross-project merge/sort, upstream-error mapping, and the
resolve ("close") action.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

try:
    import routes.admin.sentry as sentry
except ImportError:
    import backend.routes.admin.sentry as sentry  # type: ignore[no-redef]

ADMIN = {"id": "admin-1", "role": "super_admin"}


def _resp(status_code=200, payload=None, content=b"{}"):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.content = content
    r.json.return_value = payload if payload is not None else {}
    return r


# ---------------------------------------------------------------------------
# _shape_issue
# ---------------------------------------------------------------------------


def test_shape_issue_maps_fields_and_coerces_count():
    raw = {
        "id": 123,
        "shortId": "SPINR-1A",
        "title": "ValueError: bad",
        "culprit": "routes.rides in create",
        "level": "error",
        "priority": "high",
        "status": "unresolved",
        "substatus": "ongoing",
        "count": "42",  # Sentry returns count as a string
        "userCount": 7,
        "firstSeen": "2026-01-01T00:00:00Z",
        "lastSeen": "2026-01-02T00:00:00Z",
        "permalink": "https://sentry.io/x",
        "metadata": {"type": "ValueError", "value": "bad"},
    }
    out = sentry._shape_issue(raw, "backend", "spinr-backend")
    assert out["id"] == "123"
    assert out["short_id"] == "SPINR-1A"
    assert out["count"] == 42  # coerced str -> int
    assert out["user_count"] == 7
    assert out["level"] == "error"
    assert out["priority"] == "high"
    assert out["type"] == "ValueError"
    assert out["value"] == "bad"
    assert out["surface"] == "backend"
    assert out["project"] == "spinr-backend"


def test_shape_issue_bad_count_defaults_zero_and_missing_metadata_ok():
    out = sentry._shape_issue({"count": "not-a-number"}, "admin", "spinr-admin")
    assert out["count"] == 0
    assert out["type"] is None
    assert out["value"] is None
    assert out["id"] is None


# ---------------------------------------------------------------------------
# _extract_exceptions
# ---------------------------------------------------------------------------


def test_extract_exceptions_reverses_frames_and_resolves_context_line():
    event = {
        "entries": [
            {
                "type": "exception",
                "data": {
                    "values": [
                        {
                            "type": "ValueError",
                            "value": "boom",
                            "module": "builtins",
                            "stacktrace": {
                                "frames": [
                                    {  # oldest frame (Sentry lists oldest-first)
                                        "filename": "server.py",
                                        "function": "handler",
                                        "lineNo": 10,
                                        "context": [[9, "a"], [10, "crash-line"], [11, "b"]],
                                        "inApp": True,
                                    },
                                    {  # crash frame (last in Sentry order)
                                        "filename": "rides.py",
                                        "function": "create",
                                        "lineNo": 20,
                                    },
                                ]
                            },
                        }
                    ]
                },
            }
        ]
    }
    exceptions = sentry._extract_exceptions(event)
    assert len(exceptions) == 1
    exc = exceptions[0]
    assert exc["type"] == "ValueError"
    assert exc["value"] == "boom"
    # Reversed so the crash site is first.
    assert exc["frames"][0]["filename"] == "rides.py"
    assert exc["frames"][0]["lineno"] == 20
    assert exc["frames"][1]["filename"] == "server.py"
    assert exc["frames"][1]["context_line"] == "crash-line"


def test_extract_exceptions_ignores_non_exception_entries():
    event = {"entries": [{"type": "message", "data": {}}, {"type": "breadcrumbs", "data": {}}]}
    assert sentry._extract_exceptions(event) == []


# ---------------------------------------------------------------------------
# config helpers
# ---------------------------------------------------------------------------


def test_surface_projects_only_includes_set_slugs():
    with (
        patch.object(sentry.settings, "SENTRY_PROJECT_BACKEND", "spinr-backend"),
        patch.object(sentry.settings, "SENTRY_PROJECT_RIDER", None),
        patch.object(sentry.settings, "SENTRY_PROJECT_DRIVER", "spinr-driver"),
        patch.object(sentry.settings, "SENTRY_PROJECT_ADMIN", None),
    ):
        out = sentry._surface_projects()
    assert out == {"backend": "spinr-backend", "driver-app": "spinr-driver"}


def test_is_configured_requires_token_org_and_project():
    with (
        patch.object(sentry.settings, "SENTRY_API_TOKEN", "tok"),
        patch.object(sentry.settings, "SENTRY_ORG_SLUG", "spinr"),
        patch.object(sentry, "_surface_projects", return_value={"backend": "b"}),
    ):
        assert sentry._is_configured() is True
    with (
        patch.object(sentry.settings, "SENTRY_API_TOKEN", None),
        patch.object(sentry.settings, "SENTRY_ORG_SLUG", "spinr"),
        patch.object(sentry, "_surface_projects", return_value={"backend": "b"}),
    ):
        assert sentry._is_configured() is False


def test_base_url_strips_trailing_slash():
    with patch.object(sentry.settings, "SENTRY_API_BASE_URL", "https://de.sentry.io/"):
        assert sentry._base_url() == "https://de.sentry.io"
    with patch.object(sentry.settings, "SENTRY_API_BASE_URL", ""):
        assert sentry._base_url() == "https://sentry.io"


# ---------------------------------------------------------------------------
# _sentry_request error mapping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sentry_request_returns_response_on_success():
    client = MagicMock()
    client.request = AsyncMock(return_value=_resp(200))
    with patch.object(sentry.settings, "SENTRY_API_TOKEN", "tok"):
        out = await sentry._sentry_request(client, "GET", "/x/")
    assert out.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("code,expected", [(404, 404), (401, 502), (403, 502), (500, 502)])
async def test_sentry_request_maps_error_statuses(code, expected):
    client = MagicMock()
    client.request = AsyncMock(return_value=_resp(code))
    with patch.object(sentry.settings, "SENTRY_API_TOKEN", "tok"):
        with pytest.raises(HTTPException) as ei:
            await sentry._sentry_request(client, "GET", "/x/")
    assert ei.value.status_code == expected


@pytest.mark.anyio
async def test_sentry_request_wraps_httpx_error_as_502():
    client = MagicMock()
    client.request = AsyncMock(side_effect=httpx.ConnectError("boom"))
    with patch.object(sentry.settings, "SENTRY_API_TOKEN", "tok"):
        with pytest.raises(HTTPException) as ei:
            await sentry._sentry_request(client, "GET", "/x/")
    assert ei.value.status_code == 502


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_sentry_config_reports_enabled_surfaces():
    with (
        patch.object(sentry, "_is_configured", return_value=True),
        patch.object(sentry, "_surface_projects", return_value={"backend": "spinr-backend"}),
        patch.object(sentry.settings, "SENTRY_ORG_SLUG", "spinr"),
    ):
        out = await sentry.get_sentry_config(current_admin=ADMIN)
    assert out["configured"] is True
    assert out["org"] == "spinr"
    by_surface = {s["surface"]: s for s in out["surfaces"]}
    assert by_surface["backend"]["enabled"] is True
    assert by_surface["backend"]["project"] == "spinr-backend"
    assert by_surface["rider-app"]["enabled"] is False


# ---------------------------------------------------------------------------
# GET /issues
# ---------------------------------------------------------------------------


async def _call_list_issues(**overrides):
    """Invoke the endpoint directly with its real defaults filled in.

    These tests call the route function rather than going through FastAPI, so
    any parameter left unpassed keeps its ``Query(...)`` object as the value
    instead of the default that object wraps. A ``Query`` instance is not
    None and is truthy, so an omitted ``surface`` reached the
    ``surface is not None`` guard and raised
    "Surface 'annotation=Union[str, NoneType] ...' is not configured" —
    a test artifact, not route behavior. Pass the defaults explicitly.
    """
    kwargs = {
        "surface": None,
        "status": "unresolved",
        "query": None,
        "stats_period": "14d",
        "limit": sentry._DEFAULT_LIMIT,
        "current_admin": ADMIN,
    }
    kwargs.update(overrides)
    return await sentry.list_sentry_issues(**kwargs)


@pytest.mark.anyio
async def test_list_issues_not_configured_raises_503():
    with patch.object(sentry, "_is_configured", return_value=False):
        with pytest.raises(HTTPException) as ei:
            await sentry.list_sentry_issues(current_admin=ADMIN)
    assert ei.value.status_code == 503


@pytest.mark.anyio
async def test_list_issues_merges_and_sorts_by_last_seen():
    issue_b = {"id": "b1", "surface": "backend", "last_seen": "2026-01-02T00:00:00Z"}
    issue_r = {"id": "r1", "surface": "rider-app", "last_seen": "2026-01-03T00:00:00Z"}
    with (
        patch.object(sentry, "_is_configured", return_value=True),
        patch.object(
            sentry, "_surface_projects", return_value={"backend": "spinr-backend", "rider-app": "spinr-rider"}
        ),
        patch.object(sentry, "_fetch_project_issues", AsyncMock(side_effect=[[issue_b], [issue_r]])),
    ):
        out = await _call_list_issues()
    assert out["count"] == 2
    # rider issue is newer -> sorts first
    assert out["issues"][0]["id"] == "r1"
    assert set(out["surfaces"]) == {"backend", "rider-app"}
    assert out["truncated"] is False


@pytest.mark.anyio
async def test_list_issues_flags_truncated_when_page_full():
    batch = [{"id": f"i{n}", "last_seen": "2026-01-01T00:00:00Z"} for n in range(2)]
    with (
        patch.object(sentry, "_is_configured", return_value=True),
        patch.object(sentry, "_surface_projects", return_value={"backend": "spinr-backend"}),
        patch.object(sentry, "_fetch_project_issues", AsyncMock(return_value=batch)),
    ):
        out = await _call_list_issues(limit=2)
    assert out["truncated"] is True


@pytest.mark.anyio
async def test_list_issues_unknown_surface_raises_400():
    with (
        patch.object(sentry, "_is_configured", return_value=True),
        patch.object(sentry, "_surface_projects", return_value={"backend": "spinr-backend"}),
    ):
        with pytest.raises(HTTPException) as ei:
            await sentry.list_sentry_issues(surface="driver-app", current_admin=ADMIN)
    assert ei.value.status_code == 400


@pytest.mark.anyio
async def test_list_issues_invalid_status_raises_400():
    with (
        patch.object(sentry, "_is_configured", return_value=True),
        patch.object(sentry, "_surface_projects", return_value={"backend": "spinr-backend"}),
    ):
        with pytest.raises(HTTPException) as ei:
            await sentry.list_sentry_issues(status="bogus", current_admin=ADMIN)
    assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# GET /issues/{id}
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_issue_merges_detail_with_stacktrace_and_surface():
    issue_payload = {
        "id": "123",
        "title": "ValueError",
        "count": "3",
        "metadata": {"type": "ValueError", "value": "bad"},
        "project": {"slug": "spinr-backend"},
    }
    event_payload = {
        "id": "evt-1",
        "dateCreated": "2026-01-02T00:00:00Z",
        "tags": [{"key": "env", "value": "production"}],
        "entries": [
            {
                "type": "exception",
                "data": {"values": [{"type": "ValueError", "value": "bad", "stacktrace": {"frames": []}}]},
            }
        ],
    }
    request_mock = AsyncMock(side_effect=[_resp(200, issue_payload), _resp(200, event_payload)])
    with (
        patch.object(sentry, "_is_configured", return_value=True),
        patch.object(sentry, "_surface_projects", return_value={"backend": "spinr-backend"}),
        patch.object(sentry, "_sentry_request", request_mock),
        patch.object(sentry.settings, "SENTRY_ORG_SLUG", "spinr"),
    ):
        out = await sentry.get_sentry_issue("123", current_admin=ADMIN)
    assert out["surface"] == "backend"
    assert out["event_id"] == "evt-1"
    assert out["event_timestamp"] == "2026-01-02T00:00:00Z"
    assert out["tags"] == [{"key": "env", "value": "production"}]
    assert len(out["exceptions"]) == 1


# ---------------------------------------------------------------------------
# POST /issues/{id}/status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_status_rejects_invalid_status():
    with patch.object(sentry, "_is_configured", return_value=True):
        body = sentry.UpdateIssueStatusRequest(status="deleted")
        with pytest.raises(HTTPException) as ei:
            await sentry.update_sentry_issue_status("123", body, current_admin=ADMIN)
    assert ei.value.status_code == 400


@pytest.mark.anyio
async def test_update_status_resolves_issue():
    request_mock = AsyncMock(return_value=_resp(200, {"status": "resolved", "statusDetails": {}}))
    with (
        patch.object(sentry, "_is_configured", return_value=True),
        patch.object(sentry, "_sentry_request", request_mock),
        patch.object(sentry.settings, "SENTRY_ORG_SLUG", "spinr"),
    ):
        body = sentry.UpdateIssueStatusRequest(status="resolved")
        out = await sentry.update_sentry_issue_status("123", body, current_admin=ADMIN)
    assert out["id"] == "123"
    assert out["status"] == "resolved"
    # Verify it issued a PUT to the org issue endpoint with the new status.
    args, kwargs = request_mock.call_args
    assert args[1] == "PUT"
    assert kwargs["json"] == {"status": "resolved"}
