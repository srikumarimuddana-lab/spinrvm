# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-01 |
| Author | Claude Code |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | PR #4862 |
| Related issue or gap ID | Performance audit — Python-side aggregation over large tables |

## 1. Issue / gap identified

Admin dashboard endpoints fetched thousands of rows (5k–20k) from Supabase into Python to compute simple aggregates (COUNT, SUM, GROUP BY) that Postgres should handle server-side, causing unnecessary latency and memory pressure on admin API responses.

## 2. Root cause

Original implementation used `get_rows()` to pull full result sets into Python, then iterated with loops/Counters/Decimal sums. This was a natural pattern during early development but doesn't scale — each admin stats request transferred 10k+ rows over the wire for a single number.

## 3. Fix / remediation

Created 15 Postgres `SECURITY DEFINER` RPCs (migrations 380–395) that perform aggregation server-side. Updated 7 Python endpoint files to call these RPCs instead of fetching bulk data. Parallelized `admin_get_driver_stats` with `asyncio.gather` (4 concurrent queries). Raised `/api/admin/drivers` limit from 200 to 500 to match admin dashboard requests.

Migration 395 is a corrective migration superseding 385's `admin_subscription_stats_rollup` — the original had uuid/text type mismatches (`driver_subscriptions.driver_id` and `plan_id` are uuid, `drivers.id` is text). The `CREATE OR REPLACE` in 395 overwrites the broken version.

## 4. Risk & impact on existing functionality

- **Blast radius**: admin-only endpoints. No rider/driver/corporate flows touched. All RPCs are read-only (`STABLE`) and `SECURITY DEFINER` with pinned `search_path`.
- **What reads/writes the same tables**: `driver_subscriptions`, `subscription_payments`, `rides`, `drivers`, `driver_bonuses`, `referral_payouts`, `ride_incentive_claims`, `disputes`, `cloud_messages`, `payout_records`, `email_log`, `promo_usage`, `audit_logs` — all read-only in these RPCs. The existing write paths (ride lifecycle, payment settlement, etc.) are unaffected.
- **Interaction with background loops**: None. These RPCs are called only from admin HTTP endpoints, not from any of the background loops in `lifespan.py`.
- **Ride state machine**: Not touched.
- **Money/wallet deltas**: Not touched. RPCs read existing financial data but never write.
- `EXECUTE` is revoked from `anon` and `authenticated` roles on all 15 RPCs — only the service role (backend) can call them.

## 5. User-experience effect

- **Who sees a difference**: Internal admin dashboard users only.
- **What changes**: Admin stats pages load faster (fewer rows transferred, server-side aggregation). No visible UI change — same data, same format, just faster.
- **Mid-session**: No effect. Admin dashboard pages fetch fresh data on each load.
- **No copy/notification changes.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/drivers.py` | Replaced bulk fetches with RPC calls in stats/referral/bonus/ride endpoints; raised limit 200→500; parallelized driver stats with asyncio.gather | Server-side aggregation; match dashboard requests |
| `backend/routes/admin/rides.py` | Replaced payout/earnings overview fetch-all with RPCs | Server-side aggregation |
| `backend/routes/admin/subscriptions.py` | Replaced subscription stats fetch-all with single RPC | Server-side aggregation |
| `backend/routes/admin/support.py` | Replaced dispute stats fetch-all with RPC | Server-side aggregation |
| `backend/routes/admin/messaging.py` | Replaced cloud message stats fetch-all with RPC | Server-side aggregation |
| `backend/routes/admin/promotions.py` | Replaced promo stats fetch-all with RPC | Server-side aggregation |
| `backend/routes/admin/maintenance.py` | Replaced audit log actor stats fetch-all with RPC | Server-side aggregation |
| `admin-dashboard/src/lib/api/drivers.ts` | Updated stale comment (limit 200→500) | Accuracy |
| `backend/migrations/380-394` | 14 new RPC migration files | Server-side aggregation functions |
| `backend/migrations/395_fix_subscription_stats_uuid_casts.sql` | Corrective migration for 385's uuid/text mismatches | Type safety fix |

## 7. Before / after

```python
# Before (drivers.py — admin_get_driver_stats)
all_rides = await db_supabase.get_rows("rides", filters=..., limit=5000)
daily = {}
for r in all_rides:
    day = r["created_at"][:10]
    daily.setdefault(day, {"rides": 0, "earnings": 0})
    daily[day]["rides"] += 1
    if r.get("status") == "completed":
        daily[day]["earnings"] += float(r.get("driver_earnings", 0))
```

```python
# After
daily_stats = await db_supabase.rpc("admin_daily_ride_stats", {
    "p_start": start_date.isoformat(),
    "p_end": end_date.isoformat(),
    "p_driver_ids": driver_ids or None,
})
daily_chart = daily_stats if isinstance(daily_stats, list) else (daily_stats or [])
```

## 8. Rollback plan

- All RPCs use `CREATE OR REPLACE FUNCTION` — revert by re-applying the original Python code (git revert the endpoint commits). The RPCs remain in Postgres but are simply unused.
- Alternatively, drop individual functions: `DROP FUNCTION IF EXISTS public.<function_name>(<signature>);`
- No data is written by any of these changes — pure read-only aggregation. No data-level remediation needed.
- The limit change (200→500) can be reverted independently.

## 9. Verification performed

- [x] All 15 RPCs applied to production Supabase and verified with direct SQL calls
- [x] Tested each RPC with representative parameters against live data
- [x] Discovered and fixed 3 uuid/text type mismatches during live deployment (385→395)
- [x] Discovered and fixed `rides.completed_at` → `rides.ride_completed_at` column name error (389)
- [x] Discovered and fixed `driver_bonuses.driver_id` uuid cast (390)
- [x] Verified `/api/admin/drivers?limit=500` works after limit cap fix
- [x] Blast-radius grep: all modified endpoints are admin-only (`require_admin` / `require_module` decorated)

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
