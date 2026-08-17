"""The AI Console must be super_admin-only at the MOUNT, not just per-route.

Every route in routes/admin/ai_console.py already calls
``_require_super_admin(admin)`` in its own body, so the enforcement has always
been correct. What was missing was structure: with no dependency on the
``include_router()`` call, that boundary lived entirely in whether each future
route author remembered one line. A new endpoint added to this router without
it would have been reachable by any admin — and nothing at the mount would have
hinted that it should not be.

That matters more here than on most routers. These endpoints impersonate a
rider or driver in an AI chat turn and read their conversation history: PII and
an action-capable LLM session, stricter than any grantable module.

These tests pin both layers, so removing either one fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_AI_CONSOLE = Path(__file__).resolve().parents[1] / "routes" / "admin" / "ai_console.py"
_ADMIN_INIT = Path(__file__).resolve().parents[1] / "routes" / "admin" / "__init__.py"

_SUPER_ADMIN = {"id": "admin-001", "role": "super_admin", "email": "admin@spinr.app", "modules": []}
# A plain "admin" role holding every grantable module. In this codebase "admin"
# is NOT a super-admin bypass — it is module-scoped like operations/support — so
# this is the strongest non-super_admin caller that exists.
_PLAIN_ADMIN = {"id": "admin-2", "role": "admin", "email": "ops@spinr.app", "modules": ["settings", "dashboard"]}


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_client):
    return test_client


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/admin/ai/users/u1/conversations"),
        ("get", "/api/admin/ai/users/u1/conversations/c1/messages"),
        ("get", "/api/admin/ai/security-events"),
        ("post", "/api/admin/ai/chat"),
    ],
)
def test_non_super_admin_is_refused(client, app_fixture, method, path):
    """Even an "admin"-role caller holding modules must be refused."""
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _PLAIN_ADMIN
    resp = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))

    assert resp.status_code == 403, (
        f"{method.upper()} {path} returned {resp.status_code} for a non-super_admin — "
        "impersonation and chat-history reads must never be reachable by a module grant."
    )


def test_router_is_mounted_under_require_super_admin():
    """The mount itself must carry the gate.

    Asserted on the wiring rather than only through a request because that is
    the half a request cannot distinguish: a route that passes this check by
    its own in-body call looks identical from outside to one covered by the
    mount, right up until someone adds a route that forgets.
    """
    src = _ADMIN_INIT.read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    match = re.search(r"include_router\(\s*ai_console_router[^)]*\)", code, re.DOTALL)

    assert match, "ai_console_router is no longer mounted on admin_router"
    assert "require_super_admin" in match.group(0), (
        "ai_console_router is mounted without require_super_admin. Its routes enforce "
        "the role individually today, but that makes the boundary depend on every future "
        "route author remembering one line."
    )


def test_every_route_also_enforces_in_its_own_body():
    """Defence in depth: the per-route calls stay even with the mount gate.

    The mount is the structural guarantee; these are what keeps the router safe
    if it is ever included somewhere else (a test app, a future sub-mount)
    without the dependency.
    """
    lines = _AI_CONSOLE.read_text(encoding="utf-8").splitlines()
    unguarded = []
    pending = None

    for i, line in enumerate(lines):
        route = re.match(r'@router\.(get|post|put|delete|patch)\("([^"]+)"', line.strip())
        if route:
            pending = f"{route.group(1).upper()} {route.group(2)}"
            continue
        if pending and re.match(r"\s*(async )?def ", line):
            # The guard is called early in the body; 25 lines covers the
            # docstring plus the first statements without reaching the next route.
            if "_require_super_admin" not in "\n".join(lines[i : i + 25]):
                unguarded.append(pending)
            pending = None

    assert not unguarded, (
        f"ai_console route(s) with no _require_super_admin() call in the body: {unguarded}. "
        "The mount-level gate covers them today, but this router's own routes are the "
        "second layer — keep both."
    )
