"""Every write helper must say so when it silently does nothing.

Each `if not supabase:` branch in the repository layer returns a benign empty
value — `None`, `[]`, `False` — WITHOUT raising. That is what makes this class
of failure invisible: the caller sees a normal return, there is no exception to
catch, and any code treating "no exception" as "written" reports success for a
write that never happened. services/ledger_service.py hit exactly this and had
to add its own client check before it could tell a written 7-year tax-ledger
row from an unwritten one.

`repositories/_base.py::update_one` carried a NOTE deferring the fix because
four sibling helpers swallowed identically and fixing one in isolation would
have been a worse inconsistency. This file pins the coordinated result.

Reads are deliberately NOT covered: an empty read degrades visibly (no rides,
no driver) rather than silently claiming success, and logging every read would
drown the signal in the one environment where it can happen.

Why they log rather than raise: core/lifespan.py raises on a falsy client when
ENV == production, so Uvicorn never serves traffic in that state. Below
production it warns and boots on purpose, so local work without Supabase is
possible — and raising from every write helper would destroy that affordance.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_DIR = Path(__file__).resolve().parents[1] / "repositories"

# Helpers whose `if not supabase:` branch means "a write did not happen".
# Reads are excluded by design (see module docstring).
WRITE_HELPERS = {
    "_base.py": [
        "insert_one",
        "insert_many",
        "insert_many_ignore_conflicts",
        "update_one",
        "delete_many",
        "rpc",
    ],
    "driver_repo.py": [
        "update_driver_location",
        "set_driver_available",
        "match_and_claim_driver",
        "claim_driver_atomic",
        "update_acceptance_rate",
        "claim_ride_atomic",
    ],
    "ride_repo.py": ["update_ride", "resolve_complaint", "update_lost_and_found"],
    "auth_repo.py": ["verify_otp_record", "delete_otp_record"],
    "corporate_repo.py": ["update_corporate_account", "delete_corporate_account"],
    "wallet_repo.py": ["release_promo_user_slot", "mark_stripe_event_processed", "unclaim_stripe_event"],
}


def _function_source(path: Path, name: str) -> str:
    src = path.read_text().split("\n")
    start = None
    for i, line in enumerate(src):
        if re.match(rf"^(async )?def {re.escape(name)}\b", line):
            start = i
            break
    assert start is not None, f"{path.name}: no function named {name}()"
    for j in range(start + 1, len(src)):
        if re.match(r"^(async )?def |^class ", src[j]):
            return "\n".join(src[start:j])
    return "\n".join(src[start:])


@pytest.mark.parametrize(
    ("filename", "func"),
    [(f, fn) for f, fns in WRITE_HELPERS.items() for fn in fns],
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_write_helper_announces_a_skipped_write(filename: str, func: str) -> None:
    body = _function_source(REPO_DIR / filename, func)
    assert "if not supabase" in body, (
        f"{filename}::{func} no longer guards on the client — if the guard moved, move this test with it"
    )
    assert "_write_skipped(" in body, (
        f"{filename}::{func} returns silently when the Supabase client is absent. "
        "A caller cannot tell that from a successful write. Call _write_skipped()."
    )


def test_write_skipped_logs_at_error_not_warning() -> None:
    """CLAUDE.md: never logger.warning(...) and continue on a DB error. A lost
    write IS a DB error — it just arrives without an exception attached."""
    from backend.repositories import _base

    body = inspect.getsource(_base._write_skipped)
    assert "logger.error(" in body
    assert "logger.warning(" not in body


def test_write_skipped_names_the_operation_and_target() -> None:
    """The log line has to identify WHICH write vanished; a generic message
    would leave someone grepping 20 call sites."""
    from backend.repositories import _base

    logged: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_base.logger, "error", lambda msg, *a, **k: logged.append(str(msg)))
        _base._write_skipped("update_one", "rides")

    assert len(logged) == 1
    assert "update_one" in logged[0]
    assert "rides" in logged[0]


def test_write_skipped_does_not_leak_row_payloads() -> None:
    """PIPEDA: this fires on every write in a misconfigured environment, so its
    signature must not be able to carry a row. Only op + target names."""
    from backend.repositories import _base

    params = list(inspect.signature(_base._write_skipped).parameters)
    assert params == ["op", "table"], (
        "keep _write_skipped's signature to identifiers only — adding a payload "
        "parameter would put user data into a log line"
    )
