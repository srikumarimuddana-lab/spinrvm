# Change Impact & Risk Log — admin AI console quote-card tap now carries coordinates + vehicle id (AI9)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude (AI9, same audit series as AI3/AI4/AI12) |
| Surface(s) | shared, admin-dashboard, rider-app (re-export only, no behavior change) |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/ai9-admin-quote-card-coords` |
| Related issue or gap ID | `ACTION_ITEMS.md` AI9 (2026-07-28 audit, "AI assistant / MCP guardrail backlog") |

## 1. Issue / gap identified

Tapping a fare-quote option card in the admin AI console (super-admin testing surface for the rider/driver assistant) sends a prose-only message ("Book the Economy from 4500 Gordon Rd, Regina to 4325 Wakeling St, Regina.") that drops the quote's exact `[lat,lng]` coordinates and vehicle id — the same defect class already fixed for location-suggestion cards (2026-07-28, `docs/change-log/2026-07-28-ai-suggestion-tap-builder.md` / `-admin.md`), but never applied to the quote card itself.

## 2. Root cause

The admin console's quote-card `onClick` (`admin-dashboard/src/app/dashboard/ai-console/page.tsx`, then lines ~130-132) built its own inline template string from `action.pickup_address` / `action.dropoff_address` text only. It was never upgraded to the self-contained `[lat,lng]` format that the rider app's `buildQuoteBookingMessage` (`rider-app/components/bookingProposal.ts`) already used for the identical card. Because the assistant's next turn sees only message text (no tool-call history), a prose-only tap forces the model to re-geocode both addresses — a third independent geocode on top of the original quote and the confirm card, which can move pins/prices between what was priced and what gets booked (the same failure mode the 2026-07-28 suggestion-card fix addressed).

## 3. Fix / remediation

- Moved `buildQuoteBookingMessage` (pure function, no React dependency) from `rider-app/components/bookingProposal.ts` into `shared/utils/aiLocationMessages.ts`, alongside the existing `buildLocationChoiceMessage` — same file, same self-contained-tap-message contract, same doc-comment convention.
- `rider-app/components/bookingProposal.ts` now re-exports it: `export { buildQuoteBookingMessage } from '@shared/utils/aiLocationMessages';` — a thin shim so every existing rider-app import/call site is unchanged (verified below).
- `admin-dashboard/src/app/dashboard/ai-console/page.tsx`'s quote-card `onClick` now calls `buildQuoteBookingMessage(action, q)` (imported from `@spinr/shared/utils/aiLocationMessages`, the same package path the admin console already used for `buildLocationChoiceMessage`) instead of building its own prose template.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface but narrow — two consumer files, both audited below. No behavior change for the rider app; a genuine (intended) behavior change for the admin console.**

Every importer of `buildQuoteBookingMessage`, found by repo-wide grep:
1. `rider-app/app/ai-assistant.tsx` — imports from `'../components/bookingProposal'` (unchanged path). Calls `handleSend(buildQuoteBookingMessage(quote, option))` on a quote-card tap. **Unaffected** — the re-export shim keeps this import resolving to the identical function (same source moved, same signature, same behavior — proven by the pre-existing `bookingProposal.test.ts` suite passing unchanged against the re-exported symbol).
2. `rider-app/components/__tests__/bookingProposal.test.ts` — imports from `'../bookingProposal'` (unchanged path), 4 `describe('buildQuoteBookingMessage', …)` cases. **Unaffected**, ran green post-move (see Verification).
3. `admin-dashboard/src/app/dashboard/ai-console/page.tsx` — **new consumer** (this PR). This is the fix itself, not a regression risk to something else.

Every importer of `shared/utils/aiLocationMessages.ts` (the file gaining the new export), found by repo-wide grep:
1. `rider-app/app/ai-assistant.tsx` — imports `buildLocationChoiceMessage` only. **Unaffected** — that export's implementation is untouched; the file only gained an additional export.
2. `rider-app/components/__tests__/aiLocationMessages.test.ts` — imports `buildLocationChoiceMessage` only, 8 cases. **Unaffected**, ran green (see Verification).
3. `rider-app/components/bookingProposal.ts` — new re-export consumer (this PR, see above).
4. `admin-dashboard/src/app/dashboard/ai-console/page.tsx` — already imported `buildLocationChoiceMessage` from this file (2026-07-28 fix); now additionally imports `buildQuoteBookingMessage` (this PR).

No backend file, no other admin-dashboard page, and no driver-app file references either symbol (grepped `driver-app/` — zero matches).

Interaction with ride state machine / money / background loops: none. This only changes the *text* of a chat message a super-admin's tap sends to the assistant; the assistant still only *proposes* a booking (`booking_proposal` action), and the rider/driver-side `createRide()` flow — the only code path that actually books — is untouched. No wallet, Stripe, or ride-state code is in this diff.

## 5. User-experience effect

- **Internal-admin facing only.** Riders and drivers see no change — the rider app's quote-card tap message is byte-for-byte identical before and after (proven by the unchanged `bookingProposal.test.ts` assertions passing against the re-exported function).
- Super-admins using the AI console: tapping a quote option now sends the same structured `[lat,lng]`-bearing message the rider app sends, instead of a prose-only one. This is the intended UX change (AI9's fix) — before, an admin testing the assistant on a rider's behalf could trigger a re-geocode drift the rider's own client never would; after, the console reproduces the rider's exact behavior, which is the console's stated purpose ("Test the assistant as a rider... appear in the user's app").
- Not visible mid-session to anyone already using the app — the AI console is a super-admin-only testing surface (`isSuperAdmin` gate), not a rider/driver-facing screen.
- No new user-visible copy/notification beyond the chat message text itself, which is sent to the backend as a normal user turn, not rendered as new UI.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/aiLocationMessages.ts` | Added `buildQuoteBookingMessage` export (moved verbatim from `rider-app/components/bookingProposal.ts`), added `AiAction`/`FareQuoteOption` type imports | Single shared source for both apps' self-contained tap-message contract, per AI9 |
| `rider-app/components/bookingProposal.ts` | Removed the local `buildQuoteBookingMessage` implementation and its now-unused `FareQuoteAction` type alias / `FareQuoteOption`/`AiAction` imports; added `export { buildQuoteBookingMessage } from '@shared/utils/aiLocationMessages';` | Keep existing rider-app import paths working unchanged while the implementation lives in `shared/` |
| `admin-dashboard/src/app/dashboard/ai-console/page.tsx` | Quote-card `onClick` now calls `buildQuoteBookingMessage(action, q)` (imported from `@spinr/shared/utils/aiLocationMessages`) instead of an inline prose template; removed the now-dead `from`/`to`/`promo` local variables | Fix AI9 — carry `[lat,lng]` + vehicle id on quote-card tap, matching the rider app |
| `ACTION_ITEMS.md` | Marked AI9 `[x]` with a "Done" note | Close out the backlog item |
| `docs/change-log/2026-08-01-ai9-admin-quote-card-coords.md` | This file | Mandatory Change Impact Log for a live-tested admin surface + shared-utility change |

