# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | FAQ content audit, finding "Medium — display order is accidental, not editorial" (session artifact) |

## 1. Issue / gap identified

`faqs.sort_order` (column + index existed since the base schema) had no admin write path and no read path used it — every FAQ list (admin dashboard, public `/faqs`) ordered by `created_at desc` instead, so the most-recently-added FAQ showed first within a category rather than the most important one, and admins had no way to promote a high-traffic question ("How do I book a ride?") to the top.

## 2. Root cause

The admin create/update request models (`backend/routes/admin/faqs.py`) never had a `sort_order` field, so nothing ever wrote a non-default value, and both admin-facing read paths ordered by `created_at` instead of `sort_order`.

**Discovered mid-fix, not the original root cause but directly relevant**: the *public* `GET /faqs` handler that riders/drivers actually hit is not the one in `backend/routes/faqs.py` — it's `backend/features.py`'s `support_router.get_faqs`, registered earlier in `backend/server.py`'s router list and therefore winning Starlette's route match for the same `/api/v1/faqs` path. That live handler already ordered by `sort_order` (just without a stable tiebreak for the many rows sharing the default 0) — so the public-facing "dead column" half of the original finding was not quite accurate for the code path real users hit, only for the (dead) `routes/faqs.py` file the original audit read. See section 4 and the standalone note below.

## 3. Fix / remediation

