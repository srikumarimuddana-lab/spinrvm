"""F08 — boundary-safe streaming output filter (ai/stream_filter.py).

Source finding: the AI security assessment on PR #5138, F08. The orchestrator
yielded provider text directly as token frames and filtered only at the end,
for the copy written to ai_messages and the FAQ cache, so a clean database
transcript did not prove the rider saw a clean answer.

**Every fixture here is longer than `_HOLDBACK_CHARS`, deliberately.** The
first version of this suite used short strings, which meant `feed()` always
returned "" and `flush()` scrubbed the whole text in one piece — so the tests
compared `scrub(whole)` against `scrub(whole)` and passed while the incremental
path they claimed to cover was badly broken. Any new case added here must
exceed the holdback or it tests nothing. `test_fixtures_actually_exercise_the_
release_path` guards that.

The property worth asserting is a single one, and it is asserted directly:
**the concatenation of everything the filter emits equals filtering the whole
reply at once.** Everything else (secrets redacted, trip pins preserved, no
invented redactions, no data loss) follows from it.
"""

import random

import pytest

from backend.ai.pii import ScrubPolicy, filter_tool_leakage, scrub_pii
from backend.ai.stream_filter import _HOLDBACK_CHARS, StreamingOutputFilter

pytestmark = pytest.mark.unit

# Long enough that every case reaches feed()'s release path.
_PAD = "y" * 140


def _whole(text, policy=ScrubPolicy.AI_CHAT):
    """What the filter must be indistinguishable from."""
    return filter_tool_leakage(scrub_pii(text, policy=policy))


def _stream(text, chunk_size, policy=ScrubPolicy.AI_CHAT):
    f = StreamingOutputFilter(policy=policy)
    out = [f.feed(text[i : i + chunk_size]) for i in range(0, len(text), chunk_size)]
    out.append(f.flush())
    return "".join(out), f


TEXTS = [
    # Each names the defect it exists to catch; all four shipped broken once.
    ("trip_pin", "Book from 4325 Wakeling St [50.42140,-104.66410] please. " + _PAD),
    ("id_not_a_phone", "your booking reference is 1234567890123 ok " + _PAD),
    ("tool_name", "I will run find_place for you now. " + _PAD),
    (
        "substitution_dependent_pan",
        "your ride 3782 822463 10005306-555-1234S7K 1A1306-555-1234your ride "
        "jane@ex.ca306-555-1234your ride lat=52.1332, lng=-106.67your ride ",
    ),
    (
        "many_secrets",
        "call me at 306-555-1234 or email jane.doe+spinr@example.ca, card "
        "4111 1111 1111 1111, amex 3782 822463 10005, SIN 123-456-789. " + _PAD,
    ),
    ("clean", "Your driver Sam is 3 minutes away in a blue Corolla. The fare is $18.50 inc GST. " + _PAD),
    ("two_pins", "Pickup [50.42140,-104.66410] dropoff [50.40790,-104.65010] total $5.27 S7K 1A1. " + _PAD),
]


class TestSuiteIntegrity:
    def test_fixtures_actually_exercise_the_release_path(self):
        """The bug that made the first version of this suite worthless. If a
        fixture is shorter than the holdback, feed() never emits and the test
        silently degrades into a whole-string scrub comparison."""
        for label, text in TEXTS:
            assert len(text) > _HOLDBACK_CHARS, f"{label} is too short to test anything"

        f = StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT)
        text = TEXTS[0][1]
        emitted_before_flush = [f.feed(text[i : i + 5]) for i in range(0, len(text), 5)]
        assert any(emitted_before_flush), "feed() never emitted — the release path is untested"


class TestStreamEqualsWholeStringFilter:
    """The one property that matters. If this holds for every chunking, the
    filter is indistinguishable from filtering the finished reply."""

    @pytest.mark.parametrize("label,text", TEXTS, ids=[t[0] for t in TEXTS])
    @pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 13, 29, 97, 400])
    @pytest.mark.parametrize("policy", [ScrubPolicy.AI_CHAT, ScrubPolicy.STRICT])
    def test_fixed_chunk_sizes(self, label, text, chunk_size, policy):
        emitted, _ = _stream(text, chunk_size, policy)
        assert emitted == _whole(text, policy)

    def test_randomised_uneven_chunkings(self):
        """Real providers emit uneven chunks. The failures that shipped were
        all position-dependent, so the split has to land everywhere."""
        rng = random.Random(11)
        for _ in range(400):
            label, text = rng.choice(TEXTS)
            policy = rng.choice([ScrubPolicy.AI_CHAT, ScrubPolicy.STRICT])
            f = StreamingOutputFilter(policy=policy)
            out, i = [], 0
            while i < len(text):
                n = rng.randint(1, 9)
                out.append(f.feed(text[i : i + n]))
                i += n
            out.append(f.flush())
            assert "".join(out) == _whole(text, policy), f"{label} diverged"


