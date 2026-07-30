"""Regression tests for backend/ai/pii.py (PIPEDA / DV-16).

scrub_pii() runs on every user message before it reaches a third-party LLM
and before persistence to ai_messages. These cases pin the redaction
behaviour that routes/support.py relied on before the extraction, plus the
rider-AI cases (coordinates pasted from the app, postal codes in addresses).
"""

import pytest

from backend.ai.pii import scrub_pii


class TestPhoneNumbers:
    @pytest.mark.parametrize(
        "text",
        [
            "call me at 306-555-1234",
            "call me at (306) 555-1234",
            "call me at +1 306 555 1234",
            "call me at 306.555.1234",
        ],
    )
    def test_phone_variants_redacted(self, text):
        scrubbed = scrub_pii(text)
        assert "[PHONE]" in scrubbed
        assert "555" not in scrubbed

    def test_plain_numbers_not_redacted(self):
        assert scrub_pii("my ride was $18.50 on May 23") == "my ride was $18.50 on May 23"

    @pytest.mark.parametrize(
        "text",
        [
            "+13065551234",  # E.164, how the system stores them
            "13065551234",  # 11 digits with country code
            "3065551234",  # bare 10-digit, valid NANP area code
            "ph=+13065551234 ride_id=r1",  # embedded in a log line
        ],
    )
    def test_separatorless_phone_shapes_redacted(self, text):
        assert "[PHONE]" in scrub_pii(text)
        assert "5551234" not in scrub_pii(text)

    @pytest.mark.parametrize(
        "text",
        [
            "ts=1769817600 ride_id=r1",  # unix timestamp — 10 digits until 2286
            "duration_ms=1234567890",
            "build 1769817600 version 10.2.4",
            "spinr_dispatch_offer_to_accept_duration_ms=1847",
            "stripe_event evt_1769817600 processed",
        ],
    )
    def test_observability_values_are_not_mistaken_for_phones(self, text):
        """Regression: the pattern was `(?<!\\d)\\+?1?\\d{10}(?!\\d)`, which matched
        ANY bare 10-digit run — so every unix timestamp became `[PHONE]`.

        This mattered beyond cosmetics: utils/sentry_scrub applies scrub_pii to
        Sentry event text, so production events had their timestamps and
        millisecond durations rewritten. A NANP area code cannot start with 0 or 1
        and a plausible unix timestamp always does, which is the discriminator the
        pattern now uses.
        """
        assert scrub_pii(text) == text, "an observability value was redacted as a phone number"

    def test_a_10_digit_run_starting_with_1_is_never_a_phone(self):
        """The precise invariant behind the fix — asserted directly so a future
        pattern change that reintroduces the collision fails here."""
        for ts in ("1000000000", "1769817600", "1999999999"):
            assert scrub_pii(ts) == ts
        # …while the same length starting 2-9 is a valid NANP number.
        for phone in ("2065551234", "9995551234"):
            assert scrub_pii(phone) == "[PHONE]"


class TestEmails:
    def test_email_redacted(self):
        scrubbed = scrub_pii("email me at jane.doe+spinr@example.ca please")
        assert scrubbed == "email me at [EMAIL] please"


