"""`utils.pii.redact_error_detail` — the 4xx detail scrubber (WS-E).

Audit 2026-09-05 §1.7: `utils/error_handling.py` sanitised only
`status_code >= 500`, so ~30 routes' `detail=str(e)` / `detail=f"...{e}"` sent
raw exception text to the client on a 4xx. The worst case is
`routes/admin/legacy_sin_dob_backfill.py`, whose import service raises with the
offending CSV row — a SIN and a date of birth in a 400 body, and from there in
browser history, Vercel logs and Sentry breadcrumbs.

4xx details cannot be replaced wholesale the way 5xx are: they *are* the
user-facing UX ("Invalid phone number", "Card declined"). So the identifiers are
scrubbed and the sentence is kept. These tests pin both halves — what must be
removed, and what must survive.
"""

from __future__ import annotations

import pytest

from backend.utils.pii import redact_error_detail

pytestmark = pytest.mark.unit


class TestRemovesIdentifiers:
    @pytest.mark.parametrize(
        "leaked",
        [
            "Import failed on row 4: rider@example.com",  # email
            "Import failed on row 4: 123456789",  # SIN
            "Import failed on row 4: 4242 4242 4242 4242",  # card PAN
            "Import failed on row 4: +1 306 555 0142",  # phone
            "Import failed on row 4: 1987-04-12",  # date of birth
            "Charge failed for pi_3OaBcDeFgHiJkLmN",  # Stripe id
            # Split so the repo's pre-commit secret scanner does not flag this
            # fixture, matching the idiom in test_stripe_mode_audit.py.
            "Bad key " + "sk_live_" + "51AbCdEfGhIjKlMnOp",  # Stripe secret
            "Token eyJhbGciOiJIUzI1.eyJzdWIiOiIx.abc123",  # JWT
            "Ride 3f2504e0-4f89-11d3-9a0c-0305e82c3301 not found",  # UUID
            'duplicate key value violates unique constraint "users_phone_key"',
            "PGRST205 could not find the table",
        ],
    )
    def test_identifier_does_not_survive(self, leaked):
        out = redact_error_detail(leaked)
        assert "[redacted]" in out
        # The identifying substring itself must be gone.
        tail = leaked.split(": ")[-1] if ": " in leaked else leaked
        assert tail not in out or tail == leaked.split()[0]

    def test_the_sin_dob_backfill_worst_case(self):
        """The exact shape the audit called out: an import error carrying a
        whole CSV row."""
        out = redact_error_detail("Row 12 invalid: Jane Doe,jane@example.com,306-555-0142,123456789,1987-04-12")
        for secret in ("jane@example.com", "306-555-0142", "123456789", "1987-04-12"):
            assert secret not in out
        assert "Row 12 invalid" in out  # the actionable part survives

    def test_email_is_removed_whole(self):
        """Email runs first deliberately — a later digit/date pattern would
        otherwise eat the local part and leave the domain exposed."""
        out = redact_error_detail("Conflict with user 2024test.user@spinr.ca here")
        assert "spinr.ca" not in out
        assert "2024test.user" not in out


class TestKeepsUsableMessages:
    @pytest.mark.parametrize(
        "ux",
        [
            "Invalid phone number",
            "Card declined",
            "Must wait 240 more seconds before marking no-show",
            "Ride is not in driver_arrived state",
            "This ride has no pickup code — contact support to start it",
            "Too many incorrect pickup codes for this ride — try again later",
            "No active offer for this ride",
        ],
    )
    def test_ordinary_ux_copy_is_untouched(self, ux):
        assert redact_error_detail(ux) == ux

    def test_short_numbers_survive(self):
        """Amounts, counts and timeouts are not identifiers."""
        assert redact_error_detail("Wait 300 seconds") == "Wait 300 seconds"
        assert redact_error_detail("Fee is 4.50") == "Fee is 4.50"


class TestContract:
    @pytest.mark.parametrize("passthrough", [None, 123, {"code": "X"}, ["a"], ""])
    def test_non_string_details_pass_through(self, passthrough):
        """FastAPI allows dict/list details — those are structured payloads a
        route built deliberately, not interpolated exception text."""
        assert redact_error_detail(passthrough) is passthrough

    def test_is_idempotent(self):
        once = redact_error_detail("Failed for a@b.com and 123456789")
        assert redact_error_detail(once) == once

    def test_long_details_are_truncated(self):
        """A 4xx message longer than the cap is a stack trace or a dumped row,
        not UX copy."""
        out = redact_error_detail("x" * 5000)
        assert len(out) <= 301
        assert out.endswith("…")

    def test_returns_a_string_for_string_input(self):
        assert isinstance(redact_error_detail("anything"), str)
