"""Static guard on the messages the apps print verbatim.

Why this exists
---------------
A 4xx ``detail`` is sent to the client untouched (``utils/error_handling.py``
redacts PII but deliberately keeps the sentence, because 4xx is "intended
user-facing UX"), and ``SpinrException.message`` lands in the same field via
``content["detail"] = exc.message``. Nothing enforced that those sentences
were actually written for a user, so the codebase accumulated messages like
``payment_already_processing`` and::

    Ride is in status 'driver_arrived'; cannot perform this action from that
    state (allowed: ['driver_accepted']).

A manual sweep fixed those, but the sweep itself had a blind spot: it only
scanned ``HTTPException(detail=...)`` literals, so it missed every
``SpinrException(message=...)`` raise site and every message whose leak was an
*interpolated* value rather than literal text. The rider-facing twin of the
worst offender survived a full review cycle because of exactly that gap.

This test closes it. It checks the two classes that are unambiguous:

  A. a 4xx detail that is a bare lowercase machine token rather than a sentence
  B. any detail that interpolates a ride ``status`` into the text

Both are cheap to satisfy correctly and neither has a legitimate exception
that is not listed below.
"""

import ast
import os
import re

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"tests", "migrations", "__pycache__", "scripts", ".venv"}

RAISERS = ("HTTPException", "ValidationError")

# A bare machine token: lowercase, no spaces, underscore-or-dot separated.
BARE_TOKEN = re.compile(r"^[a-z0-9]+(?:[_.][a-z0-9]+)*$")

# Placeholder expressions that render a ride state at runtime.
RIDE_STATUS_EXPR = re.compile(r"""\bstatus\b|\ballowed_states\b|\bRideStatus\b""")

# Deliberate exceptions, each with the reason it is allowed to stay.
ALLOWED_BARE_TOKENS = {
    # Machine sentinels: the 5xx sanitiser in utils/error_handling.py only
    # passes a detail through when it matches ^ERR_[A-Z0-9_]+$, so this shape
    # is load-bearing. The apps map them to copy in
    # shared/errors/sentinelMessages.ts and never render the token.
    "ERR_",
}
ALLOWED_FILES = {
    # Stripe calls these endpoints, not a human. "Invalid payload" is the
    # correct register for a machine caller.
    "routes/webhooks.py",
}

# Internal-console routes. Ride, job, appeal and payout states are an admin's
# actual working vocabulary — an operator reading "Cannot complete ride from
# state 'in_progress'" knows exactly what that means, and the admin dashboard
# shows those same values in its own filters and status columns. The rule this
# module enforces exists to protect riders and drivers, who have no such
# vocabulary. Admin message quality is worth a separate pass; it is not this
# gate's job, and pretending otherwise would just mean a noisy allowlist.
ALLOWED_PREFIXES = ("routes/admin/",)


def _iter_backend_files():
    for dirpath, dirnames, filenames in os.walk(BACKEND):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def _detail_nodes(tree):
    """Yield (lineno, node) for every user-facing message expression."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        for keyword in node.keywords:
            if keyword.arg == "detail" and name in RAISERS:
                yield node.lineno, keyword.value, node
            elif keyword.arg == "message" and (name.endswith("Exception") or name.endswith("Error")):
                yield node.lineno, keyword.value, node


def _status_code(call):
    for keyword in call.keywords:
        if keyword.arg == "status_code" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _collect():
    bare, interpolated = [], []
    for path in _iter_backend_files():
        rel = os.path.relpath(path, BACKEND)
        if rel in ALLOWED_FILES or rel.startswith(ALLOWED_PREFIXES):
            continue
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except SyntaxError:  # pragma: no cover - would fail the build elsewhere
            continue
        for lineno, value, call in _detail_nodes(tree):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                text = value.value.strip()
                if any(text.startswith(prefix) for prefix in ALLOWED_BARE_TOKENS):
                    continue
                status = _status_code(call)
                is_client_error = status is None or (isinstance(status, int) and 400 <= status < 500)
                if is_client_error and len(text) > 2 and BARE_TOKEN.match(text):
                    bare.append(f"{rel}:{lineno}  {text!r}")
            elif isinstance(value, ast.JoinedStr):
                for part in value.values:
                    if not isinstance(part, ast.FormattedValue):
                        continue
                    expr = ast.unparse(part.value)
                    if RIDE_STATUS_EXPR.search(expr):
                        interpolated.append(f"{rel}:{lineno}  interpolates {expr}")
    return bare, interpolated


@pytest.mark.unit
def test_no_bare_machine_token_is_a_user_facing_message():
    """A 4xx detail is printed to the user verbatim, so it must be a sentence."""
    bare, _ = _collect()
    assert not bare, "Machine tokens returned as the whole user-facing message:\n  " + "\n  ".join(sorted(bare))


@pytest.mark.unit
def test_no_message_interpolates_a_raw_ride_status():
    """Ride states are internal vocabulary. Use backend/utils/ride_state_copy.py."""
    _, interpolated = _collect()
    assert not interpolated, (
        "Ride state interpolated into a user-facing message — use "
        "driver_phrase()/rider_phrase() from backend/utils/ride_state_copy.py:\n  " + "\n  ".join(sorted(interpolated))
    )
