# Change Impact & Risk Log — F08: filter AI output before delivery, not after

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent), for the PR #5138 remediation plan |
| Surface(s) | backend (visible in rider-app / driver-app chat UI) |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/pr-5138-implementation-27zn2l` |
| Related issue or gap ID | F08, `docs/security/2026-09-08-ai-security-assessment.md` (PR #5138), remediation order 4 |

## 1. Issue / gap identified

The orchestrator yielded provider text straight to the client as `token`
frames and applied `scrub_pii` / `filter_tool_leakage` only at the end, to the
copy written to `ai_messages` and the FAQ cache. The assessment scripted a
provider response containing a synthetic email address and watched it reach
the emitted response unchanged. **A clean database transcript did not prove
the rider saw a clean answer.** The non-streaming help-centre path concatenates
the same frames, so it inherited the gap.

## 2. Root cause

A deliberate design decision that turned out to be wrong, and was written down
as such. The comment above the final scrub read: *"the raw text has already
streamed to the client this turn, so the rider still sees the real reply; only
stored/replayed copies change."* The intent was to avoid degrading the rider's
answer. The flaw is that "the rider sees the real reply" and "the rider must
not be shown redactable content" are not in tension the way that assumed —
the model's own context is already scrubbed (see §4), so there is very little
legitimate content for the filter to take away.

Two existing tests encoded the defect as intended behaviour, asserting
`"306-555-1234" in tokens` and `"find_place" in tokens`. Both are inverted by
this change.

## 3. Fix / remediation

New `backend/ai/stream_filter.py` — `StreamingOutputFilter`, a pure,
dependency-free incremental scrubber. The orchestrator feeds every provider
text event through it and emits only what the filter releases, flushing the
withheld tail at the end of each provider stream.

Filtering a stream is not filtering a string. A value can arrive split —
`"call me at 306-"` then `"555-1234"` — and neither half matches on its own.
Two distinct hazards, two mechanisms, and **neither alone is sufficient**:

1. **An incomplete match at the tail.** `_HOLDBACK_CHARS` (96) withholds the
   last N characters until more text arrives or the stream ends, so a value
   still being assembled is never released early.
2. **A complete match straddling the cut.** A holdback still leaves a cut at
   `len - N`, and a match can start before it and end after it. Splitting
   there is *worse than useless*: the head emits the value's first characters
   raw, and the tail — no longer matching without them — emits the rest raw
   too. So a holdback alone can **defeat** the scrub. `_safe_cut` runs the real
   `_PII_PATTERNS` over the buffer and walks the cut backwards past any match
   that overlaps it.

One filter per provider stream, not per turn: holding text back across a tool
call would stall the rider's visible reply behind the tool's latency for no
safety gain, since a value cannot span two separate provider responses.

The stored copy is now derived from the **delivered** text, so the transcript
records what the rider actually received. It is still scrubbed a second time —
idempotent, since redaction tokens never re-match — deliberately, so the
persistence path does not depend on the streaming path having been correct.

## 4. Risk & impact on existing functionality

**Blast radius: every authenticated AI reply on every surface.** This is the
highest-reach change in the whole batch and the one most likely to be felt.

Consumers of the `token` frame, all now receiving filtered text:

| Consumer | Effect |
|---|---|
| `routes/ai.py` streaming SSE (rider-app main assistant) | filtered |
| `routes/ai.py` non-streaming drain (shared `SupportScreen`, both apps) | filtered — this is the second half the assessment asked for |
| `routes/support.py` legacy shim (F04) | filtered, via the same drain |
| `ai/public_assistant.py` | **untouched** — separate runner, not wired through this filter (see §10) |
| FAQ response cache | now stores the delivered copy |

**The key question — does this take away legitimate content?** Largely no, and
for a structural reason worth stating: the model's context is *already*
scrubbed under `ScrubPolicy.AI_CHAT` on both inputs — the user's message
(`orchestrator.py`, `scrub_pii(user_message, policy=AI_CHAT)`) and every tool
result (`tools.py::_cap_result`). `AI_CHAT` skips only `postal`, so phone,
email, card and SIN are already redacted before the model ever sees them. The
model therefore has almost no real identifier to echo; what this filter catches
is content that should not have been there. Trip-endpoint pins and postal codes
— the data the AI_CHAT exception exists to preserve — are explicitly kept, and
pinned by a test.

Regressions considered:

- **Frame granularity changes**, which is the user-visible cost — see §5. Two
  existing tests asserted exact token-frame counts and are relaxed to assert
  content instead; frame count is an implementation detail of the filter.
- **Could the filter drop or duplicate text?** That would be a worse bug than
  the one being fixed. Pinned by round-trip tests asserting the streamed
  output is *byte-identical* to filtering the whole string at once, plus 300
  randomised uneven chunkings.
- **Could a filter exception break a turn?** No — `_apply` and `feed` both
  swallow and degrade safely (redact wholesale / hold text), matching
  `scrub_pii_deep`'s existing "never break a chat turn" contract.
- **Cost.** `_safe_cut` runs the pattern set over a buffer bounded by the
  holdback plus one chunk, per chunk. Small and constant-ish, on a path
  already dominated by provider latency. Reasoned, not measured.

No ride-state, money, dispatch, auth or insurance-period interaction.

## 5. User-experience effect

**This is a visible change to an already-shipped screen** (CLAUDE.md gate 5),
and it is the main reason to review this change carefully:

- The rider's reply now appears **~96 characters behind** the provider. For a
  typical 200–400 character answer the text still streams, just lagging, with
  the final ~96 characters landing at once when the turn ends.
- **For a reply shorter than ~96 characters — e.g. "Your driver is 3 minutes
  away." — the streaming effect disappears entirely** and the whole answer
  appears at once at the end of the turn. Short replies are common, so this
  will be noticed.
- Where the model does echo a redactable value, the rider now sees `[PHONE]` /
  `[EMAIL]` / `[CARD]` instead of the value. Previously they saw the value and
  only the stored copy was redacted. Given §4, this should be rare.
- Not visible mid-ride; this is a chat surface. No copy or notification change.

`_HOLDBACK_CHARS` is a module constant and is the tuning knob if the lag proves
worse in practice than in reasoning — see §8.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/stream_filter.py` | **New.** `StreamingOutputFilter` + `_safe_cut`. | The fix. Kept as a pure module with no orchestrator dependencies so it can be exhaustively tested in isolation. |
| `backend/ai/orchestrator.py` | Text events routed through the filter; tail flushed per provider stream; `emitted_text` tracked; stored copy derived from delivered text; the stale "the rider still sees the real reply" comment corrected. | Wiring. |
| `backend/tests/test_ai_stream_filter.py` | **New.** ~40 cases incl. 300 randomised chunkings and a holdback-boundary sweep. | Regression cover for a subtle, split-dependent bug. |
| `backend/tests/test_ai_orchestrator.py` | Two tests **inverted** (they asserted the raw value reached the client); two frame-count assertions relaxed to content assertions. | The old tests pinned the defect. |

