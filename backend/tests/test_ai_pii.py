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

    def test_bracketed_app_coordinates_survive(self):
        # Machine-generated trip endpoints from the quote-card tap and the
        # map-pin picker — "[lat,lng]" — must reach the model verbatim.
        # Scrubbing them re-introduced the re-geocode drift the bracketed
        # format exists to prevent (rule 6/6b coordinates arrived as
        # "[COORDS]" and the model silently re-geocoded the trip).
        text = "Book the Economy from 4325 Wakeling St [50.42140,-104.66410] to 4500 Gordon Rd [50.40790,-104.65010], total $5.27."
        assert scrub_pii(text) == text

    def test_bracketed_exemption_is_narrow(self):
        # Free-text coordinates still scrub even when a bracketed pair is in
        # the same message; brackets without a coordinate pair get no pass.
        scrubbed = scrub_pii("pin [50.40790,-104.65010] but I'm at 52.131802, -106.660767")
        assert "[50.40790,-104.65010]" in scrubbed
        assert "[COORDS]" in scrubbed
        assert "52.13" not in scrubbed


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
