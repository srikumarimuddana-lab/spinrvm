"""Regression tests for backend/ai/pii.py (PIPEDA / DV-16).

scrub_pii() runs on every user message before it reaches a third-party LLM
and before persistence to ai_messages. These cases pin the redaction
behaviour that routes/support.py relied on before the extraction, plus the
rider-AI cases (coordinates pasted from the app, postal codes in addresses).
"""

import pytest

from backend.ai.pii import ScrubPolicy, filter_tool_leakage, scrub_pii, scrub_pii_deep


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
        # authenticated chat path opts in via ScrubPolicy.AI_CHAT.
        text = "Book the Economy from 4325 Wakeling St [50.42140,-104.66410] to 4500 Gordon Rd [50.40790,-104.65010], total $5.27."
        assert scrub_pii(text, policy=ScrubPolicy.AI_CHAT) == text

    def test_tapped_suggestion_message_keeps_coordinates_and_postal_code(self):
        # The location-suggestion tap message ("Use <address> [lat,lng] as my
        # dropoff.") must keep its bracketed pair — losing it forces the model
        # to re-geocode the address text, which re-trips the imprecise_address
        # gate (the "check the exact street address" loop). The postal code
        # must survive too: an earlier version of this test "accepted" it
        # being scrubbed because only the coordinates matter for booking, but
        # the scrubbed label is what the model echoes in prose and passes back
        # as pickup_address/dropoff_address, and it ended up as the literal
        # "[POSTAL]" in rides.pickup_address (2026-09-04 change-log).
        scrubbed = scrub_pii(
            "Use 655 Albert St, Regina, SK S4T 1A1 [50.44079,-104.61802] as my dropoff.",
            policy=ScrubPolicy.AI_CHAT,
        )
        assert "[50.44079,-104.61802]" in scrubbed
        assert "S4T 1A1" in scrubbed
        assert "[POSTAL]" not in scrubbed
        assert "655 Albert St" in scrubbed

    def test_bracketed_exemption_is_narrow(self):
        # Free-text coordinates still scrub even when a bracketed pair is in
        # the same message; brackets without a coordinate pair get no pass.
        scrubbed = scrub_pii("pin [50.40790,-104.65010] but I'm at 52.131802, -106.660767", policy=ScrubPolicy.AI_CHAT)
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

    def test_ai_chat_policy_optin_sites_are_enumerated(self):
        # The AI_CHAT exemption (trip pins + postal codes kept) must never
        # quietly spread to Sentry, support, the public web assistant or the
        # /mcp serializer. Enumerate every mention of the policy member in
        # non-test source: ai/pii.py defines it, and the only opt-ins are the
        # authenticated chat path (orchestrator: user message + persisted
        # reply) and the model-facing tool-result cap (tools.py). The word is
        # matched bare (not the `ScrubPolicy.AI_CHAT` spelling) so an alias
        # or a re-export can't dodge the check; the cost is that comments
        # outside these files must not use the token either.
        import re as _re
        from pathlib import Path

        backend = Path(__file__).resolve().parents[1]
        opt_in_files = set()
        for path in backend.rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            if _re.search(r"\bAI_CHAT\b", path.read_text()):
                opt_in_files.add(path.relative_to(backend).as_posix())
        assert opt_in_files == {"ai/pii.py", "ai/orchestrator.py", "ai/tools.py"}


class TestPostalCodes:
    @pytest.mark.parametrize("code", ["S7K 3R5", "S7K3R5", "s7k-3r5"])
    def test_canadian_postal_redacted(self, code):
        scrubbed = scrub_pii(f"my address postal code is {code}")
        assert "[POSTAL]" in scrubbed

    def test_ordinary_words_survive(self):
        text = "when is my T4A available"
        assert scrub_pii(text) == text


