# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude (ACTION_ITEMS.md B-AI1) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/b-ai1-corporate-billing-chat` |
| Related issue or gap ID | ACTION_ITEMS.md B-AI1 |

## 1. Issue / gap identified

A corporate rider with Work Mode on who books a ride via the AI chat card, paying by wallet, was billed personally instead of to their company — corporate policy enforcement never ran.

## 2. Root cause

`BookingProposalCard.tsx`'s inline (wallet) booking path called `createRide(paymentMethod, undefined, ...)`, hardcoding `corporateAccountId` to `undefined` regardless of the rider's corporate membership state. The standard `/ride-options` screen, by contrast, defaults `useCorporate = workModeEnabled` and passes `activeCompanyId` through, so `corporate_account_id`/`work_profile` reach the backend and `backend/routes/rides/booking.py:717-721`'s corporate policy check runs. Card-payment proposals were unaffected because they already deep-link to `/ride-options` before booking (a card needs an explicit selection the chat card can't make) — only the wallet inline-booking path skipped it. Found by the 2026-07-28 AI guardrail audit; not a regression introduced by any recent change.

## 3. Fix / remediation

`BookingProposalCard.tsx` now reads `useWorkProfileStore`'s `workModeEnabled`/`activeCompanyId` and computes `corporateAccountId = workModeEnabled && activeCompanyId ? activeCompanyId : null` — the exact same default `/ride-options.tsx` already uses (`useState(workModeEnabled)` / `activeCompanyId ?? null`), not a new payer-selection design. This was an explicit product decision (see below), not something picked unilaterally.

Also added, for parity with the standard flow:
- Calls `fetchPolicy()` when a corporate account is active, mirroring `/ride-options`' mount-time fetch, so the client-side policy check below isn't operating on a stale/empty policy.
- Runs the same `checkRide(fare, scheduledDate)` client-side policy pre-check `/ride-options` runs before booking (`handleBookRide`); on failure, shows the policy-violation reasons in the card's existing error state instead of silently booking. This is UX parity only — the real enforcement boundary is server-side (`backend/routes/rides/booking.py`), unchanged by this fix.
- Passes `corporateAccountId` through to `createRide` instead of `undefined`.
- Adds a "Charged to `<Company>`" pill next to the existing Wallet/Card pill so the rider sees the payer before confirming — no silent charge-target switch.

**Decision point resolved with the user before implementing:** the ACTION_ITEMS entry explicitly says "Do NOT silently pick a payer" and lists two possible approaches (auto-apply Work Mode default vs. force to `/ride-options` vs. add an explicit payer picker). Presented all three via `AskUserQuestion`; user chose "mirror `/ride-options`' default" (option 1).

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (rider-app), single component.** Grepped for other importers of `BookingProposalCard.tsx` — only the AI chat screen renders it (no other caller found). Grepped `useWorkProfileStore` consumers: `app/ride-options.tsx`, `app/work-profile*.tsx` screens, and now this file — no shared-state write added, only reads (`workModeEnabled`, `activeCompanyId`, `profiles`) plus calls to the store's own existing `fetchPolicy`/`checkRide` methods (unmodified).
- **Could this regress a currently-working flow?**
  - Riders **without** Work Mode on: `corporateAccountId` evaluates to `null`, `createRide(paymentMethod, null, ...)` — behaviorally identical to the previous `createRide(paymentMethod, undefined, ...)` (`rideStore.ts:726` already does `corporateAccountId || null`). No behavior change for the non-corporate majority of riders.
  - Riders **with** Work Mode on: this is the intended behavior change — see §5.
  - Card-payment proposals: untouched, still deep-link to `/ride-options` before any `createRide` call from this component at all.
- **Interaction with money/wallet/ride-state machine:** `createRide`'s corporate-billing branch (`corporate_wallet_apply_delta`, fare settlement source selection) is pre-existing, exercised identically by `/ride-options` today — this fix only makes the AI chat path reach the same already-tested code path, it does not add a new billing branch.
- **Background loops:** none touched.

## 5. User-experience effect

