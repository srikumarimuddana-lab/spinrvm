# Validate self-service driver eligibility dates

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | driver-app |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

## 1. Issue / gap identified

The app needs consistent validation for the DOB and licence issue date that go-online eligibility checks require.

## 2. Root cause

There was no shared client-side date/age validation for these eligibility fields.

## 3. Fix / remediation

Add a small reusable schema requiring strict real ISO calendar dates, adulthood, and at least three years of experience.

## 4. Risk & impact on existing functionality

Blast radius: driver-app forms importing this utility. Grepped existing eligibility fields and profile setup/profile screens. No current callers changed in this commit. No API, ride, background, or payment behavior changes.

## 5. User-experience effect

The shared validation provides field-specific errors to the onboarding and profile forms added in follow-up UI commits. No deployed screen changes in this commit.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| driver-app/utils/eligibilityProfileSchema.ts | Shared strict date and age/experience validation | Keep onboarding and profile rules consistent |
| driver-app/utils/__tests__/eligibilityProfileSchema.test.ts | Boundary and malformed date cases | Prevent eligibility validation regressions |

## 7. Before / after

Purely additive utility; no existing behavior changed.

## 8. Rollback plan

Remove the additive schema and test; no state or DB changes.

## 9. Verification performed

- [ ] Jest pending JS dependencies.
- [x] Grepped current driver app eligibility field uses.
- [x] No live DB writes or feature flags changed.
- [ ] Production build not run; no screen changed in this commit.

## 10. Sign-off

- [x] Rollback plan documented.
- [x] Blast radius stated.
