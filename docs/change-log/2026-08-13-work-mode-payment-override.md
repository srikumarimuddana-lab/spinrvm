# Change Impact & Risk Log — Work Mode Overriding a Rider's Payment Choice

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code |
| Surface(s) | rider-app |
| Domain (Sentry tag) | payments, corporate |
| Severity | Money-path — a wrong party is charged |
| Found by | Rider-app reviewer, 15-agent pre-deploy audit (`docs/reviews/2026-08-13-heatmap-predeploy-review.md`, "Already on main" #1) |

## 1. Issue / gap identified

A Work Mode rider could deliberately select their personal card, and have the
selection silently revert to corporate billing a moment later. The ride was
then charged to the **company** for a personal trip, with neither the rider
nor the company notified.

## 2. Root cause

Both booking screens keep the corporate toggle in sync with Work Mode inside a
`useEffect`:

```ts
if (workModeEnabled && corporateAccounts.length > 0) {
  setUseCorporate(true);
  ...
}
```

That effect's dependency list includes `activeCompanyId` and the corporate
account list, so it re-runs whenever either changes — which is exactly what a
`fetchWorkProfiles()` resolving shortly after mount does. The condition tests
only *platform* state, never whether the rider had already decided:

1. Rider opens the payment screen → effect applies the corporate default.
2. Rider taps their personal card → `setUseCorporate(false)`.
3. The profile fetch lands → effect re-runs → `setUseCorporate(true)`.
4. `handleBookRide` reads `useCorporate` and books against the company.

Not introduced by the heatmap work — it arrived via a lint-driven dependency
widening already merged to `main`, which increased how often step 3 fires.

## 3. Fix / remediation

The decision moves into `rider-app/utils/workModeDefault.ts`:

```ts
shouldApplyWorkModeDefault({ workModeEnabled, hasCorporateAccounts, riderChosePayment })
```

Each screen tracks `riderChosePaymentRef` — set to `true` at every point a
rider picks a payment source by hand (personal card, wallet, corporate row,
and the bill-to-business toggle in either direction). The sync effect consults
the helper instead of testing the raw condition.

Deliberate scoping:

- **The Work Mode default itself is unchanged.** A work-mode rider who has not
  chosen still gets corporate — that is the feature. The guard only suppresses
  the *re-application*, never the initial default.
- **A ref, not state.** It must not itself cause a render, and the effect reads
  it without needing it as a dependency (which would defeat the purpose).
- **Per screen-session.** Leaving and returning resets it, so a fresh booking
  correctly gets the work-mode default again rather than inheriting a decision
  from a previous ride.
- **Choosing corporate by hand also counts.** The guard is "the rider decided",
  not "the rider decided against us" — otherwise an explicit corporate choice
  would still be re-derived on the next effect run.

## 4. Risk & impact on existing functionality

**Blast radius — every `setUseCorporate` caller was enumerated (grep-verified):**

| Site | Kind | Change |
|---|---|---|
| `payment-confirm.tsx:61` | initial state | none |
| `payment-confirm.tsx` sync effect | platform default | now guarded |
| `payment-confirm.tsx` manage-cards row | rider action | marks choice |
| `payment-confirm.tsx` bill-to-business toggle | rider action | marks choice |
| `ride-options.tsx:162` | initial state | none |
| `ride-options.tsx` sync effect | platform default | now guarded |
| `ride-options.tsx` saved-card row | rider action | marks choice |
| `ride-options.tsx` add-card row | rider action | marks choice |
| `ride-options.tsx` wallet row | rider action | marks choice |
| `ride-options.tsx` corporate-account row | rider action | marks choice |

Ten call sites, four files searched (`app/`, `hooks/`, `store/`, `components/`).
No others exist.

**Swept for the same pattern elsewhere and found clean:**
`BookingProposalCard.tsx` derives `corporateAccountId` from `workModeEnabled`
directly, but it is the AI-chat proposal card with **no payment picker** — there
is no rider choice to override, so deriving is correct there. Left unchanged.

**What could regress:**

- A work-mode rider who *wants* corporate but taps a personal card by accident
  now has to change it back deliberately, rather than the app silently
  correcting them. That is the intended behaviour — silent correction is the
  bug — but it is a real behaviour change for anyone who had come to rely on
  it.
- If a future refactor adds a payment-selection control and forgets the ref,
  that path becomes silently overridable again. The contract test enumerates
  every `setUseCorporate` call reachable from `onPress`/`onValueChange` and
  fails if any lacks the marker, so a new control cannot be added without it.

**Explicitly unaffected:** fare calculation, settlement, the corporate wallet /
allowance path, Stripe idempotency, and the backend's payment-source priority
logic. This changes only which value the client sends as `corporateAccountId`
on `createRide`; every downstream money path is untouched.

## 5. User-experience effect

- **Rider-facing, mid-session visible:** yes. A rider who selects a personal
  payment method now keeps it for that booking. Previously it could flip back
  without any indication.
- **Corporate admins:** fewer erroneous company charges. No UI change.
- **Drivers:** none.
- **No copy changes.**

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/utils/workModeDefault.ts` | New: `shouldApplyWorkModeDefault` | One decision, testable, shared |
| `rider-app/app/payment-confirm.tsx` | Ref + guarded effect + 2 choice markers | Fix |
| `rider-app/app/ride-options.tsx` | Ref + guarded effect + 4 choice markers | Fix |
| `rider-app/__tests__/workModeCorporateOverride.test.ts` | New: 12 tests | Regression cover |

## 7. Before / after

```ts
// Before — tests only platform state, so any effect re-run re-asserts
// corporate billing over whatever the rider chose.
if (workModeEnabled && corporateAccounts.length > 0) {
  setUseCorporate(true);
}
```

```ts
// After — an explicit rider choice wins, in either direction.
if (shouldApplyWorkModeDefault({
  workModeEnabled,
  hasCorporateAccounts: corporateAccounts.length > 0,
  riderChosePayment: riderChosePaymentRef.current,
})) {
  setUseCorporate(true);
}

// ...and every rider-initiated selection marks the choice:
onPress={() => { riderChosePaymentRef.current = true; setSelectedPayment('wallet'); setUseCorporate(false); ... }}
```

## 8. Rollback plan

Pure client-side logic, no schema and no server change: `git revert` and ship
the next rider-app build. Nothing is persisted, so no data remediation is
needed either direction.

Note the asymmetry, because it affects urgency: **rolling this fix out is
instant for anyone on the new build, but rolling it back leaves already-updated
clients on the fixed behaviour until they update again.** Since the fixed
behaviour is "respect what the rider chose", that is the safe direction to be
stuck in.

Any incorrect charges that already happened are **not** undone by this fix —
they need the normal corporate-billing dispute path. See §10.

## 9. Verification performed

- [x] **Rider-app suite: 480 passed, 0 failed** (was 468 — 12 new).
- [x] `tsc --noEmit` clean.
- [x] **Tests verified to fail with the fix reverted** (2 of 12 fail when the
      raw condition is reinstated) — they pin the defect, not just the shape.
- [x] Blast-radius enumeration of all ten `setUseCorporate` call sites (§4),
      plus a sweep for the same force-on pattern in other screens.
- [x] Behavioural coverage of the decision itself, including the
      repeated-effect-re-run case — a guard that only held for the first
      re-run would still lose the rider's choice.

## 10. What was NOT verified

- **No device run.** The failure depends on real fetch timing (a profile
  response landing after a tap); jest cannot reproduce that ordering
  faithfully. The logic is unit-tested and the wiring is contract-tested, but
  the end-to-end race was **not** reproduced on a handset before or after.
  Worth one manual pass: work mode on, open payment, tap personal card
  immediately on screen load, confirm it sticks.
- **Screen components are not rendered in tests.** These are ~1500-line screens
  wired to Stripe, expo-router, maps and several stores; this repo's existing
  tests for these files use source contracts for the same reason. The decision
  is covered behaviourally; the wiring is covered by contract. Neither is a
  substitute for rendering the real component.
- **Blast radius covers `setUseCorporate` specifically.** If another code path
  can influence billing without calling it, this sweep would not have found it.
- **No audit of historical impact.** How many rides were mis-billed by this
  defect while it was live is unknown and was not investigated. If corporate
  customers have disputed charges recently, this is a plausible cause and
  worth checking against ride timestamps — the fix does not correct past
  charges.
