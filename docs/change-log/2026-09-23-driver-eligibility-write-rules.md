# Validate age and experience when drivers submit eligibility dates

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

## 1. Issue / gap identified

Direct registration/profile API clients could submit underage/future DOB or future/recent licence issue dates; unpadded dates were also accepted despite the API contract.

## 2. Root cause

Write handlers checked parseability only and trusted the app's client-side age/experience validation.

## 3. Fix / remediation

Require canonical `YYYY-MM-DD` and validate age/three-year experience on every newly submitted value, independent of the go-online rollout flag. Stored rows remain gated by that flag, preserving existing fleet rollout behavior.

## 4. Risk & impact on existing functionality

The change affects only submitted dates in `POST /drivers/register` and `PUT /drivers/me`; existing stored dates and omitted fields are untouched. Grepped both route handlers and tests. Blast radius: driver profile writes. No ride/background/money flow.

## 5. User-experience effect

New/updated values now return 422 with actionable eligibility reasons when malformed or ineligible. Legacy profile edits without eligibility dates remain allowed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/routes/drivers/profile.py | Canonical format, age, and experience validation | Enforce submitted values server-side |
| backend/tests/test_drivers_extended.py | Reject unpadded, underage, and recent-issue inputs | Regression coverage |

## 7. Before / after

```py
# Before
# Only parseability was checked; future/minor/recent values persisted.
```

```py
# After
_validate_eligibility_date(field, payload[field])
```

## 8. Rollback plan

Revert the route validation; no data migration or existing DB update occurred.

## 9. Verification performed

- [x] Targeted backend pytest passed (149 tests across go-online availability and driver profile routes).
- [x] `py_compile` and `git diff --check` passed.
- [x] Blast-radius search and API handler review performed.
- [x] Existing fleet remains controlled by `enforce_driver_eligibility_recheck`; no flag flipped.
- [ ] No staging/live DB writes.

## 10. Sign-off

- [x] Rollback documented.
- [x] Blast radius stated.
