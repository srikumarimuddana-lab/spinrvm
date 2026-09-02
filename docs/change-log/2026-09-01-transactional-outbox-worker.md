# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-01 |
| Author | Cursor agent session (transactional outbox) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (uncommitted; no PR) |
| Related issue or gap ID | Corrected plan `postgres_transactional_outbox_800946fd`; architecture remediation `add-durable-workers` |

## 1. Issue / gap identified

Automatic ride receipts were spawned or awaited after settlement. A crash
between the paid write and `send_ride_receipt` could drop the GST/PST receipt
with no durable retry. Background loops also all ran on every API replica.

## 2. Root cause

There was no transactional outbox. Receipt send was a second, non-atomic write
after the ride row committed paid. Process-local `loop_monitor` could not prove
a separate worker was healthy.

## 3. Fix / remediation

- Migration `399_transactional_outbox.sql`: `outbox_messages`, lease RPCs,
  `settings.outbox_receipts_enabled` default false, AFTER UPDATE trigger on
  `rides` for completed+paid (payload `{"ride_id"}` only).
- Dedicated `worker.py` (one uvicorn worker) runs the outbox poller plus
  wave-1 loops: push retry, Zoho Desk sync, driver onboarding reminders.
- API `SPINR_PROCESS_ROLE=all` (default) unchanged; `api` omits only those
  three loops. Application fallback checks **actual outbox row existence**,
  not the settings cache. Manual rider/admin resend is ungated.
- Operator CLI + runbook for dead-letter redrive. Fly process groups and
  Grafana alert stubs are in-repo; **not deployed**.
- Review follow-up: worker lifespan initializes Sentry, then Firebase, then
  `init_database` (production fail-closed). `claim_batch` raises when the
  RPC client is missing (`None`) or returns a non-list. `outbox_claim_batch`
  returns expired max-attempt rows as `dead_lettered` (`RETURNING *`) so the
  poller can metric/Sentry them without re-dispatching.

## 4. Risk & impact on existing functionality

- Blast radius: **cross-surface backend** — every automatic paid-ride receipt
  path (`routes/rides/payments.py`, `routes/webhooks.py` invoice.paid and
  payment_intent.succeeded, `utils/preauth_capture.py`,
  `services/payment_service.py` guest corporate auto-settle). Manual resend
  (`routes/rides/receipts.py`, `routes/admin/rides.py`) unchanged.
- While `outbox_receipts_enabled` is false, the trigger inserts nothing; app
  code falls back to today's direct send. Lookup failure also falls back and
  **can duplicate**.
- At-least-once: provider accept + crash before ack can send two identical
  receipts. No historical backfill.
- `SPINR_PROCESS_ROLE=api` before a healthy worker would stop push retry /
  Zoho / onboarding reminders on API with nothing replacing them.
- Fly `[processes]` plus `flyctl scale count app=8 worker=1` in
  `.github/workflows/deploy-fly.yml` and `bootstrap-fly.yml`. A bare
  `scale count 8` (no group) would create 8 workers — do not run that.
- Watchdog now uses `active_api_loop_names(process_role)` instead of a
  duplicated literal list. H3 reconciler remains classified `deferred` (still
  on API); this tree was already dirty with uncommitted H3/monitoring.
- Money paths, ride state machine, SOS, dispatch, and insurance periods are
  unchanged.

## 5. User-experience effect

- Nobody, while the producer flag stays false (default).
- After enable: riders may receive a duplicate automatic receipt in the crash
  window described above. Manual "email receipt" still sends immediately.
