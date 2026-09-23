# Durable dispatch claim identity and insurance-aware recovery

**Date:** 2026-09-22
**Surface:** Backend dispatch, cancellation, settings, stale-claim recovery, and insurance-period persistence.
**Trigger:** Review of cancelled-offer recovery found migration 442 comparing app-clock claim stamps to database-clock period stamps. That comparison can reject the same claim under clock skew, while the legacy reaper can release availability without closing the corresponding insurance period.

## Root cause and fix

Timestamps were being used both as age estimates and as ownership evidence. Migration 448 adds nullable UUID identity columns to drivers, offers, and insurance periods. Feature-flagged v2 claim RPCs assign the UUID and database timestamp atomically; offers and Period 2 retain that same ID. Cancellation and reaping lock the driver and require matching identity, active-offer/ride guards, and matching period ownership. Missing Period 2 does not create a synthetic historical row; the recovery opens the current Period 1/0 and logs the historical gap. NULL/mismatched identity or missing v2 stamp fails closed. Legacy claim/reaper and release paths remain guarded and compatible while the flag is off.

An age window alone was not selected as the ownership check: tolerating timestamp skew still makes ownership depend on two clocks and can confuse retries of one claim with a replacement. A durable UUID is assigned once in the database and copied to each dependent row. The legacy path keeps its app-clock age comparison and adds a locked timestamp compare-and-set so dark rollout does not reintroduce cross-clock comparison.

| File area | Change |
| --- | --- |
| Migration 448 | Nullable identity columns, settings switch, validation/cleanup triggers, identity-aware release/reapers and v2 claim RPCs |
| Dispatch repositories and matching/settings routes | Feature-gated claim selection and identity propagation |
| Insurance-period helper and claim reaper | Per-offer/per-claim RPC routing, guarded recovery and anomaly reporting |
| Tests and change log | Direct-pool fixture settings, legacy/v2 routing coverage, rollout/impact record |

## Blast radius and UX

The flag is `settings.dispatch_claim_identity_enabled`, default false. It gates new PostgREST identity claims and, together with the existing `dispatch_direct_pool_enabled`, the direct-pool v2 path. Linked non-NULL offer IDs always route through v2 release even after rollback so in-flight claims drain. Existing available-driver writers clear claim identity through a narrow database trigger. The offer insert trigger validates non-NULL V2 identities under the driver lock to reject delayed inserts from an old claim; NULL legacy inserts remain compatible.

Dispatch consumers include matching, direct-pool batch claims, cancellation/decline/expiry release helpers, the background claim reaper, settings API, and period transition trigger. Drivers and riders see no new normal-flow UI. On missing-period recovery, availability is restored only after current-state audit data is made consistent and an ERROR records the historical gap. Flag-off keeps legacy claim issuance and timestamp-based release; its reaper now uses a transactionally guarded stamp compare-and-set and insurance transition. The existing ordinary terminal-offer path still performs availability release and period transition as separate calls; this change does not convert that broader lifecycle into one RPC, so a process failure between those calls remains an existing audit-recovery gap.

**Before/after:**

```text
before: owner inferred from app-clock stamp vs DB-clock period timestamp; stale reaper could free driver without closing Period 2
after:  v2 owner proven by one DB-issued UUID on driver + offer + Period 2; recovery releases and transitions periods in one transaction
```

## Rollout and rollback

Apply migration 448 first; deploy all backend instances with v2-aware routing and reaper code while the flag remains off. Confirm the candidate release is stable, then enable the flag. The direct-pool path still requires its own flag. To roll back new issuance, turn `dispatch_claim_identity_enabled` off. Keep migration 448's columns, triggers, and RPCs in place while non-NULL claim IDs drain; never drop identity columns or rewrite historical insurance rows. Flag-off stops new identity claim issuance and keeps the legacy protocol's known timestamp-based limitations.

## Verification performed

- Focused Python integration suites: 158 passed, covering settings, claim routing, cancellation routing, repository claims, reaper behavior, and dispatch parity.
- Disposable PGlite SQL matrix: 17/17 cases passed for cancellation, stale/new claims, late offers, missing periods, NULL period identities, active/historical offers, duplicates, clock offsets, and ACLs.
- Final migration parsed and applied against the direct-pool fixture schema with PostgreSQL 18.3 via PGlite. Native PostgreSQL is unavailable in this sandbox; production/staging data was not accessed.
- Python command: `python -m pytest --no-cov backend/tests/test_driver_repo_coverage.py backend/tests/test_insurance_periods.py backend/tests/test_driver_claim_reaper.py backend/tests/test_dispatch_claim_parity.py backend/tests/test_dispatch_direct_pool_flag_settings.py backend/tests/test_rides_matching_coverage.py backend/tests/test_dispatch_pool.py -q`.
- No visual or mobile-device changes.

## Not verified

- Native PostgreSQL concurrent lock/race behavior; the existing CI PostgreSQL integration suite remains required.
- Staging rollout, production metrics, or live Supabase behavior.

## Post-review fixes (2026-09-23)

| Field | Detail |
|---|---|
| **Issue/gap identified** | (1) Both 448 reapers refused a claimed driver whose open Period 2/3 was left behind by a release that crashed between `set_driver_available` and the period write, so the driver stayed online without offers until they toggled offline. Main released these after about 90 s. This applies with the flag off too. (2) `set_driver_available(True)` wrote the new `availability_claim_id` column, so every release would fail with PGRST204 if the backend deployed before the migration was applied. (3) Routine reaper skips (`offer_or_ride_active`, claim-changed races) were logged at ERROR, which reaches Sentry, every 60 s for each busy driver. (4) Migration number collided with #5717's 444. |
| **Root cause** | (1) The period-ownership guard only accepted Period 1 or a matching Period 2. (2) A redundant write duplicated the `drivers_clear_availability_claim_on_release` trigger. (3) The skip set was incomplete. (4) Two PRs were branched in parallel. |
| **Fix/remediation** | (1) Under the existing driver lock, and only after the no-live-offer/no-live-ride checks pass, a Period 2/3 whose ride is completed/cancelled, or whose offer is terminal (declined/preempted/expired/cancelled), is closed and Period 1 opened. The result carries `stale_period_closed`, which is logged as a warning plus metric. (2) The Python write was removed; the trigger clears the column. (3) Benign statuses were added to both skip sets. (4) Renumbered to **448** (#5717 took 444–446, #5718 took 447). |
| **Risk & impact** | The reaper can now close an open Period 2/3 row. It never mutates any other row, and only closes when no live offer or ride exists for the driver. One existing expectation changed: a NULL-identity Period 2 on a **cancelled** ride is now recovered instead of refused, because it is a crash leftover, not another claim. A NULL-ride Period 2 still fails closed. |
| **Rollback plan** | Unchanged: flag off. The reaper change applies in both flag states; reverting it requires a follow-up migration re-creating the previous function bodies. |
| **Verification performed** | Real PostgreSQL 17 (local): `direct_pool/test_dispatch_claim_identity.py` 17 passed, and the 9 new stale-period cases fail against the previous SQL. `test_insurance_periods.py` 20, `test_driver_claim_reaper*.py` 7 + 11, `test_set_driver_available_invariant.py` + `test_driver_repo_coverage.py` 46. |
| **What was NOT verified** | No staging run or concurrent multi-replica test; other direct_pool files were not run locally (CI runs them). |
| **Deploy order** | **Migration 448 must be applied to production before this PR merges.** Deploys run on every push to main, and the reaper and cancel-release paths read 448's columns and RPCs. |
