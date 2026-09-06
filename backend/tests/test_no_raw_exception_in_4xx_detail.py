"""WS-E guard: no NEW route may put a raw exception into an HTTPException detail.

Audit 2026-09-05 §1.7 found 30 sites doing `detail=str(e)` or
`detail=f"...{e}"`. `utils/error_handling.py` sanitised only `status_code >= 500`,
so on a 4xx the raw text reached the client — and from there browser history,
Vercel logs and Sentry breadcrumbs. `routes/admin/legacy_sin_dob_backfill.py` is
the worst case: its import service raises with the offending CSV row, so a SIN or
date of birth ends up in a 400 body. PIPEDA does not distinguish "only an admin
saw it", and two driver-facing sites leak upstream text to a contractor.

Two layers now protect this:

  1. `utils.pii.redact_error_detail`, applied to every 4xx string detail in the
     exception handler — a runtime backstop that scrubs identifiers out of
     whatever a route produced. Its own tests live in
     `test_redact_error_detail.py`.
  2. This test — a build-time ratchet. The redactor is pattern-based and cannot
     catch everything (a free-text street address, a person's name, a bespoke
     upstream error), so the real fix at any site is still to raise a vetted
     message. This keeps the count going down and never up.

**Adding a file to `_KNOWN_OFFENDERS` is the wrong fix.** The list is a debt
ledger, not an allowlist: shrink it, never grow it. If a legitimately new file
needs to surface upstream text, raise a vetted message and log the exception
server-side instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BACKEND = Path(__file__).resolve().parents[1]
_ROUTES = _BACKEND / "routes"

# `detail=str(e)` / `detail=str(exc)` and `detail=f"...{e}..."`.
_RAW_EXC_DETAIL_RE = re.compile(
    r"detail\s*=\s*str\(\s*e[a-z_]*\s*\)"
    r"|detail\s*=\s*f\"[^\"]*\{\s*e[a-z_]*\s*[}!:]"
)

# Debt ledger — the files that already did this when the guard was added
# (2026-09-05). SHRINK THIS LIST. Never add to it.
_KNOWN_OFFENDERS = frozenset(
    {
        "routes/admin/booking_import.py",
        "routes/admin/data_transfer_import.py",
        "routes/admin/driver_appeals.py",
        "routes/admin/driver_import.py",
        "routes/admin/driver_statements.py",
        "routes/admin/drivers.py",
        "routes/admin/export_approvals.py",
        "routes/admin/legacy_driver_import.py",
        "routes/admin/legacy_saved_address_backfill.py",
        "routes/admin/legacy_sin_dob_backfill.py",
        "routes/admin/legacy_vehicle_history_backfill.py",
        "routes/admin/rider_import.py",
        # 5xx only (admin Stripe-event replay). The wholesale 5xx sanitiser in
        # error_handling.py already replaces this with "Internal server error"
        # before it reaches a client, so it is not a live leak — it is listed
        # because this guard matches the pattern, not the status code.
        "routes/admin/stripe_events.py",
        "routes/admin/stripe_import.py",
        "routes/admin/tax_id_import.py",
        "routes/admin/wallet_import.py",
        "routes/drivers/appeals.py",
        "routes/drivers/profile.py",
        "routes/drivers/tax_exports.py",
    }
)


def _offending_files() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(_ROUTES.rglob("*.py")):
        rel = path.relative_to(_BACKEND).as_posix()
        hits = [
            i
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _RAW_EXC_DETAIL_RE.search(line)
        ]
        if hits:
            found[rel] = hits
    return found


def test_no_new_file_leaks_a_raw_exception_into_detail():
    new = {f: lines for f, lines in _offending_files().items() if f not in _KNOWN_OFFENDERS}
    assert not new, (
        'New `detail=str(e)` / `detail=f"...{e}"` site(s):\n'
        + "\n".join(f"  {f}:{lines}" for f, lines in sorted(new.items()))
        + "\n\nA raw exception in an HTTPException detail reaches the client on a 4xx.\n"
        "Raise a vetted message and log the exception server-side instead:\n"
        '    logger.error("...", exc_info=True)  # or logger.opt(exception=True) on loguru\n'
        '    raise HTTPException(status_code=400, detail="Could not process that file")\n'
        "Do NOT add the file to _KNOWN_OFFENDERS — that list only shrinks."
    )


def test_known_offender_list_has_no_stale_entries():
    """When a file is cleaned up, drop it from the ledger — otherwise the guard
    silently stops protecting it if the pattern ever comes back."""
    stale = sorted(_KNOWN_OFFENDERS - set(_offending_files()))
    assert not stale, (
        "These files no longer leak a raw exception — remove them from "
        f"_KNOWN_OFFENDERS so the guard covers them again:\n  {stale}"
    )


def test_the_detector_actually_matches_the_patterns_it_claims():
    """A guard whose regex silently stops matching protects nothing."""
    for sample in (
        "        raise HTTPException(status_code=400, detail=str(e))",
        "raise HTTPException(400, detail=str(exc))",
        'raise HTTPException(status_code=422, detail=f"Import failed: {e}")',
        'raise HTTPException(status_code=422, detail=f"row {n}: {err}")',
    ):
        assert _RAW_EXC_DETAIL_RE.search(sample), sample
    for safe in (
        'raise HTTPException(status_code=400, detail="Invalid phone number")',
        'raise HTTPException(status_code=400, detail=f"Must wait {remaining} more seconds")',
        "logger.error(str(e))",
    ):
        assert not _RAW_EXC_DETAIL_RE.search(safe), safe
