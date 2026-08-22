# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 (step 1 of the recommended fix — item stays open overall) |

## 1. Issue / gap identified

No frontend surface uses a schema-validation library — forms validate
input via hand-rolled inline checks with no dedicated test coverage,
including money-adjacent ones like the corporate work-allowance request
form (`rider-app/app/work-allowance-request.tsx`).

## 2. Root cause

No validation library was ever adopted; each screen writes its own inline
`isValid` boolean, which works but leaves the actual accept/reject rule
untested and undiscoverable without reading the screen's render code.

## 3. Fix / remediation

Adopted `zod` on rider-app (first surface, per the item's own migration
order — money/compliance-adjacent forms first) and migrated exactly one
form: `work-allowance-request.tsx`. Extracted the screen's existing inline
check into a colocated `workAllowanceRequestSchema.ts`, with a dedicated
test file pinning 15 accept/reject cases. This is a pure extraction — the
schema's `refine`/`min` rules are written to match the old
`!isNaN(parseFloat(amount)) && parseFloat(amount) > 0 &&
reason.trim().length >= 5` check exactly, not a new or different rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** One new file (`workAllowanceRequestSchema.ts`),
  one new test file, and a 2-line change to `work-allowance-request.tsx`
  (import + swap the inline boolean for the new helper call). Grepped the
  rest of `rider-app/` for any other importer of the old inline logic or
  this screen's `isValid` variable — none exists; the screen owns its own
  validation state, nothing else reads it.
- **What could regress:** the submit button's disabled/enabled state on
  this one screen. Verified this is unchanged: the new schema's
  `safeParse(...).success` returns `true`/`false` for the exact same input
  shapes the old boolean did (15 test cases cover both directions,
  including the whitespace-trim edge case). No other screen, endpoint, or
  store action is touched.
- **Other consumers of `zustand`'s `submitRequest`/`useWorkProfileStore`:**
  unaffected — this change only touches client-side gating of when the
  submit button is enabled, not what gets sent to the backend or how the
  backend validates it. `backend/routes` still does its own server-side
  validation independently (unchanged).
- No interaction with the ride state machine, money/wallet deltas
  (Stripe), RLS, or any background loop.

## 5. User-experience effect

**None, by design.** Rider-facing: this is the "Request More Funds" screen
under the corporate work-profile flow. The submit button enables/disables
under the exact same conditions as before — same amount rule (>0, numeric),
same reason rule (≥5 trimmed characters). Not visible mid-session to
anyone already on this screen differently than before, since the rule
itself didn't change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/package.json` | Added `zod` dependency | First schema-validation library on any frontend surface |
| `rider-app/utils/workAllowanceRequestSchema.ts` | New — colocated zod schema + `isWorkAllowanceRequestValid` helper | Extract inline validation into a testable, named schema |
| `rider-app/utils/__tests__/workAllowanceRequestSchema.test.ts` | New — 15 accept/reject test cases | Close "validation-rule coverage is invisible" gap for this form |
| `rider-app/app/work-allowance-request.tsx` | Swapped inline `isValid` boolean for `isWorkAllowanceRequestValid(...)` call | Use the new schema instead of duplicating the rule |
| `docs/change-log/2026-08-22-b39-work-allowance-zod-pilot.md` | New change-log | Required Change Impact & Risk Log |
| `ACTION_ITEMS.md` | B39 — step 1 done, item stays open | Backlog hygiene |

## 7. Before / after

```tsx
// Before (app/work-allowance-request.tsx)
const parsedAmount = parseFloat(amount);
const isValid = !isNaN(parsedAmount) && parsedAmount > 0 && reason.trim().length >= 5;
```

```tsx
// After
const parsedAmount = parseFloat(amount);
const isValid = isWorkAllowanceRequestValid(amount, reason);
```

```ts
// New: utils/workAllowanceRequestSchema.ts
export const workAllowanceRequestSchema = z.object({
  amount: z.string().refine(v => !isNaN(parseFloat(v)) && parseFloat(v) > 0, { ... }),
  reason: z.string().trim().min(5, { ... }),
});

export function isWorkAllowanceRequestValid(amount: string, reason: string): boolean {
  return workAllowanceRequestSchema.safeParse({ amount, reason }).success;
}
```

## 8. Rollback plan

**`git-revert-safe`** — pure client-side validation logic, no data,
no migration, no backend change. A plain `git revert` fully restores the
old inline check with identical behavior (verified byte-for-byte
equivalent above), and `zod` itself is an additive dependency with no
other consumer yet — removing it alongside the revert is safe.

## 9. Verification performed

- [x] Automated tests run: `npx jest utils/__tests__/workAllowanceRequestSchema.test.ts` → 15 passed. Full suite: `npx jest` → 575 passed / 575 total across 71 suites, 0 failed, 0 regressions.
- [ ] Manual repro steps followed in staging — not performed; no staging deploy available this pass, relied on the automated test suite + a real production build instead.
- [x] Blast-radius grep performed: searched `rider-app/` for other importers of the old inline check pattern or this screen's `isValid` — none found; isolated to this one screen.
- [x] Reviewed against relevant CLAUDE.md convention(s): pre-merge gate #2 (additive over destructive) and #5 (no silent behavior change) — this is a pure extraction with byte-for-byte equivalent behavior, not a rule change.
- [ ] Feature-flagged — not flagged. Justification: zero behavior change (verified equivalent), isolated to one screen's client-side validation gating, no server-side/money-path change. CLAUDE.md's flag guidance targets user-visible/non-trivial changes; this form's actual accept/reject behavior is unchanged, so there is nothing for a flag to gate.

## What was NOT verified

- Not exercised against a real device/simulator — no manual/visual QA of the actual screen, matching the standing gap this repo's own docs already flag for rider-app (no visual-regression tooling exists there).
- The rest of B39's scope (driver-app, admin-dashboard, every other form across all three apps) was deliberately not touched this pass — this is step 1 of a multi-step, per-item migration the backlog entry itself calls for, not a full close.
- Whether `zod`'s bundle-size impact on rider-app's web export is meaningful — not measured; `zod` is a widely-used, tree-shakeable library and this is its first and only current usage.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, zero behavior change to restore)
- [x] Blast radius is stated, not assumed (isolated to one screen + one new schema file, grep-verified)
- [x] No silent behavior change to an already-shipped flow — verified byte-for-byte equivalent accept/reject behavior, stated explicitly above
