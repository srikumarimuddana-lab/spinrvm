# Live read-only checks against production (2026-09-25)

*Orchestrator. Read-only, metadata and configuration only. No table rows, no personal data, no secret values were read. Where a secret column mattered, the query returned only whether it is set.*

## §0 Scope and guard rails

- **Target:** only the Supabase project `spinrmobileapp` (`soavhtdhefowwvforzwb`). `.claude/context/connector-scoping.md` confirms it is production.
  - The connector has write rights until 2026-10-31 under a user exception, but only read calls were used.
  - The calls were `get_project`, `list_extensions`, `list_migrations`, `get_advisors` (security and performance), and five `SELECT` queries on `schema_migrations`, `information_schema`, and specific boolean or count columns of `settings` and `service_areas`.
- **Not touched:**
  - **Railway.** Reading its variables would return secret values.
  - **Fly.io.** Policy forbids agent use.
  - **Stripe and Sentry.** Their connectors are down.
  - **Vendor consoles**, including Meta, LogRocket and Twilio.
- **Evidence labels:** every result below is VERIFIED-LIVE as of 2026-09-25 about 03:30 UTC. It will drift.

## §1 Answers to questions the audit had left UNKNOWN

| Question | Live answer | Findings affected |
|---|---|---|
| Is the database in Canada? | Yes: `ca-central-1`, Postgres 17.6, status ACTIVE_HEALTHY. | Data residency (compliance). The Render blueprint in `render.yaml` (US region) is still unverified; this only covers Supabase. |
| Is pgaudit installed? | **No.** Available but not installed. | SEC-R10-001: there is still no database-side record of which key ran which query. |
| Is pgsodium still in use? | Yes: 3.1.8 installed, alongside `supabase_vault` 0.3.1. | SEC-A2-002 / E15: the exit plan from pgsodium is still needed. |
| Are the Meta per-ride events live? | **Yes, very likely.** Both the Meta access token and the rider dataset id are set in `settings`. `backend/utils/meta_capi.py:256,349` sends whenever both are present. | STRAT-004 moves from "configured" to "live" (VERIFIED config, INFERRED sending; no send log was read). |
| Minimal FCM offer payload on? | **No** (`minimal_fcm_offer_payload_enabled = false`). | SKB-002 confirmed: offer pushes still carry full trip coordinates. |
| AI escalation creates a support ticket? | Yes (`ai_escalation_creates_ticket = true`). | G8 / SKB lane question resolved. |
| PST charged anywhere? | **No:** 0 of 6 service areas have `pst_enabled`. | G9 / MONEY-003 / E1 confirmed; the accountant question stands. |
| Instant payout enabled? | **Yes, in all 6 service areas.** | BENCH-004 and ROADMAP N22: the server endpoint is live everywhere, so the velocity cap is urgent even though the driver UI is gone. |
| Referral payout velocity cap | 5 per day. | TSF referral-abuse controls: a cap exists. |
| Dual approval for exports | Off. | ADMIN-OPS-001 pattern: dual approval exists for exports but is switched off. |
| Incentive eligibility enforcement | Off. | Incentive abuse controls are dark. |
| Driver availability v2 (atomic release fix) | Off. | DISPATCH-003: the fix is still dark in production. |
| Rideless SOS | Off. | SOS coverage when there is no active ride is dark (TSF lane). |
| Route deviation alerts | On. | Safety: route deviation alerting is live. |
| Notification throttling | Off. | SKB lane: quiet hours and daily caps ship dark, as reported. |
| Is an SOS paging target set? | **No.** `settings.sos_paging_webhook_url` is empty (only emptiness was checked, not the value). | **TSF-001 is now VERIFIED-LIVE:** `backend/utils/safety_paging.py` pages only through this webhook, so a real SOS pages no one in production today. This is the first item in `research/session-3-trust-safety-fraud.md` §Now. |
| Are `rides` money columns float in production? | **Yes.** Live `information_schema` shows `base_fare`, `distance_fare`, `time_fare`, `surge_multiplier`, `total_fare`, `tip_amount`, `driver_earnings`, `cancellation_fee_admin` and `cancellation_fee_driver` are `double precision`; only `grand_total` is `numeric`. | MONEY-006 is VERIFIED-LIVE. This corrects `04-blueprint.md` H2 and `08-hostile-review.md` correction 19: `total_fare`, `tip_amount` and `driver_earnings` are float live, so they go back from INFERRED to VERIFIED. |
| Fare lock on? | Yes (`fare_lock_enabled = true`). | Money lane: quote locking is live. |
| Double-entry ledger writing on? | **Off (strong inference).** `settings` has no `ledger_double_entry_enabled` column, and `backend/services/ledger_service.py:426-431` reads it with a default of False. | Blueprint H2: the double-entry legs are dark in production. |
| Forced app upgrade floor set? | **No.** `min_rider_app_version` and `min_driver_app_version` are empty strings. | Research Session 4 MR-10: `ForcedUpgradeMiddleware` is built but has no floor configured, so no old app version is blocked today. |