## 7. Before / after

```python
# Before — ai/orchestrator.py
async for event in adapter.stream_turn(...):
    if event.type == "text" and event.text:
        turn_text.append(event.text)
        yield "token", {"text": event.text}      # raw provider text to the client
...
stored_text = filter_tool_leakage(scrub_pii(final_text, policy=ScrubPolicy.AI_CHAT))
# only the STORED copy was ever filtered
```

```python
# After
out_filter = StreamingOutputFilter(policy=ScrubPolicy.AI_CHAT)
async for event in adapter.stream_turn(...):
    if event.type == "text" and event.text:
        turn_text.append(event.text)
        safe = out_filter.feed(event.text)
        if safe:
            yield "token", {"text": safe}
tail = out_filter.flush()
if tail:
    yield "token", {"text": tail}
...
delivered_text = "".join(emitted_text).strip()
stored_text = filter_tool_leakage(scrub_pii(delivered_text, policy=ScrubPolicy.AI_CHAT))
```

```python
# Before — tests/test_ai_orchestrator.py  (the defect, asserted as intended)
# "the client still sees the raw text streamed this turn"
assert "306-555-1234" in tokens
```

```python
# After
assert "306-555-1234" not in tokens
assert "[PHONE]" in tokens
```

## 8. Rollback plan

