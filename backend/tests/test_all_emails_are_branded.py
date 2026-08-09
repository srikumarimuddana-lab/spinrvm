"""Every email Spinr sends carries the logo and the configured company details.

This exists because "all emails are branded" is the kind of claim that is true
on the day it is written and quietly false three PRs later. A new sender that
hands `send_transactional_email` its own hand-rolled HTML looks completely
normal in review — nothing fails, it just goes out looking like nothing else we
send, with a hardcoded company name.

So the guarantee is enforced structurally: every send site must either render
through `utils/email_layout`, or appear in `_UNBRANDED_BY_DESIGN` below with a
reason. Adding a sender without doing one of those fails this test.
"""

import pathlib
import re

import pytest

pytestmark = [pytest.mark.unit]

_BACKEND = pathlib.Path(__file__).resolve().parents[1]

#: Call sites that legitimately do not go through the shared layout.
#: Each entry is (path, why). Adding to this list should be an argued
#: decision, not a way to make the test pass.
_UNBRANDED_BY_DESIGN = {
    "utils/marketing_email.py": (
        "Marketing has its own CASL footer — sender identity, physical mailing "
        "address and a working unsubscribe — which is a legal requirement with "
        "its own shape. Wrapping it in the transactional shell would either "
        "duplicate or bury that footer. Its deliberately-separate copy of the "
        "address assembly is N16 in ACTION_ITEMS.md."
    ),
    "utils/receipt_email.py": (
        "Dead code with divergent hardcoded tax rates; no production callers. "
        "Scheduled for deletion as N8 rather than retrofit."
    ),
    # utils/email_provider.py is deliberately absent: it *defines*
    # send_transactional_email rather than awaiting one, so the detector below
    # never sees it and an entry here would be a dead exemption.
    "features.py": (
        "send_email is the generic wrapper. It renders plain-text bodies "
        "through the layout itself, which is what brands the six senders that "
        "reach recipients through it."
    ),
    "utils/email_notifications.py": (
        "The policy layer. Its callers pass an already-rendered RenderedEmail; rendering again here would double-wrap."
    ),
}

#: Files that reach the layout indirectly, via features.send_email's automatic
#: wrapping of a plain-text body rather than by importing it themselves.
_BRANDED_VIA_SEND_EMAIL = {
    "utils/corporate_low_balance.py",
    "utils/driver_statement_job.py",
    "routes/admin/driver_statements.py",
}

_SEND_CALL = re.compile(r"\bawait\s+(?:\w+\.)?send_(?:transactional_)?email\(")
_LAYOUT_IMPORT = re.compile(r"from\s+[\w.]*email_layout\s+import|email_layout\.")


def _senders() -> dict[str, str]:
    """Every module with a live email send, mapped to its source."""
    found = {}
    for path in _BACKEND.rglob("*.py"):
        rel = path.relative_to(_BACKEND).as_posix()
        if rel.startswith(("tests/", "venv/")) or "__pycache__" in rel:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if _SEND_CALL.search(source):
            found[rel] = source
    return found


def test_every_sender_is_accounted_for():
    """The whole point: no send site escapes without a decision."""
    unaccounted = []
    for rel, source in _senders().items():
        if rel in _UNBRANDED_BY_DESIGN or rel in _BRANDED_VIA_SEND_EMAIL:
            continue
        if not _LAYOUT_IMPORT.search(source):
            unaccounted.append(rel)
    assert not unaccounted, (
        "These send email without going through utils/email_layout, so they "
        "carry no logo and no company details from the admin Settings page:\n  "
        + "\n  ".join(sorted(unaccounted))
        + "\n\nEither render through email_layout, or add the file to "
        "_UNBRANDED_BY_DESIGN with a reason."
    )


def test_the_allowlist_has_not_gone_stale():
    """An exemption for a file that no longer sends email is a lie in a test."""
    senders = _senders()
    stale = [rel for rel in _UNBRANDED_BY_DESIGN if rel not in senders]
    assert not stale, f"exempted files that no longer send email: {stale}"

    stale_indirect = [rel for rel in _BRANDED_VIA_SEND_EMAIL if rel not in senders]
    assert not stale_indirect, f"listed as branded-via-send_email but no longer send: {stale_indirect}"


def test_every_exemption_states_a_reason():
    for rel, reason in _UNBRANDED_BY_DESIGN.items():
        assert len(reason) > 40, f"{rel} needs a real reason, not a placeholder"


def test_the_known_customer_facing_senders_are_all_branded():
    """Names the files explicitly, so a rename or a move is caught rather than
    silently shrinking the set the test above walks."""
    expected = {
        "utils/email_receipt.py",  # ride receipt
        "routes/drivers/subscriptions.py",  # Spinr Pass invoice
        "routes/auth.py",  # corporate sign-in code
        "routes/corporate_company.py",  # member invite
        "routes/corporate_accounts.py",  # KYB decision
        "routes/corporate_signup.py",  # ops alert
        "routes/admin/messaging.py",  # broadcast
        "routes/drivers/tax_exports.py",  # T4A / DSAR export
    }
    senders = _senders()
    missing = expected - senders.keys()
    assert not missing, f"expected senders no longer found (renamed or moved?): {missing}"
    for rel in expected:
        assert _LAYOUT_IMPORT.search(senders[rel]), f"{rel} no longer renders through email_layout"
