# AI Guardrail Risk-Acceptance Review — AI1b & AI14

**Owner:** Product / Engineering leadership
**Due:** No hard deadline — both are already-shipped, already-accepted trade-offs; recommend closing within one quarter so they don't sit as permanently-open backlog items
**Risk if missed:** Not a launch blocker for either — both systems already work as designed today. The risk is process, not incident: two deliberate trade-offs are currently backed only by an engineer's own reasoning, re-litigated informally every time someone rediscovers them, with no recorded "yes, keep it" or "no, change it" from anyone with product authority.

---

## Why this document exists

`ACTION_ITEMS.md` items **AI1b** and **AI14** are both "accepted risk, revisit as its own decision" entries — findings where an engineer made a reasonable call under time pressure, documented it honestly, and explicitly said the call itself needs a second, deliberate look rather than staying silently in place forever. Neither is a bug. Both are asking the same question in different domains: *does this codebase's existing trade-off still match what the business wants, now that someone's looking at it on purpose instead of in passing?*

This document hands both questions to whoever owns that call, with everything needed to answer in one sitting — no code reading required.

---

## Decision 1 (AI1b): Should the AI daily message cap fail open or fail closed when Redis is down?

### Background

Every rider/driver AI chat message counts against a per-user daily cap (`app_settings.ai_daily_message_cap`, default 50/day) enforced by `backend/ai/orchestrator.py::_over_daily_cap`. The counter lives in Redis (`INCR` + 24h expiry). If Redis itself is unreachable when the check runs, the function currently **fails open** — it logs a loud error and lets the message through uncapped, rather than blocking it:

```python
async def _over_daily_cap(user_id: str, cap: int) -> bool:
    """Per-user daily message cap via Redis INCR. Fails OPEN with a loud log
    (mirrors the non-OTP rate-limit policy) — the kill switch remains the
    hard stop when Redis is down."""
    key = f"ai:daily:{user_id}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    try:
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, 86400)
        return count > cap
    except Exception:
        logger.error("ai daily-cap check failed — failing open", exc_info=True, extra={"user_id": user_id})
        return False
```

