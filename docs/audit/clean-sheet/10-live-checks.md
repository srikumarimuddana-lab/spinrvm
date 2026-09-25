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
- **The remaining 24 of the 32 untracked migrations**, and files numbered below 400.
