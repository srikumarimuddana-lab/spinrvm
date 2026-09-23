# PR 5725 — read-only release preflight, 2026-09-23

## Evidence boundary

Supabase project discovery exposed only production `spinrmobileapp` (`soavhtdhefowwvforzwb`), healthy in ca-central-1, PostgreSQL 17.6.1.127. No staging project was available. Catalog and nonsecret setting queries only were used: no production fixture writes, financial mutations, deployment, or customer data reads.

## Observed catalogs and configuration

| Check | Observed result | Release implication |
|---|---|---|
| All 35 public `admin_*` functions | Anonymous/authenticated execution denied; service-role execution allowed | Catalog ACL check passes; not a complete endpoint authorization test |
| `is_party_to_lost_and_found_case(text)` | Anonymous denied; authenticated and service role allowed | Preserve the authenticated ownership helper |
| `update_live_driver_marker` | TEXT, timestamptz, jsonb; security definer; anonymous/authenticated denied, service role allowed | Correct object signature/ACL exists |
| `public.schema_migrations`, prefixes 444–450 | Only `450_revoke_client_exec_admin_rpcs.sql`, applied 2026-09-23 11:54:00.920211 UTC | Correct objects do not establish complete file provenance; do not blindly backfill or replay |
| `supabase_migrations.schema_migrations`, marker/payment 447/450 names | Only `20260829170726 location_marker_write_gate_flag` matched | Migration 447 provenance remains unresolved |
| `ride_payment_operations.ride_id` | Table exists, column TEXT | Object compatibility observed; full migration application not proven |
| `dispatch_claim_batch` | Existing text/text[]/integer[]/integer/timestamptz/timestamptz signature | No `dispatch_claim_batch_v2` or `transition_ride_status_tx` in catalog |
| `corporate_wallet_apply_delta` | Both 10- and 11-argument overloads present | Tests explicitly select the 11-argument migration-376 overload |
| Global and six service-area geo providers | `postgis` | PostGIS is already selected; no provider migration is needed merely to satisfy the plan |
| `dispatch_direct_pool_enabled` | false | Direct-pool activation still gated |
| `location_marker_write_gate_enabled` | false | Marker gate not activated |
| `dispatch_claim_identity_enabled` | absent/null | Do not infer enabled behavior from absent setting |
| `auto_payout_enabled` | null | Do not call payouts disabled: application default is enabled |
| `corporate_billing_enabled` | true | Protect existing corporate financial paths |

`claim_stripe_event` is a Python repository helper, so absence of an RPC with that name is not a missing-database-function finding.

## Reproduction

With the read-only catalog interface, inspect `pg_proc` joined to `pg_namespace` and use `pg_get_function_identity_arguments`, `prosecdef`, and `has_function_privilege` for the exact signatures/roles above. Inspect `information_schema.columns` for the payment table; inspect `public.schema_migrations` and the provider's migration history separately. Read only the named global settings and service-area provider fields. Record project, timestamp and object results; never export secrets or full business tables.

## Release rubric

Code completion is not a release grade. The plans' 14-day rollout gate, 30-day correctness/alert observation and original representative-quarter A-grade requirement are cumulative. No elapsed window or grade is awarded here. A valid release packet needs actual staging readiness/SHA and forced-restore evidence, worker inventory/ownership and receiver receipts, representative synthetic load, native physical-device journeys, two staged rides, and the required observation windows.

## Measured optimization decisions (A6)

PostGIS is already selected in the observed fleet settings. Keep direct-claim activation, OSRM, Redis GEO, arq, generated types and broad route extraction conditional on a demonstrated missing contract or measured benefit. Before a routing or storage change, capture representative staging load, query/route latency distributions, failure rate, correctness invariants and comparable resource cost on the same dataset. No such benchmark was available; speculative rewrites are not a completion criterion.

## Remaining access/evidence

No Fly token, staging Supabase project/DSN, Stripe test credentials, device service credentials, or Grafana provisioning/receipt access was available. Redis was subsequently provisioned as a disposable loopback process and its WebSocket integration test passed; PostgreSQL is still unavailable locally. Existing production objects and local simulated tests cannot replace external operational proof.

## Historical GitHub evidence

At baseline `c367e31c68834a2f8465a72ee6f3742df1f3d56c`, the GitHub Actions API reported successful PR Checks (35882188089), Security Gates (35882158724), Claude Config Audit (35882158204), CI Guard Rails (35882158735) and Fly resilience checks (35882158071). CI/CD Pipeline 35882158738 was cancelled. These are historical run-level statuses, not proof that all individual jobs executed or that the new continuation head passes. Final-head checks must be assessed separately.
