# Change Impact & Risk Log — shared builder for AI location-suggestion tap messages

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (AI location-selection loop fix, commit 3 of series) |
| Surface(s) | shared (consumed by rider-app and admin-dashboard in follow-up commits) |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Rider report: infinite "check the exact street address" loop after tapping a suggestion |

## 1. Issue / gap identified

Tapping a location-suggestion card in the AI chat (rider app and admin console) sends a prose-only message ("Use 655 Albert St as my dropoff."), discarding the candidate's already-resolved coordinates.

## 2. Root cause

The suggestion card was the one tap producer never upgraded to the self-contained `[lat,lng]` message format that the quote card (`buildQuoteBookingMessage`) and map-pin picker already use. With text-only history, the model must re-geocode the address, re-tripping the imprecise-address gate every turn.

## 3. Fix / remediation

New `shared/utils/aiLocationMessages.ts` exporting `buildLocationChoiceMessage(candidate, role)` → `Use <label> [lat,lng] as my pickup/dropoff.` (null when no label), plus an exports-map entry. No consumer is switched in this commit — purely additive.

## 4. Risk & impact on existing functionality

- Blast radius: zero — new file + additive `exports` entry in `shared/package.json`; nothing imports it until commits 4–5. The exports pattern mirrors the existing `./utils/routeSegments` entry already consumed by both apps.
- `toFixed(5)` output deliberately matches the backend `_BRACKETED_COORDS` regex (`backend/ai/pii.py`) so the PII scrubber preserves the pair — pinned by a regex test.

## 5. User-experience effect

None in this commit (no consumer wired).

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `shared/utils/aiLocationMessages.ts` | New builder | Single source for the tap-message contract |
| `shared/package.json` | exports entry | Make it importable from both apps |
| `rider-app/components/__tests__/aiLocationMessages.test.ts` | 8 unit tests | Format, roles, label fallback, backend-regex compatibility |

## 7. Before/after

Additive only — no behavior change until consumers land.

## 8. Rollback plan

`git revert`; nothing depends on it until commits 4–5 (which would revert with it).

## 9. Verification performed

- `npx jest components/__tests__/aiLocationMessages.test.ts` in rider-app — 8 passed (tests placed there because the repo has no runner wired to `shared/utils/__tests__/`).

## 10. What was NOT verified

- Admin-dashboard import resolution is verified in commit 5 (its own build), not here.
