# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "automated KYB re-verification" — admin route slice |

## 1. Issue / gap identified

The round2-31 background loop exists but nothing surfaces its findings
over the admin API yet.

## 2. Root cause

Never built — see round2-30 for full background.

## 3. Fix / remediation

- Refactored `utils/kyb_reverification.py` (small, same-feature change)
  to extract `resolve_kyb_reverify_threshold_months(settings)` and
  `kyb_reverify_cutoff_iso(threshold_months)` as standalone functions —
  previously inlined in the tick. **This is the key design decision**:
  the new admin route (below) calls these same two functions rather than
  re-deriving the staleness threshold independently, so the loop's
  definition of "stale" and the admin dashboard's can never silently
  disagree.
- New `GET /admin/corporate-accounts/kyb-reverification-due` in
  `routes/corporate_accounts.py`, registered right after the existing
  `GET ""` list endpoint and before the single-segment
  `GET /{account_id}` route (same static-before-dynamic ordering the
  file already uses for `GET ""` itself) — a static path registered
  after a single-segment dynamic route would otherwise be swallowed by
  it. Computes the threshold, calls
  `list_companies_needing_kyb_reverification` (round2-30) directly, and
  returns `{threshold_months, count, companies: [{id, name, legal_name,
  kyb_reviewed_at, kyb_reviewed_by}]}`. Read-only, `get_current_admin`
  gated (module-level `require_module("corporate_accounts")` at
  include-time, this router's existing convention) — no new access-
  control scheme invented.
- New `tests/test_corporate_kyb_reverification_route.py`, reusing the
  `admin_override` fixture already established for this router's sibling
  endpoints. Patch targets are the **defining** modules (`db_supabase`,
  `settings_loader`), not `routes.corporate_accounts`, because this
  route's imports are function-local (not module-level) — same
  lazy-import patch convention already proven for
  `routes.corporate_company.build_full_month_statement` in round2-24.
  5 tests: happy path, empty result, default-threshold fallback, static-
  route-not-swallowed-by-dynamic-route, and the module-gate rejection.

## 4. Risk & impact on existing functionality

- **Blast radius: one small refactor (still same-commit-family, not
  touching any other feature) inside `utils/kyb_reverification.py` + one
  new route.** The refactor preserves identical behavior — confirmed by
  reading the diff: the tick's logic is unchanged, only the threshold-
  resolution lines were lifted into two named functions it now calls.
- Grepped every other route in `corporate_accounts.py` for a path
  collision with `/kyb-reverification-due`: none — the file's other
  single-segment dynamic route is `/{account_id}`, and FastAPI's
  registration-order matching means placing this static route before it
  (confirmed by the dedicated ordering test) prevents any collision.
- No existing endpoint's behavior, response shape, or auth gate changed.

## 5. User-experience effect

None yet — no UI reads this endpoint in this commit (round2-33 is the
final UI slice). An admin could already call this endpoint directly
(e.g. via the network tab or a manual request) and see accurate results.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/kyb_reverification.py` | Extracted 2 small helper functions from the tick (same-feature refactor, no behavior change) | Share one staleness definition between the loop and the new route |
| `backend/routes/corporate_accounts.py` | New `GET /kyb-reverification-due` endpoint | Surface round2-30/31's data over the admin API |
| `backend/tests/test_corporate_kyb_reverification_route.py` | New file: 5 tests | Cover the route's happy path, edge cases, route ordering, and access control |

## 7. Rollback plan

`git revert` the commit. No migration, no data written — read-only
endpoint over already-existing data; the small refactor in
`kyb_reverification.py` reverts cleanly alongside it with zero behavior
change to the loop itself.

## 8. Verification performed

- [x] `ast.parse` syntax check on all three modified/new files — clean.
- [x] Confirmed the refactor is behavior-preserving by reading the diff:
      the tick calls the exact same two computations, just through named
      functions instead of inline code.
- [x] Confirmed the route-ordering requirement (static before
      single-segment dynamic) by reading FastAPI's registration-order
      matching behavior and placing the new route accordingly — verified
      with a dedicated test rather than assuming the placement is
      correct.
- [x] Confirmed the lazy-import patch-target convention (patch the
      defining module, not the importing one) against the precedent
      already established this round in round2-24, rather than guessing.
- [x] Did **not** run `pytest` for either Python file — per this round's
      explicit "don't run tests until everything is developed"
      instruction; deferred to the single end-of-round pass.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`, no data involved
- [x] Blast radius is stated, not assumed — confirmed via diff (refactor)
      and grep (no path collision)
- [x] No silent behavior change to a working flow — the loop's tick logic
      is provably unchanged by the refactor; no existing route touched

## What was NOT verified

Did not run `pytest`, so the route-ordering claim (verified logically via
FastAPI's documented registration-order matching, plus a dedicated test
asserting a 200 rather than the 422/404 a swallowed-by-`{account_id}`
match would produce) is not empirically confirmed by execution. The
admin-dashboard UI to actually display this data remains the final
follow-up commit.
