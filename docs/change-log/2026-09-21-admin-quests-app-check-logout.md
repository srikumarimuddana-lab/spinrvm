# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | agent |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (uncommitted at write time) |
| Related issue or gap ID | Admin logged out every time the Quests page is opened |

## 1. Issue / gap identified

Opening **Quests & Bonus Challenges** in the admin dashboard logged the admin out on every visit.

## 2. Root cause

The Quests page calls `/api/v1/quests/admin/*`. It was the only live dashboard page whose staff endpoints existed solely under `/api/v1/`.

Production App Check enforcement came back on around 2026-09-19: the `APP_CHECK_ENFORCEMENT=off` Fly secret from the 2026-09-17 kill-switch entry is no longer set. `/api/v1/` is App-Check-enforced, and only `/api/admin/` is exempt (`_APP_CHECK_EXEMPT_PREFIXES`, `backend/core/middleware.py`), because the dashboard is a browser app that can't send `X-Firebase-AppCheck`. So every quest call got `401 {"detail":"App Check token required"}` before admin auth ran. `admin-dashboard/src/lib/api/client.ts` treats any 401 as an expired session: silent refresh → retry → 401 again → `logout()` + redirect to `/login`.

Evidence:
- Fly logs, one page open, 2026-09-21 15:54:14–15 UTC: `GET /api/v1/quests/admin/list` 401 → `GET /api/admin/service-areas` **200 with the same token** → `POST /api/admin/auth/refresh` 200 → `GET /api/v1/quests/admin/list` 401 → `POST /api/admin/auth/logout`.
- Unauthenticated probes of `api-spinr.spinr.ca`: `/api/v1/quests/admin/list` returns `App Check token required`; `/api/admin/service-areas` returns `No authorization token provided` (it reaches the JWT layer).

`resetSurgeToAuto()` (`pricing.ts`) has the same latent problem: it targets `PUT /api/v1/service-areas/{id}/surge/auto`, which also returns `App Check token required`. **But no UI calls it.** The "Reset to auto-surge" button saves through `PUT /api/admin/service-areas/{id}`. So it wasn't causing logouts. It's fixed here only so the function no longer points at a path the browser can't reach.

## 3. Fix / remediation

The same handlers are now also served under the App-Check-exempt `/api/admin/` namespace, with the module gates their `/api/admin` siblings use, and the dashboard calls them there:
- `routes/quests.py`: the 4 admin handlers move onto a new `admin_router`. It is still included into `api_router` at `/admin`, so `/api/v1/quests/admin/*` is unchanged.
- `server.py`: mounts `quests_admin_router` at `/api/admin/quests` with `require_module("promotions")`, the same module that gates `promotions_router` and the sidebar's Quests entry. Mounts `admin_support_router` at `/api/admin` with `require_module("service_areas")`, the same gate as `service_areas_router`.
- Dashboard: `getQuests`/`createQuest`/`updateQuest`/`getQuestParticipants` → `/api/admin/quests/*`; `resetSurgeToAuto` → `/api/admin/service-areas/{id}/surge/auto`.

Alternatives considered:
- (a) Add `/api/v1/quests/admin/` to the App Check exempt list. Rejected: it puts holes in the enforced mobile namespace instead of using the one the dashboard already uses.
- (b) Set `APP_CHECK_ENFORCEMENT=off` again. Rejected: it undoes the deliberate mobile re-enablement.
- (c) Also stop `client.ts` from logging out on an App Check 401. Deferred: that is the shared request client for every admin page, and it's defense-in-depth, not the root-cause fix.

## 4. Risk & impact on existing functionality

- **Additive routes only.** Nothing is removed or renamed. `/api/v1/quests/admin/*` and `/api/v1/service-areas/{id}/surge/auto` keep serving any other caller: same handler functions, unchanged auth (`get_admin_user` only).
- **Blast radius:**
  - Quest admin handlers: only callers are `admin-dashboard/src/lib/api/staff-subscriptions.ts` and `backend/tests/test_quests.py`.
  - `admin_support_router` has one route; its only frontend reference is the uncalled `resetSurgeToAuto`.
  - Driver-facing quest routes (`GET /api/v1/quests`, `/my-quests`, `/{id}/join`, `/progress/{id}/claim`) are untouched, still App-Check-enforced, and keep the same registration order (admin routes were already registered after them).
- **Collisions:** none. A security review built the real route table and matched each new path to exactly one route. There was no existing `/api/admin/quests*` route; `PUT /api/admin/service-areas/{id}/surge` still resolves to `routes.admin.service_areas`.
- **RBAC:** on the new paths, a non-super-admin staffer without the `promotions` module now gets 403 on Quests. The sidebar already hides Quests from them, and under App Check they couldn't use the page at all. On 403, `client.ts` does not log out. Super admins always pass.
- **Unchanged:** CSRF, idle-timeout, and JTI/token_version checks don't depend on the path, so they apply identically on both prefixes. Rate limits count each prefix separately, which is negligible.
- **Deploy order:** backend first, then dashboard. If the dashboard ships first, the new paths 404: the page shows no quests, but there's no logout, because a 404 isn't a 401.