The reasoning at the time (still in the code's own docstring): if Redis is down, the platform-wide **`ai_assistant_enabled` kill switch** is the real safety net — an operator can turn the whole assistant off in seconds via the admin dashboard, which is a faster and more complete mitigation than a per-user cap ever was. The cap itself exists to bound per-user *cost* (each message triggers an LLM call, sometimes several tool-calling round-trips), not to prevent abuse or a safety issue — so its designer judged a temporary cost-cap gap, during a Redis outage the team would already be alerted to and responding to, as an acceptable trade-off against blocking every AI conversation platform-wide over one dependency's blip.

**This exact call already survived one deliberate re-examination.** On 2026-08-10, a separate session independently built a fail-closed alternative — a process-local in-memory counter with a generous fixed floor (used only when Redis itself is degraded, since the admin-configured cap in `app_settings` might be unreadable too) — and it was **discarded, not merged**, specifically to avoid silently overriding this documented trade-off without a real decision behind the change. That alternative implementation no longer exists in the codebase; it would need to be rebuilt if this review concludes fail-closed is the right call.

**A second instance of the identical pattern was found in the same investigation, not yet even logged as its own backlog item:** `backend/ai/mcp_server.py::_over_mcp_daily_cap` gates `/mcp` tool-call volume (a separate, non-chat surface — direct tool invocation by an agent client) with the exact same shape — Redis `INCR`, fail-open on any exception, same log-and-continue policy. Whatever this review decides for the chat cap should apply to this sibling too, for consistency, unless there's a reason the two surfaces warrant different policies.

### The specific question

**Should `_over_daily_cap` (and its sibling `_over_mcp_daily_cap`) keep failing open on a Redis error, or should they fail closed with a bounded floor instead?**

Concretely, three options:

- **(a) Keep fail-open as-is.** No code change. Confirms the existing trade-off (kill switch is the real safety net; a Redis-outage cost-cap gap is an acceptable, small, and short-lived exposure) is still the intended policy.
- **(b) Fail closed with a bounded floor.** Rebuild the discarded alternative: when Redis is unreachable, fall back to a process-local in-process counter with a generous fixed ceiling (not the admin-configured cap, since `app_settings` may be degraded at the same time) — capping cost exposure during an outage without a hard block on every message. Cost: real engineering work (the alternative was built once and discarded, not merged — it would need to be re-implemented and tested), and a process-local counter resets on every replica restart/deploy, so it's a soft ceiling, not an exact one.
- **(c) Fail closed entirely — block AI chat outright when Redis is unreachable.** Simplest to implement, but turns every Redis blip into an AI-assistant outage for every user, which is a materially worse user experience than option (a) or (b) for what's fundamentally a Redis reliability issue, not an AI-safety one.

### What's already been assessed (and what hasn't)

- The original engineer's reasoning (fail-open, kill switch is the backstop) is documented in the code itself and is a considered position, not an oversight — treat it as a real starting option, not a strawman.
- **Not assessed:** how often Redis is actually unreachable in production today (no incident data was reviewed to inform this trade-off — it was a reasoned default, not a data-driven one). If Redis outages are rare and short, (a) is probably fine as-is. If they're frequent or prolonged, the cost-exposure case for (b) gets stronger.
- **Not assessed:** the actual dollar cost exposure of a sustained fail-open window — how many extra LLM calls per affected user per hour of Redis downtime, at what token cost. This would materially inform whether (a)'s "small, short-lived exposure" framing holds up in practice.

### What a closed-out review looks like

1. Record the decision directly in `ACTION_ITEMS.md`'s AI1b entry — which option, and why.
2. If (a): close AI1b as "reviewed, confirmed as-is" — no code change needed for the chat cap; still open a follow-up to apply the same explicit confirmation to `_over_mcp_daily_cap` so it isn't left as a silent unreviewed sibling.
3. If (b) or (c): file a new `ACTION_ITEMS.md` entry (or reopen AI1b) scoping the actual code change for both `_over_daily_cap` and `_over_mcp_daily_cap`, including test coverage for the new fail-closed path.

---

## Decision 2 (AI14): Should a rider-tapped location suggestion with only an APPROXIMATE geocode be trusted for booking?

### Background

When a rider asks the AI assistant to book a ride to a named address, the assistant calls `find_place`, which already computes a precision signal per candidate (`backend/ai/tools_booking.py`):

```python
# Google Geocoding `geometry.location_type` values that actually pin a
# building. ROOFTOP is the building itself; RANGE_INTERPOLATED is a position
# interpolated between two known house numbers on the block — both are precise
_PRECISE_LOCATION_TYPES = frozenset({"ROOFTOP", "RANGE_INTERPOLATED"})
...
candidate["precise"] = quality in _PRECISE_LOCATION_TYPES
```

When a candidate is imprecise **and** the rider's own query looked like a specific street address (a house number was given but Google could only resolve to a street or neighbourhood centroid), `find_place` normally surfaces an `imprecise_address` warning back to the assistant. But prompt rule 6b (`backend/ai/prompts.py:96`, added on PR #2774) explicitly **suppresses that warning once the rider taps the suggestion**:

> "The same applies when the rider taps one of your location suggestions — their message reads 'Use \<address\> [lat,lng] as my pickup/dropoff': those coordinates are the find_place candidate THEY chose, so pass them ... **imprecise_address warnings do not apply to it**."

The practical effect: a rider who taps a suggestion for, say, "742 Evergreen Terrace" that Google could only geocode to the street's centroid (not the actual building) gets quoted and can book a ride to that centroid — potentially a wrong-building or wrong-block pickup/dropoff — with no further warning, because tapping the suggestion is treated as the rider's own confirmation of precision.

**This is a documented, deliberate trade-off, not an oversight.** The alternative — routing every imprecise tap through `request_map_pin` (a tool that gives the rider a button to drop an exact pin on the map) — was considered and rejected for the PR #2774 iteration specifically because:
1. It adds a mandatory extra step for every rider hitting this path, not just the ones who'd actually get a wrong address.
2. `request_map_pin` degrades to a dead end on any client that doesn't advertise the `map_pin` capability in its request (`tools_booking.py` returns `{"shown": False}` in that case) — and that exact dead-end shape is what produced the original infinite-loop bug this same prompt rule was written to fix. Trading a location-precision risk for a re-introduced infinite-loop risk was judged the worse trade.

This was originally raised as a Codex review comment on PR #2774, not found independently by engineering — worth knowing when weighing how seriously to take it, since it's an external reviewer's judgment call being carried forward, not just an internal team member's.

### The specific question

**Is booking a ride at an APPROXIMATE (street/neighbourhood-centroid) geocode acceptable when the rider tapped a suggestion showing that address, or does this need a stronger signal before quoting/booking?**

Three options, roughly in increasing cost/friction order:

- **(a) Keep as-is.** No change. Confirms that a rider's tap on a suggestion card is itself sufficient confirmation, even when the underlying geocode is only approximate — the rider saw the address text on the card and chose it, so the precision gap is judged an acceptable, rare edge case (most addresses geocode precisely; this only bites numbered street addresses Google can't pin exactly).
- **(b) The "quote + note" middle ground already scoped in the ACTION_ITEMS.md entry.** Stop suppressing the `imprecise_address`/`precise: False` signal on a tapped suggestion. Let the assistant quote and book immediately (no extra step, no dead end), but have it say something like *"I've got you down for [address] — this pin is placed at the closest exact match I could find on that street, not necessarily your exact door. Want to drop a precise pin instead?"* — offering `request_map_pin` as an **optional** refinement rather than a gate. This closes the "no warning at all" gap without reintroducing the infinite-loop risk PR #2774 already fixed, since it never blocks on the map-pin capability being available.
- **(c) Gate the booking behind `request_map_pin`** for imprecise taps specifically (the originally-rejected option). Only worth reconsidering if (b)'s softer warning is judged insufficient — this still has the dead-end risk on non-map-pin-capable clients that got it rejected the first time, so choosing this should come with an explicit plan for that dead end (e.g., only gate when the capability is actually advertised, falling back to (b)'s behavior otherwise).

