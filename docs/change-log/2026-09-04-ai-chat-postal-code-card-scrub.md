# Change Impact & Risk Log — AI chat showed `[POSTAL]` in customer-facing addresses

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code session (see PR for attribution) |
| Surface(s) | backend (rider-app and admin AI console render the corrected payload with no client change) |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/ai-chat-postal-code-bug-nx9tun` — commits `94c5e3d`, `7bab2db`, `b914b27`, `71e2b39`, `ec5bbb6` |
| Related issue or gap ID | `ACTION_ITEMS.md` AI16 (new); regression introduced by A40 (2026-08-18); ADR-012 |

## 1. Issue / gap identified

Live-testing screenshots (2026-09-04) show the Spinr Assistant rendering every Canadian postal code
as the literal token `[POSTAL]` in customer-facing text:

1. the "Choose your dropoff" cards — `2150 Prince of Wales Dr, Regina, SK [POSTAL]`;
2. the message the app sends when a card is tapped — `Use 2150 Prince of Wales Dr, Regina, SK [POSTAL] [50.44421,-104.53460] as my dropoff …`;
3. the assistant's own prose — `I found your pickup as 1855 Victoria Ave #304, Regina, SK [POSTAL], Canada`.

A second, related symptom in the same session: after "no drivers are available … try again in a few
minutes", the rider answered **"Yes"** and the assistant replied *"I don't have an active quote to book
right now"* instead of re-checking the fare.

## 2. Root cause

**`[POSTAL]` (confirmed in code, not inferred).** `backend/ai/tools.py::_cap_result` ran
`scrub_pii_deep()` over the *whole* tool result — including `_client_action`, the UI-card payload that
the orchestrator pops and streams to the rider's app as an SSE `action` frame
(`orchestrator.py:423-435`). The scrub was wired in on 2026-08-18
(`docs/change-log/2026-08-18-ai-tool-result-pii-scrub-fix.md`, A40) on the written assumption that
"no downstream code reads a tool result after `_cap_result` returns it". That assumption missed the
card path. The regex at `backend/ai/pii.py` rewrote every postal code to `[POSTAL]`; the test
`test_cap_result_scrubs_client_action_too` pinned the behaviour as intended, and
`test_tapped_suggestion_message_keeps_coordinates` explicitly "accepted" the scrub while reasoning
only about the model, never about what the rider sees.

Underneath the bug is a design flaw: **one scrubber built for telemetry/provider egress was applied
at a choke point that also feeds the rider's own app.** Four trust boundaries were treated as one. The
scrubbed string then round-tripped: the rider tapped the card → the app built a message from the
scrubbed label → the model passed `…[POSTAL]…` back into `get_fare_quote`/`propose_ride_booking` →
`_reconcile_pickup` sent it to Google as a geocode query, and on Confirm `BookingProposalCard.tsx`
handed it to `createRide()` → `rides.pickup_address` / `dropoff_address` → driver app → receipt.

**"No active quote".** `get_fare_quote`'s no-drivers branch returned *before* `_pin_quote`, tool
results are never persisted, and prompt rule 6 forbids reusing coordinates from older bracketed
messages. On the next turn the model genuinely had nothing to re-check.

## 3. Fix / remediation

1. **Name the egress boundary** (`backend/ai/pii.py`): `ScrubPolicy.STRICT` (default — logs, Sentry,
   support tickets, anonymous web chat, `/mcp`) redacts everything as before. `ScrubPolicy.AI_CHAT`
   (the authenticated in-app assistant only) keeps bracketed trip pins **and Canadian postal codes**,
   because that path already carries the street address and exact coordinates as the booking payload
   (PIA §3 exception) and a postal code adds nothing beyond them. The old `keep_trip_pins` bool is
   gone. A wrong-typed policy raises *before* `scrub_pii_deep`'s swallow-all guard.
2. **Never scrub the card** (`backend/ai/tools.py::_cap_result`): `_client_action` is split off
   non-mutatingly and re-attached untouched; the model-facing portion is scrubbed under `AI_CHAT`.
3. **Scrub `/mcp` at its own egress** (`backend/ai/mcp_server.py::_serialize_tool_payload`): the whole
   response, card included, is re-scrubbed under `STRICT` — `/mcp`'s posture is unchanged.
4. **Pin the no-drivers trip** (`tools_booking.py`) with `no_drivers=True` and *no* vehicle/total, and
   render it as a re-quote-only "LAST FARE CHECK" block (`orchestrator.py`), with a matching sentence
   in prompt rule 6c (`prompts.py`) so "Yes" re-runs `get_fare_quote` on the same endpoints.
5. ADR-012 records the four-boundary model so the next scrub-related change is made against a stated
   rule instead of an assumption.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend/ai), two behaviour changes on the provider-egress path.**

Every caller of `scrub_pii` / `scrub_pii_deep` (grepped):