## 5. User-experience effect

- Internal admin: the Quests page loads, and create/toggle/participants work without being signed out. Visible after both the backend (Fly) and dashboard (Vercel) deploys.
- No rider/driver-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/quests.py` | Admin handlers on a new `admin_router`, included into `api_router` at `/admin` | Mountable under `/api/admin`; keeps v1 paths |
| `backend/server.py` | Mount `quests_admin_router` at `/api/admin/quests` (promotions module) and `admin_support_router` at `/api/admin` (service_areas module) | App-Check-exempt paths with sibling-consistent RBAC |
| `backend/features.py` | Comment only | Stale "no /api/admin equivalent" claim; notes there's no UI caller |
| `backend/tests/test_quests.py` | `TestAdminQuestsUnderApiAdmin` | New paths served; module gate allow/deny/super_admin; still admin-only; App-Check-exempt |
| `backend/tests/test_admin_surge_auto_mount.py` | New | Both prefixes served; module gate; admin-only; exempt |
| `admin-dashboard/src/lib/api/staff-subscriptions.ts` | Quest calls → `/api/admin/quests/*` | Root-cause fix |
| `admin-dashboard/src/lib/api/pricing.ts` | `resetSurgeToAuto` → `/api/admin/...` | Same latent issue (no UI caller today) |
| `admin-dashboard/src/lib/__tests__/staff-api-app-check-paths.test.ts` | New | Pins paths; fails if any `src/lib/api/*.ts` uses `/api/v1/` again |
| `admin-dashboard/e2e/quests.spec.ts` | Mock matchers → new paths | Keep e2e mocks matching |

## 7. Before / after

```ts
// Before
request<any[]>(`/api/v1/quests/admin/list...`);
// After
request<any[]>(`/api/admin/quests/list...`);
```

```python
# Before: @api_router.get("/admin/list")  → only /api/v1/quests/admin/list (get_admin_user)
# After:  @admin_router.get("/list")       → /api/v1/quests/admin/list (get_admin_user, unchanged)
#                                            AND /api/admin/quests/list (get_admin_user + require_module("promotions"))
```

## 8. Rollback plan

- Dashboard: Vercel instant rollback to the previous deployment (no redeploy). The old build goes back to calling `/api/v1/...`, which is broken as before but no worse.
- Backend: the new mounts are additive and harmless to leave. If needed, roll back the Fly release to the previous image (`fly releases -a spinr-backend-yyz` → `fly deploy --image <previous>`).
- No data or state changes.

## 9. Verification performed

- [x] Root cause reproduced from Fly logs and live unauthenticated probes (see §2).
- [x] Backend: `pytest tests/test_quests.py tests/test_admin_surge_auto_mount.py tests/test_appcheck_portal_exempt.py tests/test_admin_routes_auth.py`: 63 passed.
- [x] Red check: with the two new `server.py` mounts commented out, the new-path tests fail (7 failures before the module-gate tests were added).
- [x] `ruff check` and `ruff format --check` clean on all changed backend files.
- [x] Dashboard: vitest `staff-api-app-check-paths.test.ts` (6 tests) plus the existing API client suites (`api.test.ts`, `src/__tests__/lib`) pass. `tsc --noEmit` exit 0. eslint clean on changed files.
- [x] `spinr-security-auditor` review of the diff. Its 2 blockers are addressed here: the missing `require_module` gates, and the surge-auto premise, corrected in §2. Its warnings are pre-existing and listed in §10.
- [ ] Dashboard production build: see the PR description.

## 10. What was NOT verified / known pre-existing gaps

- Not exercised against live production or staging after the change. Verification is TestClient with dependency overrides plus mocked fetch.
- The App Check behavior is asserted through the exempt-prefix list, not a real Firebase token check.
- Playwright e2e (`e2e/quests.spec.ts`) was not run locally. Only its mock matchers were updated.
- Visual regression: `/dashboard/quests` is not among the 6 seeded baseline pages. No visual change is intended; URL paths only.
- Pre-existing, **not fixed here** (security review warnings), each now reachable from the dashboard again:
  - `UpdateQuestRequest.reward_amount` has no bounds (create caps it at `0 < x ≤ 500`), and that value feeds the driver reward credit on claim.
  - No `log_admin_action` audit row on quest create/update or the surge reset.
  - Quest money fields are `float` in the Pydantic models.
- `client.ts` still logs out on any 401, including an App Check 401. Any other browser call to an App-Check-enforced path would repeat this bug class.
