# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch: `claude/fix-3068-payout-stats-route-order`) |
| Related issue or gap ID | #3068 |

## 1. Issue / gap identified

`GET /api/admin/payouts/stats` was 404ing because `GET /payouts/{payout_id}` was
registered before it in `backend/routes/admin/rides.py`. FastAPI/Starlette match
routes in registration order, so a request to `/payouts/stats` was captured by
`/payouts/{payout_id}` with `payout_id="stats"`, hit `db.find_one("payouts", {"id": "stats"})`
(no match), and returned a 404 "Payout not found" — `admin_get_payout_stats` was
never reached. The admin dashboard's payout-stats panel likely 404s in production.

## 2. Root cause

The file already had a comment directly above the `{payout_id}` route stating
"IMPORTANT: keep this BEFORE `@router.get('/payouts/{payout_id}')`" — but the
actual `@router.get("/payouts/stats")` handler was defined ~270 lines further
down in the file, after the `{payout_id}` route, violating the comment's own
stated invariant. Route order in FastAPI is registration order, not
specificity order — a literal path segment must be registered before a
path-parameter route that would otherwise shadow it.

## 3. Fix / remediation

Moved `admin_get_payout_stats` (and its `@router.get("/payouts/stats")`
decorator) from its old location (after `/payouts/close-period`) to
immediately above `admin_get_payout`'s `@router.get("/payouts/{payout_id}")`
registration — exactly where the existing comment already said it belonged.
No logic inside either handler changed; this is a pure reordering fix.

Also updated the regression test in `backend/tests/test_admin_rides_coverage.py`
(originally added in PR #3057 to pin the *buggy* behavior) to now assert the
*fixed* behavior: `GET /payouts/stats` returns 200 with the expected stats
shape instead of a shadowed 404.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to `backend/routes/admin/rides.py`'s payout routes**.
  Grepped for other registrations of `/payouts` paths in this file and confirmed
  no other literal `/payouts/<segment>` route exists that could newly collide
  with `/payouts/{payout_id}` after this reorder (only `/payouts`,
  `/payouts/overview`, `/payouts/close-period`, and now `/payouts/stats` — all
  either already registered above `{payout_id}` or moved above it by this fix).
- `admin_get_payout` (`GET /payouts/{payout_id}`) itself is unchanged — only its
  position in the file relative to `admin_get_payout_stats` moved, which is
  what unblocks `/payouts/stats` from being shadowed. A real payout ID request
  (e.g. `/payouts/abc-123`) still reaches `admin_get_payout` exactly as before,
  since `abc-123` never matches the literal `/payouts/stats` path.
- No other backend module imports or calls `admin_get_payout_stats` directly
  (it's only reachable via its route) — grepped `backend/` for the function name.

## 5. User-experience effect

Internal-admin-facing only. The admin dashboard's payout-stats panel should
now load correctly instead of 404ing. Not visible to riders, drivers, or
corporate admins. Not visible mid-session to anyone already using a
non-admin surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | Moved `admin_get_payout_stats` route registration above `admin_get_payout`'s `{payout_id}` route | Fixes route-shadowing 404 on `/payouts/stats`, per issue #3068 |
| `backend/tests/test_admin_rides_coverage.py` | Updated the pinned-bug regression test to assert fixed (200) behavior instead of buggy (404) behavior | Test must reflect the corrected contract |
| `docs/change-log/2026-08-01-fix-3068-payout-stats-route-order.md` | New change-log entry | Required per CLAUDE.md for a behavior-changing fix |

## 7. Before / after

```python
# Before (routes/admin/rides.py) — /payouts/stats registered AFTER /payouts/{payout_id}
@router.get("/payouts/{payout_id}")
async def admin_get_payout(payout_id: str, ...): ...
# ... ~270 lines later ...
@router.get("/payouts/stats")
async def admin_get_payout_stats(): ...
```

```python
# After — /payouts/stats registered BEFORE /payouts/{payout_id}
@router.get("/payouts/stats")
async def admin_get_payout_stats(): ...


@router.get("/payouts/{payout_id}")
async def admin_get_payout(payout_id: str, ...): ...
```

## 8. Rollback plan

`git revert` — pure code-ordering change, no schema/migration, no live-data
mutation. Reverting restores the previous (buggy but previously-shipped)
route order with no other side effects.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_admin_rides_coverage.py -q --no-cov` — 81 passed (including the updated regression test now asserting 200 + correct response shape).
- [x] Full backend suite run: `pytest backend/tests/ -q --no-cov` — see PR body for final pass/fail counts.
- [ ] Manual repro against staging — not performed; no staging environment exists for this repo (tracked separately as a standing gap, ACTION_ITEMS E1).
- [x] Blast-radius grep performed: confirmed no other `/payouts/<literal>` route collides with the reordered `{payout_id}` route; confirmed `admin_get_payout_stats` has no other callers.
- [x] Reviewed against CLAUDE.md conventions: no money arithmetic changed (still `Decimal(str(...))` via the pre-existing `_d()` helper, untouched), dual-import pattern unaffected, no ride-state-machine interaction (this is payout/admin surface, not ride lifecycle).
- [ ] Feature-flagged — not applicable; this is a bugfix restoring intended behavior on an admin-only, already-broken endpoint, not a new user-visible change requiring staged rollout.

## 10. What was NOT verified

- Not verified against the real production admin dashboard frontend — confirmed only that the backend endpoint's contract (200 + expected keys) is restored via the backend test suite. If the admin-dashboard frontend has any client-side workaround/retry logic built around the previous 404, that was not checked (out of scope for a backend-only PR; worth a quick manual check post-merge).
- No visual/snapshot regression tooling exists for the admin dashboard, so any UI-level confirmation of the payout-stats panel rendering correctly was not performed here (backend-only verification).
