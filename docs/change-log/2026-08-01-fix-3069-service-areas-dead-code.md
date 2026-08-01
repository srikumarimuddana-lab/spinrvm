# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch: `claude/fix-3069-service-areas-dead-code`) |
| Related issue or gap ID | #3069 |

## 1. Issue / gap identified

`backend/routes/admin/service_areas.py`'s `admin_update_service_area` handler
had a manual `surge_multiplier` range check (`sm < 1.0 or sm > _SURGE_MAX`,
raising a 400) that could never execute: `ServiceAreaUpdateRequest`'s Pydantic
field is declared `Field(ge=1.0, le=10.0)`, numerically identical to
`_SURGE_MAX = 10.0`, so any out-of-range value already 422s at the Pydantic
validation layer before the handler body runs.

## 2. Root cause

Defensive re-validation that duplicated a constraint already enforced one
layer up (Pydantic request-body validation), left over from before the
`Field(ge=..., le=...)` bounds were added (or added redundantly alongside
them). Not a correctness bug — out-of-range values were always correctly
rejected — just dead code that never executes and slightly misleads a reader
into thinking the handler enforces this itself.

## 3. Fix / remediation

Removed the unreachable `if sm < 1.0 or sm > _SURGE_MAX:` branch and its
400 response from `admin_update_service_area`. The surrounding
"disable-surge escape hatch" and "auto-mode-cap justification gate" logic
(the >2.5× branch) is real business logic, not simple range validation, and
is untouched. `_SURGE_MAX` itself is kept (referenced in a comment
explaining why the range check now lives solely in the Pydantic `Field`) —
not removed, since it still documents the intended ceiling.

Added a regression test (`test_surge_multiplier_out_of_range_rejected_by_pydantic_422`)
pinning that the boundary is still enforced, now purely via Pydantic
`ValidationError` on `ServiceAreaUpdateRequest` construction, replacing the
stale code-comment note that previously just described the bug without a
test.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to `admin_update_service_area`'s surge_multiplier
  validation path**. Grepped `backend/` for other callers of
  `admin_update_service_area` and other readers of `_SURGE_MAX`: none found
  outside this file.
- Behavior change: none for valid API consumers. A client sending an
  out-of-range `surge_multiplier` (e.g. `15.0` or `0.5`) previously got a
  Pydantic 422 anyway (the dead branch never ran) — this fix doesn't change
  what status code or response shape a caller sees for any input, in-range
  or out-of-range. It only removes code that could never execute.
- Justification-gate logic (>2.5× requires written justification, `surge_enabled`
  disable escape hatch) is completely unchanged — same conditions, same 400
  responses, same audit-log call.

## 5. User-experience effect

None — internal-admin-facing endpoint, and the actual observable API
behavior (status codes, error messages) for every input is unchanged. Only
unreachable server-side code was removed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/service_areas.py` | Removed unreachable manual range-check branch in `admin_update_service_area` | Dead code cleanup per issue #3069 |
| `backend/tests/test_admin_service_areas_coverage.py` | Replaced stale bug-documenting comment with a regression test pinning Pydantic-layer enforcement | Test must reflect the corrected code, not describe a bug that no longer exists |
| `docs/change-log/2026-08-01-fix-3069-service-areas-dead-code.md` | New change-log entry | Required per CLAUDE.md for a merged fix |

## 7. Before / after

```python
# Before
if area.surge_multiplier is not None:
    sm = float(area.surge_multiplier)
    if sm < 1.0 or sm > _SURGE_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"surge_multiplier must be between 1.0 and {_SURGE_MAX}",
        )
    # Disabling surge clears the multiplier to 1.0 below, so an above-cap
    ...
```

```python
# After
if area.surge_multiplier is not None:
    sm = float(area.surge_multiplier)
    # Disabling surge clears the multiplier to 1.0 below, so an above-cap
    ...
```

## 8. Rollback plan

`git revert` — pure dead-code removal, no schema/migration, no live-data
mutation, no observable behavior change for any valid or invalid request.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_admin_service_areas_coverage.py -q --no-cov` — 33 passed (32 existing + 1 new regression test).
- [x] Full backend suite run: `pytest backend/tests/ -q --no-cov` — see PR body for final pass/fail counts.
- [ ] Manual repro against staging — not performed; no staging environment exists (tracked separately, ACTION_ITEMS E1).
- [x] Blast-radius grep performed: confirmed no other callers of `admin_update_service_area` or other readers of `_SURGE_MAX` outside this file.
- [x] Reviewed against CLAUDE.md conventions: surge-cap invariants (2.5× auto-mode cap, justification-gate for manual override) fully preserved and untouched — see `@.claude/context` surge rules.
- [ ] Feature-flagged — not applicable; pure dead-code removal with zero observable behavior change.

## 10. What was NOT verified

- Not verified against the real admin-dashboard frontend's error handling for out-of-range surge_multiplier input, since the status code/response shape for that case is unchanged by this fix (still a 422 from FastAPI's Pydantic validation, exactly as before).
