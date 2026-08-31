# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 15, highest-priority candidate from the user-directed broader sweep |

## 1. Issue / gap identified

`ride-completed.tsx` computes the effective custom-tip dollar amount via
`customTip ? parseFloat(customTip) || 0 : 0`, duplicated across 4 call
sites, with no dedicated test coverage. **This expression has a real bug**:
`parseFloat('-5') || 0` evaluates to `-5` (a non-zero number is truthy in
JS), so the `|| 0` fallback does not catch a negative value. A rider
typing `-5` into the custom-tip field could have had `-$5` flow into:
- `confirmPayment` — the Stripe 3DS/SCA confirmation amount
- `rateRide(..., tipAmount > 0 ? tipAmount : undefined)` — driver rating/tip credit
- the "Pay $X & Done" button label and the change-card handoff URL

## 2. Root cause

Ad hoc validation predates any schema-validation library adoption on this
form; `rider-app` already has `zod` (added in B39 step 1), but this
expression was never migrated, and its truthy-fallback pattern was never
audited for the negative-number edge case.

## 3. Fix / remediation

New colocated `rider-app/utils/customTipSchema.ts` extracts the
computation into `getCustomTipAmount(customTip: string): number`, used at
all 4 call sites. **This is explicitly not a pure byte-for-byte
extraction** — per the user's direction (this session, 2026-08-31), it
also fixes the negative-value bug: the function rejects negative and
non-finite parsed values, clamping them to `0`. This is the same "no tip"
outcome the original code already produced for every other invalid input
(empty string, whitespace, non-numeric text) — the fix narrows the input
space that maps to a non-zero result, it does not introduce any new
non-zero output the original code never produced for a differently-typed
input.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, 4 call sites, all replaced.**
  Grepped `rider-app` for the exact duplicated expression
  (`customTip ? parseFloat(customTip)`); confirmed all 4 occurrences are
  in `ride-completed.tsx` and all 4 were updated. No other file
  duplicates this logic. The file's separate `toNum` helper (used for
  parsing server-returned Decimal-as-string money fields like
  `total_fare` — not user-typed input) was deliberately left untouched;
  it is a different function serving a different purpose (API-response
  parsing, not user-input validation) and out of scope for this change.
- **Could this regress a flow that currently works?** For every
  non-negative, finite input (the entire domain the app's own
  `keyboardType="decimal-pad"` custom-tip field can normally produce),
  `getCustomTipAmount` returns byte-for-byte the same value as the
  original expression — verified against 6 accept/reject test cases
  covering positive amounts, zero, empty string, non-numeric text, and
  the negative-value fix. The only behavior change is for negative
  input, which the original code never handled correctly (it would have
  charged a negative tip, an outcome that was never a deliberate design
  choice — no test, comment, or product spec anywhere references
  negative tips as valid).
- **Money-path interaction — this is the core of the change.** `tipAmount`
  feeds `confirmPayment` (Stripe SCA/3DS confirmation) and `rateRide`
  (driver earnings credit). Before this fix, a crafted or accidentally-
  typed negative custom tip could have reduced the charged amount below
  the fare or produced a negative driver-earnings credit — a real
  payment-integrity gap. This fix closes it at the source, before the
  amount reaches either call.
- **Dispatch / ride state machine:** not implicated — this is a
  post-completion payment/rating screen, no interaction with the active
  ride state machine.

## 5. User-experience effect

Rider-facing, on the post-ride rating/tip screen. For every value a rider
can realistically enter via the on-screen decimal-pad keyboard, the
behavior is unchanged: entering "5" still tips $5, entering nothing or
non-numeric text still tips $0. The only change is that a negative value
(only reachable via paste, an external keyboard, or programmatic
`value`/autofill — not the on-screen decimal-pad) now also produces $0
instead of a negative charge; this is a silent clamp with no new error
message, matching the original's silent-fallback UX for every other
invalid input. Not visible mid-session in any way a rider would notice
under normal use.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/utils/customTipSchema.ts` | New file — `getCustomTipAmount` (extracted + bug-fixed) | Pulls the 4 duplicated inline expressions into a colocated, independently testable module, and fixes the negative-tip gap |
| `rider-app/utils/__tests__/customTipSchema.test.ts` | New file — 6 accept/reject unit tests | Pins the extracted behavior, including the negative-value fix, so a future edit can't silently reintroduce the gap |
| `rider-app/app/ride-completed.tsx` | All 4 occurrences of the inline expression replaced with `getCustomTipAmount(customTip)` calls; import added | Same behavior for all valid input, fixed behavior for the negative-value bug |

## 7. Before / after

```ts
// Before (×4, duplicated at lines 325, 352, 445, 879)
const tipAmount = effectiveTip || (customTip ? parseFloat(customTip) || 0 : 0);
// BUG: parseFloat('-5') || 0 === -5 (truthy), so a negative custom tip
// was never caught and would flow into confirmPayment/rateRide.
```

```ts
// After
import { getCustomTipAmount } from '../utils/customTipSchema';

