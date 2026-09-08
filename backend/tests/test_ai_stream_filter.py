"""F08 — boundary-safe streaming output filter (ai/stream_filter.py).

Source finding: docs/security/2026-09-08-ai-security-assessment.md, F08.
The orchestrator yielded provider text directly as token frames and filtered
only at the end, for the copy written to ai_messages and the FAQ cache. A
scripted provider response containing a synthetic email reached the emitted
response unchanged, so a clean database transcript did not prove the rider saw
a clean answer.

The interesting cases here are all about SPLITS. Filtering a whole string is
already covered by test_ai_pii.py; what this module adds is that a value
arriving in pieces is still caught, and — the subtler half — that the
holdback cannot itself split a value and thereby defeat the scrub.
"""

import random

import pytest

from backend.ai.pii import ScrubPolicy, filter_tool_leakage, scrub_pii
from backend.ai.stream_filter import StreamingOutputFilter, _safe_cut

pytestmark = pytest.mark.unit


def _stream(text, chunk_size, policy=ScrubPolicy.AI_CHAT):
    """Feed ``text`` through the filter in fixed-size chunks."""
    f = StreamingOutputFilter(policy=policy)
    out = [f.feed(text[i : i + chunk_size]) for i in range(0, len(text), chunk_size)]
    out.append(f.flush())
    return "".join(out), f


# (label, text, the raw value that must never be emitted, its redaction token)
SECRETS = [
    ("email", "please email me at jane.doe+spinr@example.ca about it", "jane.doe+spinr@example.ca", "[EMAIL]"),
    ("phone", "you can call me at 306-555-1234 any time", "306-555-1234", "[PHONE]"),
    ("card", "the card was 4111 1111 1111 1111 and it declined", "4111 1111 1111 1111", "[CARD]"),
    ("amex_465", "amex 3782 822463 10005 declined again", "3782 822463 10005", "[CARD]"),
    ("sin", "my SIN is 123-456-789 for the form", "123-456-789", "[GOVID]"),
]


class TestValuesSplitAcrossChunks:
    """The finding itself: a value that arrives in pieces matches neither
    piece on its own, so per-chunk scrubbing sees nothing."""

    @pytest.mark.parametrize("label,text,secret,token", SECRETS, ids=[s[0] for s in SECRETS])
    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 11, 40])
    def test_secret_is_redacted_at_every_chunk_size(self, label, text, secret, token, chunk_size):
        emitted, _ = _stream(text, chunk_size)
        assert secret not in emitted
        assert token in emitted

    @pytest.mark.parametrize("label,text,secret,token", SECRETS, ids=[s[0] for s in SECRETS])
    @pytest.mark.parametrize("chunk_size", [1, 3, 8, 50])
    def test_stream_output_equals_whole_string_scrub(self, label, text, secret, token, chunk_size):
        """Stronger than "the secret is gone": the streamed result must be
        byte-identical to filtering the whole string at once. That rules out
        a filter that redacts by mangling or dropping text."""
        emitted, _ = _stream(text, chunk_size)
        assert emitted == filter_tool_leakage(scrub_pii(text, policy=ScrubPolicy.AI_CHAT))

    def test_randomised_chunkings(self):
        """Fixed chunk sizes only probe a few boundary positions. Uneven
        splits are what a real provider produces, and the split landing
        one character inside a value is the case that breaks a naive
        holdback."""
        rng = random.Random(7)
        for _ in range(300):
            label, text, secret, token = rng.choice(SECRETS)
            chunks, i = [], 0
            while i < len(text):
                n = rng.randint(1, 6)
                chunks.append(text[i : i + n])
                i += n
            f = StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT)
            emitted = "".join(f.feed(c) for c in chunks) + f.flush()
            assert secret not in emitted, f"{label}: leaked with chunks {chunks}"
            assert emitted == filter_tool_leakage(scrub_pii(text, policy=ScrubPolicy.AI_CHAT))


