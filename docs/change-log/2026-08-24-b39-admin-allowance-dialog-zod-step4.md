# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 4 (admin-dashboard's first zod migration, following rider-app steps 1/2 and driver-app step 3) |

## 1. Issue / gap identified

`allowance-dialog.tsx` (admin sets a corporate member's spend allowance —
type, amount, recurring-period dates, auto-approve caps) validates via
three throw conditions inside `save()`, with the exact same three
conditions duplicated separately in the Save button's `disabled`
expression. This is the "duplicated logic can drift" problem B39 names by
example, on a form that writes real money-governing state
(`corporate_wallet_apply_delta`-backed member allowances). admin-dashboard
had zero schema-validation library adoption — the last of the three
frontend surfaces to get one, per B39's own priority ordering
("admin-dashboard corporate/billing forms... first-priority").

## 2. Root cause

Ad hoc validation predates any schema-validation library on this surface.
The two call sites (the imperative `save()` throws and the declarative
`disabled` boolean) encode the same three rules by hand in two different
syntactic shapes, which is exactly the drift risk B39 warns about — they
happened to still agree today, but nothing enforced that.

## 3. Fix / remediation

Added `zod` to `admin-dashboard/package.json` (`^4.4.3`, matching the
version rider-app/driver-app already use). New colocated
`admin-dashboard/src/lib/allowanceFormSchema.ts`:

- `allowanceTypeSchema` — the existing `"fixed_recurring" | "one_time" |
  "unlimited"` union (already defined as `AllowanceTypeValue` in
  `lib/api/corporate.ts`; not redefined, just validated against the same
  three literals).
- `allowanceFormSchema` — a `superRefine` reproducing `save()`'s exact two
  throw conditions **in the same order** (amount check first, period-dates
  check second), so `.safeParse(...).error.issues[0].message` yields the
  same first-error-wins message the original sequential `throw`s produced.
- `isAllowanceFormValid(input)` — boolean wrapper, used to replace the
  `disabled` expression's duplicated condition.

`save()` now does one `safeParse` up front and throws
`new Error(parsed.error.issues[0].message)` on failure instead of two
separate inline `if`/`throw` blocks — same messages, same priority, one
source of truth instead of two.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped `admin-dashboard/src` for every
  importer of `AllowanceDialog` — only
  `corporate-accounts/[id]/members/page.tsx` renders it, and that file
  was not touched.
- No data schema, API contract, or background-job change. The `body`
  object built and sent to `putMemberAllowance` is unchanged — same
  shape, same conditional fields (`period_start`/`period_end`/`rollover`/
  `auto_approve_topup_amount`/`auto_approve_monthly_count` only for
  `fixed_recurring`).
- `corporate_wallet_apply_delta` and its row-level locking/idempotency are
  untouched — this PR only changes what the admin's *input form* accepts
  before that call is ever made.

## 5. User-experience effect

None for the admin using this dialog: identical accept/reject behavior,
identical error message text and priority (amount error still wins over
the period-dates error when both are missing), identical Save-button
disabled state for the same inputs as before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/package.json` | Added `"zod": "^4.4.3"` | First zod adoption on admin-dashboard |
| `admin-dashboard/package-lock.json` | `zod` promoted from a transitive `shadcn` dev-dependency (`3.25.76`) to a direct dependency (`4.4.3`); `shadcn`'s own nested `zod@3.25.76` copy now listed separately | npm dependency resolution |
| `admin-dashboard/src/lib/allowanceFormSchema.ts` | New — zod schema + `isAllowanceFormValid` helper | Colocated, testable validation |
| `admin-dashboard/src/lib/__tests__/allowanceFormSchema.test.ts` | New — 12 accept/reject cases + 2 issue-priority cases | Close validation-coverage gap |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/members/allowance-dialog.tsx` | `save()` uses one `safeParse` instead of two inline throws; `disabled` uses `isAllowanceFormValid` | Pure extraction, no behavior change |
| `ACTION_ITEMS.md` | B39 — recorded step 4 completion | Track migration progress |
| `docs/change-log/2026-08-24-b39-admin-allowance-dialog-zod-step4.md` | New change-log | Required |

## 7. Before / after

```ts
// Before — save()
const body: Parameters<typeof putMemberAllowance>[2] = { type };
if (type === "unlimited") {
  // nothing else
} else {
  if (!amount) throw new Error("Amount is required.");
  body.amount = Number(amount);
  if (type === "fixed_recurring") {
    if (!periodStart || !periodEnd) {
      throw new Error("fixed_recurring requires both period dates.");
    }
    body.period_start = periodStart;
    ...
  }
}

// After
const parsed = allowanceFormSchema.safeParse({ type, amount, periodStart, periodEnd });
if (!parsed.success) {
  throw new Error(parsed.error.issues[0].message);
}
const body: Parameters<typeof putMemberAllowance>[2] = { type };
if (type === "unlimited") {
  // nothing else
} else {
  body.amount = Number(amount);
  if (type === "fixed_recurring") {
    body.period_start = periodStart;
    ...
  }
}
```

```ts
// Before — Save button
disabled={saving || (type === "fixed_recurring" && (!periodStart || !periodEnd)) || (type !== "unlimited" && !amount)}

// After
disabled={saving || !isAllowanceFormValid({ type, amount, periodStart, periodEnd })}
```

## 8. Rollback plan

**`git-revert-safe`** — pure client-side extraction; no data/schema/config
touched, no migration involved.

## 9. Verification performed

- [x] 12/12 new `allowanceFormSchema.test.ts` accept/reject cases pass, plus
      2 issue-priority cases confirming the "amount error wins when both
      are missing" behavior is preserved
- [x] Full admin-dashboard suite: 351/351 tests pass (36 files), 0
      regressions
- [x] `npx tsc --noEmit` clean
- [x] `npx eslint` clean on all touched files
- [x] **Real production build**: `npm run build` (`next build`) completed
      successfully — not just `tsc`/dev server
- [x] Blast-radius grep performed: `AllowanceDialog` importers — only
      `members/page.tsx`, untouched
- [x] Reviewed against B39's own risk note: additive-only, one form, pure
      extraction — the two original throw conditions collapsed into one
      `safeParse` but preserve identical message text and priority order

## What was NOT verified

- End-to-end manual repro against real Supabase dev / a live corporate
  account (client-side validation only; not exercised against the actual
  `PUT` allowance endpoint's own server-side validation, which remains
  unchanged and authoritative).
- Whether `putMemberAllowance`'s backend counterpart rejects the same
  malformed inputs this schema now catches client-side — assumed
  consistent with the pre-existing behavior since the accepted/rejected
  input set is unchanged, not independently re-verified against the
  backend route in this pass.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — grepped, isolated to one page
- [x] No silent behavior change — same three rules, same error-message
      priority, documented before/after per call site