## 7. Before / after

Admin console quote-card tap handler (`admin-dashboard/src/app/dashboard/ai-console/page.tsx`):

```tsx
// Before
const from = action.pickup_address ? ` from ${action.pickup_address}` : "";
const to = action.dropoff_address ? ` to ${action.dropoff_address}` : "";
const promo = q.promo_code ? ` with promo ${q.promo_code}` : "";
return (
    <button
        key={q.vehicle_type_id ?? i}
        onClick={() => onQuickSend(`Book the ${q.vehicle_type ?? "recommended option"}${from}${to}${promo}.`)}
        ...
```

```tsx
// After
return (
    <button
        key={q.vehicle_type_id ?? i}
        onClick={() => onQuickSend(buildQuoteBookingMessage(action, q))}
        ...
```

Example resulting message change: `"Book the Economy from 4500 Gordon Rd, Regina to 4325 Wakeling St, Regina with promo SAVE75."` → `"Book the Economy (vehicle id vt-1) from 4500 Gordon Rd, Regina [50.40790,-104.65010] to 4325 Wakeling St, Regina [50.44970,-104.53450] with promo SAVE75, total $20.92."`

## 8. Rollback plan

`git revert` of this commit is sufficient and safe here: the change touches only client-side message-string construction (no migration, no data write, no Stripe/wallet/ride-state mutation). Reverting restores the admin console's old prose-only template and removes the new shared export; the rider app is unaffected either way since its call site and behavior never change. No feature flag needed — this is an internal super-admin testing tool, not a rider/driver-facing flow, so a same-day redeploy via revert is an acceptable rollback path per CLAUDE.md's guidance ("acceptable only for genuinely isolated, low-risk changes").

