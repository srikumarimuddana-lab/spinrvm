# Collect eligibility dates during driver onboarding

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | driver-app |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

## 1. Issue / gap identified

Driver onboarding did not collect DOB or licence issue date, despite the go-online gate requiring these values when eligibility recheck is enabled.

## 2. Root cause

The onboarding form sent only account profile fields and service area to registration.

## 3. Fix / remediation

Add required date inputs, validate legal age and minimum experience, and send dates through the existing `registerDriver` payload.

## 4. Risk & impact on existing functionality

Grepped the onboarding screen, `registerDriver` contract, and existing screen tests. Blast radius: driver onboarding only. New applicants must now supply valid eligibility dates; existing profiles are unaffected. Registration persists them to the already-existing driver columns. No ride, background, payment, or feature-flag change.

## 5. User-experience effect

New onboarding screens show two date fields and clear validation feedback. Not visible mid-session to existing drivers.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| driver-app/app/profile-setup.tsx | DOB/issue date fields and validated registration payload | Collect eligibility values on signup |
| driver-app/__tests__/app/profileSetupScreen.test.tsx | Fill form and assert payload | Verify values reach existing registration contract |

## 7. Before / after

```ts
// Before
await registerDriver({ service_area_id: serviceAreaId, city });
```

```ts
// After
await registerDriver({ service_area_id: serviceAreaId, city, date_of_birth: dateOfBirth, license_issue_date: licenseIssueDate });
```

## 8. Rollback plan

Revert the screen change; no database migration or existing data mutation occurred.

## 9. Verification performed

- [ ] Jest / production build pending JS dependency availability.
- [ ] No staging run.
- [x] Grepped registration contract and onboarding tests.
- [x] Shared date utility tests cover age/experience boundaries and malformed values.
- [x] No feature flags enabled or live DB writes.
- [ ] No visual regression tooling for driver-app; screen was not screenshotted.

## 10. Sign-off

- [x] Rollback plan documented.
- [x] Blast radius stated.
- [x] Onboarding user effect described.
