"""The admin module lists must agree with each other and with what's enforced.

Three separate lists describe "which permission modules exist":

  1. ``AVAILABLE_MODULES``  (routes/admin/staff.py)  — what can be GRANTED.
     The create/update handlers filter submitted modules against it.
  2. ``ALL_MODULES``        (routes/admin/auth.py)   — what a super_admin's JWT
     carries.
  3. ``ALL_MODULES``        (admin-dashboard staff page) — the checkboxes an
     operator actually ticks.

Nothing kept them in step, and all three had drifted:

  * ``heatmap`` was grantable in all three and gated **no backend route** — it
    only showed or hid a sidebar link, so granting it implied a protection it
    did not provide.
  * ``bulk_operations`` was offered by the UI after being deliberately removed
    backend-side (the Data Transfer surface is ``require_super_admin`` now), so
    the picker implied a full-fidelity PII export could be delegated.
  * ``compliance`` drifted the other way: ``require_module("compliance")`` is a
    real gate in routes/admin/__init__.py, but the string is in no grantable
    list — so nobody can hold it and the router is super_admin-only by
    omission, which is precisely the accident the ``bulk_operations`` comment
    in that file warns about.

Every one of those fails silently: the checkbox saves, the API returns 200, the
audit log records the grant, and the permission either does nothing or was
never applied. These tests make each class loud instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.routes.admin.auth import ALL_MODULES
from backend.routes.admin.staff import AVAILABLE_MODULES, ROLE_PRESETS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STAFF_PAGE = _REPO_ROOT / "admin-dashboard" / "src" / "app" / "dashboard" / "staff" / "page.tsx"
_ADMIN_INIT = _REPO_ROOT / "backend" / "routes" / "admin" / "__init__.py"
_SIDEBAR = _REPO_ROOT / "admin-dashboard" / "src" / "components" / "sidebar.tsx"


def _frontend_module_keys() -> list[str]:
    """Parse the `ALL_MODULES` key list out of the admin staff page."""
    src = _STAFF_PAGE.read_text(encoding="utf-8")
    block = re.search(r"const ALL_MODULES = \[(.*?)\n\];", src, re.DOTALL)
    assert block, "could not locate ALL_MODULES in the admin staff page"
    return re.findall(r'\{\s*key:\s*"([^"]+)"', block.group(1))


def _enforced_module_gates() -> set[str]:
    """Every module string actually passed to require_module(), anywhere.

    Scans all of backend/routes/, not just the mount file: some routers are
    gated at the mount (``include_router(..., dependencies=[...])``) and others
    per-route inside their own module (``Depends(require_module("support_tickets"))``
    in routes/admin/support_tickets.py). An earlier draft of this helper read
    only __init__.py and therefore reported support_tickets as ungated — a
    false positive that would have sent someone deleting a real permission.
    """
    gates: set[str] = set()
    for path in (_REPO_ROOT / "backend" / "routes").rglob("*.py"):
        src = path.read_text(encoding="utf-8", errors="ignore")
        # Strip comments: routes/admin/__init__.py documents removed gates by
        # quoting require_module("bulk_operations") in prose, which must not
        # count as an enforcement site.
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        gates |= set(re.findall(r'require_module\(\s*"([^"]+)"\s*\)', code))
    return gates


# Grantable modules that gate no backend route today. Each is the same class of
# defect as `heatmap` — a permission that only changes sidebar visibility — but
# removing one is a decision about the permission model, not a cleanup, so they
# are pinned here rather than deleted as a side effect of the heatmap work.
# Surfaced by this test on 2026-08-14; see the change log entry.
_KNOWN_UNGATED_GRANTS = {
    # Ranked blocker #28 / audit finding N16 (2026-08-19): "pricing" used to
    # gate the Vehicle Types sidebar link and page-level check, but that
    # page's API has always used require_module("vehicle_types") — the
    # mismatch silently locked out staff granted "vehicle_types" (no
    # sidebar link, page-level denial) while a "pricing"-only grant looked
    # like access but 403'd on every API call. Both frontend sites were
    # repointed to "vehicle_types"; "pricing" itself now gates nothing.
    "pricing",
    "surge",  # surge endpoints live on the service_areas router and use that gate
}

# Modules the sidebar gates links on that no grant can satisfy, so only
# super_admin ever sees those links.
_KNOWN_UNGRANTABLE_SIDEBAR = {
    "compliance",  # a REAL require_module gate that is not in AVAILABLE_MODULES
    # `ai_console` used to be here too: the sidebar hid that entry behind a
    # module no role could hold, which produced the right outcome for the wrong
    # reason — it depended on nobody ever adding that string to
    # AVAILABLE_MODULES. It now uses the explicit `superAdminOnly` flag (same as
    # Sentry) against a grantable module, and the router is mounted under
    # require_super_admin. See test_ai_console_super_admin_mount.py.
}


def test_backend_grantable_and_jwt_lists_match():
    """staff.py's grantable list and auth.py's super-admin list must agree.

    They are two hand-maintained copies of one concept. A module in the JWT list
    but not the grantable one can never be held by a non-super_admin; the
    reverse mints a grant the super-admin token doesn't carry.
    """
    assert sorted(AVAILABLE_MODULES) == sorted(ALL_MODULES), (
        "AVAILABLE_MODULES (staff.py) and ALL_MODULES (auth.py) have diverged: "
        f"only in staff.py={sorted(set(AVAILABLE_MODULES) - set(ALL_MODULES))}, "
        f"only in auth.py={sorted(set(ALL_MODULES) - set(AVAILABLE_MODULES))}"
    )


def test_admin_ui_offers_exactly_the_grantable_modules():
    """A checkbox the backend will discard is worse than a missing one.

    The staff handlers filter submitted modules against AVAILABLE_MODULES, so an
    extra key here ticks, saves, returns 200 and grants nothing — with an audit
    row claiming otherwise.
    """
    frontend = _frontend_module_keys()
    extra = sorted(set(frontend) - set(AVAILABLE_MODULES))
    missing = sorted(set(AVAILABLE_MODULES) - set(frontend))

    assert not extra, (
        f"admin staff page offers module(s) the backend will silently drop: {extra}. "
        "Either add them to AVAILABLE_MODULES or remove the checkbox."
    )
    assert not missing, f"grantable module(s) with no checkbox, so they can never be assigned: {missing}."


def test_no_module_is_grantable_without_gating_something():
    """A grantable module that gates no route is a permission in name only.

    This is exactly what `heatmap` was: it changed sidebar visibility and
    nothing else, so granting it implied access control that did not exist and
    denying it implied a restriction that was never enforced.

    `staff` and `audit` are the documented exceptions — both are enforced by
    require_super_admin or at the page level rather than a router-mount gate.
    `_KNOWN_UNGATED_GRANTS` pins the ones that already had this defect when the
    test was written, so today's state passes but nothing NEW can slip in.
    Shrinking that set is the follow-up; growing it should require an argument.
    """
    enforced = _enforced_module_gates()
    page_level_only = {"staff", "audit"}
    ungated = sorted(set(AVAILABLE_MODULES) - enforced - page_level_only - _KNOWN_UNGATED_GRANTS)

    assert not ungated, (
        f"module(s) grantable but gating no backend route: {ungated}. "
        "Either wire them to require_module() or drop them from AVAILABLE_MODULES — "
        "a grant that enforces nothing misleads whoever reads the permission list."
    )


def test_enforced_gates_are_reachable_through_a_grant():
    """The reverse drift: a real gate nobody can be granted.

    require_module("compliance") is enforced on the compliance router but the
    string is in no grantable list, so that surface is super_admin-only *by
    omission* — the same accident routes/admin/__init__.py documents for
    bulk_operations. Unreachable-by-grant is a legitimate design (make it
    require_super_admin explicitly); unreachable *by accident* is not, so this
    test lists the known ones and fails on any new one.
    """
    enforced = _enforced_module_gates()
    # Known and deliberate: tracked as a follow-up decision about who may reach
    # tax/compliance reporting. Documented here rather than silently tolerated.
    known_unreachable = {"compliance"}
    unreachable = sorted(enforced - set(AVAILABLE_MODULES) - known_unreachable)

    assert not unreachable, (
        f"require_module() gate(s) that no grant can satisfy: {unreachable}. "
        "Effective access is super_admin-only by omission. Either add the module to "
        "AVAILABLE_MODULES or switch the mount to require_super_admin so the boundary "
        "is explicit and independent of the grantable list."
    )


def test_role_presets_only_reference_grantable_modules():
    """A preset naming a dead module hands out a grant that gets filtered away."""
    for role, modules in ROLE_PRESETS.items():
        unknown = sorted(set(modules) - set(AVAILABLE_MODULES))
        assert not unknown, f"ROLE_PRESETS[{role!r}] references non-grantable module(s): {unknown}"


def test_sidebar_links_gate_on_grantable_modules():
    """Every sidebar entry must gate on a module that can actually be held.

    A link gated on a non-grantable string is invisible to every non-super_admin
    — which is how the Heat Map page would have silently disappeared for the
    operations role when `heatmap` was removed, had the entry not been
    repointed at `rides`. That near-miss is why this test exists: removing a
    module and repointing its link are one change, not two.

    `_KNOWN_UNGRANTABLE_SIDEBAR` pins the two that were already in this state.
    """
    src = _SIDEBAR.read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//"))
    referenced = set(re.findall(r'module:\s*"([^"]+)"', code))
    unknown = sorted(referenced - set(AVAILABLE_MODULES) - _KNOWN_UNGRANTABLE_SIDEBAR)

    assert not unknown, (
        f"sidebar entries gate on non-grantable module(s): {unknown}. Non-super_admin staff can never see those links."
    )


_VEHICLE_TYPES_PAGE = _REPO_ROOT / "admin-dashboard" / "src" / "app" / "dashboard" / "vehicle-types" / "page.tsx"


def test_vehicle_types_and_audit_logs_frontend_strings_match_backend_gate():
    """Regression pin for ranked blocker #28 / audit finding N16 (2026-08-19).

    Two frontend/backend module-string mismatches silently locked out staff
    who legitimately held the module: the sidebar and the Vehicle Types page
    checked "pricing" while the backend router is mounted behind
    require_module("vehicle_types") (routes/admin/__init__.py); the sidebar's
    Audit Logs entry checked "settings" while the audit-log endpoints are
    gated by require_module("audit") (routes/admin/maintenance.py). Neither
    mismatch was a security hole (nothing became reachable that shouldn't
    be) — the opposite failure direction: a staff member granted the correct
    backend module still couldn't see or use the page.

    This pins the corrected strings so a future edit that reintroduces
    either mismatch fails here first instead of silently locking someone
    out again.
    """
    sidebar_src = _SIDEBAR.read_text(encoding="utf-8")
    sidebar_code = "\n".join(line for line in sidebar_src.splitlines() if not line.lstrip().startswith("//"))

    vehicle_types_sidebar = re.search(r'href:\s*"/dashboard/vehicle-types".*?module:\s*"([^"]+)"', sidebar_code)
    assert vehicle_types_sidebar, "could not locate the Vehicle Types sidebar entry"
    assert vehicle_types_sidebar.group(1) == "vehicle_types", (
        f"sidebar's Vehicle Types entry gates on {vehicle_types_sidebar.group(1)!r}, "
        'not the backend\'s require_module("vehicle_types")'
    )

    audit_logs_sidebar = re.search(r'href:\s*"/dashboard/audit-logs".*?module:\s*"([^"]+)"', sidebar_code)
    assert audit_logs_sidebar, "could not locate the Audit Logs sidebar entry"
    assert audit_logs_sidebar.group(1) == "audit", (
        f"sidebar's Audit Logs entry gates on {audit_logs_sidebar.group(1)!r}, "
        'not the backend\'s require_module("audit")'
    )

    page_src = _VEHICLE_TYPES_PAGE.read_text(encoding="utf-8")
    page_gate = re.search(r'useRequireModule\("([^"]+)"\)', page_src)
    assert page_gate, "could not locate the useRequireModule() call in the Vehicle Types page"
    assert page_gate.group(1) == "vehicle_types", (
        f"Vehicle Types page gates on useRequireModule({page_gate.group(1)!r}), "
        'not the backend\'s require_module("vehicle_types")'
    )


@pytest.mark.parametrize("dead_module", ["heatmap", "bulk_operations"])
def test_removed_modules_stay_removed(dead_module):
    """Regression pin for the two strings removed on 2026-08-14.

    Both were removed for a documented reason (see the note on AVAILABLE_MODULES
    and the bulk_operations comment in routes/admin/__init__.py). Re-adding
    either should be a deliberate act that fails this test first, not a quiet
    edit — bulk_operations especially, since re-granting it would reopen a
    full-fidelity, unredacted PII export surface.
    """
    assert dead_module not in AVAILABLE_MODULES
    assert dead_module not in ALL_MODULES
    assert dead_module not in _frontend_module_keys()