class TestScrubPolicy:
    """ScrubPolicy names the egress boundary a scrub protects (ADR 012).
    STRICT is telemetry / third parties; AI_CHAT is the authenticated in-app
    assistant, where trip-location data (bracketed pins, postal codes) is the
    booking payload and must survive."""

    TEXT = "Use 655 Albert St, Regina, SK S4T 1A1 [50.44079,-104.61802] as my dropoff, call 306-555-1234 or jane@x.ca"

    def test_default_is_strict(self):
        assert scrub_pii(self.TEXT) == scrub_pii(self.TEXT, policy=ScrubPolicy.STRICT)

    def test_strict_redacts_postal_codes_and_bracketed_pins(self):
        scrubbed = scrub_pii(self.TEXT, policy=ScrubPolicy.STRICT)
        assert "[POSTAL]" in scrubbed
        assert "[COORDS]" in scrubbed
        assert "S4T 1A1" not in scrubbed
        assert "50.44079" not in scrubbed

    def test_ai_chat_keeps_postal_codes_and_bracketed_pins(self):
        scrubbed = scrub_pii(self.TEXT, policy=ScrubPolicy.AI_CHAT)
        assert "S4T 1A1" in scrubbed
        assert "[50.44079,-104.61802]" in scrubbed
        assert "[POSTAL]" not in scrubbed
        assert "[COORDS]" not in scrubbed

    def test_ai_chat_still_redacts_identifiers(self):
        scrubbed = scrub_pii(self.TEXT, policy=ScrubPolicy.AI_CHAT)
        assert "[PHONE]" in scrubbed and "555-1234" not in scrubbed
        assert "[EMAIL]" in scrubbed and "jane@x.ca" not in scrubbed
        # Free-text coordinates (not app-generated bracketed pins) are still
        # scrubbed — the exemption is the bracketed shape only.
        assert "[COORDS]" in scrub_pii("I'm at 52.131802, -106.660767", policy=ScrubPolicy.AI_CHAT)
        assert "[CARD]" in scrub_pii("card 4111 1111 1111 1111", policy=ScrubPolicy.AI_CHAT)
        assert "[GOVID]" in scrub_pii("sin 123-456-789", policy=ScrubPolicy.AI_CHAT)

    @pytest.mark.parametrize("bad", [True, "ai_chat", None, 1])
    def test_non_enum_policy_raises_instead_of_being_swallowed(self, bad):
        # scrub_pii_deep deliberately swallows exceptions from the pattern
        # pass; a wrong-typed policy must be rejected BEFORE that guard, or
        # the value would come back unscrubbed and nobody would know.
        with pytest.raises(TypeError):
            scrub_pii("S7K 3R5", policy=bad)
        with pytest.raises(TypeError):
            scrub_pii_deep({"address": "S7K 3R5"}, policy=bad)

    def test_deep_threads_the_policy_through_dicts_and_lists(self):
        # find_place returns candidates as a LIST of dicts — the list branch
        # of the recursion must honour the policy too, not only the dict one.
        result = {"candidates": [{"address": "2150 Prince of Wales Dr, Regina, SK S4V 2Z7"}], "phone": "306-555-1234"}
        chat = scrub_pii_deep(result, policy=ScrubPolicy.AI_CHAT)
        assert chat["candidates"][0]["address"].endswith("S4V 2Z7")
        assert chat["phone"] == "[PHONE]"
        strict = scrub_pii_deep(result)
        assert strict["candidates"][0]["address"].endswith("[POSTAL]")


class TestCardNumbers:
    @pytest.mark.parametrize(
        "text",
        [
            "my card is 4111111111111111",  # Visa, bare
            "my card is 4111-1111-1111-1111",  # Visa, dashed
            "my card is 4111 1111 1111 1111",  # Visa, spaced
            "card 5500000000000004 declined",  # Mastercard (legacy range)
            "card 2223000048400011 declined",  # Mastercard (2-series range)
            "amex 340000000000009 was charged twice",  # Amex, 15 digits
            "discover 6011000000000004 keeps failing",  # Discover
        ],
    )
    def test_card_variants_redacted(self, text):
        scrubbed = scrub_pii(text)
        assert "[CARD]" in scrubbed
        assert "1111" not in scrubbed and "0004" not in scrubbed and "0009" not in scrubbed

    @pytest.mark.parametrize(
        "text",
        [
            "ride reference 9999888877776666",  # 16 digits, not a recognized IIN prefix
            "session token 1234567890123456",  # ditto — starts with 1, no brand starts there
            "spinr_dispatch_offer_to_accept_duration_ms=1234567890123",  # long metric value
        ],
    )
    def test_unprefixed_long_digit_runs_are_not_mistaken_for_cards(self, text):
        """Same discriminator principle as the NANP phone fix: gate on a
        recognized card-network prefix, not digit count alone, or every long
        internal id becomes a false positive."""
        assert scrub_pii(text) == text


