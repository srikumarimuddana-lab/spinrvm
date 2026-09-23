# Preserve next-model-year eligibility

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend |
| Domain | drivers |
| PR / commit link | Local review5722/eligibility commit |
| Related issue or gap ID | PR #5722 eligibility review |

The go-online malformed vehicle-year validation now preserves the existing accepted next-model-year window (`current year + 1`), matching `validate_vehicle_year`. It still rejects nonnumeric values and years beyond that window. This is isolated to the flag-gated go-online eligibility check; flag-off behavior and normal model-year vehicles are unchanged. No DB writes, migrations, or flag changes.

Verification: targeted go-online/profile tests passed (143 tests); `py_compile` and `git diff --check` passed. No staging or live database verification.

Rollback: revert the one-line bound change; no data rollback is needed.
