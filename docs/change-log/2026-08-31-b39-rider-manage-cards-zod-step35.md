# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | rider-app |
| Domain (Sentry tag) | payments |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 35, penultimate candidate in the 21-candidate broader sweep |

## 1. Issue / gap identified

`app/manage-cards.tsx`'s `handleAddCard` validates a Stripe card-addition
form via three sequential inline checks with no dedicated test coverage:
card-details-complete state (set by Stripe's card element), cardholder
name presence, and Stripe SDK readiness (`createPaymentMethod`
undefined until the SDK finishes initializing). **No correctness bug
was found** — all three checks are logically sound. This step is a
pure extraction.

## 2. Root cause

Ad hoc validation predates zod adoption on this screen, consistent with
every other B39 candidate.

## 3. Fix / remediation

New colocated `rider-app/utils/manageCardsSchema.ts` extracts the three
checks into `isCardDetailsComplete`, `isCardholderNameValid`,
`isStripeReady`, and a combined `getManageCardsFormError` that returns
the same `{title, message}` toast pair for the first failing check, in
the same priority order — a byte-for-byte behavioral mirror of the
original sequential `if` blocks.

**Incidental fix (naming only, not a behavior change):** the call
site's local `error` variable (holding the validation-result object)
was renamed to `validationError`. The repo's `no-restricted-syntax`
eslint rule flags any `error.message` access to catch raw
API-error-message surfacing (per CLAUDE.md's error-handling
conventions); this validation object's `.message` field coincidentally
matched that text pattern despite not being an API error at all. Fixed
by renaming the variable, not by suppressing the rule — the rule's
intent (never show a raw Stripe/API error string) is unaffected, since
this code path was never an API error to begin with.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, one function
  (`handleAddCard`).** Grepped `rider-app` for the toast copy strings;
  only two other matches — a `.metro-cache` build artifact (not
  source, irrelevant) and `__tests__/manageCardsScreen.test.tsx`, an
  existing UI test that asserts on this exact toast copy. Re-ran that
  test file (31/31 pass) to confirm the extraction didn't change its
  observable behavior.
- **Could this regress a flow that currently works?** For every input
  the original three checks accept or reject, `getManageCardsFormError`
  returns byte-for-byte the same result — verified against 9
  accept/reject test cases covering all three predicates individually
  and the aggregate's priority order.
- **Money-path interaction:** `createPaymentMethod` and the subsequent
  `POST /payments/cards` call add a real payment method to the rider's
  Stripe customer. This validation gate is the only client-side check
  before that call; the fix does not change what reaches it for any
  previously-valid or previously-invalid input.
- **Dispatch / ride state machine:** not implicated — this is a
  card-management screen (also reachable from a stuck-ride payment
  retry flow via `payForRide`/`payRideWithCard`, unaffected by this
  diff since those functions are downstream of the validation gate,
  not touched).

## 5. User-experience effect

Rider-facing, on the "Add Card" form (both the standalone card-manager
screen and the stuck-ride payment-retry entry point). No behavior
change for any input — same toast titles/messages, same validation
order, same accept/reject boundary. Not visible mid-session in any way
a rider would notice.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/utils/manageCardsSchema.ts` | New file — 3 predicates + `getManageCardsFormError` | Pulls the inline three-check block into a colocated, independently testable module |
| `rider-app/utils/__tests__/manageCardsSchema.test.ts` | New file — 9 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently change the validation boundary |
| `rider-app/app/manage-cards.tsx` | `handleAddCard`'s 3 sequential `if` blocks replaced with a call to `getManageCardsFormError`; local `error` variable renamed to `validationError` (eslint false-positive fix); import added | Same behavior, now covered by tests, lint-clean |

## 7. Before / after

```ts
// Before
const handleAddCard = async () => {
  if (!cardDetailsComplete) { showToast('Missing Details', 'Please enter complete card details', 'warning'); return; }
  if (!cardName.trim()) { showToast('Missing Name', 'Please enter the cardholder name', 'warning'); return; }
  if (!createPaymentMethod) {
    showToast('Payments unavailable', 'Payment processing is still starting up. Try again in a moment.', 'warning');
    return;
  }
  setSaving(true);
  // ...
};
```

```ts
// After
import { getManageCardsFormError } from '../utils/manageCardsSchema';

const handleAddCard = async () => {
  const validationError = getManageCardsFormError(cardDetailsComplete, cardName, createPaymentMethod);
  if (validationError) {
    showToast(validationError.title, validationError.message, 'warning');
    return;
  }
  setSaving(true);
  // ...
};
```

## 8. Rollback plan

`git-revert-safe`. No data migration, no schema/table change, no feature
flag. Reverting restores the original inline checks exactly — no bug
is being fixed in this step, so a revert carries no correctness
regression risk, only a loss of test coverage. No backend or Stripe-side
change to roll back; no already-applied production data (this is a
client-side pre-submit validation gate, not a completed Stripe API
call) is affected.

## 9. Verification performed

- [x] Automated tests run — unit only:
  `npx jest utils/__tests__/manageCardsSchema.test.ts` — 9/9 pass.
  Existing UI test `__tests__/manageCardsScreen.test.tsx` re-run: 31/31
  pass, unchanged. Full suite: `npx jest` — 137/137 suites, 1925/1925
  tests pass, zero failures.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. The Stripe-card-addition flow was not
  exercised against a real Stripe test card.
- [x] Blast-radius grep performed — searched `rider-app` for the exact
  toast copy strings; only the existing UI test and a build-cache
  artifact matched, both confirmed non-issues.
- [x] Reviewed against relevant CLAUDE.md convention(s) — money: this
  touches the client-side gate before Stripe's `createPaymentMethod`
  and `POST /payments/cards`; error-handling: confirmed the renamed
  `validationError` variable still routes actual API errors (the
  `error` from `createPaymentMethod`'s destructured result, further
  down in the same function) through `getApiErrorMessage`, unchanged
  by this diff — only the earlier, unrelated validation-result variable
  was renamed.
- [x] Money/state-machine dry run (release-gate item 4): not directly
  applicable — no bug fixed, no behavior change, so no before/after
  scenario beyond "identical accept/reject boundary for every input."

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files:
clean (after the `validationError` rename fixed a false-positive
`no-restricted-syntax` trigger — see Section 3). **Real production
build** (`npm run build:web` → `expo export --platform web`) completed
successfully.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no
  bug-reintroduction risk since no bug was fixed)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  file, one function, fully replaced; the one other match — an
  existing UI test — was re-run and confirmed unaffected)
- [x] No silent behavior change to an already-shipped flow — this step
  is a pure extraction; no bug found, no behavior change made or
  needed. The variable rename is a lint-compliance fix with no runtime
  effect.

## What was NOT verified

- Not tested against a real Stripe test card or the backend's own
  `POST /payments/cards` validation — no staging/live-Stripe access
  from this session.
- No visual regression tooling exists for rider-app (per CLAUDE.md, no
  automated visual/snapshot regression tooling exists for this surface)
  — not applicable here regardless, no visual/UI change in this diff.
- The stuck-ride payment-retry entry point (`payForRide`/
  `payRideWithCard`) that also routes through this validation gate was
  not separately exercised — it is downstream of the gate and unchanged
  by this diff, but no dedicated test for that specific entry path was
  added or re-run beyond the existing suite's coverage.
- The last remaining candidate from the broader sweep,
  `emergency-contacts.tsx`, remains open — this step addresses the
  penultimate item.
