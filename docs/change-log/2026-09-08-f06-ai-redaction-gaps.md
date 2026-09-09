# Change Impact & Risk Log — F06: incomplete redaction across AI egress paths

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent), for the PR #5138 remediation plan |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/pr-5138-implementation-27zn2l` |
| Related issue or gap ID | F06, the AI security assessment on PR #5138, remediation order 2 |

## 1. Issue / gap identified

Three defects, each reproduced against the current code before fixing:

1. **Amex 4-6-5 survives `scrub_pii`.** `3782 822463 10005` — the grouping Amex
   itself prints — passed through unredacted. Only the unseparated form and a
   4-4-4-3 chunking were matched.
2. **Numeric coordinates survive the STRICT deep scrub.** `scrub_pii_deep`
   recurses into string leaves only, so `{"lat": 52.1332, "lng": -106.67}` was
   walked straight past. `/mcp`'s "STRICT serialization" therefore shipped
   exact pickup/dropoff coordinates to an external MCP client, and the
   saved-place tool supplies exactly those structured fields.
3. **Staff instructions bypass the scrub boundary.** `build_ticket_context`
   scrubbed the ticket subject, description and every thread message, then
   appended the support agent's instruction verbatim.

The assessment also notes bare nine-digit SIN-shaped values surviving —
addressed by (2)'s mechanism rather than a content regex; see §3.

## 2. Root cause

(1) is a pattern gap. (2) and (3) share one: **the redaction model reasoned
about string content and about who authored text, rather than about what leaves
the process.**

- The recursive scrubber's contract was "scrub every string leaf", which is a
  statement about representation. A coordinate's sensitivity does not depend on
  whether it is serialized as `"52.1332"` or `52.1332`.
- `build_ticket_context`'s docstring justified the instruction passthrough as
  "first-party staff input — not customer PII". That confuses the **author** of
  the text with its **subject**. A customer's phone number pasted into agent
  guidance is the customer's PII reaching the provider regardless of who typed
  it — and the identical string in the ticket body one line earlier was
  scrubbed.

## 3. Fix / remediation

1. **Amex** — added a `3[47]\d{2}[\s-]\d{6}[\s-]\d{5}` alternative, ordered
   before the existing 4-4-4-3 branch. Still IIN-prefix-gated, so the
   long-digit-run false-positive discipline the card pattern already documents
   is preserved.
2. **Key-name denylist** in `_scrub_deep` — a value under a location key
   (`lat`/`lng`/`latitude`/…) is redacted to `[COORD]` under STRICT and **kept
   under AI_CHAT**, matching the documented trip-endpoint exception (ADR 012 /
   PIA §3). Identity-document keys (`sin`, `social_insurance_number`,
   `license_number`, `passport`, `cvv`, …) are redacted under **every** policy —
   the assistant has no use for them and CLAUDE.md forbids them outright.
3. **Staff instruction** is now `scrub_pii`-ed like every other field, and the
   docstring records the reversal and its reasoning.

**On the bare-SIN half of defect 1:** deliberately **not** fixed with a content
regex. `pii.py`'s own `govid` comment documents why — nine bare digits collide
with this codebase's id and timestamp shapes, and matching on digit count alone
reproduces a regression already recorded there for phone numbers. The field
*name* is the discriminator instead, which has no such ambiguity. This is the
assessment's own recommended direction ("redact sensitive fields by meaning as
well as string pattern"), and `test_bare_ungrouped_nine_digits_is_not_redacted`
is left passing rather than overridden.