| Caller | Policy after this change | Behaviour change? |
|---|---|---|
| `ai/orchestrator.py` — user message (persisted + sent), assistant reply (persisted copy) | `AI_CHAT` | **Yes**: postal codes now reach the provider and `ai_messages` (they already reached both inside the street address) |
| `ai/tools.py::_cap_result` — model-facing tool results | `AI_CHAT`; `_client_action` exempt | **Yes**: postal codes in *all* chat-path tool results (booking, ride history, saved places, driver tools) reach the provider; cards are no longer scrubbed |
| `ai/mcp_server.py::_serialize_tool_payload` (new) | `STRICT` on the whole payload | No — identical output to before for `/mcp` clients |
| `ai/support_assistant.py:140,142,163`, `ai/public_assistant.py:148,216`, `routes/support.py:122`, `utils/log_guard.py:157`, `utils/sentry_scrub.py:25` | default (`STRICT`) | No |

- **Ride state machine / money / background loops:** not touched. `get_fare_quote` still calls
  `compute_ride_estimates`; no fare arithmetic changed.
- **Quote pin (`ai:quote:{conversation_id}`, Redis, 15-min TTL):** a no-drivers pin now overwrites any
  earlier priced pin in the same conversation. Since it carries no `vehicle_type_id`/`total`, rule 6c's
  booking shortcut cannot fire on it; the next successful quote overwrites it again. Existing
  `TestPinnedQuoteContext` behaviour for priced pins is unchanged.
- **Aliasing hazard (documented in the `_cap_result` docstring):** `find_place`'s card aliases the same
  candidate dicts as its model-facing list. The split in `_cap_result` must remain non-mutating;
  `test_cap_result_never_scrubs_client_action` asserts the handler's dict is untouched.
- **Data already written:** ride rows created from an affected booking card during live testing may
  carry the literal `[POSTAL]` in `pickup_address` / `dropoff_address`. This change does **not** repair
  them (see §8).

## 5. User-experience effect

- **Rider (AI assistant):** postal codes reappear in dropoff-choice cards, quote/booking cards, the
  tapped-card message bubble and the assistant's prose. After a "no drivers" result, answering "Yes" now
  re-checks the fare instead of "I don't have an active quote". Visible immediately on the next chat
  turn to anyone mid-session; no app update needed.
- **Driver / receipt:** new bookings made through the assistant carry the full address; nothing
  changes for rides already created.
- **Internal admin (AI console):** mirrors the rider card, so it sees the same correction.
- **`/mcp` clients:** no visible change.
- Copy change: one model-facing tool note and one prompt sentence (not rider-visible text).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/pii.py` | `ScrubPolicy` enum; category-tagged pattern list; per-policy skip set; `keep_trip_pins` removed; policy threaded through both `scrub_pii_deep` branches; type check before the swallow-all guard | Name the boundary instead of a one-off bool; stop redacting trip-location data on the chat path |
| `backend/ai/tools.py` | `_cap_result` splits `_client_action` off untouched, scrubs the rest under `AI_CHAT`; docstring rewritten | The fix — the card is the rider's own data |
| `backend/ai/mcp_server.py` | `_serialize_tool_payload` (STRICT, whole payload) used by `_call_tool`; module docstring | Keep `/mcp`'s third-party posture unchanged |
| `backend/ai/orchestrator.py` | two call sites → `policy=ScrubPolicy.AI_CHAT`; `_pinned_quote_context` renders the no-drivers variant | Call-site rename; "Yes → re-quote" |
| `backend/ai/public_assistant.py` | comment only | Guard test forbids the bare policy token outside allowed files |
| `backend/ai/tools_booking.py` | no-drivers branch pins endpoints (`no_drivers=True`, no vehicle/total) and its note offers a re-check | "No active quote" root cause |
| `backend/ai/prompts.py` | rule 6c: one sentence on the "LAST FARE CHECK" block | Model must re-quote, never book, from that block |
| `backend/tests/test_ai_pii.py` | policy tests; tap-message test now asserts the postal code survives; call-site guard greps `AI_CHAT`; `_cap_result` card tests replaced | Encode the corrected contract |
| `backend/tests/test_ai_mcp.py` | `TestSerializeToolPayload` | Where the "/mcp scrubs the card too" guarantee now lives |
| `backend/tests/test_ai_tools_booking.py` | `TestCardsKeepPostalCodes` (find_place / get_fare_quote / propose through `execute_tool`); no-drivers pin test | End-to-end regression guards through the real `_cap_result` |
| `backend/tests/test_ai_orchestrator.py` | action-frame test through the real `_cap_result`; no-drivers replay test; rule-6c prompt test | Frame-level proof + replay variant |
| `docs/adr/012-ai-egress-trust-boundaries.md`, `docs/adr/README.md` | new ADR + index row | The rule this fix is made against |
| `docs/compliance/pia-ai-surfaces-2026-08.md` | amendment notes on the affected rows + amendment log | Keep the PIA truthful about what reaches the provider |

## 7. Before / after

```python
# Before — backend/ai/tools.py, _cap_result
    if isinstance(result, dict):
        result = scrub_pii_deep(result)                       # whole result, card included, STRICT
    client_action = result.pop("_client_action", None) if isinstance(result, dict) else None
