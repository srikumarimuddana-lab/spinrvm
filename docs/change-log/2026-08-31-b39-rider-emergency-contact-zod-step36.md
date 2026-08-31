# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code session |
| Surface(s) | rider-app |
| Domain (Sentry tag) | safety |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B39 — step 36, last item in the entire 21-candidate broader sweep |

## 1. Issue / gap identified

`app/emergency-contacts.tsx`'s `handleAdd` validates a new emergency
contact via two sequential inline checks with no dedicated test
coverage: name required (after trim), and phone must have at least 10
digits after stripping non-digit characters. **No correctness bug was
found** — both checks are logically sound. This step is a pure
extraction.

## 2. Root cause

Ad hoc validation predates zod adoption on this screen, consistent with
every other B39 candidate.

## 3. Fix / remediation

New colocated `rider-app/utils/emergencyContactSchema.ts` extracts the
two checks into `isContactNameValid`, `isContactPhoneValid`, and a
combined `getEmergencyContactFormError` that returns the same `{title,
message}` toast pair for the first failing check, in the same priority
order — a byte-for-byte behavioral mirror of the original two
sequential `if` blocks. Function names and signatures intentionally
mirror driver-app's own `emergencyContactSchema.ts` (B39 step 30, same
validation shape) for cross-surface consistency, kept as a separate
file since rider-app and driver-app don't share a `utils/` module.

The call site's local variable holding the validation result was named
`validationError` from the start (not `error`), applying the lesson
from step 35 (`manage-cards.tsx`), where an `error` variable with a
`.message` field triggered a false-positive on the repo's
`no-restricted-syntax` eslint rule (intended to catch raw API-error
surfacing).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file, one function
  (`handleAdd`).** Grepped `rider-app` for the exact `trimmedPhone.length
  < 10` condition and the "Please enter a contact name." toast copy;
  only two other matches — a `.metro-cache` build artifact (not source,
  irrelevant) and `__tests__/emergencyContactsScreen.test.tsx`, an
  existing UI test that asserts on this exact form. Re-ran that test
  file (20/20 pass) to confirm the extraction didn't change its
  observable behavior.
- **Could this regress a flow that currently works?** For every input
  the original two checks accept or reject, `getEmergencyContactFormError`
  returns byte-for-byte the same result — verified against 8
  accept/reject test cases, including the phone-digit-stripping
  behavior (formatted input like `(123) 456-7890` correctly counts as
  10 digits).
- **Safety-critical interaction:** emergency contacts feed the SOS
  flow — an unreachable phone number silently saved would have real
  safety consequence (per CLAUDE.md's Safety domain and the SOS flow
  described in `.claude/context/domain-safety.md`). This extraction
  preserves the exact same 10-digit-minimum boundary; no weakening or
  strengthening of the check.
- **Dispatch / ride state machine:** not implicated — this is a
  standalone contact-management screen, no interaction with active
  rides.

## 5. User-experience effect

Rider-facing, on the "Add Emergency Contact" form. No behavior change
for any input — same toast titles/messages, same validation order,
same accept/reject boundary. Not visible mid-session in any way a rider
would notice.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/utils/emergencyContactSchema.ts` | New file — 2 predicates + `getEmergencyContactFormError` | Pulls the inline two-check block into a colocated, independently testable module |
| `rider-app/utils/__tests__/emergencyContactSchema.test.ts` | New file — 8 accept/reject unit tests | Pins the extracted behavior so a future edit can't silently change the validation boundary |
| `rider-app/app/emergency-contacts.tsx` | `handleAdd`'s 2 sequential `if` blocks replaced with a call to `getEmergencyContactFormError`; import added | Same behavior, now covered by tests |

## 7. Before / after

```ts
// Before
const handleAdd = async () => {
  const trimmedName = name.trim();
  const trimmedPhone = phone.trim().replace(/\D/g, '');

  if (!trimmedName) {
    showToast('Missing Name', 'Please enter a contact name.', 'warning');
    return;
  }
  if (trimmedPhone.length < 10) {
    showToast('Invalid Phone', 'Please enter a valid phone number (at least 10 digits).', 'warning');
    return;
  }
  setSaving(true);
  // ...
};
```

```ts
// After
import { getEmergencyContactFormError } from '../utils/emergencyContactSchema';

const handleAdd = async () => {
  const trimmedName = name.trim();
  const trimmedPhone = phone.trim().replace(/\D/g, '');

  const validationError = getEmergencyContactFormError(name, phone);
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
regression risk, only a loss of test coverage. No backend change to
roll back; no already-applied production data (this is a client-side
pre-submit validation gate, not a completed `POST
/users/emergency-contacts` call) is affected.

## 9. Verification performed

- [x] Automated tests run — unit only:
  `npx jest utils/__tests__/emergencyContactSchema.test.ts` — 8/8 pass.
  Existing UI test `__tests__/emergencyContactsScreen.test.tsx` re-run:
  20/20 pass, unchanged. Full suite: `npx jest` — 138/138 suites,
  1933/1933 tests pass, zero failures.
- [ ] Manual repro steps followed in staging — not done; no staging
  access from this session. The `POST /users/emergency-contacts` call
  was not exercised against a real backend.
- [x] Blast-radius grep performed — searched `rider-app` for the exact
  phone-length condition and the missing-name toast copy; only the
  existing UI test and a build-cache artifact matched, both confirmed
  non-issues.
- [x] Reviewed against relevant CLAUDE.md convention(s) — Safety: this
  is the client-side gate before emergency contacts feed the SOS flow;
  the fix preserves the exact same 10-digit-minimum phone boundary, no
  weakening.
- [x] Money/state-machine dry run (release-gate item 4): not directly
  applicable — no money or state-machine path touched, no bug fixed,
  no behavior change.

`npx tsc --noEmit`: clean. `npx eslint` on the three touched files:
clean (the `validationError` naming avoided the false-positive that
step 35 had to fix after the fact). **Real production build**
(`npm run build:web` → `expo export --platform web`) completed
successfully.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no
  bug-reintroduction risk since no bug was fixed)
- [x] Blast radius is stated, not assumed (grepped, isolated to one
  file, one function, fully replaced; the one other match — an
  existing UI test — was re-run and confirmed unaffected)
- [x] No silent behavior change to an already-shipped flow — this step
  is a pure extraction; no bug found, no behavior change made or
  needed.

## What was NOT verified

- Not tested against a real `POST /users/emergency-contacts` call or
  the backend's own validation — no staging access from this session.
- No visual regression tooling exists for rider-app (per CLAUDE.md, no
  automated visual/snapshot regression tooling exists for this surface)
  — not applicable here regardless, no visual/UI change in this diff.
- Whether the SOS flow itself correctly uses the saved contact's phone
  number end-to-end was not re-verified here — out of scope for this
  validation-extraction step; this fix only concerns what's allowed to
  be saved.
- This is the last candidate from the 2026-08-31 broader sweep — no
  further B39 candidates from that specific 21-item list remain open,
  though B39 itself (the underlying "adopt zod, migrate one form at a
  time" item) is not marked complete, since its scope was never
  "migrate every form in the codebase."