class TestTheFourDefectsThatShipped:
    """Named regressions. Each of these was live in the first implementation
    and is re-checked at every offset around the holdback boundary, because
    all four were position-dependent."""

    def test_ai_chat_trip_pins_are_preserved(self):
        """Was destroyed at 60/60 offsets: the cut split '[' from the digits,
        so _BRACKETED_COORDS could no longer stash the pin and it became
        '[[COORDS]]'. That is the 2026-09-04 re-geocode regression (ADR 012 /
        PIA S3), and it corrupted the persisted row too."""
        pin = "[50.42140,-104.66410]"
        for pad in range(0, 40):
            text = ("z" * pad) + f"Book from 4325 Wakeling St {pin} please. " + _PAD
            emitted, _ = _stream(text, 7)
            assert pin in emitted, f"trip pin destroyed at pad={pad}"

    def test_no_invented_redactions_from_split_anchors(self):
        """Was wrong at 9/60 offsets: a 13-digit reference is correctly
        rejected by the phone pattern's `(?<![\\d+])…(?!\\d)` anchors, but a
        split tail '4567890123' matches `[2-9]\\d{9}` at string start. The
        filter invented '[PHONE]' and corrupted a legitimate answer — the
        id-collision class pii.py's own comment documents."""
        for pad in range(0, 40):
            text = ("z" * pad) + "your booking reference is 1234567890123 ok " + _PAD
            emitted, _ = _stream(text, 7)
            assert emitted == _whole(text), f"invented redaction at pad={pad}"
            assert "1234567890123" in emitted

    def test_tool_name_never_leaks(self):
        """Was leaked at 18/60 offsets: the cut split 'find_' from 'place',
        and neither fragment matches the snake_case pattern."""
        for pad in range(0, 40):
            text = ("z" * pad) + "I will run find_place for you now. " + _PAD
            emitted, _ = _stream(text, 7)
            assert "find_place" not in emitted, f"tool name leaked at pad={pad}"

    def test_substitution_dependent_card_is_redacted(self):
        """The subtlest one, and the reason a 'scan for matches and avoid
        splitting them' approach cannot work: the Amex pattern only matches
        AFTER the phone substitution creates a non-digit boundary. A scan of
        the raw buffer cannot see a match that does not exist yet, so the cut
        landed inside it and the full PAN reached the rider — and the stored
        copy, and the cross-user FAQ cache."""
        text = TEXTS[3][1]
        for chunk in (2, 7, 11):
            emitted, _ = _stream(text, chunk)
            assert "3782 822463 10005" not in emitted
            assert emitted == _whole(text)


class TestNoDataLoss:
    """A filter that drops or mangles text is a worse bug than the one it
    fixes — the rider gets a broken answer."""

    @pytest.mark.parametrize("chunk_size", [1, 4, 9, 500])
    def test_clean_text_passes_through_byte_identical(self, chunk_size):
        clean = "Your driver Sam is 3 minutes away in a blue Corolla. The fare is $18.50. " + _PAD
        emitted, _ = _stream(clean, chunk_size)
        assert emitted == clean

    def test_flush_drains_and_is_idempotent(self):
        f = StreamingOutputFilter()
        f.feed("short reply")
        assert f.flush() == "short reply"
        assert f.flush() == ""

    def test_empty_chunks_are_harmless(self):
        f = StreamingOutputFilter()
        assert f.feed("") == ""
        f.feed("  ")
        assert f.flush() == "  "

    def test_emitted_text_matches_what_was_returned(self):
        emitted, f = _stream(TEXTS[4][1], 5)
        assert f.emitted_text == emitted
        assert "jane.doe+spinr@example.ca" not in f.emitted_text


class TestFailureHandling:
    def test_a_scrub_failure_withholds_rather_than_emitting_raw(self):
        """Emitting unfiltered text is the one outcome this module exists to
        prevent, so a broken scrub must withhold — the orchestrator's own
        final scrub still produces a clean stored copy."""
        import backend.ai.stream_filter as sfmod

        f = StreamingOutputFilter()
        original = sfmod.scrub_pii
        sfmod.scrub_pii = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            assert f.feed("call 306-555-1234 " + _PAD) == ""
            assert f.flush() == ""
        finally:
            sfmod.scrub_pii = original


class TestOrchestratorWiring:
    """The filter only helps if the orchestrator routes through it."""

    def test_orchestrator_emits_filtered_text_not_raw_events(self):
        # _run_chat_turn, not run_chat_turn: the public name is a thin
        # conversation-lock wrapper containing none of the streaming code, so
        # asserting against it would pass vacuously.
        import inspect

        from backend.ai import orchestrator

        src = inspect.getsource(orchestrator._run_chat_turn)
        assert "StreamingOutputFilter" in src
        assert 'yield "token", {"text": event.text}' not in src
        assert "out_filter.flush()" in src

    def test_stored_copy_is_derived_from_the_delivered_text(self):
        import inspect

        from backend.ai import orchestrator

        src = inspect.getsource(orchestrator._run_chat_turn)
        assert "out_filter.emitted_text" in src
        assert "scrub_pii(delivered_text" in src