## §2 New finding: the migration tracking table does not match the schema

**LIVE-001 (HIGH). The tracking table misses about 32 changes that are applied.** Production's `schema_migrations` table records 518 applied files (first 2026-08-14, last 2026-09-24). The repo has 554 migration files. Two of them are on the runner's `NEVER_APPLY` list.
- **What the gap is:** 32 repo files numbered 424 and above have **no tracking row**, including 424–429, 432–448, 451, 452, 454–457 and 465.
- **What was checked:** a sample of objects those files create, read from `information_schema`: migrations 424, 426, 429, 438, 444 (table and columns), 452, 457 and 465.
- **Result:** **every sampled object exists.** So these changes are applied to production but were never recorded.
- **Other tracking paths:** Supabase's own migration history (`list_migrations`) holds 46 entries applied through the Supabase tools, and it stops at 419. So they were not applied through that path either.
- **Consequence:** running `python -m backend.scripts.run_migrations` against production would try to re-apply about 32 files.
  - Most are idempotent (`IF NOT EXISTS`).
  - Function redefinitions and backfills are not safe to replay in a different order. For example, 444 would re-run after its own fix, 447, and 457 after 458–464.
  - `--status` would also report them as pending, which is misleading.
- **Root cause:** HIST-006 and CARTO-005, confirmed live. Schema changes reach production by hand, and there is no step that records them.
- **Fix:** do not run the runner against production until this is reconciled.
  1. For each of the 32 files, confirm the objects exist. A read-only query per file is enough.
  2. Insert the tracking rows with the runner's own checksum logic.
  3. Only then adopt the single apply path in ROADMAP N24 and blueprint delta 2.
- **Owner:** migration owner (unowned today, per `matrices/ownership.md`).
- **Rollback:** tracking-row inserts are reversible by deleting those rows.
- **Not verified:** the full 32-file object check; only a sample of 8 files was checked. Files numbered below 400 were not diffed file by file. The counts suggest about 2 more differences there beyond the NEVER_APPLY pair.

## §3 Security advisor (Supabase linter)

| Lint | Level | Count | Reading |
|---|---|---|---|
| RLS enabled, no policy | INFO | 61 tables, including `users`, `rides`, `drivers`, `wallets`, `payouts` | Deny-by-default for `anon` and `authenticated`. That fits the backend's service-role-only model (CLAUDE.md, C108), so this is not an exposure today. It becomes one only if a direct client path is ever added. |
| Function search_path mutable | WARN | 16 functions, including `match_and_claim_driver`, `find_nearby_drivers`, `update_driver_location`, `fare_split_pay_share`, `increment_promo_uses`, and the immutability guards on `audit_logs` and `financial_events` | Hardening gap. Set `search_path` explicitly on each function. The immutability guards and the dispatch claim function matter most. **New (LIVE-002, MEDIUM).** |
| SECURITY DEFINER callable by `authenticated` | WARN | 1: `is_party_to_lost_and_found_case(text)` via `/rest/v1/rpc/` | Dormant today because no end user holds a Supabase JWT (C108), but it breaks the lockdown pattern of migrations 354 and 450. Revoke `EXECUTE` from `authenticated`. **New (LIVE-003, LOW).** |

