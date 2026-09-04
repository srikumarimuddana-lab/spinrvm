# ADR-013: Zod, adopted incrementally and risk-ordered, for frontend form validation

- Status: Accepted
- Date: 2026-09-04
- Deciders: Claude Code (executing session), per ACTION_ITEMS.md B39's own steps 1-37 (2026-08-22 through 2026-09-04) — backfilling the decision that was already made and followed in practice, per explicit user request in this session
- Domain: platform (cross-cutting: rider-app, driver-app, admin-dashboard)
- Affects: `rider-app/utils/*Schema.ts`, `driver-app/utils/*Schema.ts`, `admin-dashboard/src/lib/*Schema.ts`, and the `zod` dependency in each of the three surfaces' `package.json`

## Context

None of the three frontend surfaces (`rider-app`, `driver-app`,
`admin-dashboard`) used a schema-validation library. Every form validated
input via hand-rolled, inline `if`/regex checks, spot-checked and confirmed
in `rider-app/app/login.tsx`, `profile-setup.tsx`, and
`work-allowance-request.tsx` (ACTION_ITEMS.md B39, found 2026-08-22). This
was not automatically wrong — a small app can reasonably skip a validation
library — but it created three concrete problems:

1. **Duplication/drift risk.** The same shape of data (a phone number, an
   email address, a required-amount field) could be validated differently
   on different screens with no single source of truth.
2. **Invisible coverage.** There was no single place to point a coverage
   tool at to answer "is every validation rule tested?" — validation logic
   lived inside each screen's own component file, untested independently
   of that screen's own (if any) UI test.
3. **Real financial/regulatory exposure on some forms.** Several of these
   forms are money- or compliance-adjacent — the rider corporate
   `work-allowance-request.tsx` amount field, wallet top-up amounts feeding
   a live Stripe `PaymentSheet`, driver payout GST/BN/SIN fields
   (CRA-adjacent), and admin corporate wallet-adjustment forms backed by
   `corporate_wallet_apply_delta` — where a validation gap (a rejected
   amount silently coerced instead of blocked) has real consequence, not
   just a UX nit.

Introducing a validation library is itself a UX-risk change under
CLAUDE.md's pre-merge release gates (gate #3: feature-flag/stage anything
user-visible and non-trivial; gate #5: no silent behavior change to an
already-shipped flow) — a stricter or differently-shaped validation rule on
a live-tested screen could reject input that previously passed. Any fix
here had to be additive and incremental, not a mass find-and-replace.

## Decision

We adopt `zod` as the one schema-validation library across all three
frontend surfaces (rider-app/Expo, driver-app/Expo, admin-dashboard/Next.js
— one tool works identically in all three, no separate library per
surface), and migrate to it **one form at a time**, never in bulk, ordered
by risk:

1. **Money and compliance-adjacent forms first** — corporate/billing
   inputs, payment-sheet amounts, KYC/onboarding fields, safety forms.
   **Cosmetic/profile forms last.**