class TestGovernmentIds:
    @pytest.mark.parametrize("sin", ["123-456-789", "123 456 789"])
    def test_grouped_sin_redacted(self, sin):
        scrubbed = scrub_pii(f"my SIN is {sin}")
        assert "[GOVID]" in scrubbed
        assert "456" not in scrubbed

    def test_bare_ungrouped_nine_digits_is_not_redacted(self):
        """Deliberate scope limit, documented in pii.py: an ungrouped 9-digit
        run has no reliable discriminator (unlike the card-prefix gate), so
        matching on digit count alone would repeat the timestamp-collision
        regression. Only the separated 3-3-3 form is covered."""
        assert scrub_pii("order number 123456789") == "order number 123456789"

    def test_extra_leading_digit_is_not_mistaken_for_a_sin(self):
        # A 3-3-3 shape immediately preceded by another digit (no separator)
        # must be rejected by the (?<!\d) lookbehind, not matched starting one
        # character in. Deliberately NOT using a trailing-digit variant here:
        # any 3-3-4 digit shape (e.g. "123-456-7890") is caught first by the
        # pre-existing, unrelated phone-separator pattern earlier in
        # _PII_PATTERNS (it doesn't validate area codes), so it isn't a clean
        # test of the SIN pattern's own boundary.
        text = "ref 9123-456-789 open"
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


