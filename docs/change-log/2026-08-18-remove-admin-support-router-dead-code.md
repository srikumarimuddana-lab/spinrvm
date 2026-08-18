# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Flagged in `docs/change-log/2026-08-18-check-route-shadowing-cross-router.md`'s "Additional finding" section (PR #4204) |

## 1. Issue / gap identified

`backend/features.py`'s `admin_support_router` hosted 9 admin endpoints (ticket CRUD, FAQ CRUD, manual surge override, push-notification send). None of them were live:

- `GET /tickets` and `GET /faqs` were Starlette-shadowed by `support_router`'s identical, earlier-registered paths in the same `v1_api_router` mount (found by `scripts/check_route_shadowing.py --server-mounts`, PR #4204) — literally unreachable.
- The other 7 (`POST /tickets/{id}/reply`, `POST /tickets/{id}/close`, `POST /faqs`, `PUT /faqs/{id}`, `DELETE /faqs/{id}`, `PUT /service-areas/{id}/surge`, `POST /notifications/send`) weren't Starlette-shadowed, but `admin-dashboard` never calls any of their `/api/v1/...` paths — every one of these admin actions is served by a separate, richer implementation under `/api/admin/...` (the `routes/admin` package), which is what the dashboard actually calls.

## 2. Root cause

`admin_support_router` predates (or was built in parallel with, never converged with) the real admin surface in `routes/admin/`. As `routes/admin/support.py`, `support_tickets.py`, `faqs.py`, and `service_areas.py` grew into the actual admin CRUD implementation the dashboard calls, `admin_support_router`'s equivalents in `features.py` were never removed — the two `GET` routes went dead the moment `support_router` happened to register the same literal path earlier in `server.py`; the other 7 were never wired to any UI in the first place (or were superseded when the real admin surface was built) and nothing ever flagged them as unreachable, because Starlette shadowing is silent and "no client calls this" isn't something any existing tooling checked for.

## 3. Fix / remediation

Grounded every claim in real code before deleting anything — for each of the 9 routes, found and verified the actual live equivalent:

| Removed (`admin_support_router`, `/api/v1/...`) | Live equivalent (`/api/admin/...`) |
|---|---|
| `GET /tickets` | `routes/admin/support_tickets.py`'s `GET /tickets` |
| `POST /tickets/{id}/reply` | `routes/admin/support.py` and `support_tickets.py`'s `POST /tickets/{id}/reply` |
| `POST /tickets/{id}/close` | `routes/admin/support.py`'s `POST /tickets/{id}/close` |
| `GET /faqs` | `routes/admin/faqs.py`'s `GET /faqs` |
| `POST /faqs` | `routes/admin/faqs.py`'s `POST /faqs` |
| `PUT /faqs/{id}` | `routes/admin/faqs.py`'s `PUT /faqs/{id}` |
| `DELETE /faqs/{id}` | `routes/admin/faqs.py`'s `DELETE /faqs/{id}` |
| `PUT /service-areas/{id}/surge` | `routes/admin/service_areas.py`'s `PUT /service-areas/{id}/surge` (the "canonical audited admin endpoint" the removed handler's own comment already pointed to) |
| `POST /notifications/send` | `routes/admin/faqs.py`'s `POST /notifications/send` — richer (audience broadcast: all/riders/drivers, rate-limited via `admin_mass_notify_limit`, logs a `notifications` row), not just an equally-good duplicate |

Confirmed `admin-dashboard` calls the `/api/admin/...` path for every row above (`safety-disputes.ts`, `content-area.ts`, `pricing.ts`), never the removed `/api/v1/...` path.

**One route was NOT removed**: `PUT /service-areas/{area_id}/surge/auto` (reset to automatic surge). This is the sole live implementation — `admin-dashboard/src/lib/api/pricing.ts`'s `resetSurgeToAuto()` calls `PUT /api/v1/service-areas/{id}/surge/auto` directly, and `routes/admin/service_areas.py` has no `/auto` variant (only the manual override). Kept exactly as-is; only the 8 other, dead handlers around it were removed from the same router.

Also removed the 5 Pydantic request models (`ReplyToTicketRequest`, `CreateFaqRequest`, `UpdateFaqRequest`, `UpdateSurgeRequest`, `SendNotificationRequest`) that existed only to serve the deleted handlers — confirmed via grep they're not imported or used anywhere else in the backend. Removed the now-unused `pydantic.Field` import (only the deleted `CreateFaqRequest`/`UpdateFaqRequest` used it).

Shrank `backend/tests/test_check_route_shadowing.py`'s `_KNOWN_VIOLATIONS` allowlist to empty (it held exactly the 2 shadowed routes fixed here) — `scripts/check_route_shadowing.py --server-mounts server.py` now reports zero violations.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `backend/features.py` + one test file.** Grepped the full backend for every deleted function name and every deleted Pydantic model name — no other module, test, or AI tool references any of them. `admin-dashboard` was grepped first, before deletion, to confirm the real `/api/admin/...` equivalents are what it actually calls (not assumed from the routes existing — independently verified per-route).
- **`admin_reset_surge_to_auto` (the one kept route) is untouched** — same function, same decorator, same router. `backend/tests/test_surge_reset_to_auto.py` (its dedicated regression test) still passes unmodified.
- **No other reader of `admin_support_router`**: it's mounted exactly once (`v1_api_router.include_router(admin_support_router)` in `server.py`, unchanged by this PR) and has no other consumer.
- No interaction with the ride state machine, wallet/payment deltas, RLS policies, or any of the 16 background loops. No schema change, no migration.
- **Full test collection succeeds with zero import errors** after the deletion — if any other module had imported the deleted functions or models, collection would have failed immediately.

