# Collect eligibility dates in the driver application

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | driver-app, backend |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

## 1. Issue / gap identified

The actual Become a Driver application wizard did not collect DOB or licence issue date, so an applicant could complete registration yet be blocked at go-online when the eligibility recheck flag was on.

## 2. Root cause

The wizard's Personal step validated name, email, gender, and service area only; its registration payload omitted eligibility dates.

## 3. Fix / remediation

Add required ISO date inputs, validate adult age and three years of experience before advancing, preserve both fields in the local application draft, and send them through the existing registration request.

## 4. Risk & impact on existing functionality

Only new Become a Driver submissions are affected; stored fleet rows are untouched and the feature flag is unchanged. The existing registration endpoint now validates submitted date values. Blast radius: driver onboarding and registration. No ride, background, or money path.

## 5. User-experience effect

Applicants must provide eligible dates during the Personal step and receive an alert explaining malformed or ineligible values. Draft recovery restores the fields. Existing drivers can edit values through the profile form.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| driver-app/app/become-driver.tsx | Add inputs, validation, draft persistence, and registration fields | Complete the actual driver application flow |
| driver-app/__tests__/app/becomeDriverScreen.test.tsx | Assert registration payload and draft restoration | Verify field persistence through the wizard |

## 7. Before / after

```ts
// Before
registerDriver({ first_name, last_name, email, gender, city, service_area_id });
```

```ts
// After
registerDriver({ first_name, last_name, email, gender, city, date_of_birth, license_issue_date, service_area_id });
```

## 8. Rollback plan

Revert the additive wizard fields/payload; no migration or live data modification occurred.

## 9. Verification performed

- [ ] Driver-app Jest and production build not run; dependencies are absent from this worktree.
- [x] Shared utility tests cover malformed, underage, and insufficient-experience dates.
- [x] Backend registration/profile tests passed (149 tests in the targeted suite before UI change).
- [x] No feature flags enabled or live DB writes.
- [ ] No mobile visual regression tooling.

## 10. Sign-off

- [x] Rollback plan documented.
- [x] Blast radius stated.
- [x] Applicant and legacy fleet effects described.
