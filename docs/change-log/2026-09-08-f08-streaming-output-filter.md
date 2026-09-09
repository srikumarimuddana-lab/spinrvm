# Change Impact & Risk Log — F08: filter AI output before delivery, not after

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent), for the PR #5138 remediation plan |
| Surface(s) | backend (visible in rider-app / driver-app chat UI) |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/pr-5138-implementation-27zn2l` |
| Related issue or gap ID | F08, the AI security assessment on PR #5138, remediation order 4 |

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

> **This section is a correction.** The first version of this change shipped
> broken, and this log's own "All pass" evidence was the reason it looked
> fine. Recorded here because the failure mode — a confident verification
> statement backed by a vacuous test — is more useful to the next person than
> the fix is.

**What went wrong.** Every fixture in the original test suite and offline probe
was shorter than `_HOLDBACK_CHARS` (96), so `feed()` always returned `""` and
`flush()` scrubbed the whole string in one piece. The "400 randomised
chunkings" therefore compared `scrub(whole)` against `scrub(whole)` — trivially
equal — and passed while the incremental path was defective in four separate
ways, all found in review:

| Defect | Measured |
|---|---|
| AI_CHAT bracketed trip pins destroyed (`[[COORDS]]`) — the 2026-09-04 re-geocode regression, and it corrupted the persisted row too | 60/60 offsets |
| Invented redactions: a split 13-digit reference tail matched the phone pattern at string start | 9/60 offsets |
| Tool-name leakage: `find_` + `place` matches neither fragment | 18/60 offsets |
| A full Amex PAN emitted raw, because that match only exists *after* the phone substitution creates its boundary | reproduced |

The root cause is one sentence: **`scrub_pii` is not decomposable over string
splits.** It stashes bracketed coordinates before the pattern pass, applies
patterns sequentially so one substitution creates the next one's boundary, and
relies on lookbehind anchors that need the whole string. Any design that hands
it a fragment is unsound, and "scan for matches and avoid splitting them"
cannot fix it — a scan cannot see a match that does not exist yet.

**The rewrite** always scrubs the whole accumulated buffer and holds back in
*output* space, so every scrub sees exactly the string the final scrub sees.

- [x] **Differential property, asserted directly:** the concatenation of
      everything emitted equals filtering the whole reply at once. Verified
      against the real module across 7 texts x 9 chunk sizes x 2 policies, plus
      **2000 randomised uneven chunkings** — all exact matches.
- [x] **All four shipped defects re-tested at 40-60 offsets each: 0 failures.**
- [x] **A vacuity guard is now a test** (`test_fixtures_actually_exercise_the_
      release_path`): every fixture must exceed the holdback, and `feed()` must
      emit before `flush()`. That assertion would have failed on the original
      suite.
- [x] **Cost measured, not assumed.** Whole-buffer re-scrubbing on every token
      is ~515 ms of CPU for a 4 KB reply at 4-char chunks. `_MIN_RELEASE_CHARS`
      (32) coalesces it to ~67 ms in ~0.5 ms slices — sub-millisecond per call,
      so no meaningful event-loop block.
- [x] `ruff check` + `ruff format --check` clean.

## 10. What was NOT verified

- **The pytest suite still has not run** — PyPI is unreachable (gateway 403),
  so the tests are exercised only by executing the same assertions against the
  real module in a stub harness. CI must be green before merge.
- **No real reply was watched streaming.** The UX characteristics in §5 are
  reasoned and measured offline, not observed in the app. Given this is a
  visible change to a shipped screen, that remains the check most worth doing.
- **`ai/public_assistant.py` is still NOT covered** — separate runner, separate
  emission path. F08 is closed for the authenticated surface only.
- **Scope limit inherited from `pii.py`:** only regex-detectable categories.
  A plain name or free-form address streams through untouched.
- The correctness argument rests on the holdback exceeding the longest possible
  single match. That is true for the current patterns; a future pattern longer
  than 96 characters would need the constant raised. The `startswith` guard in
  `_release` detects that case and withholds rather than corrupting, but it has
  never fired in testing, so its behaviour is unexercised.
