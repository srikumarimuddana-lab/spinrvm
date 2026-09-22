# Durable dispatch claim identity and insurance-aware recovery

**Date:** 2026-09-22
**Surface:** Backend dispatch, cancellation, settings, stale-claim recovery, and insurance-period persistence.
**Trigger:** Review of cancelled-offer recovery found migration 442 comparing app-clock claim stamps to database-clock period stamps. That comparison can reject the same claim under clock skew, while the legacy reaper can release availability without closing the corresponding insurance period.

## Root cause and fix

Timestamps were being used both as age estimates and as ownership evidence. Migration 444 adds nullable UUID identity columns to drivers, offers, and insurance periods. Feature-flagged v2 claim RPCs assign the UUID and database timestamp atomically; offers and Period 2 retain that same ID. Cancellation and reaping lock the driver and require matching identity, active-offer/ride guards, and matching period ownership. Missing Period 2 does not create a synthetic historical row; the recovery opens the current Period 1/0 and logs the historical gap. NULL/mismatched identity or missing v2 stamp fails closed. Legacy claim/reaper and release paths remain guarded and compatible while the flag is off.

An age window alone was not selected as the ownership check: tolerating timestamp skew still makes ownership depend on two clocks and can confuse retries of one claim with a replacement. A durable UUID is assigned once in the database and copied to each dependent row. The legacy path keeps its app-clock age comparison and adds a locked timestamp compare-and-set so dark rollout does not reintroduce cross-clock comparison.

| File area | Change |
| --- | --- |
| Migration 444 | Nullable identity columns, settings switch, validation/cleanup triggers, identity-aware release/reapers and v2 claim RPCs |
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

Apply migration 444 first; deploy all backend instances with v2-aware routing and reaper code while the flag remains off. Confirm the candidate release is stable, then enable the flag. The direct-pool path still requires its own flag. To roll back new issuance, turn `dispatch_claim_identity_enabled` off. Keep migration 444's columns, triggers, and RPCs in place while non-NULL claim IDs drain; never drop identity columns or rewrite historical insurance rows. Flag-off stops new identity claim issuance and keeps the legacy protocol's known timestamp-based limitations.

## Verification performed

- Focused Python regression suites: 155 passed; a second run after adding explicit v2/legacy routing checks: 76 passed.
- Disposable PGlite SQL matrix: 15 cases passed for cancellation, stale/new claims, late offers, missing periods, NULL period identities, active/historical offers, duplicates, clock offsets, and ACLs. The updated app-clock legacy cutoff signature is being rerun before merge.
- Migration parsed and applied against the direct-pool fixture schema with PostgreSQL 18.3 via PGlite. Native PostgreSQL is unavailable in this sandbox; production/staging data was not accessed.
- No visual or mobile-device changes.

## Not verified

- Native PostgreSQL concurrent lock/race behavior; the existing CI PostgreSQL integration suite remains required.
- Staging rollout, production metrics, or live Supabase behavior.