class TestFilterToolLeakage:
    """ACTION_ITEMS.md AI13 -- structural backstop for the prompt-only
    driver-persona-secrecy rule against printing tool names / internal
    jargon in the persisted/replayed reply copy."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "find_place",
            "get_fare_quote",
            "propose_ride_booking",
            "some_future_tool_name",  # not in today's registry -- still caught
        ],
    )
    def test_snake_case_tool_names_redacted(self, tool_name):
        text = f"Let me call {tool_name} to check that for you."
        filtered = filter_tool_leakage(text)
        assert tool_name not in filtered
        assert "[internal]" in filtered

    def test_normal_prose_is_unaffected(self):
        text = "Your ride to 123 Main St will be $18.50, arriving in about 5 minutes."
        assert filter_tool_leakage(text) == text

    def test_single_word_is_not_matched(self):
        # A single lowercase word has no underscore -- not tool-name-shaped.
        text = "search for a nearby driver"
        assert filter_tool_leakage(text) == text

    def test_multiple_leaks_in_one_message_all_redacted(self):
        text = "I ran find_place then get_fare_quote to build your quote."
        filtered = filter_tool_leakage(text)
        assert "find_place" not in filtered
        assert "get_fare_quote" not in filtered
        assert filtered.count("[internal]") == 2

    def test_idempotent(self):
        once = filter_tool_leakage("calling propose_ride_booking now")
        assert filter_tool_leakage(once) == once


class TestScrubPiiDeep:
    """2026-08-18 fleet audit — the structural gap: no AI tool RESULT was
    ever PII-scrubbed anywhere in the codebase before this fix, only the
    user's own message and the model's final reply text. scrub_pii_deep is
    the recursive-value scrub wired into tools.py::_cap_result, the single
    choke point both execute_tool() (chat loop) and /mcp funnel through."""

    def test_scrubs_a_string_leaf_nested_in_a_dict(self):
        from backend.ai.pii import scrub_pii_deep

        result = {"driver": {"phone": "306-555-1234"}}
        assert scrub_pii_deep(result) == {"driver": {"phone": "[PHONE]"}}

    def test_scrubs_string_leaves_inside_a_list(self):
        from backend.ai.pii import scrub_pii_deep

        result = {"notes": ["call me at 306-555-1234", "no PII here"]}
        assert scrub_pii_deep(result) == {"notes": ["call me at [PHONE]", "no PII here"]}

    def test_non_string_leaves_pass_through_unchanged(self):
        from backend.ai.pii import scrub_pii_deep

        result = {"count": 3, "active": True, "amount": 18.5, "note": None}
        assert scrub_pii_deep(result) == result

    def test_does_not_mutate_the_input(self):
        from backend.ai.pii import scrub_pii_deep

        original = {"driver": {"phone": "306-555-1234"}}
        scrub_pii_deep(original)
        assert original == {"driver": {"phone": "306-555-1234"}}

    def test_depth_limit_stops_a_pathological_structure_without_raising(self):
        from backend.ai.pii import scrub_pii_deep

        deep = {"phone": "306-555-1234"}
        for _ in range(20):
            deep = {"nested": deep}
        # Must never raise, regardless of how deep the structure recurses.
        scrub_pii_deep(deep)

    def test_a_bare_name_key_is_not_caught_here_by_design(self):
        """scrub_pii_deep is value-pattern-only, deliberately NOT key-name
        based like utils/sentry_scrub.py's _scrub_deep (whose KEY_ALLOWLIST
        treats a bare "name" key as a benign symbol). A plain name is not
        regex-detectable either way -- the real fix for a tool leaking a
        person's name is data minimization at the source, not this scrub.
        See tools_rides.py::_driver_public for the concrete example."""
        from backend.ai.pii import scrub_pii_deep

        result = {"name": "Nighil Kumar"}
        assert scrub_pii_deep(result) == {"name": "Nighil Kumar"}


class TestCapResultScrubsToolResults:
    """The wiring in tools.py::_cap_result — the structural fix itself."""

    def test_cap_result_scrubs_regex_detectable_pii_in_a_tool_result(self):
        from backend.ai.tools import _cap_result

        result = _cap_result({"driver_phone": "306-555-1234", "ok": True})
        assert result == {"driver_phone": "[PHONE]", "ok": True}

    def test_cap_result_never_scrubs_client_action(self):
        """The card is the rider's own data going back to the rider's own
        app, not a third-party egress (ADR 012). Scrubbing it here is what
        put the literal "[POSTAL]" into every dropoff-choice card, the
        tapped-card message, the model's prose and rides.pickup_address
        (2026-09-04 change-log). /mcp -- the one consumer that IS a third
        party -- re-scrubs its whole response in
        mcp_server._serialize_tool_payload instead."""
        from backend.ai.tools import _cap_result

        card = {
            "type": "location_suggestions",
            "candidates": [{"address": "2150 Prince of Wales Dr, Regina, SK S4V 2Z7", "phone": "306-555-1234"}],
        }
        handler_result = {"ok": True, "_client_action": card}
        result = _cap_result(handler_result)
        assert result["_client_action"] == card
        assert result["_client_action"] is card  # same object, never copied through the scrubber
        # The handler's own dict is left alone (find_place aliases the same
        # candidate dicts in its model-facing list; an in-place split would
        # corrupt the card through that alias).
        assert handler_result == {"ok": True, "_client_action": card}

    def test_cap_result_model_portion_keeps_postal_codes(self):
        """ScrubPolicy.AI_CHAT on the model-facing portion: identifiers go,
        trip-location data (postal code beside a street address) stays --
        otherwise the model echoes "[POSTAL]" in prose and passes it back
        into get_fare_quote / propose_ride_booking as the address."""
        from backend.ai.tools import _cap_result

        result = _cap_result(
            {"address": "1855 Victoria Ave #304, Regina, SK S4P 3T7, Canada", "driver_phone": "306-555-1234"}
        )
        assert result == {"address": "1855 Victoria Ave #304, Regina, SK S4P 3T7, Canada", "driver_phone": "[PHONE]"}

    def test_cap_result_scrub_survives_truncation(self):
        from backend.ai.tools import TOOL_RESULT_MAX_CHARS, _cap_result

        huge_with_pii = "306-555-1234 " + ("x" * TOOL_RESULT_MAX_CHARS)
        result = _cap_result({"blob": huge_with_pii})
        assert result.get("_truncated") is True
        assert "[PHONE]" in result["preview"]
        assert "306-555-1234" not in result["preview"]

    def test_does_not_interfere_with_pii_placeholders(self):
        # scrub_pii's own placeholder tokens are uppercase, no underscore --
        # confirm chaining the two scrubbers doesn't cross-contaminate.
        text = scrub_pii("call 306-555-1234")
        assert filter_tool_leakage(text) == text
        assert "[PHONE]" in text
