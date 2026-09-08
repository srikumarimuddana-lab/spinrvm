"""Boundary-safe streaming output filter (F08).

The problem
-----------
``ai/orchestrator.py`` yielded provider text straight to the client as it
arrived, and applied ``scrub_pii`` / ``filter_tool_leakage`` only at the end,
to the copy written to ``ai_messages`` and the FAQ cache. So a clean database
transcript did not prove the rider saw a clean answer: the 2026-09-08 AI
security assessment (F08) scripted a provider response containing a synthetic
email address and watched it reach the emitted response unchanged.

Filtering a stream is not the same as filtering a string. A redactable value
can arrive split across chunks — ``"call me at 306-"`` then ``"555-1234"`` —
and scrubbing each chunk on its own sees neither half as a match. That is the
"detecting sensitive values across token boundaries" the assessment asks for.

The approach
------------
Hold back a tail of the text and only emit what can no longer change.

Two distinct hazards, handled by two distinct mechanisms:

1. **An incomplete match at the tail.** ``"...306-555-12"`` is not a phone
   number yet and would emit unredacted, then the remaining ``"34"`` would
   follow harmlessly. ``_HOLDBACK_CHARS`` keeps the last N characters
   unemitted until more text arrives or the stream ends, so a value that is
   still being assembled is never released early. N must exceed the longest
   value that could plausibly be split this way.

2. **A complete match straddling the cut point.** Holding back N characters
   still leaves a cut at ``len - N``, and a match can start before it and end
   after it. Splitting there is worse than useless: the head emits the value's
   first characters raw, and the tail — no longer matching the pattern without
   them — emits the rest raw too, so a holdback alone can *defeat* the scrub
   rather than help it. ``_safe_cut`` therefore runs the real patterns over
   the buffer and walks the cut backwards past any match that overlaps it.

Neither mechanism alone is sufficient; both are cheap.

What this deliberately does NOT do
----------------------------------
It does not make the emitted text equal to the fully-scrubbed final text in
every case. Redaction is applied to progressively larger prefixes, so a value
that only becomes matchable with context far beyond the holdback window can
still slip. Regex-detectable categories are what this closes — the same scope
limit ``pii.py`` documents for itself. A plain name is not caught here either.

It also does not replace the final scrub in the orchestrator: the stored copy
must still be filtered independently, because ``flush()`` correctness is not
something the persistence path should have to depend on.
"""

import logging
from typing import List

try:
    from .pii import _PII_PATTERNS, _POLICY_SKIPS, ScrubPolicy, filter_tool_leakage, scrub_pii
except ImportError:  # python -m backend.server vs top-level
    from ai.pii import (  # type: ignore
        _PII_PATTERNS,
        _POLICY_SKIPS,
        ScrubPolicy,
        filter_tool_leakage,
        scrub_pii,
    )

logger = logging.getLogger(__name__)

# How much text to withhold from the client until more arrives.
#
# Sized above the longest value that could realistically be assembled across
# chunk boundaries: an email address is the longest of the categories
# `pii.py` matches (cards top out around 25 characters, phones 20, bracketed
# coordinates 30, SIN 11, postal code 8). 96 gives generous headroom for a
# long local-part@domain without making the UI feel like it stutters — the
# holdback only ever delays text, it never drops it, and `flush()` releases
# whatever is left the moment the turn ends.
#
# This bounds hazard 1 only (a partial match at the tail). Complete matches of
# ANY length are handled by `_safe_cut`, which does not depend on this value.
_HOLDBACK_CHARS = 96


def _safe_cut(buffer: str, cut: int, policy: ScrubPolicy) -> int:
    """Move ``cut`` back so it does not fall inside a redactable match.

    Returns the largest index <= ``cut`` such that no pattern match in
    ``buffer`` starts before it and ends after it. A match that *ends exactly*
    at the cut is fine — it is wholly inside the emitted head and will be
    redacted there.

    Runs the same ``_PII_PATTERNS`` the scrubber does, honouring the policy's
    skip set, so it can never disagree with ``scrub_pii`` about what counts as
    a match.
    """
    if cut <= 0:
        return 0
    skips = _POLICY_SKIPS[policy]
    for tag, pattern, _replacement in _PII_PATTERNS:
        if tag in skips:
            continue
        for match in pattern.finditer(buffer):
            if match.start() < cut < match.end():
                cut = match.start()
                if cut <= 0:
                    return 0
    return cut


class StreamingOutputFilter:
    """Incremental scrub over a token stream.

    Usage::

        f = StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT)
        for chunk in provider_stream:
            out = f.feed(chunk)
            if out:
                yield out
        tail = f.flush()
        if tail:
            yield tail

    ``feed`` returns the text now safe to emit (often ``""`` early in a
    stream, while the holdback fills). ``flush`` returns whatever remains,
    fully scrubbed. Every character fed is eventually emitted exactly once,
    modulo redaction — asserted by the round-trip tests.

    Never raises on content: a filter failure must not break a chat turn, the
    same contract ``scrub_pii_deep`` holds. On an unexpected error it falls
    back to scrubbing and emitting the whole buffer, which is the safe
    direction (redacted output, no data loss) rather than emitting raw.
    """

    def __init__(self, *, policy: ScrubPolicy = ScrubPolicy.AI_CHAT, holdback: int = _HOLDBACK_CHARS):
        self._policy = policy
        self._holdback = max(0, holdback)
        self._buffer = ""
        self._emitted: List[str] = []

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._buffer += chunk
        cut = len(self._buffer) - self._holdback
        if cut <= 0:
            return ""
        try:
            cut = _safe_cut(self._buffer, cut, self._policy)
        except Exception:  # noqa: BLE001 - never break the turn over filtering
            logger.error("streaming output filter failed on cut; holding text", exc_info=True)
            return ""
        if cut <= 0:
            return ""
        head, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return self._apply(head)

    def flush(self) -> str:
        """Emit the withheld tail. Safe to call more than once (idempotent:
        the buffer is cleared), so a caller can flush in a ``finally`` without
        tracking whether the normal path already did."""
        if not self._buffer:
            return ""
        head, self._buffer = self._buffer, ""
        return self._apply(head)

    @property
    def emitted_text(self) -> str:
        """Everything released to the client so far, post-filter.

        The orchestrator needs this to answer "what did the rider actually
        see?" — which, once filtering is incremental, is no longer the same
        string as the raw provider text.
        """
        return "".join(self._emitted)

    def _apply(self, text: str) -> str:
        try:
            out = filter_tool_leakage(scrub_pii(text, policy=self._policy))
        except Exception:  # noqa: BLE001
            # Emitting raw here would defeat the whole point; emitting nothing
            # would silently truncate the rider's answer. Redact wholesale.
            logger.error("streaming output filter failed on scrub; redacting chunk", exc_info=True)
            out = "[redacted]"
        self._emitted.append(out)
        return out
