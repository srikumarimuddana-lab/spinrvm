# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (session `session_013hMEsuEPVu7gwAk21XB4hF`) |
| Surface(s) | shared, rider-app, admin-dashboard |
| Domain (Sentry tag) | ai |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/5177 |
| Related issue or gap ID | `ACTION_ITEMS.md` AI17, sub-item F2 |

## 1. Issue / gap identified

Tapping a quote option in the AI chat sends a self-contained message back to
the assistant that includes `(vehicle id <uuid>)` — e.g. "Book the Economy
(vehicle id 3fa85f64-...) from ...". That literal UUID renders in the
rider's own chat bubble (and the admin AI console's mirror of it), with no
meaning to a human reading it.

## 2. Root cause

The UUID is not cosmetic — `backend/ai/prompts.py` rule 6 documents it as
the mechanism the model uses to know exactly which vehicle tier the rider
tapped, since the assistant's next turn sees only message text, never tool
results. `shared/utils/aiLocationMessages.ts`'s `buildQuoteBookingMessage`
was written to serve both purposes at once — the string sent to the model
and the string shown to the rider — so removing the id to fix the display
problem would have also removed it from what the model receives, silently
making the AI fall back to booking the "recommended" option instead of
whatever the rider actually tapped. This was flagged and deliberately left
unfixed in this PR's first commit, pending exactly this design (already
specified when the item was filed — see AI17's own text for F2 — but missed
during initial investigation).

## 3. Fix / remediation

Split the one string into two: `buildQuoteBookingMessage` (unchanged,
still includes the vehicle id — this is what reaches the model) and a new
`buildQuoteBookingDisplayMessage` (same trip/promo/total, no id — this is
what's rendered). `AiChatMessage.displayContent` (new, optional field)
carries the display twin on the local echo only; `content` keeps carrying
the real text sent to the backend. Both the rider-app chat screen and the
admin AI console mirror render `displayContent ?? content`, falling back
to the existing behavior wherever no display twin is given (every message
type except a quote-card tap).

## 4. Risk & impact on existing functionality

- `buildQuoteBookingMessage`'s existing behavior, signature and return
  value are **byte-for-byte unchanged** — verified by the 4 pre-existing
  jest assertions on its exact string output, all still passing.
- What actually reaches the AI model (`content`, sent via `streamChat`/
  `adminAiChat`) is unchanged for every message type — this fix only
  changes what's *displayed*.
- `handleSend`/`send()` only forward the new second argument when a
  display twin is actually given; every other call site (typed input,
  suggestion chips, location-choice taps, map-pin drops) keeps its
  existing single-argument call shape, so no unrelated test or behavior
  changed.
- Blast radius: cross-surface (`shared/types/ai.ts`, `shared/utils/
  aiLocationMessages.ts`) but purely additive — a new optional type field
  and a new exported function, no existing export's shape changed.

## 5. User-experience effect

Rider taps a quote option → their own chat bubble now reads "Book the
Economy from 4500 Gordon Rd..." instead of "...(vehicle id
3fa85f64-...)...". Same change mirrored in the admin AI console. No effect
on what gets booked — the model still receives the exact same vehicle id
it always did. Not mid-session-disruptive; this is a one-time echo at the
moment of tapping, not a live-updating value.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/aiLocationMessages.ts` | Added `buildQuoteBookingDisplayMessage`; `buildQuoteBookingMessage` refactored onto a shared private helper, output unchanged | Rider-visible twin of the model-facing message |
| `shared/types/ai.ts` | `AiChatMessage` gained optional `displayContent` | What to render instead of `content`, when present |
| `rider-app/components/bookingProposal.ts` | Re-exports the new function alongside the existing one | Existing re-export pattern for this shared util |
| `rider-app/store/aiChatStore.ts` | `sendMessage` takes an optional `displayText`; stores it as `displayContent` | Wire the display twin into the local echo |
| `rider-app/app/ai-assistant.tsx` | `handleSend` takes an optional `displayText` (only forwarded when given); quote-card tap now passes both messages; bubble renders `displayContent ?? content` | Rider-facing fix |
| `admin-dashboard/src/app/dashboard/ai-console/page.tsx` | `ChatBubble` gained `displayContent`; `send`/`onQuickSend`/`ActionBubble` thread an optional display arg; bubble renders `displayContent ?? content` | Mirror the rider-app fix in the admin console |
| `rider-app/components/__tests__/bookingProposal.test.ts` | 2 new tests for `buildQuoteBookingDisplayMessage` | Cover the new function |
| `rider-app/__tests__/aiAssistantScreen.test.tsx` | Mock + 1 assertion updated for the new second argument | Keep the existing quote-select test accurate |

## 7. Before / after

```ts
// Before — one function serves both the model and the rider
const message = buildQuoteBookingMessage(quote, option);
handleSend(message); // rider sees "...(vehicle id 3fa85f64-...)..."

// After — model text and display text are separate
handleSend(
  buildQuoteBookingMessage(quote, option),        // unchanged — sent to the model
  buildQuoteBookingDisplayMessage(quote, option), // new — rider sees this instead
);
```

## 8. Rollback plan

`git revert` — no data/migration/flag involved. Reverting restores the
prior single-message behavior (UUID visible again), not a regression
relative to what was already live.

## 9. Verification performed

- [x] Automated tests: rider-app `npx jest components/__tests__/bookingProposal.test.ts __tests__/aiAssistantScreen.test.tsx` — 88 passed (2 new, 1 updated for the new call shape)
- [x] rider-app `tsc --noEmit` — clean, 0 errors
- [x] admin-dashboard **real production build** (`npm run build`) — succeeded, `/dashboard/ai-console` compiled with no errors (not just `tsc`/dev server, per this repo's admin-dashboard verification requirement)
- [x] admin-dashboard `eslint` on the changed file — 0 errors, 1 pre-existing warning unrelated to this change (an existing `useEffect` setState pattern a few lines above, untouched by this diff)
- [ ] Manual repro steps followed in staging — not done this session
- [x] Blast-radius grep performed — every real (non-test/doc) caller of `buildQuoteBookingMessage` found and updated: `rider-app/components/bookingProposal.ts`, `rider-app/app/ai-assistant.tsx`, `admin-dashboard/src/app/dashboard/ai-console/page.tsx`

**Not verified**: no live device/browser run — reasoned through the rendering code, not screenshotted (rider-app has no visual-regression tooling; admin-dashboard's Playwright visual-regression suite only seeds `login`, `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings`, `dashboard-rides` — `ai-console` is not one of the 6 seeded pages, so this change has no visual-regression coverage either way).
