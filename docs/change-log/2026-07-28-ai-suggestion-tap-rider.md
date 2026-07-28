# Change Impact & Risk Log — rider app: AI suggestion tap sends coordinates

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (AI location-selection loop fix, commit 4 of series) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Rider report: infinite "check the exact street address" loop after tapping a Canadian Tire suggestion |

## 1. Issue / gap identified

Tapping a location suggestion in the AI chat sent `Use <address> as my dropoff.` with no coordinates, forcing the assistant to re-geocode and loop on imprecise street addresses.

## 2. Root cause

`rider-app/app/ai-assistant.tsx` built the tap message inline from the label only, discarding `candidate.lat/lng` — the one tap producer never upgraded to the self-contained `[lat,lng]` format the quote card and map-pin picker already use.

## 3. Fix / remediation

The suggestion-tap handler now sends `buildLocationChoiceMessage(candidate, role)` (commit 3's shared builder): `Use 655 Albert St, Regina… [50.44079,-104.61802] as my dropoff.` Prompt rule 6b (commit 1, deployed first) instructs the model to use these coordinates verbatim and never re-geocode.

## 4. Risk & impact on existing functionality

- Blast radius: single screen. `LocationSuggestionsCard` is rendered only by `ai-assistant.tsx` (grep: one consumer). No shared component/hook behavior changed for other screens.
- Server-side safety unchanged: `_dropoff_pair_refusal` / `_reconcile_pickup` still validate every quote/proposal pair; a tapped candidate passes because the label geocode is biased at the tapped pin.
- The PII scrubber preserves the bracketed pair (`keep_trip_pins=True` chat path) — pinned by commit 1's test.
- Old app builds keep sending prose-only taps until an EAS build ships; commit 1's "never re-ask twice" rule breaks the loop for them via the map-pin exit.

## 5. User-experience effect

Rider-facing, visible mid-session once built: tapping a suggestion now leads directly to a quote instead of repeated "check the exact street address" requests. The tapped message bubble now visibly includes the bracketed coordinates (same as the existing quote-card tap and map-pin messages — an accepted, established pattern).

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/app/ai-assistant.tsx` | Suggestion-tap handler uses `buildLocationChoiceMessage`; import added | Send the chosen candidate's coordinates |

## 7. Before/after

```tsx
// before
const label = candidate.address || candidate.name;
if (!label) return;
const role = item.action?.type === 'location_suggestions' ? item.action.location_role : null;
const suffix = role === 'pickup' ? ' as my pickup' : role === 'dropoff' ? ' as my dropoff' : '';
handleSend(`Use ${label}${suffix}.`);
// after
const role = item.action?.type === 'location_suggestions' ? item.action.location_role : null;
const message = buildLocationChoiceMessage(candidate, role);
if (!message) return;
handleSend(message);
```

## 8. Rollback plan

`git revert` + new EAS build. Backend needs no rollback: prompt rule 6b handles both message formats, so mixed client versions are safe in both directions.

## 9. Verification performed

- `npx tsc --noEmit` — clean.
- Full rider-app `npx jest` suite run in this session (builder tests + existing aiChatStore/bookingProposal suites).
- No EAS production build run in this session ([build] tag reserved for release); `tsc` + jest are the stated verification level.

## 10. What was NOT verified

- Not exercised against a live model provider or live backend — end-to-end tap→quote behavior is verified manually in staging via the admin AI console after commit 5.
- No visual regression tooling exists in this repo; the changed message bubble content was reasoned about, not screenshotted.
