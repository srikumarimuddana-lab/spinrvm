# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-27 |
| Author | Claude Code |
| Surface(s) | admin-dashboard, backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | Found during manual QA checklist review of `/dashboard/promotions` |

## 1. Issue / gap identified

The Promotions Analytics chart's "All Promos / Public Codes Only / Private Coupons Only" filter dropdown is wired to state, but changing it has no visible effect on the chart — it always shows all-promo data regardless of selection.

## 2. Root cause

The frontend `chartData` memo has a code comment stating the limitation directly: `daily_usage` from `GET /api/admin/promotions/stats` was always a flat, un-split `{date, count, amount}[]` array — the backend had no way to break usage down by promo type (public vs. private), so the memo just returned `stats.daily_usage` unfiltered no matter what `chartFilter` was set to. Not a frontend bug in isolation — the backend simply never emitted the data the frontend filter needed.

## 3. Fix / remediation

**Backend** (`backend/routes/admin/promotions.py`, `admin_get_promo_stats`): build a `promo_id -> is_private` map from the already-fetched `all_promos` list (same `promo_type == "private"` convention the route already uses for its public/private code counts), and extend each daily bucket with `public_count`, `public_amount`, `private_count`, `private_amount` alongside the existing `count`/`amount` totals. Purely additive — no existing field removed or renamed.

**Frontend** (`admin-dashboard/src/app/dashboard/promotions/page.tsx`): widened the `PromoStatsData.daily_usage` type to include the four new optional fields, and rewrote the `chartData` memo to actually branch on `chartFilter` — returning the `public_*`/`private_*` fields (renamed to `count`/`amount` for the chart's existing `dataKey`s) when a specific type is selected, falling back to the original combined array for "all".

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped for every consumer of `PromoStatsData` and `getPromoStats` — only `admin-dashboard/src/lib/api.ts` (the fetch wrapper, untouched) and `promotions/page.tsx` itself. Grepped the backend for `admin_get_promo_stats` / `promotions/stats` — only the one route, no other endpoint reads or reuses this function's logic.
- The new fields are additive to the JSON response; nothing that reads `count`/`amount`/`date` (the "All Promos" default view, unaffected by this change) sees any difference.
- No interaction with the ride state machine, background loops, or money-moving code paths — `promo_applications` rows are read-only here, nothing is written or mutated by this endpoint.
- Public/private classification reuses the exact convention (`promo_type == "private"`) the route already applies elsewhere in the same function for `total_private`/`active_private` — no new business-logic decision introduced, just applied per-usage-row instead of only per-promo-count.

## 5. User-experience effect

- Internal admin only (Promotions Analytics chart). Not visible to riders, drivers, or corporate admins.
- Not visible mid-session in a way that matters — this is an admin analytics chart, not a live/in-progress user flow. An admin who reloads the page after this deploy will simply see the filter dropdown start working as its label already implied it should.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/promotions.py` | `admin_get_promo_stats`: added a `promo_is_private` lookup and four new per-day fields (`public_count`, `public_amount`, `private_count`, `private_amount`) to each `daily_usage` bucket | Backend previously had no per-type breakdown for the frontend filter to select from |
| `admin-dashboard/src/app/dashboard/promotions/page.tsx` | Widened `PromoStatsData.daily_usage` type; rewrote the `chartData` `useMemo` to branch on `chartFilter` instead of ignoring it | Frontend filter was a no-op with no data to filter against |
| `backend/tests/test_admin_promo_stats.py` (new) | Two unit tests: split totals sum correctly to the combined totals, and a usage row referencing a deleted/unknown promo still counts (as public) rather than being silently dropped | No existing test covered `/api/admin/promotions/stats`; this is genuinely new behavior needing its own coverage per CLAUDE.md testing conventions |

## 7. Before / after

```
# Before — backend/routes/admin/promotions.py
for u in all_usage:
    d = u.get("created_at", "")[:10]
    if d in daily:
        daily[d]["count"] += 1
        daily[d]["amount"] = float(
            Decimal(str(daily[d]["amount"]))
            + Decimal(str(u.get("discount_applied", 0)))
        )
```

```
# After — backend/routes/admin/promotions.py
promo_is_private = {p["id"]: p.get("promo_type") == "private" for p in all_promos}
...
for u in all_usage:
    d = u.get("created_at", "")[:10]
    if d not in daily:
        continue
    discount = Decimal(str(u.get("discount_applied", 0)))
    is_private = promo_is_private.get(u.get("promo_id"), False)

    daily[d]["count"] += 1
    daily[d]["amount"] = float(Decimal(str(daily[d]["amount"])) + discount)

    if is_private:
        daily[d]["private_count"] += 1
        daily[d]["private_amount"] = float(Decimal(str(daily[d]["private_amount"])) + discount)
    else:
        daily[d]["public_count"] += 1
        daily[d]["public_amount"] = float(Decimal(str(daily[d]["public_amount"])) + discount)
```

```
# Before — admin-dashboard/.../promotions/page.tsx
const chartData = useMemo(() => {
    if (!stats?.daily_usage) return [];
    // For public/private filtering, we'd need per-type data from backend
    // For now, show all data with the filter label context
    return stats.daily_usage;
}, [stats, chartFilter]);
```

```
# After — admin-dashboard/.../promotions/page.tsx
const chartData = useMemo(() => {
    if (!stats?.daily_usage) return [];
    if (chartFilter === "public") {
        return stats.daily_usage.map((d) => ({
            date: d.date, count: d.public_count ?? 0, amount: d.public_amount ?? 0,
        }));
    }
    if (chartFilter === "private") {
        return stats.daily_usage.map((d) => ({
            date: d.date, count: d.private_count ?? 0, amount: d.private_amount ?? 0,
        }));
    }
    return stats.daily_usage;
}, [stats, chartFilter]);
```

## 8. Rollback plan

`git revert` is sufficient and complete here — this endpoint is read-only (no writes to `promotions` or `promo_applications`), so there is no live data to remediate. Reverting both commits/files restores the prior (no-op filter) behavior with no data cleanup needed.

## 9. Verification performed

- [x] Automated tests run — backend: `pytest tests/test_admin_promo_stats.py tests/test_promo_discount_parity.py tests/test_promo_rate_limit.py` (23 passed, 2 new); frontend: `npx vitest run` (137/137 passed)
- [x] `ruff check` on the two backend files touched — clean
- [x] Frontend `npm run lint` (0 errors) and `npx tsc --noEmit` (no new errors — same pre-existing unrelated test-file error as before)
- [x] **`npm run build` (real production build, not just dev server/tsc) — exit 0, all routes compile**
- [ ] Manual repro in staging — not done (no staging backend access in this session); reasoned from the data flow (backend returns new fields → frontend selects them) plus the automated coverage above, not click-tested end-to-end
- [x] Blast-radius grep performed — see Section 4
- [x] Reviewed against relevant CLAUDE.md conventions — no money/state-machine/RLS/PIPEDA implications (read-only admin analytics)
- [x] Not feature-flagged — read-only additive change to an internal-admin-only analytics view, not user-visible/non-trivial in the sense the flagging guidance targets

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data remediation needed)
- [x] Blast radius is stated, not assumed — isolated to one page + one endpoint, confirmed via grep
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — Section 5 covers it (fixes a no-op into working, admin-only, no mid-session visibility concern)
