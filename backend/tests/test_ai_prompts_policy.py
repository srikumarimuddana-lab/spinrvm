"""Policy assertions on the central AI system prompts (ai/prompts.py).

Moved here from tests/test_routes_support_coverage.py on 2026-09-08 (F04).
They used to assert against routes/support.py's own `SYSTEM_PROMPT`, a second
copy of the persona that existed only because the legacy /support/chat
endpoint called Gemini directly. That copy is exactly how the endpoint's
prompt came to drift: it stated a fabricated "2-3 business days" payout
timeline and described driver earnings as reduced by a "platform service
fee", directly contradicting Spinr's 0%-commission model as stated in
schemas.py's platform_fee_percent default, fare_service.py, the faqs table,
and ai/prompts.py itself.

The copy is gone and /support/chat now uses these prompts. The guard follows
the prompt rather than being deleted with the endpoint — a duplicated persona
is a drift hazard, but so is dropping the only automated check on the claims
the assistant is allowed to make about money and emergencies.

Prose can't be meaningfully unit-tested for tone. These specific factual and
policy claims can be asserted against directly, and are the two that carry
real consequences: a fabricated fee claim is a commercial misrepresentation
(CLAUDE.md: "Not a hidden-fee operator"), and a missing 911 redirect is a
safety gap (CLAUDE.md: "Not a 911 replacement").
"""

import pytest

from backend.ai.prompts import _CORES

pytestmark = pytest.mark.unit

# Rider and driver are the authenticated customer personas. "web" is the
# anonymous public-site assistant, which has no earnings or payout content and
# is asserted separately below only for the safety rule.
_CUSTOMER_AUDIENCES = ["rider", "driver"]


@pytest.mark.parametrize("audience", _CUSTOMER_AUDIENCES)
class TestNoFabricatedMoneyClaims:
    def test_no_fabricated_timelines_or_amounts(self, audience):
        lowered = _CORES[audience].lower()
        for banned in ("2–3 business day", "2-3 business day", "minimum payout is $10"):
            assert banned not in lowered, f"fabricated timeline/amount in {audience!r} prompt: {banned!r}"

    def test_no_platform_fee_deduction_language(self, audience):
        """Checks the affirmative phrasings that claim a fee IS deducted — not
        the (correct) negation of one, which the prompts do contain."""
        lowered = _CORES[audience].lower()
        for banned in (
            "minus the platform service fee",
            "what is the platform fee",
            "platform fee varies",
        ):
            assert banned not in lowered, f"platform-fee deduction claim in {audience!r} prompt: {banned!r}"

    def test_states_the_zero_commission_model(self, audience):
        assert "100% of the fare" in _CORES[audience]


class TestEmergencyRedirect:
    @pytest.mark.parametrize("audience", sorted(_CORES))
    def test_every_persona_redirects_to_911(self, audience):
        """Including "web": the public-site assistant can be asked a safety
        question by a visitor, and CLAUDE.md's rule ("Not a 911 replacement")
        is not scoped to authenticated surfaces."""
        assert "911" in _CORES[audience], f"{audience!r} prompt has no 911 redirect"

    @pytest.mark.parametrize("audience", _CUSTOMER_AUDIENCES)
    def test_customer_personas_disclaim_being_an_emergency_service(self, audience):
        lowered = _CORES[audience].lower()
        assert (
            "not an emergency service" in lowered
            or "not a replacement" in lowered
            or "never a replacement" in lowered
        ), f"{audience!r} prompt claims no emergency-service disclaimer"


def test_every_audience_has_a_core_prompt():
    """A new audience added without a prompt would fall through to a KeyError
    at request time; make the omission visible here instead."""
    assert set(_CORES) == {"rider", "driver", "web"}