**A denylist is not the allowlist `scrub_pii_deep`'s docstring rejects.** That
rejection is about which keys to *skip* (`utils/sentry_scrub.py` treating a bare
`name` key as a benign symbol — wrong here, because a tool result's `name` is
routinely a person's name). Denying specific keys *adds* redaction and never
exempts a key from the pattern pass. Pinned by
`test_denylist_does_not_exempt_any_key_from_the_pattern_pass`.

## 4. Risk & impact on existing functionality

**Blast radius: cross-cutting within the AI/support surface.** `scrub_pii` and
`scrub_pii_deep` are shared utilities — this is the change in this batch with
the widest reach, and it is the one the release gates care most about.

`grep -rn "scrub_pii\b\|scrub_pii_deep" backend/ --include=*.py` — consumers:

| Consumer | Policy | Effect of this change |
|---|---|---|
| `ai/tools.py::_cap_result` (chat tool results) | AI_CHAT | Numeric trip coords **kept** — unchanged. Amex now caught. |
| `ai/tools.py::_cap_result` (web audience) | STRICT | Numeric coords now redacted. The web assistant has no booking flow and no personal tools, so it should carry none. |
| `ai/mcp_server.py::_serialize_tool_payload` | STRICT | **The intended fix** — coordinates stop leaving to external MCP clients. |
| `ai/orchestrator.py` (user message, reply persistence) | AI_CHAT | Trip coords kept — unchanged. Amex now caught. |
| `ai/public_assistant.py` | STRICT | Same as web audience above. |
| `ai/support_assistant.py` | STRICT | Instruction now scrubbed (the fix); other fields unchanged. |
| `routes/support.py` | STRICT | Amex now caught; no structured payloads. |
| `utils/log_guard.py`, `utils/sentry_scrub.py` | STRICT | Strictly more redaction — the correct direction for logs/Sentry. |

Regressions specifically considered:

- **Could this re-trip the 2026-09-04 postal-code regression?** That incident
  was AI_CHAT over-scrubbing trip data into `rides.pickup_address`. This change
  adds redaction **only under STRICT** for location, and explicitly keeps
  numeric coordinates under AI_CHAT. Pinned by
  `test_ai_chat_keeps_numeric_trip_coordinates`.
- **Could `[COORD]` reach a booking field?** Only via a STRICT path, and no
  STRICT path writes ride addresses — `/mcp` is read-only and the web assistant
  has no booking flow. Not exhaustively proven; see §10.
- **Could a denylisted key holding a dict lose structure?** It is recursed
  rather than replaced, exactly to avoid that. Pinned.
- **Could the new Amex branch eat internal ids?** It is prefix-gated on 34/37
  with exact 4-6-5 lengths; a same-shaped run with any other prefix is added to
  the existing false-positive parametrize list.
- **Money math:** untouched. No Decimal/float path is involved.

## 5. User-experience effect

- **Rider / driver:** none. The in-app assistant's AI_CHAT policy is unchanged
  for location, and a real card number appearing in chat was already meant to
  be redacted.
- **Internal admin / support agent:** *visible change.* An agent can no longer
  get a real phone number, email or card number into a drafted reply via the
  guidance field — it arrives as `[PHONE]`/`[EMAIL]`/`[CARD]`. The guidance
  itself still reaches the model, so the steer works; only identifiers are
  redacted. This is the same constraint the customer's own message body already
  operates under, and the draft is reviewed and edited by the agent before
  sending — it is never auto-sent.
  **This is a real workflow change for support staff and should be mentioned to
  them rather than discovered.**
- **External MCP client:** coordinates become `[COORD]`. MCP defaults off.
- Not visible mid-session to anyone already using the rider/driver app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/pii.py` | Amex 4-6-5 branch; `_LOCATION_KEYS` / `_ALWAYS_SENSITIVE_KEYS` / `_redaction_for_key`; `_scrub_deep` consults the denylist. | The three defects. |
| `backend/ai/support_assistant.py` | `scrub_pii()` on the instruction; docstring records the reversal. | Defect 3. |
| `backend/tests/test_ai_pii.py` | +2 Amex fixtures, +1 false-positive guard, new `TestStructuredFieldRedaction` (8 tests), +1 SIN-by-field test. | Regression cover. |
| `backend/tests/test_support_assistant.py` | +1 test: instruction is scrubbed but still delivered. | Regression cover. |

## 7. Before / after

```python
# Before — ai/pii.py::_scrub_deep
if isinstance(value, dict):
    return {k: _scrub_deep(v, depth + 1, policy) for k, v in value.items()}
# {"lat": 52.1332} has no string leaf -> walked past, reaches the MCP client
```

```python
# After
if isinstance(value, dict):
    out = {}
    for k, v in value.items():
        token = _redaction_for_key(k, policy)
        if token is not None and not isinstance(v, (dict, list, tuple)):
            out[k] = token                      # {"lat": "[COORD]"} under STRICT
        else:
            out[k] = _scrub_deep(v, depth + 1, policy)
    return out
```

```python
# Before — ai/support_assistant.py::build_ticket_context
lines.append(_truncate(instruction.strip(), _MAX_FIELD_CHARS))
```

```python
# After
lines.append(_truncate(scrub_pii(instruction.strip()), _MAX_FIELD_CHARS))
```

## 8. Rollback plan

No flag, and deliberately so: a switch that turns redaction *off* is a
PIPEDA-hostile lever, and CLAUDE.md's gate 3 asks for flags on user-visible
*features*, not on a privacy control. `git revert` is a complete rollback here —
this change writes no data, runs no migration, and alters no stored value.

The one thing a revert does **not** undo is data already sent to a provider
before the fix, which is inherent to an egress finding and is why the fix is not
itself a remediation for past disclosure.

If the support-staff workflow change in §5 proves unworkable, the narrow
follow-up is a typed allowlist for the instruction field (the assessment's
preferred long-term shape) rather than restoring the verbatim passthrough.

## 9. Verification performed

- [x] **Defects reproduced first** against unmodified code (`probe_f06_before.py`):
      spaced/dashed Amex unredacted, bare 9-digit SIN unredacted, numeric
      `lat`/`lng` untouched by the STRICT deep scrub. All three confirmed.
- [x] **Offline probe after the fix** (`probe_f06_after.py`): **17 cases, all
      pass** — 5 card (incl. Visa no-regression and a prefix false-positive
      guard), 4 coordinate (STRICT redacts, nested-in-list, AI_CHAT keeps,
      string coords still redacted), 4 identity-field, 4 anti-regression
      (`name` key not exempted, container recursed, non-string key, unrelated
      numerics untouched). Probes are throwaway harnesses, not committed; the
      same cases are committed as pytest.
- [x] **Blast-radius grep performed** — every `scrub_pii` / `scrub_pii_deep`
      consumer enumerated with its policy in §4.
- [x] Checked the new tests against existing expectations that could conflict:
      `test_bare_ungrouped_nine_digits_is_not_redacted` and the AI_CHAT
      postal/bracketed-coordinate tests are all left intact and passing by
      construction.
- [x] `ruff check` + `ruff format --check` clean on all changed files.
- [x] Reviewed against CLAUDE.md's PIPEDA rules (no PII in logs; the new code
      adds no logging at all) and ADR 012 / PIA §3 for the AI_CHAT exception.

## 10. What was NOT verified

- **The pytest suite was not run.** PyPI is unreachable from this environment
  (gateway 403 on `pypi.org`), so backend dependencies cannot be installed. The
  12 new/extended tests are **unrun** and must go green in CI before merge.
  This matters more here than for the other findings in this batch: `pii.py` is
  a shared utility with ~10 consumers, and `test_ai_pii.py` is a large existing
  suite whose other assertions I reasoned about but did not execute.
- **The `_ALWAYS_SENSITIVE_KEYS` list is not exhaustive** and was not derived
  from a schema audit of every tool result. It covers the field names the
  assessment named plus obvious neighbours. A tool returning a SIN under some
  other key name is still unredacted — the typed, allowlisted payloads the
  assessment recommends (per recipient) remain the real fix, and are **not**
  done here.
- **No end-to-end `/mcp` response was captured.** The coordinate fix is verified
  at `scrub_pii_deep`, not by observing an actual MCP tool response. MCP
  defaults off and the SDK is not in the lockfile, so this could not be
  exercised even with deps installed.
- **Names, free-form addresses and provincial licence numbers remain
  unanonymized by the regex approach**, exactly as the assessment states. This
  change does not alter that, and no claim of complete de-identification should
  be read into it.
- **No measurement of the added scrub cost.** `_redaction_for_key` is two
  frozenset lookups per dict key. Reasoned to be negligible against the existing
  regex pass, not benchmarked.
- **Support staff have not been told** about the instruction-field change in §5.
  That communication is owed before this reaches production.

---

## Review follow-up (2026-09-09)

**The key denylist only covered SCALAR values, and §4's justification for
recursing into containers was false.** It read "its leaves get the same
treatment one level down anyway" — untrue for exactly the data the denylist
exists to catch, because the leaves are floats and the string pattern pass
cannot see a number. So `{"lat": [52.13]}`, `{"pickup": {"coords": [...]}}` and
GeoJSON `{"location": [lng, lat]}` all still shipped exact coordinates to /mcp,
one container deep — the same leak, unfixed.

`_redact_numeric_leaves` now redacts every numeric leaf inside a denylisted
key's container while preserving structure and still pattern-scrubbing strings
inside it. `_LOCATION_CONTAINER_KEYS` adds the container spellings that carry no
per-axis key (`location`, `coordinates`, `coords`, `latlng`, `position`).
Booleans are excluded (bool is an int subclass). The AI_CHAT trip-endpoint
exception applies at every depth.

The parallel `_KEY_REDACTIONS` map is also gone: it duplicated `_LOCATION_KEYS`
with every entry mapping to the same constant, and a key added to one but not
the other raised `KeyError` inside `_scrub_deep`'s broad `except`, which returns
the value **unscrubbed** — a silent privacy regression dressed as resilience.