- **Rider-facing, corporate riders with Work Mode on, wallet-payment chat proposals only.** Previously: booked personally (wrong payer, silent). Now: booked to their company, with a visible "Charged to `<Company>`" pill on the confirmation card before they tap Confirm, and a policy-violation message (instead of a silent book) if the ride would breach the company's fare cap or allowed time windows.
- **Visible mid-session?** Yes, but only at the point the rider is actively confirming a chat-proposed ride — not a change to an already-in-progress ride.
- **Non-corporate riders, and corporate riders with Work Mode off:** no visible change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/components/BookingProposalCard.tsx` | Reads Work Mode state, computes `corporateAccountId`, runs the standard policy pre-check, passes the id to `createRide`, adds a "Charged to" pill | Close the corporate-billing bypass; UX parity with `/ride-options` |
| `rider-app/__tests__/bookingProposalCardCorporate.test.tsx` | New regression tests (4 cases: Work Mode off, Work Mode on books to company, policy failure blocks booking, card path unaffected) | Pin the fix; would fail against the pre-fix `undefined` hardcode |
| `rider-app/__tests__/bookingProposalCardPromo.test.tsx` | Added a stub `workProfileStore` mock | The component now imports `useWorkProfileStore`, which pulls in the native `AsyncStorage` module under Jest unless mocked; unrelated to this test's own assertions |

## 7. Before / after

```tsx
// Before
const ride = await createRide(paymentMethod, undefined, undefined, undefined, {
  allowSamePlace: !!proposal.same_location_confirmed,
});
```

```tsx
// After
const corporateAccountId = workModeEnabled && activeCompanyId ? activeCompanyId : null;
// ...
if (corporateAccountId) {
  const check = checkRide(grandTotalOf(estimate), scheduledDate ?? undefined);
  if (!check.ok) { /* show policy-violation error, return without booking */ }
}
const ride = await createRide(paymentMethod, corporateAccountId, undefined, undefined, {
  allowSamePlace: !!proposal.same_location_confirmed,
});
```

## 8. Rollback plan

`git-revert-safe` — pure frontend logic/UI change, no schema, no migration, no already-applied money movement to undo. A revert returns the wallet-inline chat path to its previous (personal-billing-only) behavior; any rides already booked to a corporate account under the new behavior remain correctly attributed and are not affected by reverting the code going forward.

## 9. Verification performed

- [x] Automated tests: added `rider-app/__tests__/bookingProposalCardCorporate.test.tsx` (4 new tests); full `rider-app` suite re-run — `51 test suites passed, 434 tests passed` (was 50/430 before this change; the one pre-existing unrelated teardown warning in `privacySettingsToggles.test.tsx` is unaffected).
- [x] `npx tsc --noEmit` — clean, no errors in this file or elsewhere.
- [x] Blast-radius grep performed (§4) — one component consumer, no other `BookingProposalCard` importers, no other writers to the `useWorkProfileStore` fields read here.
- [x] Reviewed against `CLAUDE.md`'s corporate billing convention ("Payment source selection ... happens at fare settlement") — this change only supplies the `corporate_account_id` input that selection already consumes; it does not touch the settlement logic itself.
- [x] Decision on payer-selection approach explicitly confirmed with the user (`AskUserQuestion`) rather than picked unilaterally, per the ACTION_ITEMS item's own "Do NOT silently pick a payer" instruction and CLAUDE.md's escalate-on-money-ambiguity gate.
- [ ] Not feature-flagged: this is a bug fix restoring the same default the standard `/ride-options` screen already applies to the same rider state, not new functionality — judged proportionate to skip a flag, but flagging is a fallback if broader review disagrees.
- [x] Attempted the real production build CLAUDE.md requires for `rider-app` changes (`npx expo export --platform web`, the closest equivalent to `npm run build` for this Expo app) — it fails before touching any app code, at config-plugin resolution: `PluginError: Failed to resolve plugin for module "react-native-fbsdk-next"`. This is the same pre-existing, environment-level Facebook SDK config-plugin failure already known from this session's CI context (driver/rider-app E2E), unrelated to this diff — `BookingProposalCard.tsx` and its tests are untouched by it. Not something fixable from this change; noted rather than silently skipped.

## 10. What was NOT verified

- **No real production build succeeded** for `rider-app` — attempted (see above) and blocked by a pre-existing environment issue unrelated to this diff, not skipped. `tsc --noEmit` (clean) and the full Jest suite (434/434 passing) are the strongest verification available in this environment; per CLAUDE.md, that is explicitly not equivalent to a production build passing.
- Not run against a real Supabase/backend dev instance or a live rider account with an actual corporate membership — verified only via mocked `useWorkProfileStore`/`useRideStore` per the existing test convention in this file's sibling test.
- No manual device/simulator run through Expo — not available in this environment; the fix was verified via unit tests and static typechecking only, not by tapping through the actual chat UI.
- The `checkRide` policy pre-check's exact reason strings were exercised only with a synthetic fixture, not against a real company policy shape from the DB.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level remediation).
- [x] Blast radius is stated, not assumed (§4).
- [x] No silent behavior change: the payer-selection approach was explicitly confirmed with the user before implementation, and the fix surfaces the payer to the rider via the new "Charged to" pill rather than changing billing silently.
