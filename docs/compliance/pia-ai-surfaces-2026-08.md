# Privacy Impact Assessment — Spinr AI/LLM Assistant Surface

```
PIA Reference:       SPINR-PIA-2026-08
Version:              1.0 (Draft)
Classification:       Internal — Privacy Sensitive
Program/System:       In-app AI assistant (rider + driver), admin AI console, /mcp
Assessment Date:      2026-08-02
Assessed by:          Claude Code (automated PIA pass) — requires human Privacy
                       Officer / counsel review before this becomes an approved PIA
Next Review:          Before rider-facing launch, and at any provider/model change
```

This assessment was produced by static code review of the surfaces in scope. It is
a documented starting point for the Privacy Officer and legal counsel, **not** a
substitute for their sign-off. Every "requires confirmation" note below is a real
gap, not a formality.

---

## 1. Executive Summary

Spinr's `backend/ai/` module gives riders and drivers an in-app chat assistant that
can answer questions from their own account data (rides, wallet, promos, driver
documents/earnings) and, for riders, quote and propose ride bookings. It routes
every conversational turn through one of four possible third-party LLM back-ends
(Anthropic, OpenAI, Google Gemini, or OpenRouter — a fourth-party pass-through
router), selected at runtime by an admin-configured setting, plus an optional
separate embeddings provider (OpenAI or Gemini) for FAQ semantic search. A
super-admin console can impersonate any rider/driver to test the same pipeline
against their real data. An optional `/mcp` endpoint exposes a read-only subset of
the same tools to external agent clients (e.g. a rider's own Claude Desktop/Code).

**Overall privacy risk rating: HIGH**, driven by two findings that are blocking, not
merely noteworthy:

1. **No live consent basis exists for this disclosure at all.** `docs/legal/privacy-policy.md`
   is an unpublished draft. Until a privacy policy is actually published and users
   have agreed to it, there is no consent instrument in force for *any* third-party
   processor — AI included.
2. **The privacy policy draft and vendor register only disclose Google (Gemini)** as
   an AI processor, but the code's default provider is **Anthropic**, and OpenAI and
   OpenRouter are equally reachable via an admin dropdown with no additional gate.
   If Anthropic (the code default) or OpenAI ships to production, the disclosure a
   user would eventually see is for the wrong company.

Architecturally, the tool-scoping and audit-logging design is well-built (server-injected
identity, per-argument ownership verification, a 90-day hard-delete retention purge that
is actually wired into `purge_pii_retention`, and a PII-scrubbing helper applied to all
persisted/replayed conversation text). The core scrubbing control, however, **only
covers user-authored and persisted-assistant text — it does not touch the tool-result
payloads that carry the richest personal information (addresses, driver name, plate,
wallet balance, earnings) to the LLM provider.** That is expected and largely
unavoidable given how tool-calling LLMs work (the model needs that data to answer),
but it means the "disclosed to third party" surface is much larger than `ai/pii.py`
alone would suggest, and the PIA and consent language must say so honestly.

**Recommendation: NO-GO for general rider-facing launch until Section 9's blocking
items are closed.** The underlying engineering is close to launch-ready; what's
missing is the compliance instrument (published, accurate policy language + DPAs),
not a rebuild of the pipeline.

Findings: 2 Critical | 4 High | 5 Medium | 3 Low

---

## 2. System Description

### 2.1 Purpose

In-app conversational assistant for riders (ride status/history/receipts, wallet,
promos, service-area/fare info, fare quoting and booking proposal) and drivers
(application/document/earnings status, FAQ). A super-admin console runs the same
pipeline against a real user's account for support/QA. An optional MCP endpoint
lets an authenticated rider/driver's own external agent client call a read-only
subset of the same tools.

### 2.2 Components in scope

| Component | File(s) | Role |
|---|---|---|
| Orchestrator | `backend/ai/orchestrator.py` | Owns the chat-turn loop: persists messages, calls the provider adapter, executes tool calls, streams SSE frames |
| PII scrubber | `backend/ai/pii.py` | Regex-based redaction applied to user text (in) and assistant text (before persistence) |
| Provider adapters | `backend/ai/providers/{anthropic,openai,gemini}_adapter.py`, `providers/__init__.py` | Translate the canonical message/tool format to each vendor's wire format |
| Tool registry | `backend/ai/tools.py`, `tools_rides.py`, `tools_account.py`, `tools_booking.py`, `tools_driver.py`, `tools_support.py` | Server-scoped read/write handlers the model can invoke |
| Embeddings | `backend/ai/embeddings.py` | Separate OpenAI/Gemini call to vectorize FAQ text + rider query for semantic search |
| Response cache | `backend/ai/response_cache.py` | Redis cache of cacheable (FAQ-only) turns, cross-user |
| Conversation store | `backend/ai/conversations.py` | Supabase `ai_conversations`/`ai_messages` persistence |
| Threat detection | `backend/ai/threat.py` | Regex tripwire for prompt-injection/exfiltration signals; logs tags only |
| MCP server | `backend/ai/mcp_server.py` | Optional streamable-HTTP mount exposing read-only tools to external agent clients |
| Chat route | `backend/routes/ai.py` | `/ai/chat` SSE endpoint, conversation list/delete |
| Admin console route | `backend/routes/admin/ai_console.py` | `/admin/ai/chat` — super-admin impersonation, audited |
| Rider client | `rider-app/app/ai-assistant.tsx` | Chat UI; renders SSE frames, booking/quote/location cards |

### 2.3 High-level data flow

```
Rider/Driver device
  │  message text + optional device lat/lng + capability flags
  ▼
POST /ai/chat (backend/routes/ai.py)
  │  scrub_pii(user_message) ──► persisted to ai_messages (Supabase, Canada)
  ▼
orchestrator.run_chat_turn
  │  loads scrubbed history + system prompt
  ├──────────────► LLM provider (Anthropic | OpenAI | Gemini | OpenRouter)  [outside Canada]
  │                    ▲                              │
  │        tool_result (UNSCRUBBED — addresses,        │ text / tool_call
  │        driver name+plate, wallet, earnings, etc.)   │
  │                    │                              ▼
  ├──── tools_*.py handlers ──► Supabase (rides, wallets, drivers, faqs, saved_addresses …)
  │                                          │
  │                              (FAQ semantic search only)
  │                                          ▼
  │                          Embeddings provider (OpenAI | Gemini) [outside Canada]
  │
  ├──► response_cache (Redis) — cacheable FAQ-only turns, cross-user, TTL-bound
  ├──► scrub_pii(final_text) ──► persisted to ai_messages (Supabase, Canada)
  └──► SSE frames ──► rider/driver device
```

### 2.4 Interconnections / third parties

- **LLM chat providers**: Anthropic, OpenAI (also serves OpenRouter via base-URL
  override), Google Gemini. Selected by the single `settings.ai_provider` admin
  setting; default in code is `"anthropic"` (`backend/ai/providers/__init__.py:61`).
- **Embeddings provider**: OpenAI or Gemini, independently configured
  (`settings.ai_embedding_provider`), used only for FAQ semantic search.
- **OpenRouter**: a fourth-party router — Spinr sends data to OpenRouter, which
  then forwards it to whatever underlying model the configured `ai_model` string
  names (e.g. `anthropic/claude-haiku-4.5`). This is a *distinct* sub-processor
  from Anthropic/OpenAI/Gemini direct and is not listed anywhere in
  `docs/vendor-register.md`.
- **Zoho Desk** (`services/zoho_desk_integration.py`, called from
  `tools_support.escalate_to_support`): receives an already-scrubbed conversation
  transcript when `ai_escalation_creates_ticket` is on. Out of this PIA's code
  scope but noted because the transcript originates here.
- **/mcp external agent clients**: an authenticated rider/driver's own MCP-capable
  client (e.g. Claude Desktop) can call the read-only tool subset directly. Spinr
  does not control what that client or its own configured LLM does with the data
  afterward — this is a disclosure Spinr makes to a channel it does not audit
  end-to-end.

---

## 3. Personal Information Inventory

Determined by reading `backend/ai/tools_*.py` handler return values and what the
orchestrator forwards to the provider, not assumed. "Reaches LLM provider" means
the model actually receives it as tool-result JSON, un-redacted, because the model
needs it to compose a natural-language answer or a booking card. "Scrubbed?" refers
only to `ai/pii.py`'s regex pass, which — per Section 6 — is applied **only** to
user-authored input text and to the assistant's final reply text before it is
*persisted*, never to the tool-result JSON that is what actually carries most of
this data to the provider in real time.

| Tool module | Data element(s) sent to LLM provider | Category | Scrubbed by `ai/pii.py`? |
|---|---|---|---|
| `tools_rides.py` (`get_active_ride`, `get_ride_history`, `get_ride_details`) | Pickup/dropoff **addresses** (not raw GPS — deliberately excluded), fare amounts, surge multiplier, payment method/status, ride timestamps, cancellation reason (free text) | Location (address-level), Financial | No |
| `tools_rides.py` (driver public view) | Driver rating, total rides, vehicle make/model/color, **license plate**, driver first+last name | Basic Identity, Vehicle | No |
| `tools_rides.py` (`get_ride_receipt`) | Full receipt line items, tip amount, promo code, discount, grand total | Financial | No |
| `tools_account.py` (wallet) | Wallet balance, transaction type/amount/description, `balance_after` | Financial | No |
| `tools_account.py` (`get_saved_places`) | Saved place **name + address + lat/lng** (e.g. "Home", "Work") | Location (precise) | No |
| `tools_booking.py` (`find_place`, `get_rider_location`, `get_fare_quote`, `propose_ride_booking`) | **Precise pickup/dropoff GPS coordinates**, resolved addresses, device location fix, most-recent-ride pickup as a location hint, fare totals, promo application | Location (precise GPS), Financial | No — GPS explicitly carried through (`keep_trip_pins`) even in the one place `scrub_pii` runs, by design |
| `tools_driver.py` (`get_driver_application_status`, `get_document_status`) | Document review status, rejection reason (free text), **document expiry dates** (license, insurance, CRC/background check, vehicle inspection, work eligibility) | Employment/eligibility, Legal | No |
| `tools_driver.py` (`get_driver_earnings_summary`) | Per-trip driver earnings, payment status | Financial | No |
| `tools_support.py` (`search_faqs`) | The rider/driver's **search query text** (model-constructed, may echo user phrasing) sent to the *embeddings* provider for vectorization, separately from the chat provider | Online activity / potentially any category the user typed | No — not passed through `scrub_pii` before `embed_texts` |
| `tools_support.py` (`escalate_to_support`) | Ticket reason (free text, ≤300 chars), category, and — only for the Zoho ticket, not the LLM — a transcript pulled from already-scrubbed `ai_messages` rows | Varies | Transcript: yes (inherits scrub from persistence). Reason string in the tool call itself: sent to LLM provider as part of the *next* turn's history only after the assistant's reply is scrubbed on persistence — but the reason was generated by the model this turn and is not separately scrubbed before being echoed back into `messages` within the same turn. |
| User's own chat message | Free text, scrubbed for phone/email/GPS/postal code/card number/grouped SIN; bracketed trip-pin coordinates deliberately preserved | Any (self-reported) | **Yes**, partially (see Section 6 for gaps) |
| System prompt | Company phone/email (Spinr's own contact info, not a data subject's PII) | N/A | N/A |

**What is *not* sent**: `tools_rides.py` explicitly excludes raw ride GPS
coordinates from its rider-facing fields (comment: "Precise GPS coordinates are
deliberately excluded from results"); no OTPs, no payment card numbers/PANs, no
government ID numbers, no driver earnings *split* internals, no other users'
data (enforced by `FORBIDDEN_ID_ARGS` + per-tool `owned_id_args` ownership
verification in `backend/ai/tools.py`).

**Coordinates are the standout item**: `tools_booking.py` sends full-precision
pickup/dropoff latitude/longitude to whichever LLM provider is configured, every
time a fare is quoted or a booking is proposed — necessarily, since the model must
carry them through the tool-calling loop and the client-rendered card. This is
squarely "raw GPS," which CLAUDE.md's compliance section lists as something that
must never appear in **logs/Sentry/analytics** — that rule does not literally cover
LLM-provider egress, but the same sensitivity applies, and it is not currently
named anywhere in the privacy policy draft or vendor register.

---

## 4. Purpose & Necessity (PIPEDA data-minimisation)

| Data element | Stated purpose | Necessary for that purpose? |
|---|---|---|
| Pickup/dropoff GPS + addresses | Fare quoting, booking proposal, "where's my driver" | Yes — the assistant cannot quote or book without coordinates. Architecture already minimizes exposure elsewhere (ride list endpoints exclude raw GPS); the AI path is the one place precise coordinates are a *necessary* input, not an incidental one. |
| Driver name, vehicle, plate | Answering "who's my driver / what car is coming" | Yes, and matches what the rider already sees in-app during an active ride (same `DriverPublicView` shape used elsewhere). |
| Wallet balance/transactions | Answering balance/history questions | Yes, and scoped to the signed-in user only. |
| Saved place name/address/GPS | Resolving "home"/"work" language during booking | Yes. |
| Document expiry dates (driver) | Answering "am I eligible / what's expiring" | Yes — the assistant deliberately mirrors the same `go_online` eligibility gate so it can't tell an ineligible driver they're clear (good design), which requires exact dates. |
| Driver earnings, per trip | Answering "was I paid" | Yes. |
| Rider's raw search query (embeddings) | Semantic FAQ matching | Marginal — most FAQ queries are innocuous ("how does surge work"), but nothing prevents a rider typing something personal into what they think is a private assistant, and that exact text can be forwarded verbatim to a *second*, less-visible provider (the embeddings vendor) with no scrubbing at all. |
| Conversation history (12 messages by default) replayed each turn | Multi-turn context | Yes, but this means every item above is re-sent to the provider on every subsequent turn in the conversation, not just the turn that produced it — the same address/plate/balance can cross the wire to the LLM several times in one conversation. |

No element in this inventory looks like collection *beyond* what the feature
needs. The minimisation question is not "is this tool over-scoped" (it isn't —
the ownership/whitelist discipline in `tools.py`/`tools_rides.py` is genuinely
good) but "has Spinr documented and consented to disclosing this specific set of
elements to a named third party," which is Section 5's question, not this one.

---

## 5. Consent Basis

**Finding: there is currently no in-force consent instrument for this disclosure.**

- `docs/legal/privacy-policy.md` is headed *"Draft for Legal Review"* and states
  explicitly it "needs review by counsel... before publication." Its own
  pre-publication checklist (item 2) names disclosing "Gemini and LogRocket" as
  one of the things that must happen before the document is published.
- Until that document (or an equivalent) is published and presented to users at
  signup/consent-refresh, **no user has agreed to any third-party AI processing**,
  regardless of which provider is technically configured. This is a blocking gap
  independent of every other finding in this report.
- Once published, the draft's current text only says: *"Google (Gemini), a
  generative-AI service we use for certain in-app support and text-processing
  features. Text you enter in those specific features may be processed by this
  service."* It does **not** name Anthropic, OpenAI, or OpenRouter anywhere.
- The code's actual default provider is Anthropic
  (`ai/providers/__init__.py:61`, `settings.get("ai_provider") or "anthropic"`),
  and `docs/vendor-register.md` has no row for Anthropic, OpenAI, or OpenRouter at
  all — only Gemini. **If the assistant ships with its current default (or is ever
  switched to OpenAI/OpenRouter by an admin, which the settings UI allows with no
  additional review gate), the disclosed processor and the actual processor will
  not match**, even after the privacy policy above is published.
- The privacy policy's scope statement ("Text you enter in those specific
  features") also does not describe what Section 3 of this PIA found: that tool
  results — GPS coordinates, driver identity, wallet balances, document
  expiry — are what actually cross the wire to the provider on most turns, not
  just the text the rider typed. A user reading the current draft would
  reasonably believe only their own typed message is shared, which understates
  the disclosure.
- No consent-version stamping exists for privacy-policy acceptance (the codebase
  has a `CONSENT_VERSION` mechanism, but it is scoped to marketing-channel opt-in
  in `routes/marketing.py`, not general privacy-policy consent) — so even after
  publication there is no way to prove which users saw which version of the AI
  disclosure, or to gate the AI feature on having agreed to a version that
  mentions it.

**Consent basis for the current architecture: not established.** This is squarely
a legal-team action item, not something engineering can resolve by writing more
code — but engineering can (and should) fix the mismatch between "what the code
can do" (any of 4 providers) and "what any future published policy will say"
before launch, ideally by pinning the launch configuration to exactly the
disclosed provider.

---

## 6. Data Residency

CLAUDE.md requires: *"Supabase project must be in a Canadian region... All primary
storage... must be region-matched or justify exception... Changing regions is a
compliance event."* None of the AI providers in scope meet that bar today:

| Provider | Processing region (as configured in code) | Canadian option available? |
|---|---|---|
| Anthropic (`anthropic_adapter.py`, direct SDK, default endpoint) | US (Anthropic's public API has no region parameter in this integration) | Not via this SDK path — would require e.g. AWS Bedrock in a Canadian-adjacent region, not what's implemented |
| OpenAI (`openai_adapter.py`, direct SDK) | US | No region parameter set in code |
| OpenRouter (same adapter, `base_url` override) | Routes to whichever underlying provider/region the selected model maps to — effectively unconfirmable per-call | No |
| Google Gemini (`gemini_adapter.py`, `google.generativeai`) | `genai.configure(api_key=...)` sets no region — defaults to Google's standard (US-centric) endpoint | `docs/vendor-register.md` flags `northamerica-northeast1` as an *evaluation* item, not implemented |
| OpenAI/Gemini embeddings (`embeddings.py`) | Same as above, same providers, same lack of region pinning | Same |

This is a genuine, currently-unavoidable cross-border transfer under PIPEDA
s.4.1.3 for every provider path the code supports. It is not inherently
non-compliant — PIPEDA permits cross-border transfer with comparable protection
and disclosure — but two things must both be true, and neither is yet:

1. **Disclosure**: the privacy policy must name the actual processor(s) (Section 5).
2. **Comparable protection**: a signed DPA per processor. `docs/vendor-register.md`
   lists "File signed DPA with Google LLC (Gemini)" as `_TBD_`, and has no row for
   Anthropic/OpenAI/OpenRouter to even start that process.

`Supabase` (conversation storage) and `Redis` (response cache, quote-pin cache) are
**not** implicated here — those remain Canadian-region primary storage per
CLAUDE.md, consistent with the residency commitment. The gap is specifically the
LLM/embeddings provider calls, which are ephemeral (not stored at the provider in
Spinr's control) but still constitute a disclosure/transfer at the moment of the
API call.

---

## 7. Retention

| Store | What it retains | Retention | Enforcement |
|---|---|---|---|
| `ai_conversations` / `ai_messages` (Supabase) | Scrubbed user + assistant text, tool **names** only (never arguments/results), token counts, provider/model label | 90 days, then hard-deleted | **Verified wired**: migration 141's `purge_pii_retention` Step J deletes `ai_messages` older than 90 days and cascades empty `ai_conversations`; the daily `retention_purge_loop` (`backend/core/lifespan.py`) calls it. This is one of the better-attested retention claims in the codebase — confirmed in the SQL, not just a comment. |
| `response_cache` (Redis, `ai:respcache:*`) | Scrubbed question hash → scrubbed answer text, cross-user, FAQ-only turns | TTL = `ai_faq_cache_ttl_seconds`, default 3600s (1 hour), admin-configurable | TTL-enforced by Redis itself; no separate purge job needed. In the in-process fallback mode (`REDIS_URL` unset), it resets on process restart instead. |
| `ai:quote:<conversation_id>` (Redis) | Last priced pickup/dropoff coordinates + address + vehicle/promo for one conversation | 900 seconds (15 min) | TTL-enforced. |
| `ai_tool_audit` (Supabase, migration 217) | Tool name, user_id, audience, outcome, latency, **argument key names only** (no values/results) | Migration comment says *"Retention is handled by the existing retention purge if/when configured"* — **not actually wired**: `purge_pii_retention` (migration 141, current version) has no step touching `ai_tool_audit`. | **Gap** — unbounded retention today. Low severity since the table holds no PII values, only key names and outcomes, but it does hold `user_id` indefinitely, which is itself personal information under PIPEDA's broad definition. |
| `ai_security_events` | Signal tags + user_id + conversation_id, no message text | No retention step found in `purge_pii_retention` either | Same gap as above — low severity, same reasoning. |
| LLM provider (Anthropic/OpenAI/Gemini/OpenRouter) | Whatever each vendor's API-layer retains for abuse monitoring / safety review | **Requires confirmation from each vendor's API terms of service / DPA** — do not assume a figure. Spinr has not filed any of these DPAs yet (Section 6). | Outside Spinr's control; must be documented per-vendor before launch, not estimated here. |
| Embeddings provider (OpenAI/Gemini) | Same caveat — the rider's FAQ query text passes through here too (Section 3) | Requires confirmation from vendor | Same |
| Zoho Desk (escalation tickets) | Scrubbed transcript + ticket text | Out of this PIA's code scope (Zoho terms apply) | Not assessed here |

**Net assessment**: the first-party retention story (Supabase conversations, Redis
caches) is solid and independently verifiable in the migration SQL — this is a
genuine strength, not just an aspiration. The third-party retention story (what
each LLM/embeddings vendor keeps) is completely open and must be closed with
signed DPAs before this can be represented as compliant in the privacy policy.

---

## 8. Effectiveness of `ai/pii.py`

`scrub_pii()` regex-redacts: NANP phone numbers (both separated and bare
E.164/10-digit forms), email addresses, GPS coordinates in three shapes
(labelled `lat=/lng=`, bare pairs, dict-repr), Canadian postal codes, payment card
numbers (network-prefix-gated), and grouped Canadian SINs (`123-456-789` shape
only). It explicitly does **not** attempt to redact names or driver's license
numbers — the module docstring is candid about this and states the mitigation is
data-minimisation instead: the system prompts never *ask* for a name or SIN/DL
number, so the residual exposure is limited to a user who volunteers one
unprompted in free text.

**Where it is actually applied — traced call by call:**

| Egress path | `scrub_pii` applied? | Source |
|---|---|---|
| User's chat message → persisted to `ai_messages`, and thus replayed to the LLM provider on the next turn | **Yes** (`keep_trip_pins=True`) | `orchestrator.py:196` |
| User's chat message → the *live* turn it arrives on (the very first time the LLM sees it) | **Yes** — the scrubbed value (`scrubbed`) is what gets appended to `history`/`messages` and sent, not the raw `user_message` | `orchestrator.py:196-250` |
| Assistant's final reply → persisted to `ai_messages` / stored to response cache | **Yes** (`keep_trip_pins=True`) | `orchestrator.py:393` |
| Assistant's final reply → what the rider actually sees this turn (streamed token-by-token) | **No** — deliberately, since the raw text has already streamed before scrubbing runs; only the *stored/replayed* copy changes | `orchestrator.py:385-393` (by design, not a gap for the *client*, since the client is the data subject) |
| **Tool-result JSON appended to `messages` mid-turn** (`{"role": "tool_result", "content": json.dumps(result, ...)}`) | **No — never scrubbed at all**, in any adapter, for any provider | `orchestrator.py:356-364` |
| `find_place`/`get_fare_quote`/`propose_ride_booking` results (GPS, addresses) | Not scrubbed — and the one `scrub_pii` call in the booking-tool path (none exists) is irrelevant here since these results flow through the tool-result path above, which is unscrubbed by construction | `tools_booking.py` |
| `search_faqs` query text → embeddings provider | **No** — `embed_texts([query] + ...)` in `tools_support.py:173` sends the raw model-constructed query with no `scrub_pii` pass | `embeddings.py:74`, `tools_support.py:162-196` |
| MCP tool results → external agent client | **No** — `mcp_server.py`'s `_call_tool` calls `execute_tool` directly and returns the raw result; same unscrubbed tool-result path | `mcp_server.py:163-176` |
| Escalation transcript → Zoho ticket | **Yes**, inherits scrub because it's built from already-persisted (scrubbed) `ai_messages` rows via `conversations.load_history` | `tools_support.py:311-328` |

**Conclusion: `ai/pii.py` is applied consistently and correctly on the two paths it
was designed for (user input persistence, assistant-output persistence) — that
part of the control works as documented and is not a false claim.** But it is
**not** a general LLM-egress scrubber, and nothing in the codebase claims it is
except by omission — the module docstring says "PII scrubbing for AI assistant
traffic," which reads more broadly than what it actually covers. The real
exposure control for tool-result data is the *authorization* layer (owned-id
verification, field whitelisting in `tools_rides.py`/`tools_account.py`), which
limits *whose* data reaches the model, not *what fields* of that data are
redacted before they do. Those are two different controls and only one of them
exists for the tool-result channel.

This is not something to patch inside this PIA (per the task constraints), but it
is the single most important fact this document needs to convey to whoever signs
off on launch: **the privacy policy's disclosure needs to describe "ride/wallet/driver
account data used to answer the question," not just "text you enter."**

---

## 9. Risk Register

| ID | Description | Likelihood | Impact | Rating | Mitigation | Residual |
|---|---|---|---|---|---|---|
| R-01 | No published privacy policy exists, so no user has consented to any third-party AI disclosure | 5 (Almost certain — confirmed by reading the draft's own header) | 5 (Catastrophic — voids consent for the entire feature) | **Critical** | Publish an accurate privacy policy (Section 5) before enabling `ai_assistant_enabled` for any real user cohort; gate the feature flag on policy-publication being complete | Low, once published |
| R-02 | Disclosed processor (Gemini only, in draft) does not match actual configured/default processor (Anthropic) | 5 (the mismatch exists today in the repo) | 4 (Major — publishing a policy that names the wrong company is itself a misrepresentation, not just an omission) | **Critical** | Either (a) pin production `ai_provider` to Gemini until the policy is corrected and all four are disclosed + DPA'd, or (b) rewrite the policy to name all providers actually reachable via the admin setting, before launch | Low, once aligned |
| R-03 | Tool-result payloads (GPS, driver identity, wallet, document expiry) are disclosed to the LLM provider with no scrubbing, and this is not clearly described anywhere user-facing | 5 (this is the normal, working path — not an edge case) | 3 (Moderate — the data is scoped to the user's own account, but the *category* of disclosure, especially precise GPS, is more sensitive than "text you type") | **High** | Rewrite the privacy-policy AI section to describe the actual disclosure (account/ride/wallet/driver data used to answer, not just typed text); consider whether a lighter-weight tool result (e.g. rounded coordinates, name-only driver identification) is sufficient for some tools | Medium — some disclosure of precise GPS to an LLM provider is inherent to the booking feature and cannot be fully mitigated away |
| R-04 | No DPA on file with any of the 4 chat providers or 2 embedding providers | 5 | 4 | **High** | File DPAs before launch (already an open item in `docs/vendor-register.md` for Gemini; add rows + DPAs for Anthropic, OpenAI, OpenRouter) | Low, once filed |
| R-05 | All LLM/embeddings processing occurs outside Canada with no region pin, inconsistent with CLAUDE.md's residency framing for "primary storage" (the AI call itself is not storage, but the transfer still needs disclosure + DPA per PIPEDA s.4.1.3) | 5 | 3 | **High** | Disclose explicitly in the privacy policy (per-provider country), evaluate Canadian-region alternatives per `docs/vendor-register.md`'s existing open item for Gemini and extend that evaluation to Anthropic/OpenAI | Medium — full Canadian-only LLM processing may not be achievable near-term; disclosure + DPA is the realistic compliance path, not region migration |
| R-06 | FAQ semantic-search query text reaches a *second*, less-visible provider (embeddings) with zero scrubbing, and this provider is not mentioned in the privacy policy draft at all | 3 (semantic search is opt-in via `ai_faq_semantic_enabled`, off by default in the settings model) | 3 | **Medium** | Name the embeddings provider in the policy alongside the chat provider; consider running the same `scrub_pii` pass on the query before embedding | Low, once documented and (optionally) scrubbed |
| R-07 | OpenRouter is a distinct fourth-party sub-processor invisible in `docs/vendor-register.md` and the privacy policy draft; the actual downstream model/region is not confirmable from Spinr's side per call | 2 (requires an admin to select `openrouter` as `ai_provider`) | 3 | **Medium** | Either exclude OpenRouter from the admin-selectable provider list until it has its own vendor-register entry + DPA, or add both before enabling it | Low, once addressed |
| R-08 | `ai_tool_audit` and `ai_security_events` have no retention purge step, unlike every other AI table | 2 | 2 (low-sensitivity fields, but `user_id` is retained indefinitely) | **Medium** | Add a Step to `purge_pii_retention` (new migration) covering both tables, matching the 90-day chat window or a longer audit-appropriate window per security-team input | Low, once added |
| R-09 | No consent-version stamping for privacy-policy acceptance (only marketing-channel consent is versioned) — even after publication, Spinr cannot prove which users saw an AI-disclosing policy version, or gate the feature on it | 3 | 3 | **Medium** | Extend the existing consent-version pattern (`routes/marketing.py`) to general privacy-policy acceptance, or confirm an equivalent `legal_documents` acceptance-tracking mechanism exists and covers this | Low, once confirmed/built |
| R-10 | `/mcp` disclosure is to a rider/driver's *own* external agent client, whose downstream handling (which LLM, what it does with the data) is entirely outside Spinr's visibility | 2 (feature is off by default, `ai_mcp_enabled` false) | 3 | **Medium** | Document this channel distinctly in the privacy policy if/when `ai_mcp_enabled` is turned on generally; keep it default-off until then | Low while default-off |
| R-11 | Admin AI console lets a super-admin impersonate a real rider/driver and send real messages that flow to the third-party LLM under that user's real data, without the user's knowledge at the moment it happens | 2 (super-admin-only, audited) | 3 (a real user's data reaches a third party for a purpose — internal QA — the user did not initiate) | **Low** | Already reasonably controlled: `super_admin`-only, every action writes `audit_logs`, staff accounts are excluded as targets. Confirm this internal-testing purpose is covered by "we may access account information to provide support" language in the eventual privacy policy, or add it explicitly | Low |
| R-12 | Threat-detection scan (`threat.py`) and tool-audit logging are well-designed (tags/ids only, no text) | — | — | **Low (control working as intended)** | No action — noted as a positive control, not a finding | — |
| R-13 | Names and driver's-license numbers are not regex-scrubbed from user-typed text; mitigation is purely "the prompt doesn't ask" | 2 (requires a user to volunteer one unprompted) | 2 | **Low** | Already disclosed candidly in the `pii.py` docstring; acceptable residual risk given the difficulty of reliably regex-matching names, but should be named explicitly in the PIA (done here) rather than left implicit | Low |

---

## 10. Privacy Controls Assessment (FIPPs)

| Principle | Assessment |
|---|---|
| **Accountability** | No named Privacy Officer sign-off exists yet for this specific surface. `docs/legal/privacy-policy.md` names a Privacy Officer contact (`privacy@spinr.ca`) once published. |
| **Identifying Purposes** | Well done in-code: every tool has a "call this when..." description tying it to a specific rider/driver need; the system prompts (`prompts.py`) explicitly scope what the assistant can and cannot do. Not yet mirrored in user-facing consent language. |
| **Consent** | **Not established** — see Section 5. Blocking. |
| **Limiting Collection** | Strong — `FORBIDDEN_ID_ARGS`, per-tool field whitelists, and explicit exclusion of raw ride GPS from list views are real, verifiable minimisation controls. |
| **Limiting Use/Disclosure/Retention** | Retention is well-built for first-party stores (Section 7); disclosure to third parties is broader than currently documented (Section 3, 6, 8). |
| **Accuracy** | Not directly applicable to this surface — data read by tools is sourced live from the same tables other authenticated surfaces read from. |
| **Safeguards** | Server-side identity injection (model can never supply a user id), ownership verification before any owned-resource read, size-capped tool results, rate/cap limits (daily message cap, MCP daily cap, admin-console rate limit), threat-detection tripwire. All genuinely present and correctly wired based on code review. |
| **Openness** | Currently absent for this feature specifically — no published notice describes AI processing at all (Section 5). |
| **Individual Access** | `GET /ai/conversations/{id}/messages` gives a user their own conversation text. Tool-result data itself is not separately exportable through this surface, but the underlying rides/wallet/etc. records are presumably covered by the existing "Download My Data" flow referenced in the privacy policy draft. |
| **Challenging Compliance** | Depends on the general Spinr support/Privacy Officer channel; nothing AI-specific exists or is needed beyond that. |

---

## 11. Recommendations

**[CRITICAL] — Publish an accurate privacy policy before enabling the feature for real users.**
Action: Legal to finalize and publish `docs/legal/privacy-policy.md` (or successor)
with Section 3/6/8's findings folded in — name every provider actually reachable
via `ai_provider`/`ai_embedding_provider`, describe tool-result disclosure (not
just typed text), and state the correct residency/country for each. Owner: Legal
+ Privacy Officer. Success criterion: published policy text matches the running
configuration exactly, verified by a follow-up code-vs-policy diff before each
provider change.

**[CRITICAL] — Resolve the provider/disclosure mismatch before go-live.**
Action: Either restrict production `ai_provider` to whichever single provider is
actually disclosed and DPA'd (recommend: enforce via a startup/settings-save
check, not just documentation) or complete disclosure + DPA for all four
reachable providers before removing that restriction. Owner: Engineering +
Legal. Success criterion: the set of providers the settings UI allows to be
selected in production is a subset of the set disclosed in the published policy.

**[HIGH] — File DPAs for every AI sub-processor and add the missing vendor-register rows.**
Action: Add Anthropic, OpenAI, and OpenRouter rows to `docs/vendor-register.md`
(mirroring the existing Gemini row's format) and file DPAs for all four, plus the
embeddings providers. Owner: Legal. Success criterion: no `_TBD_` DPA cells for
any AI provider in the vendor register.

**[HIGH] — Rewrite the AI section of the privacy policy to describe tool-result disclosure, not just typed text.**
Action: See Section 8's conclusion — the policy must describe "your ride, wallet,
and driver-account data used to answer your question" as a category, not imply
only free-text input is shared. Owner: Legal, informed by this PIA's Section 3
inventory. Success criterion: privacy-policy language reviewed against the
Section 3 table by the Privacy Officer.

**[HIGH] — Disclose cross-border processing region per provider.**
Action: State the actual processing country for each AI provider in the privacy
policy (US for all as currently configured), consistent with how the policy
already handles Stripe/Twilio/Firebase. Owner: Legal. Success criterion: policy
text includes a per-AI-provider country line, matching `docs/vendor-register.md`.

**[MEDIUM] — Name and (optionally) scrub the embeddings provider path.**
Action: Add the embeddings provider (OpenAI or Gemini, whichever is
`ai_embedding_provider`) to vendor register + policy; consider passing the FAQ
query through `scrub_pii` before `embed_texts` as defense-in-depth, since a rider
could type something sensitive into what looks like a plain search box. This is a
code change and, per this task's constraints, is recorded here as a follow-up,
not implemented in this pass. Owner: Engineering (implementation) + Legal
(disclosure). Success criterion: query text scrubbed before leaving
`tools_support.py`; embeddings provider named in policy.

**[MEDIUM] — Wire `ai_tool_audit` and `ai_security_events` into the retention purge.**
Action: New migration adding a step to `purge_pii_retention` for both tables.
Follow-up item, not implemented in this pass. Owner: Engineering. Success
criterion: both tables appear in a `purge_pii_retention` step with an
explicit, documented retention window.

**[MEDIUM] — Extend consent-version tracking to general privacy-policy acceptance.**
Action: Confirm whether `legal_documents`/an equivalent table already tracks
policy-version acceptance per user; if not, build it, mirroring
`routes/marketing.py`'s `CONSENT_VERSION` pattern. Owner: Engineering + Legal.
Success criterion: each user's accepted privacy-policy version is queryable, and
the AI feature can (if desired) be gated on "accepted version ≥ the version that
first disclosed AI processing."

**[LOW] — Document the admin-console impersonation purpose explicitly in the privacy policy's "why we collect it" section, or confirm existing "to provide support" language covers it.** Owner: Legal.

**[LOW] — Document the `/mcp` external-agent-client disclosure channel distinctly if it is ever turned on outside of testing (`ai_mcp_enabled` is default-off).** Owner: Legal + Engineering, before flipping the flag broadly.

**[LOW] — No action needed on `ai/pii.py`'s name/DL-number non-coverage** beyond what this PIA already documents (Section 8, R-13) — the data-minimisation mitigation (prompts never ask) is a reasonable and already-intentional design choice; just make sure the eventual policy doesn't overstate what the scrubber catches.

---

## 12. Go / No-Go Recommendation

**NO-GO for general rider-facing launch of the AI assistant today.**

The blocking issues are R-01 and R-02: there is no published consent instrument
in force, and even the draft that exists names the wrong (or an incomplete set
of) processor(s) relative to what the code can actually reach. Both are legal/
compliance-process gaps, not architecture rewrites — the underlying tool-scoping,
authorization, and retention engineering is sound and does not need to change to
close them.

**Conditions for GO:**
1. Privacy policy published (Section 5) with AI disclosure matching the actual
   running provider configuration and tool-result data categories (Sections 3, 8).
2. DPAs filed for whichever provider(s) production is actually configured to use
   (Section 6, Recommendation "HIGH — File DPAs").
3. Production `ai_provider` pinned to a value that is a subset of what's disclosed
   (Recommendation "CRITICAL — Resolve the provider/disclosure mismatch").

None of the Medium/Low items in Section 9 are individually blocking, but R-06
(embeddings provider) and R-09 (consent versioning) should not be deferred
indefinitely — they are the kind of gap that becomes a real incident the first
time a rider or a regulator asks "which companies see my data and did I agree to
that."

A narrower **staff-only / internal-testing launch** (admin console only,
`ai_assistant_enabled` off for the general rider/driver population) does not
require the same consent instrument, since no rider/driver is a data subject of
an undisclosed-to-them processing activity in that mode — R-11's existing
controls (super-admin-only, audited) are adequate for that limited scope. This
PIA does not block continued internal QA use under the admin console as-is.

---

## 13. What Was NOT Verified

- Each AI provider's/embeddings provider's actual contractual data-retention and
  training-use terms — **requires confirmation from each vendor's DPA/API terms of
  service**, not assumed or estimated in this document.
- Whether Anthropic, OpenAI, or OpenRouter offer any Canadian-region processing
  option at all — not researched here; flagged as an evaluation item mirroring
  the existing Gemini `northamerica-northeast1` evaluation in the vendor register.
- Whether a `legal_documents`/policy-acceptance-tracking table exists elsewhere in
  the schema beyond what `grep` surfaced (`routes/marketing.py`'s
  `CONSENT_VERSION`, which is marketing-channel-scoped only) — worth a direct
  follow-up grep/schema check by whoever owns R-09, since this PIA's search may
  have missed a table.
- Zoho Desk's own data-handling terms (out of code scope for this PIA).
- Runtime behavior — this PIA is a static code-review assessment. It does not
  include a live-traffic capture, a provider-side API log inspection, or a
  penetration test of the tool-authorization boundary. Those would be reasonable
  next steps for the `spinr-ai-guardrail-reviewer` and `spinr-security-auditor`
  agents referenced in CLAUDE.md's PR-review section, applied specifically to
  this surface.
- No production `app_settings.ai_provider` value was read (this PIA has no
  database access) — Section 5/9's "code default is Anthropic" claim is about the
  code's *fallback* value, not a confirmed production setting. Whoever owns R-02
  should check the actual production value before treating that recommendation as
  resolved.

---

## 14. Sign-Off

```
Prepared by:     Claude Code (automated PIA pass, code-review only)   Date: 2026-08-02
Reviewed by:     _________________________ (Privacy Officer)          Date: __________
Approved by:     _________________________ (Legal Counsel)            Date: __________
Engineering ack: _________________________ (backend/ai/ owner)        Date: __________
```

This document is a starting point for those sign-offs, not a substitute for them.
