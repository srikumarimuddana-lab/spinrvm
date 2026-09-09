"""Boundary-safe streaming output filter (F08).

The problem
-----------
``ai/orchestrator.py`` yielded provider text straight to the client as it
arrived, and applied ``scrub_pii`` / ``filter_tool_leakage`` only at the end,
to the copy written to ``ai_messages`` and the FAQ cache. So a clean database
transcript did not prove the rider saw a clean answer: the AI security
assessment (PR #5138, F08) scripted a provider response containing a synthetic
email address and watched it reach the emitted response unchanged.

Why the obvious implementation is wrong
---------------------------------------
The tempting approach is to hold back a tail of the buffer, scrub the head, and
emit it. **That is unsound, and the first version of this module shipped it.**
``scrub_pii`` is not decomposable over string splits, for three separate
reasons:

* Under ``ScrubPolicy.AI_CHAT`` it stashes bracketed trip pins
  (``_BRACKETED_COORDS``) before the pattern pass. Split ``[`` from the digits
  and the stash never matches, so the pin is destroyed — the exact 2026-09-04
  re-geocode regression the AI_CHAT exception exists to prevent.
* Patterns run **sequentially**, and an earlier substitution can create the
  boundary a later pattern needs. ``3782 822463 10005306-555-1234`` only
  matches the Amex pattern *after* the phone substitution produces a non-digit
  boundary. Scanning the raw buffer for "matches to avoid splitting" cannot see
  a match that does not exist yet, so the split lands inside it and a full PAN
  is emitted raw.
* Several patterns rely on lookbehind/lookahead anchors to avoid colliding with
  this codebase's own ids and timestamps. Cut a 13-digit reference number in
  the middle and the tail ``4567890123`` matches ``[2-9]\\d{9}`` at string
  start — the filter *invents* a redaction and corrupts a legitimate answer.

So: never hand ``scrub_pii`` a fragment.

The approach
------------
Always scrub the **whole accumulated buffer**, and hold back in *output* space:
emit only the prefix of the scrubbed result that is far enough from its end
that more input cannot change it. Every scrub therefore sees exactly the string
the final scrub will see, and the streamed bytes are identical to filtering the
whole reply at once — which is what the differential tests assert.

``_MIN_RELEASE_CHARS`` coalesces the re-scrubs. Scrubbing the whole buffer on
every token would be O(n^2) with a painful constant (measured: ~515 ms of CPU
for a 4 KB reply at 4-char chunks). Attempting a release only once ~32 new
characters have accumulated brings that to ~67 ms in ~0.5 ms slices, which is
affordable against a turn that already costs seconds of provider latency. This
is purely a cost knob — correctness does not depend on it, because the scrub
still sees the whole buffer either way.

What this deliberately does NOT do
----------------------------------
Only regex-detectable categories are caught — the same scope limit ``pii.py``
documents for itself. A plain name, a free-form address or a provincial licence
number streams through untouched, exactly as they do in the stored copy.

It also does not replace the final scrub in the orchestrator: the stored copy is
filtered independently, so the persistence path never depends on this module
having been correct.
"""

import logging

try:
    from .pii import ScrubPolicy, filter_tool_leakage, scrub_pii
except ImportError:  # python -m backend.server vs top-level
    from ai.pii import ScrubPolicy, filter_tool_leakage, scrub_pii  # type: ignore

logger = logging.getLogger(__name__)

# How much of the SCRUBBED output to withhold until more input arrives.
#
# Sized above the longest single value the patterns can match, so that adding
# more input can never rewrite text already released: an email address is the
# longest category (cards top out around 25 characters, bracketed coordinates
# 30, phones 20, SIN 11, postal code 8). 96 leaves generous headroom.
#
# The holdback only ever delays text — `flush()` releases whatever remains the
# moment the turn ends, and `_release` additionally refuses to emit anything
# that would contradict what was already sent.
_HOLDBACK_CHARS = 96

# Minimum new input between release attempts. A pure cost control — see the
# module docstring. Larger means fewer whole-buffer scrubs and chunkier
# streaming; smaller means smoother streaming and more CPU.
_MIN_RELEASE_CHARS = 32


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

    ``feed`` returns the text now safe to emit (``""`` while the holdback fills
    or between coalesced releases). ``flush`` returns the remainder. The
    concatenation of everything returned equals
    ``filter_tool_leakage(scrub_pii(whole_text, policy))`` — asserted directly
    by the differential tests, which is the only property worth having here.

    Never raises on content: a filter failure must not break a chat turn, the
    same contract ``scrub_pii_deep`` holds.
    """

    def __init__(
        self,
        *,
        policy: ScrubPolicy = ScrubPolicy.AI_CHAT,
        holdback: int = _HOLDBACK_CHARS,
        min_release: int = _MIN_RELEASE_CHARS,
    ):
        self._policy = policy
        self._holdback = max(0, holdback)
        self._min_release = max(0, min_release)
        self._raw = ""
        self._emitted = ""
        self._unreleased = 0

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._raw += chunk
        self._unreleased += len(chunk)
        if self._unreleased < self._min_release:
            return ""
        self._unreleased = 0
        return self._release(final=False)

    def flush(self) -> str:
        """Emit everything still withheld. Idempotent — safe to call from a
        ``finally`` without tracking whether the normal path already ran."""
        return self._release(final=True)

    @property
    def emitted_text(self) -> str:
        """Everything released to the client so far, post-filter.

        The orchestrator persists this rather than the raw text, so the stored
        transcript records what the rider actually received.
        """
        return self._emitted

    def _release(self, *, final: bool) -> str:
        try:
            full = filter_tool_leakage(scrub_pii(self._raw, policy=self._policy))
        except Exception:  # noqa: BLE001 - a filter failure must not break the turn
            logger.error(
                "streaming output filter failed; withholding text until flush",
                exc_info=True,
                extra={"domain": "ai", "surface": "backend"},
            )
            # Withhold rather than emit raw. On the final call there is nothing
            # left to withhold FOR, but emitting unfiltered text is the one
            # outcome this module exists to prevent, so it stays withheld and
            # the orchestrator's own final scrub still produces a clean stored
            # copy.
            return ""

        stable = full if final else full[: max(0, len(full) - self._holdback)]

        if not stable.startswith(self._emitted):
            # A late redaction rewrote text already sent to the client. The
            # holdback is sized so this cannot happen (no pattern match is
            # longer than it), and no test case has ever produced it — but we
            # cannot un-emit, so the safe response is to stop adding to a
            # transcript we know is inconsistent, and say so loudly.
            logger.error(
                "streaming output filter: emitted prefix diverged from the final scrub — withholding the remainder",
                extra={"domain": "ai", "surface": "backend"},
            )
            return ""

        out = stable[len(self._emitted) :]
        self._emitted = stable
        return out