class TestNoDataLoss:
    """A filter that drops or duplicates text would be a worse bug than the
    one it fixes — the rider would get a mangled answer."""

    @pytest.mark.parametrize("chunk_size", [1, 4, 9, 200])
    def test_clean_text_passes_through_byte_identical(self, chunk_size):
        clean = "Your driver Sam is 3 minutes away in a blue Corolla. The fare is $18.50 including GST."
        emitted, _ = _stream(clean, chunk_size)
        assert emitted == clean

    def test_empty_and_whitespace_chunks_are_harmless(self):
        f = StreamingOutputFilter()
        assert f.feed("") == ""
        f.feed("  ")
        assert f.flush() == "  "

    def test_flush_drains_and_is_idempotent(self):
        """Callers should be able to flush in a `finally` without tracking
        whether the normal path already did."""
        f = StreamingOutputFilter()
        f.feed("short reply")
        assert f.flush() == "short reply"
        assert f.flush() == ""

    def test_emitted_text_matches_what_was_returned(self):
        emitted, f = _stream(SECRETS[0][1], 5)
        assert f.emitted_text == emitted
        assert "[EMAIL]" in f.emitted_text
        assert "jane.doe" not in f.emitted_text


class TestHoldbackCannotDefeatTheScrub:
    """The subtle half. Holding back N characters still leaves a cut point,
    and a value straddling it would be split — emitting its head raw, and then
    its tail raw too (the tail no longer matches the pattern without the
    head). A holdback alone is worse than useless; _safe_cut is what makes it
    correct."""

    def test_safe_cut_walks_back_out_of_a_match(self):
        buf = "call 306-555-1234 now"
        start = buf.index("306")
        # A cut in the middle of the phone number must move back to its start.
        cut = _safe_cut(buf, start + 4, ScrubPolicy.AI_CHAT)
        assert cut == start

    def test_safe_cut_leaves_a_clean_position_alone(self):
        buf = "no secrets here at all"
        assert _safe_cut(buf, 10, ScrubPolicy.AI_CHAT) == 10

    def test_safe_cut_never_returns_negative(self):
        buf = "306-555-1234 trailing text"
        assert _safe_cut(buf, 3, ScrubPolicy.AI_CHAT) == 0

    def test_a_value_at_the_holdback_boundary_is_still_redacted(self):
        """Positions the secret so a fixed-holdback cut lands inside it."""
        for pad in range(0, 40):
            text = ("x" * pad) + " call 306-555-1234 now " + ("y" * 120)
            emitted, _ = _stream(text, 8)
            assert "306-555-1234" not in emitted, f"leaked at pad={pad}"
            assert "[PHONE]" in emitted


class TestPolicyIsHonoured:
    def test_ai_chat_keeps_bracketed_trip_pins(self):
        """The documented AI_CHAT exception (ADR 012 / PIA S3) must survive
        the streaming path too — redacting trip endpoints here would re-trip
        the 2026-09-04 re-geocode regression from a new direction."""
        text = "Book from 4325 Wakeling St [50.42140,-104.66410] please"
        emitted, _ = _stream(text, 3, ScrubPolicy.AI_CHAT)
        assert "[50.42140,-104.66410]" in emitted

    def test_strict_redacts_them(self):
        text = "Book from 4325 Wakeling St [50.42140,-104.66410] please"
        emitted, _ = _stream(text, 3, ScrubPolicy.STRICT)
        assert "[50.42140,-104.66410]" not in emitted


class TestToolLeakageIsFilteredOnTheStream:
    def test_snake_case_tool_name_split_across_chunks(self):
        emitted, _ = _stream("Let me run find_" + "place to check that", 4)
        assert "find_place" not in emitted
        assert "[internal]" in emitted


class TestOrchestratorWiring:
    """The filter only helps if the orchestrator actually routes through it."""

    def test_orchestrator_emits_filtered_text_not_raw_events(self):
        # _run_chat_turn, not run_chat_turn: the public name is a thin
        # conversation-lock wrapper and contains none of the streaming code,
        # so asserting against it would pass vacuously.
        import inspect

        from backend.ai import orchestrator

        src = inspect.getsource(orchestrator._run_chat_turn)
        assert "StreamingOutputFilter" in src
        # The pre-F08 line yielded the provider event verbatim.
        assert 'yield "token", {"text": event.text}' not in src
        # The withheld tail must be released, or replies would truncate.
        assert "out_filter.flush()" in src

    def test_stored_copy_is_derived_from_the_delivered_text(self):
        """The transcript must record what the rider actually received. Before
        F08 the stored copy was filtered from the RAW text, so the two could
        legitimately differ and only the stored one was clean."""
        import inspect

        from backend.ai import orchestrator

        src = inspect.getsource(orchestrator._run_chat_turn)
        assert "out_filter.emitted_text" in src
        assert "scrub_pii(delivered_text" in src
