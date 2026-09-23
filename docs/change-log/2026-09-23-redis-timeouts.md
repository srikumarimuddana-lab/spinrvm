# Bounded Redis waits — PR 5725

| Field | Value |
|---|---|
| Issue / root cause | No explicit connect/command deadlines and implicit Redis retries can prolong dependency outages. Initialization errors could disclose Redis credentials. |
| Fix | Two-second connect/socket deadlines, zero client retries; initialization log contains exception class only. |
| Risk | Shared client: slow operations raise sooner, callers retain established error behavior. Deadlines are per operation, not an end-to-end SLA. A timed-out write may have executed remotely; DB/Stripe idempotency remains essential. |
| Alternative | Per-call deadlines duplicate logic; common client options enforce one consistent policy. |
| UX effect | Errors surface earlier during Redis stalls. No frontend/build change. |
| Rollback | Restore client options and redeploy. No data changes. Restore Redis health before enabling processing; do not bypass payment gates. |
| Verification | Timeout-option and credential-leak tests failed before implementation; full Redis coverage run before commit. |
| Not verified | Live Redis, failover and all consumers under network stalls. |

Files: Redis client (options/logging), its coverage test (regressions), this record.

Before: `from_url(url, encoding="utf-8", decode_responses=True)` and raw exception string.
After: `socket_connect_timeout=2, socket_timeout=2, retry=Retry(NoBackoff(), 0)`; exception class only.

## Blast-radius search

Production modules importing this shared client (local `rg` search; existing fallback contracts unchanged):

- `backend/utils/referral_payout.py`
- `backend/utils/stuck_ride_sweeper.py`
- `backend/server.py`
- `backend/utils/scheduled_rides.py`
- `backend/utils/auto_payout.py`
- `backend/utils/stale_in_progress_ride_alerter.py`
- `backend/utils/stale_intent_reconciler.py`
- `backend/ai/conversations.py`
- `backend/ai/response_cache.py`
- `backend/ai/orchestrator.py`
- `backend/ai/tools_booking.py`
- `backend/ai/mcp_server.py`
- `backend/utils/route_finalizer.py`
- `backend/utils/idempotency.py`
- `backend/utils/capacity_watchdog.py`
- `backend/utils/route_gap_monitor.py`
- `backend/utils/location_integrity.py`
- `backend/utils/suspension_reactivation.py`
- `backend/utils/route_deviation_alerter.py`
- `backend/utils/maps_budget.py`
- `backend/utils/surge_engine.py`
- `backend/utils/driver_claim_reaper.py`
- `backend/utils/retention_purge.py`
- `backend/utils/ledger_projection.py`
- `backend/utils/driver_presence.py`
- `backend/utils/stale_p3_closer.py`
- `backend/utils/period1_distance_finalizer.py`
- `backend/utils/zoho_desk_sync.py`
- `backend/utils/breadcrumbs.py`
- `backend/utils/safety_checkin_loop.py`
- `backend/utils/retention_guard_monitor.py`
- `backend/utils/payment_retry.py`
- `backend/utils/reconciliation.py`
- `backend/utils/offer_expiry_reaper.py`
- `backend/utils/preauth_capture.py`
- `backend/services/lms_service.py`
- `backend/utils/orphaned_hold_reconciler.py`
- `backend/repositories/_base.py`
- `backend/utils/session_revocation.py`
- `backend/utils/push_retry.py`
- `backend/socket_manager.py`
- `backend/utils/h3_index_reconciler.py`
- `backend/utils/route_distance.py`
- `backend/utils/t4a_annual_job.py`
- `backend/utils/distance_reconciliation.py`
- `backend/diagnose_nearby_drivers.py`
- `backend/utils/driver_daily_rollup.py`
- `backend/utils/stripe_reconcile.py`
- `backend/utils/location_write_gate.py`
- `backend/utils/insurance_period_reconciler.py`
- `backend/dependencies/__init__.py`
- `backend/utils/h3_location_index.py`
- `backend/utils/maps_eta.py`
- `backend/utils/notification_throttle.py`
- `backend/routes/websocket.py`
- `backend/routes/fares.py`
- `backend/routes/users.py`
- `backend/routes/maps_proxy.py`
- `backend/routes/admin/monitoring.py`
- `backend/routes/drivers/profile.py`
- `backend/routes/drivers/location.py`
- `backend/routes/admin/sentry.py`
- `backend/routes/drivers/subscriptions.py`
- `backend/routes/drivers/_shared.py`
- `backend/routes/admin/auth.py`
- `backend/routes/drivers/ride_flow.py`
- `backend/routes/rides/payments.py`
- `backend/routes/admin/analytics.py`
- `backend/routes/rides/safety.py`
- `backend/routes/rides/tracking.py`
- `backend/routes/rides/matching.py`
- `backend/routes/rides/_shared.py`
- `backend/routes/auth.py`
- `backend/routes/admin/subscriptions.py`