```

```python
# After
    if isinstance(result, dict):
        client_action = result.get("_client_action")          # the rider's own card: untouched
        result = scrub_pii_deep(
            {k: v for k, v in result.items() if k != "_client_action"}, policy=ScrubPolicy.AI_CHAT
        )
    else:
        client_action = None
```

```python
# Before — backend/ai/pii.py
def scrub_pii(text: str, *, keep_trip_pins: bool = False) -> str:
    ...
    for pattern, token in _PII_PATTERNS:
        text = pattern.sub(token, text)
```

```python
# After
def scrub_pii(text: str, *, policy: ScrubPolicy = ScrubPolicy.STRICT) -> str:
    _check_policy(policy)
    skips = _POLICY_SKIPS[policy]            # AI_CHAT → {"postal"}
    ...
    for category, pattern, token in _PII_PATTERNS:
        if category in skips:
            continue
        text = pattern.sub(token, text)
```

```python
# Before — backend/ai/mcp_server.py, _call_tool
            return [mcp_types.TextContent(type="text", text=json.dumps(payload, default=str))]
# After
            return [mcp_types.TextContent(type="text", text=_serialize_tool_payload(payload))]
```

## 8. Rollback plan

- **Code:** `git revert` of the five commits restores the previous behaviour on the next deploy. No
  schema change, no migration, no `app_settings` value. No feature flag was added: the change restores
  intended behaviour, leaves `/mcp` unchanged, and `ai_assistant_enabled` remains the kill switch for the
  whole surface. A settings flag would have cost a migration + `schemas.py` + admin route + allowlist
  test for a fix whose only "new" data exposure is a postal code beside an already-transmitted street
  address.
- **Data:** ride rows already written with the literal `[POSTAL]` are not touched by this change and are
  not repaired by a revert either. Remediation, if wanted, is a one-off admin query of
  `rides.pickup_address ILIKE '%[POSTAL]%' OR dropoff_address ILIKE '%[POSTAL]%'` and a manual
  correction — a decision for the product owner, not made here.
- **Redis:** a no-drivers pin expires on its own (15 min) or is overwritten by the next quote.

## 9. Verification performed

- [x] `backend/tests/test_ai_pii.py` — **71 passed, 0 failed** (1 skipped: `test_support_route_uses_shared_impl`
  needs FastAPI), executed through a dependency-free runner because PyPI was unreachable from this
  session (HTTP 403 on both the direct and proxied route), so `pytest` itself could not be installed.
- [x] Direct execution of `tools._cap_result` and `mcp_server._serialize_tool_payload` against a
  postal-code + phone-number payload (card untouched; model portion keeps the postal code, redacts the
  phone; `/mcp` output redacts both).
- [x] `build_system_prompt({}, "rider")` builds and carries the new rule-6c sentence and the existing
  no-leak assertions.
- [x] `ruff check` and `ruff format --check` clean on every modified file.
- [x] Blast-radius grep: every `scrub_pii(`/`scrub_pii_deep(` caller (table in §4); every `_client_action`
  producer (`tools_booking.py` ×4, `tools_support.py` ×1 — all `mcp_exposed=False`); every
  `keep_trip_pins` reference (none left outside this change-log and one `shared/` comment).
- [x] Reviewed against `CLAUDE.md`: PIPEDA ban-list (raw GPS still never reaches Sentry/logs — STRICT
  default unchanged), "do not silently swallow errors" (policy type check placed before the guard),
  dual-import pattern (both branches updated in `tools.py`, `orchestrator.py`, `mcp_server.py`).
- [ ] Feature-flagged — **not**, see §8.
- [ ] Manual repro in staging — **not performed** (no staging or live provider in this session).

## What was NOT verified

- **`test_ai_tools_booking.py`, `test_ai_orchestrator.py`, `test_ai_mcp.py` were written but not
  executed here** — they need the full backend dependency set (FastAPI, httpx, the mocked Supabase
  fixture), which could not be installed because PyPI was blocked. CI on the pull request is the first
  real run. The fixtures were mirrored line-for-line from adjacent passing tests
  (`test_vague_place_returns_clickable_nearby_suggestions`, `test_quote_uses_estimate_engine_and_applies_best_promo`,
  `test_proposal_emits_client_action_and_no_writes`) to minimise that risk.
- No live model turn against a provider; the "Yes → re-quote" behaviour is a prompt-level instruction and
  was verified only by asserting the prompt/replay text, not by observing a model act on it.
- rider-app has no visual-regression tooling; the card rendering was reasoned about from
  `ai-assistant.tsx` and `aiLocationMessages.ts`, not screenshotted.
- `/mcp` with a real MCP client was not exercised (SDK not in the lockfile here); the serializer is
  unit-tested only.
- Whether any live-test ride rows currently carry `[POSTAL]` was not checked (no DB access).