`ai_assistant_enabled` false in `app_settings` (admin dashboard, no redeploy)
takes the whole AI surface down — the blunt lever if the filter misbehaves.

For the likely complaint, which is the streaming lag rather than correctness:
`_HOLDBACK_CHARS` in `backend/ai/stream_filter.py` is a single constant. Lowering
it shortens the lag and narrows only hazard 1 (a partial match at the tail);
`_safe_cut` is independent of it, so complete matches stay protected at any
value. That is a code change and therefore a redeploy — **deliberately not a
runtime flag**, because a runtime switch that turns output filtering off would
re-open a confirmed data-egress defect at an operator's discretion.

No migration, no schema change, no data written. `git revert` is a complete
code rollback, with the caveat that reverting restores the finding.

## 9. Verification performed

- [x] **Offline probe against the real module** (`probe_f08.py`) — the module is
      pure, so it was exercised directly with `pii.py` loaded and `core.config`
      stubbed: 5 secret types × 7 chunk sizes all redacted; streamed output
      byte-identical to the whole-string scrub at 4 chunk sizes each; clean text
      preserved exactly; `emitted_text` consistent; flush idempotent; both
      policies honoured; and **400 randomised uneven chunkings** all redacted
      and matched the whole-string result. **All pass.**
- [x] **Second probe** (`probe_f08b.py`) re-ran every assertion in the new
      pytest file against the real implementation, including a sweep placing
      the secret at 40 different offsets so a fixed-holdback cut lands inside
      it. **All pass.**
- [x] **The orchestrator-wiring assertions were verified by AST extraction**,
      which caught a real defect: the test originally inspected
      `run_chat_turn`, a thin conversation-lock wrapper containing none of the
      streaming code, so all four assertions would have **passed vacuously**.
      Retargeted to `_run_chat_turn` and re-verified.
- [x] **Blast-radius review** — every `token`-frame consumer enumerated in §4,
      and the already-scrubbed-input argument checked against the actual
      `AI_CHAT` skip set (`_POLICY_SKIPS`) rather than assumed.
- [x] Existing tests audited for conflicts: found three that depended on the
      old behaviour (two asserting the defect, one on frame count) and updated
      all three with the reversal explained in the test itself.
- [x] `ruff check` + `ruff format --check` clean on all changed files.

## 10. What was NOT verified

- **The pytest suite was not run.** PyPI is unreachable (gateway 403), so
  backend dependencies cannot be installed. The new tests and the four updated
  ones are **unrun** and must go green in CI before merge. The probes exercise
  the filter thoroughly but do **not** exercise it inside a real
  `_run_chat_turn` with a real adapter — the wiring is verified statically
  only.
- **No real streaming UX check.** The §5 lag is reasoned from the holdback
  size, not observed against a live provider on a device. Given this is a
  visible change to a shipped screen, **watching one real reply stream is the
  check most worth doing before merge**, and the one I could not do.
- **`ai/public_assistant.py` is NOT covered by this change.** It has its own
  runner and its own emission path. The assessment's F08 text concerns the
  orchestrator, and the public assistant's tool results are already scrubbed
  under STRICT, but its *model prose* is emitted on the same
  filter-after-the-fact basis. **F08 is therefore only closed for the
  authenticated surface**; the public assistant needs the same treatment and
  is left open.
- **Scope limit inherited from `pii.py`:** only regex-detectable categories are
  caught. A plain name, a free-form address, or a provincial licence number
  streams through untouched, exactly as they do in the stored copy today.
- **A value that only becomes matchable with context beyond the 96-character
  window can still slip.** The holdback is a bound, not a proof.
- No latency or CPU measurement of `_safe_cut`.
