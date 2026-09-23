# Driver eligibility rejects malformed profile values

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

## Issue / gap identified

With eligibility recheck enabled, nonempty malformed date and vehicle-year values parsed as absent and passed the raw-value required check.

## Root cause

The gate treated parse failure as `None`, then tested raw truthiness for required fields.

## Fix / remediation

Malformed values join the missing-eligibility response and block go-online; valid underage, experience, and vehicle-age outcomes retain their specific checks.

## Risk & impact on existing functionality

- The same `update_driver_status` path that controls `is_online` and availability now rejects malformed eligibility data. Valid values and the feature-flag-off path are unchanged.
- Blast radius: backend driver go-online path. Grepped `backend/routes/drivers/status.py`, `backend/tests/test_go_online_availability.py`, and driver eligibility field consumers.
- No ride state, background loop, or money path changes.

## User-experience effect

Drivers with malformed data receive the existing actionable “Update your profile” required-field error when the recheck flag is on. No mid-session change; the check applies at go-online.

## Files modified

| File path | What changed | Why |
|---|---|---|
| backend/routes/drivers/status.py | Mark invalid DOB, issue date, and vehicle year as missing eligibility | Prevent fail-open parsing |
| backend/tests/test_go_online_availability.py | Add malformed-value regressions | Lock down rejection behavior |

## Before / after

```py
# Before
except ValueError:
    dob_date = None

# After
except ValueError:
    invalid_eligibility.append("date of birth")
```

## Rollback plan

Disable `enforce_driver_eligibility_recheck` in app settings to restore the prior gated behavior; a code revert is also available before rollout.

## Verification performed

- [ ] Automated tests run: attempted `pytest backend/tests/test_go_online_availability.py -q -k malformed_required`; pytest is not installed in this environment.
- [ ] Manual repro in staging: not run; no live DB or feature-flag changes made.
- [x] Blast-radius grep performed for eligibility fields and go-online callers.
- [x] Reviewed against Saskatchewan eligibility rules in `CLAUDE.md`.
- [x] Existing rollout flag remains unchanged; no flag enabled.
- [x] `python -m py_compile backend/routes/drivers/status.py` and `git diff --check` passed.

## Sign-off

- [x] Rollback plan is concrete.
- [x] Blast radius stated.
- [x] Existing shipped-flow UX effect described.
