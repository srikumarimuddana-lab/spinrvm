# Change Impact & Risk Log — admin AI console: suggestion tap sends coordinates

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (AI location-selection loop fix, commit 5 of series) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/rider-ai-location-selection-yn0mem` |
| Related issue or gap ID | Same defect class as the rider-app suggestion tap (identical inline prose-only message) |

## 1. Issue / gap identified

The admin AI console's location-suggestion quick-send reproduced the rider loop: it sent `Use <label> as my dropoff.` with no coordinates.

## 2. Root cause

Same as the rider app — inline label-only message; the console mirrors rider cards but never adopted the coordinate-carrying format.

## 3. Fix / remediation

The suggestion button now sends `buildLocationChoiceMessage(candidate, role)` from `@spinr/shared/utils/aiLocationMessages` (commit 3).

## 4. Risk & impact on existing functionality

- Blast radius: one component (`ActionBubble` in `ai-console/page.tsx`), one action type. The console's fare-quote quick-send still uses its older prose-only format — a separable known gap (backlogged; same fix class using the shared quote builder).
- The shared import follows the proven `@spinr/shared/utils/routeSegments` pattern; production build verified.

## 5. User-experience effect

Internal-admin-facing only: console suggestion taps now behave like the fixed rider app (no clarification loop while testing rider scenarios).

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/ai-console/page.tsx` | Suggestion quick-send uses shared builder; import added | Send tapped candidate's coordinates |

## 7. Before/after

```tsx
// before
onClick={() => label && onQuickSend(`Use ${label}${suffix}.`)}
// after
const message = buildLocationChoiceMessage(c, action.location_role);
onClick={() => message && onQuickSend(message)}
```

## 8. Rollback plan

`git revert` + Vercel redeploy.

## 9. Verification performed

- `npx vitest run` — 138 passed.
- **Real production build run**: `npm run build` (Next.js) — succeeded.

## 10. What was NOT verified

- No component test asserts the console's quick-send string (the console has no test file for `ActionBubble`); the message format itself is covered by the shared builder's 8 unit tests.
- Not exercised against a live model provider in this session.
