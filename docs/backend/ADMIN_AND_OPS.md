# Admin & Ops Domain

Admin console endpoints, operational tooling, and live monitoring.

**Files covered:**
`routes/admin/analytics.py`, `routes/admin/monitoring.py`, `routes/admin/maintenance.py`, `routes/admin/faqs.py`, `routes/admin/settings.py`, `routes/admin/support.py`, `routes/admin/documents.py`, `routes/admin/drivers.py`, `routes/admin/rides.py`, `routes/admin/users.py`, `routes/admin/staff.py`, `routes/admin/wallet.py`, `routes/admin/promotions.py`, `routes/admin/subscriptions.py`, `routes/admin/messaging.py`, `routes/admin/auth.py`.

Admin auth is documented in `AUTH_AND_USERS.md` §4. This file covers the operational surfaces available once an admin is authenticated.

---

## 1. Roles & modules

Admin roles: `admin`, `super_admin`, `operations`, `support`, `finance`, `custom`.

Every admin row carries a `modules` JSON array (e.g. `["rides","drivers","finance","support"]`). The admin dashboard gates each page on the module list. Super-admin implicitly has all modules.

Current module catalog (18 keys):

```
users  drivers  rides  corporate  wallet  payments  promotions
loyalty  quests  subscriptions  disputes  support  messaging
analytics  monitoring  maintenance  settings  staff
```

---

## 2. Analytics (`routes/admin/analytics.py`)

Operational dashboards. Aggregation endpoints with flexible date ranges.

| Endpoint | Filters | Returns |
|---|---|---|
| `GET /admin/analytics/cancellation-reasons` | `date_range ∈ today/7d/30d/90d/1y`, `service_area_id` | Reasons + cancelled_by (rider/driver/unspecified) + hourly distribution. |
| `GET /admin/analytics/acceptance-rate` | range, area | Driver acceptance trend. |
| `GET /admin/analytics/ride-funnel` | range | Searching → assigned → accepted → arrived → started → completed. |
| `GET /admin/analytics/revenue` | range | Gross, platform cut, driver payouts. |

Implementation pattern: SQL aggregation via Supabase `rpc(…)` or direct filtered queries with client-side rollup when the query language is insufficient.

---

## 3. Monitoring — live map (`routes/admin/monitoring.py`)

Feeds the ops live view.

| Endpoint | Returns |
|---|---|
| `GET /admin/monitoring/drivers` | All drivers: location, availability, on-ride flag, vehicle, rating, total rides. |
| `GET /admin/monitoring/rides` | Active rides (searching / driver_assigned / driver_accepted / driver_arrived / in_progress): participants, coords, fare, distance, created_at. |

The dashboard polls these endpoints and also consumes WebSocket `driver_location_update` + `ride_status_changed` events for push-style updates. See `RIDES_AND_DISPATCH.md` §10.

---

## 4. Maintenance (`routes/admin/maintenance.py`)

Idempotent housekeeping.

| Endpoint | Args | What it does |
|---|---|---|
| `POST /admin/maintenance/cleanup-location-history` | `days=30` | Delete `driver_location_history` older than N days. Also purges `online_idle` points >24 h old. |
| `POST /admin/maintenance/rollup-driver-daily` | `target_date=ISO` | Aggregate GPS + rides for a driver into `driver_daily_stats`: online_minutes, idle_km, navigating_km, trip_km, ride counts, earnings. Upsert keyed on (driver_id, stat_date). |

Safe to re-run — upsert semantics + idempotent deletes.

---

## 5. Settings (`routes/admin/settings.py`)

Runtime-editable app-wide settings (Stripe keys, Twilio creds, platform fee %, cancellation fees, matching algorithm, subscription requirement flag, ToS + Privacy text). Backed by a singleton `app_settings` row.

Mobile app consumes a public subset via `/settings` (handled by `routes/settings.py`), e.g. `google_maps_api_key`, `stripe_publishable_key`, legal text. Secrets stay on the backend only.

---

## 6. Support (`routes/admin/support.py`)

Ticket queue for rider / driver / admin-originated issues.

| Endpoint | Purpose |
|---|---|
| `GET /admin/support/tickets` | Paginated list with filters (status, category, assigned_to). |
| `GET /admin/support/tickets/{id}` | Full thread. |
| `POST /admin/support/tickets/{id}/reply` | Agent replies; adds thread message, notifies user. |
| `PATCH /admin/support/tickets/{id}` | Status, assignee, tags. |

User-side: `POST /support/tickets`, `GET /support/tickets`. See `features.py`.

---

## 7. FAQs (`routes/admin/faqs.py`)

CRUD for knowledge base entries (`question`, `answer`, `category`, `sort_order`, `is_active`). Rendered in the mobile app help section.

---

## 8. Documents (`routes/admin/documents.py`)

Driver onboarding document review.

| Endpoint | Purpose |
|---|---|
| `GET /admin/documents/pending` | All submitted documents awaiting review. |
| `POST /admin/documents/{id}/approve` | Mark approved; advances driver verification if all docs approved. |
| `POST /admin/documents/{id}/reject` | Mark rejected with reason; notifies driver. |
| `GET /admin/documents/{id}/download` | Signed URL for secure download from Supabase Storage. |
| `GET /admin/documents/requirements` | Dynamic requirements catalog (per role / region). |

Driver-side upload lives in `documents.py`. Requirements are stored in DB — `requirement_key` is normalized after migration 28b.

---

## 9. Drivers (`routes/admin/drivers.py`)

Fully covered in `RIDES_AND_DISPATCH.md` §12. Summary:

- List (deduped by phone+user_id), stats dashboard, update, verify, action (approve/reject/suspend/ban/unban/reactivate), status-override, notes CRUD, activity log, ride history, daily stats, assign service area, GPS trail.

