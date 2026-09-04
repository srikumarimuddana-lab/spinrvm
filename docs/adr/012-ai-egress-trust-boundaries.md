# ADR-012: AI assistant egress trust boundaries — scrub at the boundary, never at the source

- Status: Accepted
- Date: 2026-09-04
- Deciders: Claude Code session on branch `claude/ai-chat-postal-code-bug-nx9tun`, prompted by live-testing screenshots showing `[POSTAL]` in customer-facing addresses
- Domain: ai
- Affects: `backend/ai/pii.py` (`ScrubPolicy`), `backend/ai/tools.py` (`_cap_result`), `backend/ai/orchestrator.py`, `backend/ai/mcp_server.py` (`_serialize_tool_payload`); referenced by `docs/compliance/pia-ai-surfaces-2026-08.md` §3/§8/§15 and `.claude/agents/spinr-ai-guardrail-reviewer.md`

## Context

The assistant's data flow has one PII scrubber (`ai/pii.py::scrub_pii`) and, since 2026-08-18, one
choke point where every tool result passes through it (`tools.py::_cap_result`). That choke point was
chosen because it is the single place both the chat loop and `/mcp` funnel through — a good property
for *coverage*, and a bad one for *correctness*, because the same result object then fans out to
consumers with completely different trust levels:

| Consumer | Who it is | What it needs |
|---|---|---|
| LLM provider (Anthropic / OpenAI / Gemini) | Third-party processor under the PIA | Identifiers redacted; **trip-location data intact**, because booking is the job |
| `/mcp` client | Third party chosen by the rider (Claude Desktop etc.) | Everything redacted — read-only tools, no booking, PIA R-10 |
| The rider's own app (SSE `action` cards, `token` stream) | The data subject | **Nothing redacted** — it is their data; the concern is *internal-detail* leakage (tool names, ids, raw errors) |
| Telemetry (logs, Sentry, support tickets, anonymous web chat) | Ops and vendors | Everything redacted, GPS above all (PIPEDA hard rule). The AI escalation transcript is re-scrubbed STRICT in `tools_support._recent_transcript` at the Zoho boundary, because persistence is no longer strict; the anonymous web assistant's tool results stay STRICT via `tools._policy_for_audience` |

Applying the telemetry policy at the choke point rewrote every Canadian postal code in the
rider-facing `_client_action` cards to the literal `[POSTAL]`. Because the app builds the tapped-card
message from the card label, the token then travelled back through the model into
`get_fare_quote`/`propose_ride_booking`, into a Google geocode query, and into
`rides.pickup_address` on Confirm. The 2026-08-18 change-log had stated that "no downstream code reads
a tool result after `_cap_result` returns it"; the card path was that downstream code.

The scrubber also had only a boolean escape hatch (`keep_trip_pins`) for the one exemption anyone had
thought of, so the next exemption — the postal code — had nowhere principled to go.

## Decision

1. **Scrub at the egress boundary, never at the source.** A tool result is not an egress. The model
   context, the `/mcp` response body, and the log line are. Each of those boundaries applies its own
   policy at the point where data leaves the process for that receiver.
2. **`ScrubPolicy` names the boundary** (`ai/pii.py`):
   - `STRICT` (default): every regex category redacted, bracketed trip pins included. Used by logs,
     Sentry, support tickets, the anonymous web assistant and `/mcp`.
   - `AI_CHAT`: the authenticated in-app assistant only (`orchestrator.py`'s user-message and
     reply-persistence path; `tools.py`'s model-facing tool-result cap). Identifiers (phone, email, card,
     SIN, free-text GPS) are still redacted. Trip-location data — app-generated bracketed `[lat,lng]`
     pins and Canadian postal codes — is kept, because this path already carries the street address
     (never regex-scrubbable) and the exact coordinates (as floats in tool traffic) under the PIA §3
     trip-endpoint exception, and a postal code adds no identifying power beyond the street address it
     accompanies.
   - A policy is an explicit per-call-site opt-in. `tests/test_ai_pii.py` enumerates every file that
     mentions `AI_CHAT` in non-test source and fails if the set changes.
3. **`_client_action` is never scrubbed in `_cap_result`.** It is the rider's own data going back to
   the rider's own app. The split is non-mutating (the card can alias the model-facing structure).
4. **`/mcp` re-scrubs its whole response under `STRICT`** in `mcp_server._serialize_tool_payload`,
   card included. Its posture is exactly what it was before this ADR; the guarantee moved from the
   choke point to the boundary.
5. **Data minimisation stays the first line.** The scrubber is a regex backstop for six identifier
   shapes. A tool that would put a person's name, a government ID, or another user's data into a
   result must whitelist fields at the source (`_pick(row, FIELDS)`), exactly as before. A tool that
   emits a card must build that card from data the rider is entitled to see — the scrub will not clean
   it, by design.

## Consequences

**Positive**
- Postal codes render correctly in cards, tapped messages, assistant prose and new ride rows.
- The next "should X be redacted here?" question has a place to be answered: pick the boundary, pick
  the policy, and the call-site guard test forces the decision to be visible in review.
- `/mcp` keeps its stricter posture without depending on an inspection-time fact ("no `mcp_exposed`
  tool returns a card today").

**Negative / accepted**
- **One pin per conversation, most recent request wins.** A no-drivers fare check overwrites a
  still-valid priced pin for an earlier trip (same Redis key). Kept deliberately after review: a second
  key with a second replay block adds a state machine (delete-on-success, diverging TTLs), and a "yes"
  after a fare check must never be read as confirming an older priced trip. The replay block tells the
  model an earlier trip must be re-quoted before proposing; the rider then sees a fresh quote card. In
  practice the pickup is usually shared, so the earlier trip would have no drivers either.
- `/mcp` output is byte-identical to before except at the truncation boundary: `_cap_result` measures
  the cap on the `AI_CHAT`-scrubbed dict, so an over-cap result can truncate at a different offset.
- Postal codes now reach the LLM provider and `ai_messages` as bare text on the chat path (they
  previously reached both inside the street address; the marginal exposure is a code that identifies a
  block face, beside an address that identifies the building). Recorded as a PIA amendment.
- The `_cap_result` docstring carries an aliasing caveat that future refactors must respect.
- The rider-facing boundary has *no* PII policy, only an internal-detail policy — and today that policy
  is enforced on the persisted copy only (`filter_tool_leakage` at `orchestrator.py`). Closing the live
  stream gap is a follow-up (`ACTION_ITEMS.md` AI16 follow-ups F1–F3).

**Rejected alternative** — keep scrubbing postal codes to the provider and only exempt the card. The
model would still see `[POSTAL]` in its tool results and echo it in prose and tool arguments, so ride
rows and receipts would stay corrupted. Fixing that would require stripping redaction tokens out of
model-supplied addresses in every booking tool — scrubbing at the source, then un-scrubbing at the
source, which is the anti-pattern this ADR exists to stop.
