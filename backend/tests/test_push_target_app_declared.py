"""Every push call site must say which app it is for.

``send_push_notification``'s ``target_app`` picks the per-app FCM token
column (migration 102) and, since migration 436, also the inbox row's
``audience``. A call site that omits it falls through to the legacy
``users.fcm_token`` column, which ``register_push_token`` rewrites on every
registration from either app — so for a dual-role user (one ``users`` row
carrying both is_rider and is_driver, because routes/auth.py reuses the row
found by get_user_by_phone on OTP verify) an undeclared push lands in
whichever app was opened most recently, and its inbox row shows up in both.

ACTION_ITEMS.md N10 swept this in batches and each batch added its own
per-call-site assertions. Those only ever covered the sites that existed
when they were written, which is why the gap kept reappearing. This test is
the standing guard instead: it reads the source, so a newly added call site
that forgets target_app fails here rather than being found in production.

Modelled on test_loguru_call_conventions.py, which statically scans for a
different silently-wrong call convention in the same codebase.

To add a legitimate account-level push, add it to
``_ACCOUNT_LEVEL_BY_DESIGN`` below WITH a reason. Do not raise a count
without one — an undeclared push is a routing bug unless the notification
genuinely belongs to the account rather than to one of its two roles.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

_BACKEND = Path(__file__).resolve().parents[1]

_VALID_TARGETS = {"rider", "driver"}

# path relative to backend/  ->  (how many undeclared calls are expected, why)
_ACCOUNT_LEVEL_BY_DESIGN: dict[str, tuple[int, str]] = {
    "routes/admin/faqs.py": (
        1,
        "audience='all' admin broadcast spans both roles with no per-user role "
        "lookup, so it cannot map to a single column. Matches the precedent in "
        "routes/admin/messaging.py::_target_app_for_audience.",
    ),
    "routes/notifications.py": (
        1,
        "POST /notifications/test-push is an admin diagnostic that deliberately "
        "exercises the legacy fcm_token column it reports on.",
    ),
    "routes/rides/_shared.py": (
        1,
        "_push_in_background is a *args/**kwargs forwarder, not a call site — "
        "its callers supply target_app and it passes theirs straight through.",
    ),
    "routes/webhooks.py": (
        1,
        "wallet_topup: the personal wallet is role-agnostic (see "
        "routes/admin/wallet.py::_wallet_target_app), so for a dual-role user "
        "the topped-up balance is the same balance in both apps.",
    ),
    "utils/payment_retry.py": (
        1,
        "the retries-exhausted alert goes to users with role='admin', who have "
        "no rider/driver app surface for either per-app column to address.",
    ),
    "utils/suspension_reactivation.py": (
        1,
        "account reactivation is account-level, not role-level — users.role is "
        "admin-RBAC only and an account can be both via is_rider/is_driver. "
        "Pre-existing documented decision (ACTION_ITEMS.md N-series).",
    ),
}


def _push_calls(tree: ast.AST) -> list[tuple[int, str | None]]:
    """(lineno, declared target_app) for each send_push_notification call.

    The declared value is the literal when one is given, the sentinel
    '<dynamic>' when it is computed (e.g. _wallet_target_app(user)), and None
    when the keyword is absent entirely.
    """
    found: list[tuple[int, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name != "send_push_notification":
            continue
        kw = next((k for k in node.keywords if k.arg == "target_app"), None)
        if kw is None:
            found.append((node.lineno, None))
        elif isinstance(kw.value, ast.Constant):
            found.append((node.lineno, kw.value.value))
        else:
            found.append((node.lineno, "<dynamic>"))
    return found


def _scan() -> dict[str, list[tuple[int, str | None]]]:
    by_file: dict[str, list[tuple[int, str | None]]] = {}
    for path in sorted(_BACKEND.rglob("*.py")):
        rel = path.relative_to(_BACKEND)
        if "tests" in rel.parts or "scripts" in rel.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:  # pragma: no cover - a broken file fails its own tests
            continue
        calls = _push_calls(tree)
        if calls:
            by_file[rel.as_posix()] = calls
    return by_file


def test_the_scanner_actually_finds_call_sites():
    """Guard the guard.

    A rename or an import-shape change that made _push_calls match nothing
    would turn every assertion below into a vacuous pass. Pin a floor well
    under the real count (95 at the time of writing) so this fails loudly
    instead of going quiet.
    """
    total = sum(len(v) for v in _scan().values())
    assert total > 60, f"scanner found only {total} call sites — it has stopped matching"


def test_every_push_declares_target_app_or_is_a_documented_exception():
    undeclared = {f: [ln for ln, t in calls if t is None] for f, calls in _scan().items()}
    undeclared = {f: lns for f, lns in undeclared.items() if lns}

    problems: list[str] = []
    for f, lns in sorted(undeclared.items()):
        allowed, _reason = _ACCOUNT_LEVEL_BY_DESIGN.get(f, (0, ""))
        if len(lns) > allowed:
            problems.append(
                f"{f}: {len(lns)} call site(s) without target_app at line(s) "
                f"{', '.join(map(str, lns))}, but only {allowed} is account-level by design. "
                f"Declare target_app='rider'/'driver', or add it to "
                f"_ACCOUNT_LEVEL_BY_DESIGN with a reason."
            )

    assert not problems, "Undeclared push target_app:\n  " + "\n  ".join(problems)


def test_documented_exceptions_have_not_gone_stale():
    """A fixed exception must be removed from the allow-list, not left to rot.

    Otherwise the list slowly stops describing the code and a genuinely new
    undeclared call site can hide behind a stale allowance.
    """
    undeclared = {f: sum(1 for _, t in calls if t is None) for f, calls in _scan().items()}
    stale = [f for f, (allowed, _r) in _ACCOUNT_LEVEL_BY_DESIGN.items() if undeclared.get(f, 0) < allowed]
    assert not stale, (
        "These files now declare target_app everywhere (or have fewer undeclared "
        f"calls than allowed) — tighten or drop their _ACCOUNT_LEVEL_BY_DESIGN entry: {stale}"
    )


def test_every_declared_target_app_is_a_real_app_surface():
    """'rider'/'driver' are the only values features.py routes on.

    Anything else silently behaves as None — it selects no per-app token
    column and records audience='both' — so a typo would reintroduce exactly
    the bug this sweep closed, without any error.
    """
    bad = [
        f"{f}:{ln} -> {t!r}"
        for f, calls in _scan().items()
        for ln, t in calls
        if t is not None and t != "<dynamic>" and t not in _VALID_TARGETS
    ]
    assert not bad, "Invalid target_app values: " + ", ".join(bad)