All lifecycle actions write to `driver_activity_log` via `_log_driver_activity`.

---

## 10. Rides (`routes/admin/rides.py`)

Fully covered in `RIDES_AND_DISPATCH.md` §12. Summary:

- List, active monitoring, force-cancel (frees driver, notifies both), overall + detailed stats, ride details, GPS trail, live update, invoice, route-map PNG, heatmap, earnings, exports (CSV/JSON), payouts + stats.

---

## 11. Users (`routes/admin/users.py`)

Covered in `AUTH_AND_USERS.md` §5. Summary:

- Paginated list with filters, full profile + ride/wallet summary, status transitions (active/suspended/banned), force-logout (bump `token_version`), ride history, wallet ledger.

---

## 12. Staff (`routes/admin/staff.py`)

Covered in `AUTH_AND_USERS.md` §5. Summary:

- List admins, create (super-admin only; bcrypt hash), update role/modules/status, delete (token_version bump for revocation), password reset.

---

## 13. Wallet (`routes/admin/wallet.py`)

Rider wallet adjustments for compliance / support.

| Endpoint | Purpose |
|---|---|
| `GET /admin/wallet/{user_id}` | Balance + recent ledger. |
| `POST /admin/wallet/{user_id}/credit` | Manual credit; `reason=admin_adjustment`. |
| `POST /admin/wallet/{user_id}/debit` | Manual debit; `reason=admin_adjustment`. |
| `GET /admin/wallet/transactions/search` | Search ledger across users. |

Every adjustment is a ledger row — never an update to a running total. Keeps the audit trail intact.

---

## 14. Promotions (`routes/admin/promotions.py`)

Promo CRUD + targeting. See `WALLET_AND_PAYMENTS.md` §6 for the 10 validation rules. Admin surface adds:

- Targeting editor (segment: new riders / existing / region / role / first-ride-only).
- Usage analytics: redemption rate, per-promo revenue delta.
- Push-to-segment: notify eligible users directly.

---

## 15. Subscriptions (`routes/admin/subscriptions.py`)

Spinr Pass (driver-side subscription for reduced platform fees / premium features).

| Endpoint | Purpose |
|---|---|
| `GET /admin/subscriptions/plans` | List plans. |
| `POST /admin/subscriptions/plans` | Create plan. |
| `PATCH /admin/subscriptions/plans/{id}` | Update plan. |
| `GET /admin/subscriptions/{driver_id}` | Driver's subscription state. |
| `POST /admin/subscriptions/{driver_id}/cancel` | Force-cancel. |

Stripe subscriptions sync via webhook (`customer.subscription.*`). The `subscription_expiry` background loop nudges drivers <24 h before expiry.

---

## 16. Messaging (`routes/admin/messaging.py`)

Broadcast push / SMS to segments.

| Endpoint | Purpose |
|---|---|
| `POST /admin/messaging/push` | Push to segment (all riders / all drivers / filtered subset). |
| `POST /admin/messaging/sms` | SMS to segment (careful — cost + opt-out law). |
| `GET /admin/messaging/history` | Past broadcasts with delivery counts. |

Segment filters: role, region, tier, active-in-last-N-days, corporate_account_id.

---

## 17. Corporate B2B admin pages

The corporate B2B admin dashboard mounts its own pages under `admin-dashboard/src/app/dashboard/corporate-accounts/`. The backend routes live under `routes/corporate_accounts.py` and `routes/corporate_wallet.py`. Full reference: `docs/CORPORATE_B2B.md`.

---

## 18. Common ops runbooks

| Scenario | Steps |
|---|---|
| **Live incident: dispatch stuck** | Check `surge_engine` logs; check driver availability via `/admin/monitoring/drivers`; force-cancel orphan rides via `/admin/rides/{id}/cancel`. |
| **Driver reports missing earnings** | `GET /admin/drivers/{id}/rides`; `GET /admin/drivers/{id}/daily-stats`; if a ride was not counted, check `complete_ride` logs + `driver_location_history`. |
| **Rider reports wrong fare** | `GET /admin/rides/{id}/invoice`; compare `planned_distance_km` vs `actual_distance_km`; if recalculation misfired, inspect phase_distances. |
| **Stripe webhook failing** | Check `/admin/wallet/transactions/search` for the expected credit. If missing, inspect `stripe_events` row — `status=processing` means we claimed but never finished. Manually credit via `/admin/wallet/.../credit` and file a follow-up. |
| **Suspected account compromise** | `POST /admin/users/{id}/force-logout` — bumps `token_version`. Reset password if admin. |
| **Promo abuse** | Disable promo in `/admin/promotions`; tighten targeting; review redemptions via analytics. |
| **Bulk GPS / rollup missing** | `POST /admin/maintenance/rollup-driver-daily?target_date=YYYY-MM-DD`. Safe to re-run. |
| **DB growth** | `POST /admin/maintenance/cleanup-location-history?days=30`. Also consider cutting back GPS write frequency at the source. |

---

## 19. Common tasks

| Task | Where |
|---|---|
| Add a new admin module | Update `modules` enum/list; add route guard; register page in admin dashboard; document here. |
| Gate a route to a specific module | Dependency wrapping `get_admin_user` + module-list check (pattern in existing routes). |
| Add a new analytics metric | Build the SQL aggregation (`db_supabase.rpc` or direct query), expose via `routes/admin/analytics.py`, render in dashboard. |
| Add a new broadcast target | Extend segment filters in `routes/admin/messaging.py`. |
| Add a rollup job | New background loop or new endpoint under `routes/admin/maintenance.py`. Keep it idempotent. |