### What's already been assessed (and what hasn't)

- The infinite-loop failure mode (b) and (c) both have to avoid is real and previously shipped — this isn't a hypothetical risk being used to wave off a fix.
- The underlying `precise` signal already exists and is already computed for every candidate — option (b) is a prompt-copy and suppression-removal change, not new engineering. `find_place` doesn't need new code; only the prompt rule that currently hides the signal on a tap needs to change.
- **Not assessed:** how often this actually happens in production — no query has been run against real ride/geocode data to see what fraction of AI-booked rides used an imprecise tapped candidate, or whether any of those led to a real wrong-pickup/dropoff incident or support ticket. That data would materially strengthen or weaken the case for moving off option (a).
- **Not assessed:** rider-side wording for (b) — the "quote + note" copy above is illustrative, not tested or reviewed against this codebase's tone conventions.

### What a closed-out review looks like

1. Record the decision directly in `ACTION_ITEMS.md`'s AI14 entry.
2. If (a): close as "reviewed, confirmed as-is" — no code change.
3. If (b): scope a small prompt-only change to `backend/ai/prompts.py` rule 6b (stop suppressing the imprecise-address signal on a tap; add the "quote + note" instruction) plus a regression test confirming the assistant still books without requiring `request_map_pin`. Low effort — no new tool, no new API surface.
4. If (c): scope the `request_map_pin`-gating change, explicitly including the capability-unavailable fallback path so the original infinite-loop bug can't recur.

---

## Contact

- **Internal escalation:** whoever owns AI-assistant product risk decisions at Spinr (no single named owner identified in this repo for AI guardrail trade-offs specifically — recommend assigning one if this class of decision recurs, same gap already flagged for privacy decisions in `reports/legal/data-transfer-implied-consent-review.md`)
- **Engineering questions / module owners:** `backend/ai/orchestrator.py`, `backend/ai/mcp_server.py` (Decision 1); `backend/ai/tools_booking.py`, `backend/ai/prompts.py` (Decision 2)
- **Tracking items:** `ACTION_ITEMS.md` AI1b, AI14

---

## Status

| Step | Owner | Status | Date |
|---|---|---|---|
| **Decision 1 (AI1b) — fail-open vs. fail-closed on Redis error** | | | |
| Review the question and reach a determination (a/b/c) | Product / Engineering leadership | ⬜ Open | — |
| Record the determination in `ACTION_ITEMS.md` AI1b | Product / Engineering leadership | ⬜ Open | — |
| If (b) or (c): file the follow-up engineering item and implement | Engineering | ⬜ Open | — |
| Apply the same determination (or an explicit different one) to `_over_mcp_daily_cap` | Product / Engineering leadership | ⬜ Open | — |
| **Decision 2 (AI14) — trust level for tapped-but-approximate geocodes** | | | |
| Review the question and reach a determination (a/b/c) | Product / Engineering leadership | ⬜ Open | — |
| Record the determination in `ACTION_ITEMS.md` AI14 | Product / Engineering leadership | ⬜ Open | — |
| If (b) or (c): file the follow-up engineering item and implement | Engineering | ⬜ Open | — |