class TestCoordinates:
    def test_lat_lng_pair_redacted(self):
        scrubbed = scrub_pii("I'm at 52.131802, -106.660767 right now")
        assert "[COORDS]" in scrubbed
        assert "52.13" not in scrubbed

    def test_money_amounts_survive(self):
        # A fare like 18.50 must not be mistaken for a coordinate.
        assert "[COORDS]" not in scrub_pii("the fare was 18.50, tip 3.00")

    def test_bracketed_app_coordinates_survive_in_chat_mode(self):
        # Machine-generated trip endpoints from the quote-card tap and the
        # map-pin picker — "[lat,lng]" — must reach the model verbatim.
        # Scrubbing them re-introduced the re-geocode drift the bracketed
        # format exists to prevent (rule 6/6b coordinates arrived as
        # "[COORDS]" and the model silently re-geocoded the trip). Only the
        # chat-message path opts in via keep_trip_pins.
        text = "Book the Economy from 4325 Wakeling St [50.42140,-104.66410] to 4500 Gordon Rd [50.40790,-104.65010], total $5.27."
        assert scrub_pii(text, keep_trip_pins=True) == text

    def test_tapped_suggestion_message_keeps_coordinates(self):
        # The location-suggestion tap message ("Use <address> [lat,lng] as my
        # dropoff.") must keep its bracketed pair — losing it forces the model
        # to re-geocode the address text, which re-trips the imprecise_address
        # gate (the "check the exact street address" loop). The postal code is
        # still scrubbed; that is accepted — the coordinates, not the label,
        # are what the model books on.
        scrubbed = scrub_pii(
            "Use 655 Albert St, Regina, SK S4T 1A1 [50.44079,-104.61802] as my dropoff.",
            keep_trip_pins=True,
        )
        assert "[50.44079,-104.61802]" in scrubbed
        assert "[POSTAL]" in scrubbed
        assert "655 Albert St" in scrubbed

    def test_bracketed_exemption_is_narrow(self):
        # Free-text coordinates still scrub even when a bracketed pair is in
        # the same message; brackets without a coordinate pair get no pass.
        scrubbed = scrub_pii("pin [50.40790,-104.65010] but I'm at 52.131802, -106.660767", keep_trip_pins=True)
        assert "[50.40790,-104.65010]" in scrubbed
        assert "[COORDS]" in scrubbed
        assert "52.13" not in scrubbed

    def test_bracketed_coordinates_scrub_by_default(self):
        # scrub_pii is shared — utils/sentry_scrub.py runs it over exception
        # messages and breadcrumbs, where raw GPS must NEVER survive (PIPEDA).
        # Without the explicit chat-path opt-in, bracketed pairs scrub like
        # any other coordinates.
        scrubbed = scrub_pii("geocode failed near [50.40790,-104.65010]")
        assert "[COORDS]" in scrubbed
        assert "50.40790" not in scrubbed

    def test_orchestrator_is_the_only_trip_pin_optin(self):
        # The keep_trip_pins exemption must never quietly spread to Sentry or
        # support scrubbing. Enumerate every call site: only the chat-message
        # path (orchestrator) opts in.
        import re as _re
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1]
        opt_ins = []
        for path in backend.rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            for match in _re.finditer(r"scrub_pii\([^)]*keep_trip_pins\s*=\s*True", path.read_text()):
                opt_ins.append(path.relative_to(backend).as_posix())
        assert opt_ins == ["ai/orchestrator.py"]


class TestPostalCodes:
    @pytest.mark.parametrize("code", ["S7K 3R5", "S7K3R5", "s7k-3r5"])
    def test_canadian_postal_redacted(self, code):
        scrubbed = scrub_pii(f"my address postal code is {code}")
        assert "[POSTAL]" in scrubbed

    def test_ordinary_words_survive(self):
        text = "when is my T4A available"
        assert scrub_pii(text) == text


class TestComposite:
    def test_multiple_identifiers_in_one_message(self):
        scrubbed = scrub_pii("I'm Jane, 306-555-1234, jane@x.ca, at 52.1318,-106.6608, S7K 3R5")
        assert "[PHONE]" in scrubbed
        assert "[EMAIL]" in scrubbed
        assert "[COORDS]" in scrubbed
        assert "[POSTAL]" in scrubbed

    def test_idempotent(self):
        once = scrub_pii("call 306-555-1234")
        assert scrub_pii(once) == once

    def test_support_route_uses_shared_impl(self):
        # routes/support.py must keep importing the shared scrubber rather
        # than re-growing a private copy. Under the dual-import convention the
        # module object identity can legitimately differ (ai.pii vs
        # backend.ai.pii resolve to distinct module objects depending on the
        # import root), so assert by source module + behaviour rather than `is`.
        from backend.routes import support

        assert support.scrub_pii.__module__.endswith("ai.pii")
        assert support.scrub_pii.__name__ == "scrub_pii"
        sample = "call 306-555-1234 or email jane@x.ca"
        assert support.scrub_pii(sample) == scrub_pii(sample)
