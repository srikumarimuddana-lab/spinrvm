# Driver eligibility fields are self-service writable

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

## 1. Issue / gap identified

The registration endpoint accepted DOB and licence issue date, but the driver's profile PUT contract did not, leaving existing users without an app-backed update path.

## 2. Root cause

`UpdateDriverProfileRequest` and its field whitelist omitted both eligibility values.

## 3. Fix / remediation

Add both ISO date fields to the self-service profile schema and write allowlist; reject malformed dates with 422 before DB writes. Edits follow existing vehicle/document re-review behavior for active drivers.

## 4. Risk & impact on existing functionality

`PUT /drivers/me` and registration both write the `drivers` row; status checks read DOB and issue date on go-online. Grepped these handlers and the test suite. Blast radius: backend driver profile and go-online contract. No ride, background, or money paths. Active profile edits already re-review vehicle/document changes; dates now follow that same established behavior.

## 5. User-experience effect

Profile clients can save these dates and receive a specific format error. Active drivers editing eligibility details are taken offline for re-review by existing policy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/routes/drivers/profile.py | Request fields, allowlist, date validation | Make profile PUT persist valid dates |
| backend/tests/test_drivers_extended.py | Persistence and invalid-input regression tests | Verify contract and reject before write |

## 7. Before / after

```py
# Before
updates = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in allowed_fields}
# DOB and licence issue date were absent from allowed_fields.
```

```py
# After
for field, label in (("date_of_birth", "Date of birth"), ("license_issue_date", "Licence issue date")):
    if field in updates:
        datetime.strptime(updates[field], "%Y-%m-%d")
```

## 8. Rollback plan

Revert this code change. No schema or live database change was made.

## 9. Verification performed

- [ ] Automated tests pending shared pytest environment.
- [ ] No staging or live DB writes.
- [x] Grepped profile/status callers and eligibility tests.
- [x] Reviewed against driver eligibility conventions in `CLAUDE.md`.
- [x] No feature flags changed.
- [x] `git diff --check` and `python -m py_compile backend/routes/drivers/profile.py` passed.

## 10. Sign-off

- [x] Rollback plan documented.
- [x] Blast radius stated.
- [x] Shipped-flow effect described.