## §4 Performance advisor (summary; the full output was 75 KB and was summarised by script)

| Lint | Level | Count | Example tables |
|---|---|---|---|
| RLS policy re-evaluates `auth.*()` per row | WARN | 44 | `driver_offer_receipts`, `driver_documents`, `push_tokens`, `emergency_contacts`, `driver_insurance_periods` |
| Multiple permissive policies | WARN | 19 | corporate members and allowances, `disputes`, `driver_documents` |
| Duplicate index | WARN | 7 | `corporate_accounts` (3), `loyalty_transactions`, `promo_applications`, `ride_incentive_claims` |
| Unindexed foreign keys | INFO | 12 | `corporate_subscriptions`, `data_export_objects`, `driver_insurance_period_corrections`, `drivers` |
| Unused index | INFO | 150 | `ride_payment_operations`, `payouts`, `ride_offers`, `driver_documents`, `driver_insurance_periods` |
| Auth DB connections absolute | INFO | 1 | project setting |

**Reading.** The RLS-policy lints have no live cost today, because traffic uses the service role and bypasses RLS. The duplicate indexes cost write speed on corporate and loyalty tables. The unindexed foreign keys and unused indexes are routine tuning. "Unused" counts reset with statistics, so check the stats age before dropping anything. None of this changes the audit's priorities. It feeds the Later tuning item in ROADMAP.

## §5 Still not verified live

- **Railway and Fly secrets and config:**
  - whether `ALERT_WEBHOOK_URL` is set;
  - whether `SPINR_PROCESS_ROLE` is set;
  - OTP pepper and App Check;
  - Render service status.
- **Whether the old service-role key was rotated.** The Supabase dashboard's API key history is needed. The connector does not expose it.
- **The Supabase backup and PITR tier.**
- **Branch protection on `main`.**
- **Whether Meta events were actually sent**, and how many. That needs the Meta Events Manager.
- ~~The remaining 24 of the 32 untracked migrations, and files numbered below 400.~~ Done in §6.1.

## §6 Follow-up checks (2026-09-25, about 13:00–14:00 UTC)

*Run after PR #5773 merged, to close three items that §5 and `09-final-report.md` §4 listed as not verified. Everything was read-only. The Supabase queries read only catalog metadata (table, column, function, index, trigger and policy names) and the `schema_migrations` file list. Railway reads were limited to service status, the deployment list and CPU/memory metrics; no variables were read.*

### 6.1 Migration tracking: the full comparison

The repo now has 560 migration files (main at `9e27915`); production's `schema_migrations` has 518 rows. A full file-by-file comparison, not a sample, gives:

| Group | Count | What it means |
|---|---|---|
| On the runner's `NEVER_APPLY` list (70, 78, 137) | 3 | Expected to be untracked. Correct. |
| Untracked, below 400 (26, 379) | 2 | **Applied.** Row-level security is on for every table that still exists. The two import tables that 379 also covered no longer exist, so there is nothing to check there. |
| Untracked, 424–466 and 468–471 | 37 | **Applied.** For each file, every object it creates or changes (tables, columns, functions, indexes, triggers, policies) was looked up in production. 111 objects across 35 files were checked, and 110 are present. The one absent is 429's admin-read policy, which 439 later removes on purpose. The other two files were checked differently. 432's four renamed admin policies are all present. 447 re-creates objects from 444 and 445, and those are all present. |
| **Untracked and NOT applied: 467** (`driver_always_location_gate_enabled`) | 1 | **Pending.** The column does not exist in production. The file was merged at 05:36 UTC today. The backend reads the flag with `.get(...) is True` (`backend/routes/drivers/profile.py:103`), so the missing column reads as off, the file's intended default. But saving this setting from the admin settings screen (`backend/routes/admin/settings.py:566`) will fail until the column exists. **LIVE-006 (LOW).** |
| **Tracked, but no such file in the repo**: `415_route_deviation_alert_enabled_setting.sql` | 1 | **LIVE-004 (MEDIUM), new.** Production has the `settings.route_deviation_alert_enabled` column, and the flag is **on** (§1). No file in `backend/migrations/` creates it. The clone is shallow, so git cannot show whether the file was ever committed. **Consequence:** a database rebuilt from the repo (staging, a restore drill, disaster recovery) would lack the column. `backend/utils/route_deviation_alerter.py:133-135` then reads the flag as missing and **silently leaves route-deviation safety alerts off**. **Fix:** add the missing migration (`ADD COLUMN IF NOT EXISTS`, so it is a no-op in production), then include it in N25's back-fill. |