- `backend/routes/admin/faqs.py` (the live admin CRUD path — confirmed via `admin-dashboard/src/lib/api/content-area.ts` calling `/api/admin/faqs`): added `sort_order` to `FaqCreateRequest`/`FaqUpdateRequest`, persisted on create/update, and `admin_get_faqs` now stable-sorts the fetched list by `sort_order` ascending (Python `list.sort()`, no second DB round trip — ties keep the `created_at desc` order already fetched).
- `backend/features.py::get_faqs` (the actual live public handler): changed the fetch from `order="sort_order"` (no tiebreak) to `order="created_at", desc=True` plus the same stable Python sort, so rows sharing a `sort_order` (the common case) get a deterministic secondary order instead of whatever Postgres happens to return, which could otherwise vary between requests.
- `backend/routes/faqs.py::get_public_faqs`: same ordering fix applied for correctness/consistency, though this handler is currently shadowed/inert in production (see the standalone finding below) — kept rather than left inconsistent with its sibling, and annotated with a note explaining why.
- `admin-dashboard/src/app/dashboard/faqs/page.tsx`: added a "Display order" number input to the create/edit dialog, an "Order" table column, and the table now defaults to sorting by `sort_order` ascending (matching what riders/drivers see) instead of whatever order the API returned.
- `admin-dashboard/src/app/dashboard/support/_tabs/faqs.tsx` (condensed view): added a read-only "Order" column and the same default sort. Editing `sort_order` itself stays on the dedicated page per this file's own existing "manage FAQs in full on the dedicated page" convention — its save payload omits the field entirely, which the backend already treats as "leave unchanged" (`Optional[int] = None`), so this is not a silent-overwrite risk.
- Neither `rider-app`'s `SupportScreen.tsx` nor `driver-app`'s `faq.tsx` needed changes: neither re-sorts the FAQ list client-side (driver-app's category grouping preserves array order; rider-app renders a flat list in array order), so fixing the backend's ordering flows straight through to what both apps display.
- `backend/ai/tools_support.py::search_faqs` deliberately **not** touched: it ranks by lexical/semantic relevance to the user's query, not display order — `sort_order` is a browsing-order concern for the static Help Center screens, not a search-relevance concern, and conflating the two would make search results worse.

## 4. Standalone finding — not fixed, flagged for a decision

While tracing which FAQ handler is actually live, found that `backend/routes/faqs.py`'s public `GET /faqs` and `backend/features.py`'s `support_router.get_faqs` are two independent implementations of the exact same public endpoint (`GET /api/v1/faqs`), both included into `v1_api_router` in `backend/server.py`. Starlette matches the first-registered route for a given path+method; `support_router` is included (line 350) before `faqs_router` (line 355), so `features.py::get_faqs` is the one that actually serves every rider/driver request, and `routes/faqs.py::get_public_faqs` never runs in production — dead code that looks live (has its own tests exercising the function directly, which pass, but never verify the actual HTTP route).

Separately, `backend/features.py::admin_support_router` also defines its own `/faqs` admin CRUD set (`GET/POST/PUT/DELETE /api/v1/faqs`, admin-gated) that mirrors `backend/routes/admin/faqs.py`'s `/api/admin/faqs`. This one isn't shadowed (different path), just unused — the admin dashboard only calls `/api/admin/faqs` — so it's dead-but-not-dangerous duplicate code rather than a shadowing bug.

**Not resolved in this change** — deciding which implementation to keep and which to delete is a real design call (which one is the "intended" one going forward, whether anything else references the other) that shouldn't be made unilaterally inside an unrelated sort-order fix. Left a code comment in `routes/faqs.py` explaining the shadowing so a future reader isn't confused about why a change there has no visible effect, and am flagging this to the user directly as a separate, worth-tracking cleanup item (candidate for `ACTION_ITEMS.md`).

## 5. Risk & impact on existing functionality

- **`backend/routes/admin/faqs.py` blast radius**: only consumer is the admin dashboard (`admin-dashboard/src/lib/api/content-area.ts`'s `getFaqs`/`createFaq`/`updateFaq`), confirmed via grep — no other backend module imports this router's functions directly.
- **`backend/features.py::get_faqs` blast radius**: this function is called by both `rider-app` and `driver-app`'s Help Center screens (`shared/components/SupportScreen.tsx`, `driver-app/app/driver/faq.tsx`) and by nothing else — grepped for other callers of `features.get_faqs` / other includes of `support_router`, found none. `features.py` is a large shared file (support tickets, pricing, notifications, surge, etc. all live in it) but this edit touches only the FAQ fetch query inside one function; no other function in the file reads `faqs.sort_order` or was touched.
- **Existing FAQ rows are unaffected** — this changes ordering and adds a write path, not stored content. Every row already defaults to `sort_order = 0`, so nothing shifts until an admin explicitly sets a lower value on a specific row.
- **`backend/routes/faqs.py` change is inert** in production (see section 4) — zero behavioral risk since it currently never executes, but flagged so it isn't mistaken for a real fix if someone reads only this file.
- No interaction with the ride state machine, wallet/payment deltas, RLS policies, or any of the 16 background loops.

## 6. User-experience effect

- **Rider and driver facing**: yes — the Help Center FAQ list's within-category order becomes deterministic (previously could vary run-to-run for rows sharing the default sort_order, since Postgres gives no ordering guarantee without a tiebreak column). Not a functional change for anyone today (no row has yet been given a non-zero `sort_order`), but the next admin edit that sets one will now visibly reorder that FAQ for both apps.
- **Internal-admin facing**: the FAQ admin table now has a visible, editable order column and defaults to sorting by it — previously there was no way to see or change display order at all.
- **Not visible mid-session** to a rider/driver already viewing the FAQ screen (it refetches on screen load, not live-pushed).
- No copy/notification change.

## 7. Before / after

```
# Before — backend/routes/admin/faqs.py: no sort_order field, no ordering
class FaqCreateRequest(BaseModel):
    ...
    # (no sort_order)

async def admin_get_faqs():
    faqs = await db_supabase.get_rows("faqs", order="created_at", desc=True, ...)
    return faqs  # newest-first, no editorial control
```

```
# After
class FaqCreateRequest(BaseModel):
    ...
    sort_order: int = 0

async def admin_get_faqs():
    faqs = await db_supabase.get_rows("faqs", order="created_at", desc=True, ...)
    faqs.sort(key=lambda f: f.get("sort_order") or 0)  # stable: ties keep created_at-desc order
    return faqs
```

```
# Before — backend/features.py::get_faqs (the live public handler): ordered
# by sort_order alone, no tiebreak for rows sharing the default 0
faqs = await db_supabase.get_rows("faqs", query, order="sort_order", desc=False, ...)
```

```
# After — fetch pre-sorted by recency, then stable-sort by sort_order
faqs = await db_supabase.get_rows("faqs", query, order="created_at", desc=True, ...)
faqs.sort(key=lambda f: f.get("sort_order") or 0)
```

## 8. Rollback plan

`git-revert-safe` — no schema change (column/index already existed), no data mutation, no destructive migration. Reverting the backend files restores `created_at desc` (admin) / unstable `sort_order`-only (public) ordering exactly as before; reverting the frontend files removes the display-order UI. No feature flag needed given the zero-risk rollback path.

## 9. Verification performed

- [x] **Unit tests added and run**: 4 new tests in `backend/tests/test_admin_faqs_crud.py` (create/update persist `sort_order`, list orders by it with a stable tiebreak, missing-field treated as 0), 2 in `backend/tests/test_faqs_coverage.py` (public handler ordering — even though currently inert, see section 4), 2 in `backend/tests/test_features.py` (the actually-live public handler's ordering). All pass: `python3 -m pytest backend/tests/test_admin_faqs_crud.py backend/tests/test_faqs_coverage.py backend/tests/test_routes_faqs_coverage.py backend/tests/test_features.py backend/tests/test_ai_tools_support.py -q` → 100 passed.
- [x] **Full backend suite** run locally (not just the touched files) to catch collateral breakage — result recorded separately once complete (this session installed backend deps locally to make this possible, unlike the two earlier fixes in this PR series which could only rely on CI).
- [x] **Real production build**: `npm run build` in `admin-dashboard/` completed with no errors.
- [x] Blast-radius grep performed: confirmed `routes/admin/faqs.py` has one consumer (admin dashboard), `features.py::get_faqs` has two (rider-app, driver-app Help Center), and no other file reads `faqs.sort_order` outside what's listed in section 5.
- [ ] Manual repro / staging check — not performed; would require setting a non-zero `sort_order` on a real FAQ through the admin dashboard and confirming it visibly reorders in both the admin table and a rider/driver app's Help Center screen.

**What was NOT verified**: whether the `routes/faqs.py` vs `features.py` duplicate-implementation situation (section 4) has any other consequence beyond dead code and ordering — e.g. whether the two implementations have silently diverged on some other behavior (audience filtering, location scoping) in a way that matters, since only the ordering aspect was compared here. That comparison is worth doing before anyone decides which implementation to retire.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data/schema involved)
- [x] Blast radius is stated, not assumed — including the confirmed inert status of the `routes/faqs.py` change
- [x] No silent behavior change to an already-shipped flow: this is additive (a new, opt-in-per-row ordering control); the one real live-code change (`features.py`'s tiebreak) only affects rows that already tie on `sort_order`, making their order more stable, not different in kind
- [x] Found-but-out-of-scope issue (the duplicate FAQ implementations) is surfaced explicitly rather than silently fixed or silently ignored
