# Reject malformed eligibility data at driver registration

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

## 1. Issue / gap identified

The registration handler accepted raw eligibility date and vehicle-year values into the driver row, allowing malformed data to wait until a later go-online attempt.

## 2. Root cause

The registration handler copied whitelisted request keys without validating those three fields.

## 3. Fix / remediation

Reject malformed ISO dates and vehicle years before either registration insert or update. Year parsing preserves numeric JSON values and supports through next model year.

## 4. Risk & impact on existing functionality

`POST /drivers/register` is used by new applications and resubmissions; `PUT /drivers/me` is separately validated. Grepped both handlers and registration tests. Blast radius: backend registration. Valid payloads unchanged; malformed inputs receive 422. No ride, background, payment, or schema change.

## 5. User-experience effect

Clients receive a specific 422 for malformed eligibility values rather than saving data that later fails the online gate. No live-session effect.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/routes/drivers/profile.py | Validate registration eligibility dates and vehicle year | Block malformed values at persistence boundary |
| backend/tests/test_drivers_extended.py | Verify malformed values are rejected before insert | Regression coverage |

## 7. Before / after

```py
# Before
payload = {k: v for k, v in body.items() if k in allowed and v is not None}
```

```py
# After
# Validate date strings and vehicle year before insert/update; malformed data returns 422.
```

## 8. Rollback plan

Revert this validation code; no data migration or live DB mutation occurred.

## 9. Verification performed

- [x] `pytest backend/tests/test_go_online_availability.py backend/tests/test_drivers_extended.py -q -o addopts=` passed (143 tests).
- [x] `py_compile` and `git diff --check` passed.
- [x] Registration route and profile/status consumers grepped.
- [ ] No staging/live DB writes.
- [x] No flags changed.

## 10. Sign-off

- [x] Rollback plan documented.
- [x] Blast radius stated.