const tipAmount = effectiveTip || getCustomTipAmount(customTip);
// getCustomTipAmount rejects negative/non-finite parsed values,
// clamping them to 0 — the same "no tip" outcome as every other
// invalid input.
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no schema/table change, no feature
flag. Reverting restores the original 4 inline expressions exactly,
**including the negative-tip bug** — this is a real regression risk of a
rollback, not just a UI-behavior reversion, so a rollback here should be
paired with re-applying at least the bug fix even if the extraction
itself is reverted. No backend or Stripe-side change to roll back; no
already-applied production data (this is a client-side pre-submit
computation, not a completed charge) is affected.

## 9. Verification performed

- [x] Automated tests run — unit only: `npx jest utils/__tests__/customTipSchema.test.ts`
  — 6/6 pass. Full suite: `npx jest` — 1896/1902 pass; the 6 failures are
  all in `__tests__/rideDetailsScreen.test.tsx` (a different, unrelated
  screen) and were confirmed pre-existing on `main` by re-running with
  this change `git stash`-ed out (same 5-6 failures reproduce
  identically without this diff).
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. The negative-value fix in particular was not
  exercised against a real Stripe test charge — verified only via the
  unit test pinning `getCustomTipAmount('-5') === 0`.
- [x] Blast-radius grep performed — searched `rider-app` for the exact
  duplicated expression; all 4 occurrences found and updated, no others
  exist. The file's unrelated `toNum` helper (API-response parsing) was
  explicitly identified and left untouched.
- [x] Reviewed against relevant CLAUDE.md convention(s) — money: this
  touches a Decimal-adjacent amount feeding Stripe's `confirmPayment` and
  a driver-earnings credit via `rateRide`; per CLAUDE.md's Decimal-only
  money-math rule, note that `tipAmount` here is a plain JS `number`
  (not `Decimal`) both before and after this change — this extraction
  does not introduce that pattern, it is pre-existing in this file's
  client-side tip-amount handling (server-side money math uses
  Python `Decimal`; this is the client's pre-submit display/gate value
  only, not the source of truth for the charge, which the backend
  computes and validates independently).
- [x] Money/state-machine dry run (release-gate item 4): described above
  as a concrete before/after scenario (negative tip → `-$5` charge
  attempt, now → `$0`); not exercised against `mock_supabase_client`
  fixtures since this is a rider-app (React Native) change with no
  backend fixture harness — the backend's own independent validation of
  the charge amount (outside this diff's scope) is the actual safety net
  this client-side fix is layered in front of, not a replacement for it.

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files: clean,
no errors or warnings. **Real production build**
(`npm run build:web` → `expo export --platform web`) completed
successfully — not just `tsc`/dev server, per CLAUDE.md's explicit
requirement for rider-app changes.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert` — see
  Section 8's caveat about the bug reintroduction risk)
- [x] Blast radius is stated, not assumed (grepped, isolated to 4 call
  sites in one file, all updated)
- [ ] No silent behavior change to an already-shipped flow — **this is a
  deliberate exception, explicitly called out**: the negative-tip fix
  IS a behavior change on an already-shipped screen, made with explicit
  user direction (not unilaterally) after the gap was surfaced and
  flagged, per CLAUDE.md's "Escalate, don't silently ship, when in
  doubt" gate. This is not a silent change — it is documented here, in
  ACTION_ITEMS.md, and was confirmed with the user before implementation.

## What was NOT verified

- Not tested against a real Stripe charge or the backend's own amount
  validation — this session has no staging/live-Stripe access. The fix
  was verified only at the client-side computation layer; whether the
  backend already independently rejects a negative tip amount (a
  defense-in-depth question) was not investigated as part of this change.
- No visual regression tooling exists for rider-app (per CLAUDE.md, no
  automated visual/snapshot regression tooling exists for this surface)
  — not applicable here regardless, since this change has no visual/UI
  surface change (same input field, same button labels).
- The other 20 candidates from the broader sweep (driver-app's
  `vehicle-info.tsx` negative-year bug included) remain open — this step
  addresses only the single highest-risk finding.