## 5. User-experience effect

None for any real user — every deleted route was either unreachable (Starlette-shadowed) or unreachable in practice (no caller). **Zero admin-dashboard behavior changes**: every admin action (viewing/replying to/closing tickets, managing FAQs, the manual surge override) already goes through the `/api/admin/...` paths this PR didn't touch. Not visible mid-session to anyone, since nothing was ever calling the removed paths to begin with.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | Removed 8 dead `admin_support_router` route handlers (2 Starlette-shadowed, 6 uncalled-but-not-shadowed) and their 5 now-orphaned Pydantic request models; removed the unused `pydantic.Field` import; kept `admin_reset_surge_to_auto` (the one live route) and added a header comment explaining what was removed and why, with pointers to the live `/api/admin/...` equivalents | Remove dead code discovered by PR #4204's route-shadowing checker extension |
| `backend/tests/test_check_route_shadowing.py` | Shrank `_KNOWN_VIOLATIONS` to empty; updated module docstring | The 2 violations it tracked are now fixed |

## 7. Before / after

```
# Before — backend/features.py
# ============ Admin: Support Tickets ============
@admin_support_router.get("/tickets") ...
@admin_support_router.post("/tickets/{ticket_id}/reply") ...
@admin_support_router.post("/tickets/{ticket_id}/close") ...
# ============ Admin: FAQs ============
@admin_support_router.get("/faqs") ...
@admin_support_router.post("/faqs") ...
@admin_support_router.put("/faqs/{faq_id}") ...
@admin_support_router.delete("/faqs/{faq_id}") ...
# ============ Admin: Surge Pricing ============
@admin_support_router.put("/service-areas/{area_id}/surge") ...
@admin_support_router.put("/service-areas/{area_id}/surge/auto")
async def admin_reset_surge_to_auto(area_id: str): ...
```
```
# ...later in the file...
@admin_support_router.post("/notifications/send")
async def admin_send_notification(req: SendNotificationRequest): ...
```

```
# After — backend/features.py
# ============ Admin: Surge Pricing ============
#
# admin_support_router used to also host ... [explanatory comment,
# see the file] ... All 8 were dead code, removed 2026-08-18.
@admin_support_router.put("/service-areas/{area_id}/surge/auto")
async def admin_reset_surge_to_auto(area_id: str): ...
```

## 8. Rollback plan

`git-revert-safe` — pure Python source deletion, no schema, no data migration, no config/flag. A plain `git revert` restores all 9 routes and their models exactly (including the 2 dead-by-shadowing ones, if that were ever desired, which it isn't).

## 9. Verification performed

- [x] **`backend/tests/test_check_route_shadowing.py`**: 11/11 passed — `scripts/check_route_shadowing.py --server-mounts server.py` now reports zero violations (route count in `v1_api_router`'s group dropped from 162 to 153, matching the 9 removed handlers).
- [x] **`backend/tests/test_features.py`**: 36/36 passed.
- [x] **`backend/tests/test_surge_reset_to_auto.py`** (the kept route's dedicated test): 2/2 passed, unmodified.
- [x] **`backend/tests/test_n10_admin_push_target_app.py`** (tests the real `routes/admin/faqs.py::admin_send_notification`, a different module — confirms it's untouched and unaffected): 9/9 passed.
- [x] **`ruff check`** on both touched files: all checks passed (including catching and removing the now-unused `Field` import).
- [x] **`pytest --collect-only`**: full suite collects with zero import errors.
- [x] **Full backend suite** (`pytest -q --no-cov -x`): 12,066 passed, 8 skipped, 1 xfailed, **0 failures** (515s). No collateral breakage anywhere in the suite.
- [x] **Grounded every "this is the live equivalent" claim against actual `admin-dashboard` source** (`safety-disputes.ts`, `content-area.ts`, `pricing.ts`) before deleting anything, not assumed from the `routes/admin/*.py` handlers merely existing.
- [ ] Not run against a real/throwaway Supabase schema — pure code deletion, no schema risk, but live HTTP behavior (a 404 on the old `/api/v1/...` paths) not confirmed against a running server in this session.

**What was NOT verified**: whether any external caller outside the two shipped apps (a partner integration, manual QA scripts, an internal admin tool not in this repo) depends on any of the removed `/api/v1/...` paths — grepped only `admin-dashboard/`, `driver-app/`, `rider-app/` source, the best evidence available in this session but not a guarantee against every possible caller. Given 2 of the 9 were provably unreachable (Starlette-shadowed) and the other 7 duplicate a richer, already-live admin surface, this risk is judged low.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data involved)
- [x] Blast radius is stated, not assumed — every "dead" claim independently verified against real `admin-dashboard` source and real backend route definitions, not inferred from absence alone
- [x] No silent behavior change to an already-shipped flow: every deleted route was either unreachable or uncalled; the one route real users' admin actions depend on (`admin_reset_surge_to_auto`) is untouched