2. **Every migration is a pure, byte-for-byte extraction** of the existing
   inline check into a colocated `*Schema.ts` file — never a validation-rule
   change bundled into the same commit as the extraction. If a real
   correctness bug is found alongside a validation gap (e.g. a negative
   value that should have been rejected but wasn't), it is called out
   explicitly and fixed only with the user's direct authorization in that
   step, never silently folded into "just an extraction."
3. **Each migrated form gets a colocated schema file** (`utils/*Schema.ts`
   on rider-app/driver-app, `src/lib/*Schema.ts` on admin-dashboard) **and
   a dedicated accept/reject unit test file** — this closes the "invisible
   coverage" problem directly, independent of any broader UI-test coverage
   effort.
4. **"Nothing to extract" is itself a valid, documented outcome.** A form
   whose only guard is a plain truthy/length check with no real rule and no
   user-facing error message (e.g. `rider-app/app/login.tsx`'s
   `phoneNumber.length === 10`, several admin-dashboard "Save"-button
   `disabled` props gated only on a required-field presence check) is
   checked, explicitly recorded as checked, and left alone — not silently
   skipped, and not forced into a schema wrapper with nothing behind it.
5. Where two call sites hand-duplicate the *exact same* check (e.g. an
   inline guard and a button's `disabled` prop repeating the identical
   expression), the migration step consolidates both onto the one extracted
   predicate. Where two call sites check something that only *looks*
   similar but is not identical (different input, different normalization),
   they are kept as separate predicates rather than merged — merging would
   itself be a validation-rule change.

This decision was reached and followed identically across 37 incremental
steps recorded in `ACTION_ITEMS.md` B39 (2026-08-22 → 2026-09-04) before
this ADR was written down; this ADR formalizes what was already the
consistent, de facto standard rather than proposing something new.

## Consequences

### Positive
- Every migrated form now has an independently testable, colocated
  validation module and a dedicated accept/reject test file — the
  "invisible coverage" problem is closed for every form migrated so far.
- Two real correctness bugs were caught and fixed as a direct side effect
  of this discipline (not the extraction itself, but the act of writing
  down and testing what the inline check actually did):
  `rider-app/app/ride-completed.tsx`'s custom-tip field accepted a negative
  value via `parseFloat(customTip) || 0` (`-5 || 0` → `-5`, truthy); and
  `driver-app/app/vehicle-info.tsx`'s vehicle-year field silently coerced a
  non-numeric year to `0` via the same `|| 0` pattern with no `isNaN`
  guard. Both are documented in ACTION_ITEMS.md B39 steps 15/16 as the only
  two migrations that were *not* pure extractions, done only after
  explicit user authorization.
- Duplicated exact-match checks (an inline guard repeated in a button's
  `disabled` prop) were consolidated onto one shared predicate at several
  migration steps, closing small pieces of the duplication/drift risk this
  item names, one form at a time.
- All three surfaces now have `zod` as a direct dependency, so any new form
  going forward can use it without a dependency-adoption step.

### Negative / trade-offs
- **Not every form on any surface is on zod.** This is accepted, not a
  gap: the item's own scope was always "migrate one form at a time, don't
  mass-migrate," never "migrate every form in the codebase." Forms outside
  the specific sweeps recorded below have never been exhaustively
  enumerated across all three apps — a standing, explicitly acknowledged
  boundary of this effort, not an oversight.
- Two independent implementations of the same underlying Spinr Pass plan
  form exist (`admin-dashboard/src/lib/subscriptionPlanSchema.ts` for
  `subscriptions/page.tsx`'s `PlanModal`, and
  `spinrPassAreaPlanSchema.ts` for `service-areas/page.tsx`'s
  `SpinrPassAreaTab`) — genuinely different field types today (a typed
  `number` vs. a raw string), so merging them was explicitly deferred
  rather than folded into either extraction (ACTION_ITEMS.md B39 step 13).
  Not de-duplicated as of this ADR.
- The "kept separate when not identical" rule (item 5 above) means a
  reader has to check each schema file's own comment to know whether a
  near-duplicate elsewhere was deliberately left alone or simply not yet
  found — there is no single index of these decisions beyond the
  per-step Change Impact Logs and this ADR's own tables below.
- No lint rule or CI check enforces "new forms must use zod" — the
  discipline is currently carried entirely by convention (this ADR +
  `ACTION_ITEMS.md` B39's running log), not by tooling.

### Neutral
- No schema, migration, API contract, or server-side validation changed —
  every migration documented here is client-side only. Backend pydantic
  validation was independently confirmed to already exist as a backstop on
  these routes (ACTION_ITEMS.md B39's original "What was NOT verified"
  note), so a client-side gap here was a UX/defense-in-depth issue, not by
  itself a security hole.

## What's been migrated so far

Cited from the `ACTION_ITEMS.md` B39 step history (steps 1-37); this ADR
does not re-verify each one's code, only its own change-log entry. Each
row's schema file is colocated with the form it validates and has its own
`__tests__/*Schema.test.ts` file with accept/reject cases (count noted).

### rider-app (`rider-app/utils/`)

| Form | Schema file | Step / date |
|---|---|---|
| `app/work-allowance-request.tsx` | `workAllowanceRequestSchema.ts` | step 1, 2026-08-22 |
| `app/wallet.tsx` (Add Funds top-up) | `walletTopUpSchema.ts` | step 2, 2026-08-23 |
| `app/profile-setup.tsx` | `profileSetupSchema.ts` | step 5, 2026-08-24 |
| `app/ride-completed.tsx` (custom tip — incl. negative-value bug fix) | `customTipSchema.ts` | step 15, 2026-08-31 |
| `app/become-driver.tsx` (3-step wizard) | `becomeDriverSchema.ts` | step 28, 2026-08-31 |
| `app/manage-cards.tsx` | `manageCardsSchema.ts` | step 35, 2026-08-31 |
| `app/emergency-contacts.tsx` | `emergencyContactSchema.ts` | step 36, 2026-08-31 |

`app/login.tsx` — checked (step 7, 2026-08-24), nothing to extract: its
only check is a bare 10-digit length comparison with no error message.

### driver-app (`driver-app/utils/`)

| Form | Schema file | Step / date |
|---|---|---|
| `app/driver/payout.tsx` (GST/BN, SIN) | `payoutFormsSchema.ts` | step 3, 2026-08-24 |
| `app/profile-setup.tsx` | `profileSetupSchema.ts` | step 7, 2026-08-24 |
| `app/vehicle-info.tsx` (incl. silent invalid-year bug fix) | `vehicleInfoFormSchema.ts` | step 16, 2026-08-31 |
| `app/become-driver.tsx` | `becomeDriverSchema.ts` | step 29, 2026-08-31 |
| `app/driver/emergency-contacts.tsx` | `emergencyContactSchema.ts` | step 30, 2026-08-31 |
| `app/report-safety.tsx` | `reportSafetySchema.ts` | step 31, 2026-08-31 |
| `app/driver/(tabs)/profile.tsx` | `driverProfileSchema.ts` | step 32, 2026-08-31 |
| `app/driver/settings.tsx` (account-deletion confirmation) | `accountDeletionSchema.ts` | step 33, 2026-08-31 |
| `app/driver/addresses.tsx` + `destination-mode.tsx` (shared predicates) | `addressGeocodeSchema.ts` | step 34, 2026-08-31 |

### admin-dashboard (`admin-dashboard/src/lib/`)

| Form | Schema file | Step / date |
|---|---|---|
| `.../members/allowance-dialog.tsx` | `allowanceFormSchema.ts` | step 4, 2026-08-24 |
| `corporate-accounts/[id]/policy/page.tsx` (time windows) | `policyTimeWindowSchema.ts` | step 6, 2026-08-24 |
| `corporate-accounts/[id]/page.tsx` (wallet adjustment) | `walletAdjustmentSchema.ts` | step 8, 2026-08-29 |
| `subscriptions/page.tsx` (`PlanModal`) | `subscriptionPlanSchema.ts` | step 9, 2026-08-29 |
| `service-areas/page.tsx` (surge justification) | `surgeJustificationSchema.ts` | step 10, 2026-08-29 |
| `staff/page.tsx` | `staffFormSchema.ts` | step 11, 2026-08-30 |
| `service-areas/page.tsx` (tax justification) | `taxJustificationSchema.ts` | step 12, 2026-08-30 |
| `service-areas/page.tsx` (`SpinrPassAreaTab` plan) | `spinrPassAreaPlanSchema.ts` | step 13, 2026-08-30 |
| `service-areas/page.tsx` (create-area, airport-zone) | `serviceAreaFormSchema.ts` | step 14, 2026-08-31 |
| `users/page.tsx` (wallet credit/debit) | `userWalletActionSchema.ts` | step 17, 2026-08-31 |
| `disputes/page.tsx` (partial refund) | `disputeResolutionSchema.ts` | step 18, 2026-08-31 |
| `promotions/page.tsx` | `promotionFormSchema.ts` | step 19, 2026-08-31 |
| `rides/_components/create-ride-modal.tsx` | `createRideFormSchema.ts` | step 20, 2026-08-31 |
| `safety/page.tsx` (incident log + merge) | `safetyIncidentFormSchema.ts` | step 21, 2026-08-31 |
| `cloud-messaging/page.tsx` (broadcast + suppression) | `cloudMessagingFormSchema.ts` | step 22, 2026-08-31 |
| `corporate-accounts/[id]/members/page.tsx` (invite) | `companyMemberInviteSchema.ts` | step 23, 2026-08-31 |
| `drivers/page.tsx` (photo-upload MIME check) | `driverPhotoUploadSchema.ts` | step 24, 2026-08-31 |
| `users/page.tsx` (ban/suspend reason) | `userModerationSchema.ts` | step 25, 2026-08-31 |
| `venues/page.tsx` | `venueFormSchema.ts` | step 26, 2026-08-31 |
| `faqs/page.tsx` | `faqFormSchema.ts` | step 27, 2026-08-31 |
| `rides/_components/ride-complaint-form.tsx`, `ride-flag-form.tsx` (silent no-op → toast fix) | `rideComplaintFormSchema.ts`, `rideFlagFormSchema.ts` | step 28, 2026-08-31 |
| `support-tickets/tickets/page.tsx` (create-ticket subject) | `createTicketFormSchema.ts` | step 37, 2026-09-04 |
| `support-tickets/tickets/[id]/page.tsx` (reply, internal note) | `ticketReplyFormSchema.ts` | step 37, 2026-09-04 |

Checked and confirmed to have nothing worth extracting (no real accept/reject
rule, or the field is explicitly optional): `subscriptions/page.tsx`'s
per-company plan-assignment gate; `kyb-queue/page.tsx`'s reject-note field
(optional by design); `corporate-accounts/[id]/page.tsx`'s KYB Verification
section (read-only display); `subscriptions/page.tsx`'s `TaxConfigModal`
(HTML `min`/`max` only, no JS-level check); `support-tickets/_components/
zoho-config-card.tsx` (no accept/reject gate on save — relies entirely on
backend validation).

## Out of scope / explicitly deferred

- Every other form across all three apps that has not yet been named in a
  B39 step. `ACTION_ITEMS.md` B39 has run two dedicated sweeps to date — an
  opportunistic one (steps 1-14, forms found while otherwise working the
  item) and a full 21-candidate broader sweep across all three apps'
  `app/`/`src/app/` trees (steps 15-36, 2026-08-31) — but neither claims
  full enumeration; a form outside both sweeps' scope may still have an
  ad hoc inline rule.
- `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx` — a
  near-duplicate of the migrated `support-tickets/tickets/page.tsx`
  subject-required check, found during step 37's blast-radius grep and
  deliberately left unmigrated (flagged as a follow-up candidate in
  `ACTION_ITEMS.md` B39, not folded into step 37's scope).
- De-duplicating the two independent Spinr Pass plan-form schemas
  (`subscriptionPlanSchema.ts` vs. `spinrPassAreaPlanSchema.ts`) — a
  refactor decision, not a validation extraction, flagged in step 13.
- `service-areas/page.tsx`'s fee/incentive/heatmap-config tabs — inline
  `parseFloat`/`NaN` sanitization with no user-facing error message, the
  same "nothing to extract" shape as several already-checked forms, but
  not swept field-by-field beyond what steps 10-14 named.
- Enforcing "new forms must use zod" via lint rule or CI check — currently
  convention-only (this ADR + the B39 running log), not tooling-enforced.

## Alternatives considered

### Mass-migrate every form to zod in one PR
Rejected: this is itself a live-tested-surface UX-risk change per
CLAUDE.md's pre-merge release gates. A single large diff touching dozens
of forms across three apps would make it impossible to isolate which
specific validation-rule change (if any slipped in) broke which screen,
and would violate the "surgical changes" / "batch size" working-style
rules in `CLAUDE.md` regardless of the release-gate concern.

### A different validation library per surface (e.g. Yup for React Native, a native Next.js form library for admin-dashboard)
Rejected: `zod` works identically across Expo/React Native and Next.js
with no surface-specific adapter needed, avoiding the maintenance cost of
two mental models and two sets of validation idioms for engineers who
touch more than one surface.

### One shared "fail on first error, aggregate schema" refactor instead of a predicate-per-check style
Rejected as a default for every form: several forms show a *different*
toast/error message per failing check (e.g. `promotionFormSchema.ts`,
`walletAdjustmentSchema.ts`, `disputeResolutionSchema.ts`) — collapsing
multiple checks into one aggregate boolean loses which message to show.
Where a form's checks are genuinely independent from the UI's point of
view (a plain required-field check with no distinct per-rule messaging),
a single combined predicate is used instead (e.g. `faqFormSchema.ts`,
`venueFormSchema.ts`) — the shape is chosen per form, not forced uniformly.

## Rollout

- Migration path: incremental, form-by-form, ordered by risk (see
  Decision). No cutover date — the item remains open by design as an
  ongoing backlog item, not a finite checklist with a completion state.
- Feature flag: none needed. Each migration is a pure extraction with
  identical accept/reject behavior to the code it replaces, so there is no
  behavior change to flag or stage — the two documented exceptions (the
  negative-tip and invalid-vehicle-year bug fixes) were explicit,
  user-authorized behavior changes on top of an extraction, each with
  their own Change Impact Log entry.
- Rollback plan: revert the individual commit/PR for any single migrated
  form — each is independent of every other; there is no shared migration
  state or cutover to unwind.

## Spinr-specific impact

- Money / payments: several migrated forms are money-adjacent (wallet
  top-up, corporate wallet adjustment, promotions, admin fare override,
  driver payout tax fields) — all migrated as pure extractions preserving
  the exact accept/reject behavior already live, except the two explicitly
  authorized bug fixes noted above (customTipSchema.ts,
  vehicleInfoFormSchema.ts).
- Safety / insurance periods: emergency-contact and safety-report forms on
  both rider-app and driver-app are migrated; no change to insurance-period
  logic itself (client-side form validation only).
- PIPEDA / retention: `driver-app/app/driver/settings.tsx`'s type-DELETE
  account-deletion confirmation and admin-dashboard's cloud-messaging
  marketing-suppression form are migrated; no change to the underlying
  deletion/retention behavior.
- Regulatory (SK/SGI): `becomeDriverSchema.ts` (both rider-app and
  driver-app) mirrors the SGI vehicle-age (<10-year) rule client-side; the
  authoritative check remains server-side, unchanged by this ADR.
- Performance SLAs: negligible — all client-side, synchronous validation;
  no network or backend call path affected.

## References

- `ACTION_ITEMS.md` B39 (full step history, steps 1-37)
- `docs/change-log/2026-08-22-b39-work-allowance-zod-pilot.md` through
  `docs/change-log/2026-09-04-b39-admin-support-tickets-zod-step37.md`
  (one Change Impact Log per migration step)
