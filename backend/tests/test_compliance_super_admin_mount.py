"""The compliance router must be gated at the MOUNT by require_super_admin.

Decision log 2026-08-19, section 2 ("`compliance` admin module"), option B:
`routes/admin/__init__.py` used to mount `compliance_router` under
`Depends(require_module("compliance"))`, but `"compliance"` was never added
to `AVAILABLE_MODULES`/`ROLE_PRESETS` (`routes/admin/staff.py`) — the staff
create/update handlers filter submitted modules against that list before
persisting, so no non-super-admin could ever hold the grant. `require_module`
auto-passes `super_admin` (`dependencies/__init__.py`), so the router was
already reachable by super_admin only in practice; this just states that
restriction explicitly with `require_super_admin` instead of leaving a
module string that reads as grantable but never has been. Zero functional
change from the prior behavior — see docs/change-log/2026-08-21-compliance-
module-super-admin-fix.md.

Mirrors `test_ai_console_super_admin_mount.py`'s
`test_router_is_mounted_under_require_super_admin` for the same reason: a
request-level check alone can't distinguish "gated at the mount" from "gated
by a module string nobody can hold" — both currently 403 a non-super-admin,
but only one keeps doing so if `AVAILABLE_MODULES` ever changes. Asserting on
the wiring pins the actual mechanism.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADMIN_INIT = Path(__file__).resolve().parents[1] / "routes" / "admin" / "__init__.py"

_SUPER_ADMIN = {"id": "admin-001", "role": "super_admin", "email": "admin@spinr.app"}
# A plain "admin" role holding every other grantable module. In this codebase
# "admin" is NOT a super-admin bypass — it is module-scoped like
# operations/support — so this is the strongest non-super_admin caller that
# exists, and it must still be refused.
_PLAIN_ADMIN = {"id": "admin-2", "role": "admin", "email": "ops@spinr.app", "modules": ["settings", "dashboard"]}


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


def test_router_is_mounted_under_require_super_admin():
    """The mount itself must carry the gate, not a dead module string."""
    src = _ADMIN_INIT.read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    match = re.search(r"include_router\(\s*compliance_router[^)]*\)", code, re.DOTALL)

    assert match, "compliance_router is no longer mounted on admin_router"
    assert "require_super_admin" in match.group(0), (
        "compliance_router is mounted without require_super_admin — see decision log "
        "2026-08-19 section 2 (option B). A module-string gate here silently locks out "
        'every non-super-admin as long as "compliance" stays out of AVAILABLE_MODULES, '
        "which is not a structural guarantee."
    )
    assert 'require_module("compliance")' not in code, (
        'the dead require_module("compliance") gate should be fully replaced, not left alongside require_super_admin'
    )


def test_non_super_admin_is_refused(test_client, app_fixture):
    """A module-holding "admin" role must still 403 — no module can satisfy require_super_admin."""
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _PLAIN_ADMIN
    resp = test_client.get("/api/admin/compliance/gst-pst-remittance")

    assert resp.status_code == 403, (
        f"GET /api/admin/compliance/gst-pst-remittance returned {resp.status_code} for a "
        "non-super_admin — compliance reports must never be reachable by a module grant."
    )


def test_super_admin_passes_the_mount_gate(test_client, app_fixture):
    """A super_admin caller must clear the mount gate (may still 503 downstream on mocked DB)."""
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _SUPER_ADMIN
    resp = test_client.get("/api/admin/compliance/gst-pst-remittance")

    assert resp.status_code != 403, (
        "GET /api/admin/compliance/gst-pst-remittance returned 403 for a super_admin caller — "
        "the mount gate should have passed."
    )