## 9. Verification performed

- [x] Automated tests run: `npx jest components/__tests__/bookingProposal.test.ts components/__tests__/aiLocationMessages.test.ts` in `rider-app/` — **42 passed, 0 failed** (34 in `bookingProposal.test.ts` including the 4 `buildQuoteBookingMessage` cases now exercising the re-exported shared function; 8 in `aiLocationMessages.test.ts` for `buildLocationChoiceMessage`, unaffected but re-run to confirm the file still loads/typechecks with the new export added).
- [x] Real production build run for `admin-dashboard`: `npm run build` (Next.js 16.2.11, Turbopack) — **compiled successfully**, TypeScript pass finished with no errors, all 70 routes generated including `/dashboard/ai-console`, `.next/BUILD_ID` produced. This is the actual `next build` used for deployment, not `tsc --noEmit` alone or a dev server.
- [x] `rider-app`: `npx tsc --noEmit -p tsconfig.json` — clean, no errors. (Rider app has no `npm run build` script — it ships via Expo EAS, gated on a `[build]` commit-message tag per `CLAUDE.md`'s Deployment section, and EAS was intentionally **not** triggered for this change since it's additive-only for that surface. `tsc --noEmit` + the passing test suite above are the checks available short of a full EAS build.)
- [x] `npx eslint src/app/dashboard/ai-console/page.tsx` in `admin-dashboard/` — 0 errors, 1 pre-existing warning unrelated to this diff (a `react-hooks/set-state-in-effect` warning on an untouched `useEffect` at line 325, not in the code this PR changed).
- [x] Blast-radius grep performed: every importer of `buildQuoteBookingMessage` and of `shared/utils/aiLocationMessages.ts` repo-wide (see Section 4) — also grepped `driver-app/` explicitly, zero matches for either symbol.
- [x] Reviewed against relevant `CLAUDE.md` conventions: not a ride-state, money, or RLS change — no state-machine/wallet dry-run applicable; PIPEDA — the message still only carries coordinates the assistant/backend already had (same `[lat,lng]` transport the backend's `keep_trip_pins` PII-scrub rule already preserves for the suggestion-card and map-pin flows); Observability — no new logging added, none needed.
- [ ] Feature-flagged: **not applicable/not done** — this is a super-admin-only internal testing tool (`isSuperAdmin` gate already restricts the whole page), not a 3+-page shared UI surface, and the change is additive to what the tool already does (make it match the rider app more faithfully). Judged low enough risk for a direct-edit rollout per CLAUDE.md's flagging guidance; escalate-if-unsure was considered and the isolated blast radius (2 consumer files, both audited above) resolved the doubt.

## 10. What was NOT verified

- **No manual staging click-through** was performed (no running backend/Supabase instance in this environment) — verification is build + unit-test based only, not an end-to-end click of the admin console against a live backend.
- **No visual/snapshot regression tooling exists for `admin-dashboard`** (confirmed by grep: no Percy/Chromatic/Playwright-screenshot config found, no `toMatchSnapshot` usage in this file) — this is a standing gap per `CLAUDE.md` §6, not something newly discovered by this change. The diff is a JS logic change (which string gets sent), not a layout/styling change, so a visual diff would show nothing new regardless; this was reasoned about, not screenshotted, and is explicitly flagged here rather than left implicit.
- **EAS mobile build was not run** for `rider-app` (see Verification §9) — the rider-app change is a pure re-export with an already-green test suite proving byte-identical output, and mobile builds are gated behind an explicit `[build]` commit tag per this repo's deployment convention; not triggering one was a deliberate scope decision, not an oversight.
- **Backend `/admin/ai` console endpoint behavior was not exercised** — this fix is entirely client-side message construction; the backend receives the same shape of user-turn text it already accepts from the rider app (a string), so no backend-side testing was judged necessary, but it was not independently re-verified against a running backend in this session.