**What this changes in ROADMAP N25.** The back-fill list is **39 files**: 2 below 400, and 37 from 424 up. That is not "about 32". All 39 are confirmed applied at the object level, so inserting their tracking rows is safe. **Separately:**
- apply 467 through the normal path;
- add the missing 415 route-deviation file.

**Limits.** This is an object-existence check. It shows the objects exist, but not that each function body or policy text matches the file byte for byte. Files that only change data (`UPDATE`) or permissions (`GRANT`/`REVOKE`) were checked only through the objects they also create. A body-level diff of the replaced functions (for example `purge_pii_retention`, `apply_stripe_refund_cumulative`, `transition_driver_availability`) is still open.

### 6.2 Railway standby (project `cooperative-harmony`, service `spinrvm`)

| Check | Live answer |
|---|---|
| Health | Online; 1 of 1 replica running; 0 crashes and 0 failed deployments in 24 h. The Redis service is also online. |
| Running current code | Yes. The latest successful deployment (12:49 UTC today) followed the push of `main` at `9e27915`. |
| Load, last 24 h | CPU averages 0.03 vCPU (peak 1.3). Memory averages about 1.0 GB (peak about 2.0 GB). |
| **Region** | **`us-east4` (Virginia, United States).** |
| Stale work item | One deployment has been stuck in "removing" since 2026-04-14. It is cosmetic; clear it in the Railway dashboard. |

**LIVE-005 (HIGH, compliance), with an existing escalation now verified live.** `docs/vendor-register.md:24` already says "US (Railway default)". But `docs/runbooks/railway-fly-failover.md:3,25`, `docs/framework/06-operations-deployment.md:10` and CLAUDE.md's Deployment section all say "Railway (Canada)", and those docs are wrong. The standby runs every background loop against the production database (REL-003). So rider and driver personal data is processed in the US **today**, not only during a failover. This moves escalation **E-S5** from "standby (US)" as an inference to VERIFIED-LIVE. The founder's options there (accept and justify, or move to a Canadian region) are unchanged. Correcting the three documents belongs in ROADMAP N16.

### 6.3 Automated tests and Semgrep, run in the audit sandbox

**Semgrep: Spinr's own rules (`.semgrep/spinr-rules.yml`, v1.178.0) over 592 files found 9 findings and 0 errors.**
- **4 in `backend/`.** These are exactly the 4 "known untriaged" findings listed in `.github/workflows/security-gates.yml` (lifespan idempotency ×2, the ride-state guard at `repositories/driver_repo.py:411`, and the static Stripe idempotency key at `routes/payments.py:486`). They are unchanged since 2026-08-14 and still untriaged after six weeks.
- **5 `spinr-pii-in-logs` findings in operator scripts.** Four are in `scripts/manage_admin.py:30-52` and one in `loadtest/preauth_bots.py:134`. Each prints a phone number to the operator's own console. The risk is low, since this is local tooling and not production logs, but the rule is right: print the last four digits only.
- **The money gate (SR-03) is clean**, consistent with CI.

**Semgrep: public rule packs (`p/python`, `p/typescript`, `p/secrets`, `p/owasp-top-ten`) did not run.** The sandbox's network proxy blocks `semgrep.dev`, where the packs are downloaded. This half is still **NOT VERIFIED here**. CI runs it on every PR, so its results are in the GitHub Security tab.

**Backend tests:** the run was still in progress when this section was first committed. Its results follow in the next commit.
