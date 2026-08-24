# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | corporate (tax/payout compliance surface — closest fit; not `payments`, no Stripe amount math involved) |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 3 (driver-app's first zod migration, following rider-app steps 1/2) |

## 1. Issue / gap identified

`app/driver/payout.tsx` validates two CRA/tax-compliance-adjacent fields
(GST/HST Business Number, SIN) via three separate inline regex/length
checks with no dedicated test coverage — the exact "validation-rule
coverage is invisible" and "duplicated logic can drift" problems B39
names. driver-app had zero schema-validation library adoption (rider-app
adopted `zod` in B39 steps 1/2; driver-app and admin-dashboard were still
unmigrated).

## 2. Root cause

Ad hoc validation predates any schema-validation library on this surface;
the GST/BN format check in particular was duplicated in two places
(`handleSaveGst`'s save-guard and the `gstOnFile` checklist-display flag)
with subtly different normalization (one uppercases + strips whitespace,
the other doesn't) — exactly the drift risk B39 warns about, though in
this case the two call sites' differing behavior is intentional (see
Before/After) and preserved as-is, not "fixed", per this item's own
pure-extraction requirement.

## 3. Fix / remediation

Added `zod` to `driver-app/package.json` (same `^4.4.3` pin as rider-app;
resolved to an already-present transitive `zod@4.4.3`, so no new package
version enters the tree). New colocated `driver-app/utils/payoutFormsSchema.ts`
extracts three predicates as pure functions backed by two zod schemas:

- `gstBnSchema` (`z.string().regex(/^\d{9}(RT\d{4})?$/)`) — the CRA
  Business Number / program-account format.
- `isGstBnValid(cleaned)` — mirrors `handleSaveGst`'s accept condition
  (empty is valid — it clears `gst_bn`; non-empty must match the format,
  case-sensitive, no normalization).
- `isGstBnOnFile(value)` — mirrors the `gstOnFile` checklist flag (empty
  is NOT on file; normalizes whitespace + uppercase before matching).
- `sinDigitsSchema` (`z.string().length(9)`) + `isSinValid(cleaned)` —
  mirrors `handleSaveSin`'s length-only check. The SIN checksum/leading-
  digit rule stays server-side only (`backend/utils/sin.py`), unchanged —
  duplicating it client-side was explicitly out of scope before and stays
  out of scope now.

Each helper is a byte-for-byte behavioral match for the check it replaces
— **the two GST predicates were deliberately kept separate**, not merged
into one "more correct" shared function, because merging them would be a
validation-rule change (release gate #2/#5), not a pure extraction.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped `driver-app/app` and `driver-app/utils`
  for every reader of `gstOnFile`, `handleSaveGst`, `handleSaveSin`,
  `gst_bn`, and the old inline regex — `app/driver/payout.tsx` is the only
  caller of all three extracted checks. No other screen, hook, or shared
  component reads the GST/SIN validation logic.
- No data schema, API contract, or background-job change. Client-side
  validation only; the backend remains authoritative and unchanged.
- The three call sites were replaced 1:1 with the imported helper — no
  reordering, no new gating logic, no change to what triggers a save vs.
  what triggers the "toast and return" reject path.

## 5. User-experience effect

None. Driver-facing: identical accept/reject behavior, identical toast
copy, identical checklist-done state for the same input as before. Not
visible mid-session in a way that differs from before (payout setup is
not a live-tested-mid-ride flow).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/package.json` | Added `"zod": "^4.4.3"` | First zod adoption on driver-app |
| `driver-app/yarn.lock` | Added `zod@^4.4.3` as an explicit resolution alongside the pre-existing `zod@^3.25.0 \|\| ^4.0.0"` range (same resolved `4.4.3` version — no new package version) | Lockfile consistency |
| `driver-app/utils/payoutFormsSchema.ts` | New — `gstBnSchema`/`sinDigitsSchema` + `isGstBnValid`/`isGstBnOnFile`/`isSinValid` | Colocated, testable validation |
| `driver-app/utils/__tests__/payoutFormsSchema.test.ts` | New — 20 accept/reject cases | Close validation-coverage gap |
| `driver-app/app/driver/payout.tsx` | Use the three new helpers instead of inline regex/length checks | Pure extraction, no behavior change |
| `ACTION_ITEMS.md` | B39 — recorded step 3 completion | Track migration progress |
| `docs/change-log/2026-08-24-b39-driver-payout-zod-step3.md` | New change-log | Required |

## 7. Before / after

```ts
// Before — handleSaveGst
const cleaned = gstNumber.replace(/\s/g, '');
if (cleaned && !/^\d{9}(RT\d{4})?$/.test(cleaned)) {
  showToast('warning', 'Invalid Format', '...');
  return;
}

// After
const cleaned = gstNumber.replace(/\s/g, '');
if (!isGstBnValid(cleaned)) {
  showToast('warning', 'Invalid Format', '...');
  return;
}
```

```ts
// Before — gstOnFile checklist flag
const gstOnFile = /^\d{9}(RT\d{4})?$/.test((gstNumber || '').replace(/\s/g, '').toUpperCase());

// After
const gstOnFile = isGstBnOnFile(gstNumber);
```

```ts
// Before — handleSaveSin
const cleaned = sinInput.replace(/\D/g, '');
if (cleaned.length !== 9) {
  showToast('warning', 'Invalid Format', 'Your SIN is 9 digits.');
  return;
}

// After
const cleaned = sinInput.replace(/\D/g, '');
if (!isSinValid(cleaned)) {
  showToast('warning', 'Invalid Format', 'Your SIN is 9 digits.');
  return;
}
```

## 8. Rollback plan

**`git-revert-safe`** — pure client-side extraction, no data/schema/config
touched. A revert restores the three inline checks verbatim.

## 9. Verification performed

- [x] 20/20 new `payoutFormsSchema.test.ts` cases pass
- [x] Full driver-app suite: 1089/1089 tests pass (110 suites), 0 regressions
- [x] `npx tsc --noEmit` clean
- [x] `npx eslint` clean on all touched files
- [x] **Real production build**: `npm run build:web` (`expo export --platform web`) completed successfully — not just `tsc`/dev server
- [x] Blast-radius grep performed: `gstOnFile`, `handleSaveGst`, `handleSaveSin`, `gst_bn`, and the old inline regex — `payout.tsx` is the only caller
- [x] Reviewed against B39's own risk note: additive-only, one form, pure extraction, two GST predicates kept intentionally separate rather than merged

## What was NOT verified

- End-to-end manual repro against real Supabase dev / a live driver account
  (client-side validation only; not exercised against the actual
  `PUT /drivers/me` 422 path this mirrors).
- Whether the backend's own GST/BN and SIN format validators use the exact
  same regex/length rule as what's mirrored here — assumed from the existing
  code comments (`gst_bn (CRA Business Number)...`, `backend/utils/sin.py`)
  but not independently re-read against the backend source in this pass.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — grepped, isolated to `payout.tsx`
- [x] No silent behavior change — three checks, byte-for-byte equivalent,
      documented before/after per call site
