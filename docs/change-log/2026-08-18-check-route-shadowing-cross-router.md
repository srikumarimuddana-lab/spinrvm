# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend (dev tooling + test only — no production code path changed) |
| Domain (Sentry tag) | admin |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Flagged in `docs/change-log/2026-08-18-retire-duplicate-faqs-route.md`'s "Additional finding" section (PR #4199) |

## 1. Issue / gap identified

`backend/scripts/check_route_shadowing.py` only detected a literal path shadowed by an earlier *parameterized* route (`/{id}` before `/leaderboard`) **within one router's own registration order**. It did not detect two *separate* routers registering the identical literal path against the same parent router — the exact bug class that let `backend/routes/faqs.py` sit dead-shadowed by `backend/features.py`'s `get_faqs` for months (fixed in PR #4199). A checker that can't catch the bug it exists to catch is worse than no checker: it looks like coverage that isn't there.

## 2. Root cause

The original script's design scope was "one router package's own registration order" — reasonable for its original purpose, but it was never extended when the codebase grew to ~40 separately-authored routers all mounted into one shared `v1_api_router` in `backend/server.py`, which is where cross-router collisions actually happen.

## 3. Fix / remediation

Added a second mode, `--server-mounts <path-to-server.py>`, alongside the original per-package CLI usage (unchanged, verified byte-identical output against the pre-change script on real targets):

- Parses `server.py`'s own `from X import Y as Z` imports and every top-level `PARENT.include_router(ROUTER[, prefix=...])` call, in source order.
- Groups calls by `(parent_router_variable, mount_prefix)` — the same grouping FastAPI itself uses to build the final route table, so intentionally-repeated mounts at *different* prefixes (e.g. `auth_router` mounted at `/api/v1/auth`, `/api/auth`, and `/api/portal/auth` — all deliberate) are never cross-contaminated into one group.
- Within each group, resolves every router alias back to its defining module + its own `APIRouter(prefix=...)` (if any, e.g. `documents_router`'s baked-in `/drivers` prefix) to compute each route's true effective path, then checks the concatenated, registration-ordered route list for both violation classes: the original param-shadow class, and the new exact-literal-duplicate class.
- **Documented, deliberate limitation**: router aliases imported from a *package* (a directory with its own `__init__.py` — currently only `routes.admin`, contributing `admin_router` and `admin_auth_router`) are skipped with a printed note, not walked recursively. Resolving a package's own internal aggregation order correctly would need to recurse into its `__init__.py` the same way the original per-package CLI mode already does, and admin routes carry their own dedicated review surface (`spinr-admin-rbac-reviewer`). This is a scope line, stated explicitly — not a silent gap.
- Refactored the printing logic into `find_server_mount_violations()`, returning structured violation records, so a test can assert on the exact findings rather than parsing printed text.
- **Added `backend/tests/test_check_route_shadowing.py`**, run against the *real* `backend/server.py` on every normal `pytest` run (this repo's `ci.yml` runs the full suite) — so this checker is now an actual CI backstop, not a script nobody invokes. 11 tests: 2 integration tests against the live `server.py`, 9 unit tests on the detection/parsing logic independent of `server.py`'s ever-changing content.

## 4. Additional finding — not fixed, flagged for a decision

**Running the new check against the real `server.py` immediately found a genuine, currently-live instance of the exact bug class it was built to catch**: `features.py`'s `admin_support_router.get("/tickets")` and `.get("/faqs")` are shadowed by `support_router`'s identical, earlier-registered `GET /tickets` / `GET /faqs` in the same `v1_api_router` mount group (`support_router` is included at `server.py`'s line before `admin_support_router`). Checked further: `admin-dashboard`'s actual ticket/FAQ admin CRUD calls `/api/admin/tickets` and `/api/admin/faqs` (`admin-dashboard/src/lib/api/safety-disputes.ts`, `content-area.ts`), served by the separate `routes/admin` package (`routes/admin/support.py` / `support_tickets.py` / `faqs.py`) — **not** `/api/v1/tickets` or `/api/v1/faqs` at all. So `admin_support_router`'s ticket/FAQ handlers appear to be fully dead code (shadowed *and* uncalled by any known client), the same shape as the `routes/faqs.py` finding PR #4199 already fixed — just discovered inside `features.py` this time.

**Not fixed here** — deliberately out of scope for "fix the checker's detection gap," which is what this PR does. Fixing the dead code itself would mean auditing `admin_support_router`'s *other* routes too (`PUT /service-areas/{area_id}/surge[/auto]`, `POST /notifications/send` — not addressed by this finding, not yet checked for the same dead-code pattern) before touching anything, which is a genuinely separate, larger investigation. Recorded in `backend/tests/test_check_route_shadowing.py`'s `_KNOWN_VIOLATIONS` as a closed, documented allowlist (2 entries) so the new regression test doesn't fail CI on a pre-existing issue this PR isn't fixing, while still failing loudly on any *new* violation. Flagging directly for a follow-up decision — this is a real admin-surface finding, not a hypothetical one.

## 5. Risk & impact on existing functionality

- **Blast radius: isolated to dev tooling + tests.** No production code path changed — `backend/scripts/check_route_shadowing.py` is not imported by any application module (confirmed by grep before this change), and `backend/tests/test_check_route_shadowing.py` is a new, additive test file.
- **The original CLI mode's behavior is unchanged** — verified by diffing output against the pre-change script on real targets (`routes/support.py`, `routes/admin`) byte-for-byte identical, including the pre-existing (unrelated) "cannot find include order" quirk on `routes/admin`'s current `__init__.py` structure, which this PR does not touch or fix.
- **No CI workflow currently invokes this script** (grepped `.github/workflows/` — no hits, confirmed both before and after this change). This PR does not add one either — the new regression test enforces the check via the existing full `pytest` run in `ci.yml`, which is lower-risk than introducing a new dedicated CI gate (a gate is a merge-blocking decision with broader process implications; a test is scoped to this repo's existing, already-accepted test-suite gate). Whether to also wire `--server-mounts` into a dedicated guardrail (like `check_corporate_coverage_floor.py` is wired into `ci-guardrails.yml`) is a separate decision, not made here.

## 6. User-experience effect

None — dev tooling and test-only change. No rider/driver/corporate-admin/internal-admin-facing behavior changed.

## 7. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/check_route_shadowing.py` | Added `--server-mounts` mode (cross-router literal-duplicate + param-shadow detection across `server.py`'s full mount graph); refactored violation-finding into `find_server_mount_violations()` for structured testability; original per-package CLI mode unchanged | Close the detection gap that let the `routes/faqs.py`/`features.py` duplication (PR #4199) go unnoticed |
| `backend/tests/test_check_route_shadowing.py` | New file: 2 integration tests against the real `server.py` (with a documented, closed known-issues allowlist for the pre-existing `admin_support_router` finding) + 9 unit tests on the detection/parsing logic | Make the checker an actual CI backstop instead of a script nobody runs |

## 8. Rollback plan

`git-revert-safe` — pure dev-tooling/test addition, no schema, no data, no config/flag, no production code path touched.

## 9. Verification performed

- [x] **`backend/tests/test_check_route_shadowing.py`**: 11/11 passed.
- [x] **Original CLI mode unchanged**: diffed output of the pre-change script vs. this PR's script on `routes/support.py` (single-module target) and `routes/admin` (package target) — byte-identical in both cases, including the pre-existing "cannot find include order" quirk on `routes/admin`.
- [x] **`pytest --collect-only`**: 12,069 tests collected (11 more than before this PR, matching the new test count), zero import errors.
- [x] **`ruff check`** on both touched files: all checks passed.
- [x] **Blast-radius grep**: confirmed `check_route_shadowing.py` is not imported by any application module, and no CI workflow currently invokes it (before or after this change).
- [x] **Manually ran `--server-mounts server.py` against the real file** and independently verified the finding (grepped `admin-dashboard/src` to confirm it calls `/api/admin/tickets`/`/api/admin/faqs`, not `/api/v1/tickets`/`/api/v1/faqs`) before deciding to allowlist rather than silently suppress or unilaterally fix it.
- [ ] Not run against a real/throwaway Supabase schema — not applicable, no DB code touched.

**What was NOT verified**: whether `admin_support_router`'s other two routes (`PUT /service-areas/{area_id}/surge[/auto]`, `POST /notifications/send`) have the same dead-code pattern — flagged as an open question for whoever picks up the follow-up, not investigated further here (would require checking whether the admin dashboard calls those specific `/api/v1/...` paths, which this PR's scope didn't cover).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow: dev tooling + tests only
- [x] Found-but-out-of-scope issue (`admin_support_router`'s dead ticket/FAQ handlers, section 4) surfaced explicitly, documented in a closed test allowlist rather than silently suppressed, and flagged for a follow-up decision
