# Let existing drivers edit eligibility details

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | driver-app, backend |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

## 1. Issue / gap identified

Existing drivers could not enter or correct DOB and licence issue date in the app, even when the go-online gate told them to update their profile.

## 2. Root cause

The profile screen only exposed licence number and class; it had no eligibility-date editor.

## 3. Fix / remediation

Extend the existing licence editor with DOB and licence issue date, validate using the shared age/experience rules, and save through `PUT /drivers/me`. The editor remains accessible from Personal Info after fields are complete.

## 4. Risk & impact on existing functionality

Profile edits use existing `/drivers/me` vehicle/document handling; active drivers with changed licence/eligibility values are taken offline for re-review, consistent with current licence edits. Grepped the profile editor and API route. Blast radius: driver profile only. No ride state, background, payment, or live DB changes.

## 5. User-experience effect

Drivers can open “Licence & eligibility details” from Personal Info, with an incomplete-profile banner for missing values. Invalid age/experience/date inputs get actionable feedback before save. Changes while active trigger the existing re-review flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| driver-app/app/driver/(tabs)/profile.tsx | Add eligibility inputs, validation, API payload, and entry point | Enable existing-driver self-service |

## 7. Before / after

```ts
// Before
await api.put('/drivers/me', { license_number, license_class });
```

```ts
// After
await api.put('/drivers/me', { license_number, license_class, date_of_birth, license_issue_date });
```

## 8. Rollback plan

Revert the profile-screen change. No migration or live data change is involved.

## 9. Verification performed

- [x] Backend contract/persistence tests passed in the shared pytest environment (140 tests across go-online availability and driver profile tests).
- [ ] Driver-app Jest and production build not run; JS dependencies are absent from this worktree and available sibling paths.
- [ ] No staging or visual regression run; rider/driver apps have no visual tooling.
- [x] Grepped profile screen and `PUT /drivers/me` field handlers.
- [x] Shared utility tests cover malformed, underage, and insufficient-experience values, but Jest could not be run.
- [x] No flags enabled and no live DB writes.

## 10. Sign-off

- [x] Rollback plan documented.
- [x] Blast radius stated.
- [x] Existing-driver UX effect described.