- Not visible mid-session as a UI change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/399_transactional_outbox.sql` | Table, trigger, RPCs, RLS | Atomic producer |
| `backend/services/outbox.py` | RPC/query boundary | Worker + CLI |
| `backend/utils/outbox_worker.py` | Poller, leases, metrics | Delivery |
| `backend/services/outbox_receipts.py` | Row-existence gate | Cutover without cache |
| `backend/utils/email_provider.py` | Structured delivery result | Retry vs terminal |
| `backend/utils/email_receipt.py` / `payment_service.py` | `send_ride_receipt_result` | Worker hydration |
| `backend/routes/rides/payments.py`, `webhooks.py`, `preauth_capture.py` | Auto-receipt gate | Skip spawn when queued |
| `backend/core/background_loop_registry.py`, `lifespan.py`, `config.py` | Process role | Wave-1 split |
| `backend/worker.py` | Dedicated app | `/health` + `/metrics`; Firebase/Sentry/DB init |
| `backend/utils/sentry_runtime.py` | Shared `sentry_sdk.init` | PIPEDA options on API + worker |
| `backend/scripts/outbox_admin.py` | list-dead / show / redrive | Ops |
| `backend/fly.toml` | app + worker process groups | Staged deploy |
| `.github/workflows/deploy-fly.yml`, `bootstrap-fly.yml` | `scale count app=8 worker=1` | Process-group-aware burst pool |
| `backend/utils/loop_monitor.py` | Thresholds for outbox + wave-1 | Worker `/health` stale detection |
| `metrics-agent/grafana/alert-rules.yaml` | Worker / backlog / DLQ | Alert stubs |
| `docs/runbooks/transactional-outbox.md` | Rollout + rollback | Operators |

## 7. Before / after

```
# Before (process-payment)
_deps.spawn(send_ride_receipt(ride, current_user["id"], tip_rounded))
```

```
# After
await maybe_send_auto_receipt(
    ride, current_user["id"], tip_rounded, spawn=_deps.spawn
)
# skip spawn when outbox_messages already has topic=ride_receipt.v1
# dedupe_key=auto:{ride_id}
```

## 8. Rollback plan

Without a second deploy of application code:

1. `UPDATE public.settings SET outbox_receipts_enabled = false WHERE id = 'app_settings';`
2. Leave the worker up to drain committed rows.
3. Set `SPINR_PROCESS_ROLE=all` (already the Fly `[env]` default) and confirm
   wave-1 loops on API.
4. `fly scale count 0 --process-group worker`.
5. Do not drop `outbox_messages`.

`git revert` is not sufficient once rows or emails exist.

Railway C5: failover still has no equivalent worker until that blocker is
closed.

## 9. Verification performed

- [x] Focused pytest (review-fix pass): `tests/test_outbox_worker.py`,
      `tests/test_worker_app.py`, `tests/test_outbox_admin.py`,
      `tests/test_outbox_receipts.py`, `tests/test_lifespan_watchdog_coverage.py`,
      `tests/test_sentry_frame_vars.py::test_server_init_uses_the_pipeda_options_and_does_not_override_them`.
      RLS outbox tests written; **skipped** (no `TEST_DATABASE_URL`).
      SQL `outbox_claim_batch` now has exactly two `RETURN QUERY` statements
      (dead-letter UPDATE `RETURNING *`, then claim UPDATE).
- [ ] Manual staging payment scenarios (card, wallet, corporate, preauth,
      webhook) — not run this session.
- [x] Blast-radius grep for `send_ride_receipt` auto vs manual paths.
- [x] Conventions: Decimal unchanged, dual-import, RLS grants in migration,
      PIPEDA (IDs only in payload/logs), metrics names from the plan.
- [x] Feature-flagged producer (`outbox_receipts_enabled` default false).
- [ ] `npm run build` N/A (backend only).
- [ ] Docker image build — not run this session.
- [ ] Fly/Railway deploy — not run. Workflows now scale `app=8 worker=1`;
      still do not deploy until a human follows the staged rollout in
      `docs/runbooks/transactional-outbox.md`.

## 10. What was NOT verified

- Real Postgres trigger/lease/RLS behaviour (tests self-skip without DSN).
- Live SES/Resend, production worker scrape, or staging settlement matrix.
- Visual regression: N/A (no customer UI). rider-app/driver-app/admin-dashboard
  have no active visual baselines for this change.
- Railway standby worker (C5 still open).
- Uncommitted H3/monitoring work (396–398) was left in the dirty tree and not
  edited except prior tiny allowlist/fixture notes.
