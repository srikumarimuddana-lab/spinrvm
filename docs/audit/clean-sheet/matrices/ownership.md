# Ownership matrix — feature, service, background loop, runbook → owner

**Lane:** R20 Operating-Model Designer · **Wave:** W5 · **Date:** 2026-09-25 · **Mode:** report-only.

Seeded from `.github/CODEOWNERS`, the owner lines in `docs/runbooks/*.md` headers, the loop table in `02-findings/reliability.md` §3, the `.claude/agents/spinr-*.md` descriptions, and the owner questions every lane raised. **Where no person or role is actually accountable, this file says "unowned" rather than inventing one.** A proposed owner role is given separately so it is never mistaken for a current fact.

## §0 How ownership works today (VERIFIED)

1. **CODEOWNERS has two accounts and no teams.** Its own header (2026-08-28) records that the repo has exactly two collaborators and zero GitHub teams: one admin account (the org/repo owner) and one write account that authors most PRs. Every rule, including the catch-all `*`, lists both. GitHub never counts a PR author's own approval, so **on most PRs the admin account's approval is the only one that can count.** The domain team handles the file used to name (payments, schema, auth, dispatch, safety reviewers) were never real and have been removed. Whether "Require review from Code Owners" is switched on is UNKNOWN because branch protection has never been read (RR-01, C21). ACTION_ITEMS E8 (review routing) is open.
2. **Runbook "owners" are team labels that do not exist as teams.** Headers name `backend`, `devops`, `infra`, `data`, `security`, `compliance`, `product`, "Engineering Lead", "Legal/Privacy Officer", "Support lead", "Finance". None is backed by a GitHub team, an on-call rotation that is recorded as staffed, or a named role holder in the repo (INFERRED from CODEOWNERS §0.1 plus the absence of any roster). One runbook says so explicitly: `dual-run-driver-roster-policy.md` — "Owner: unassigned — needs a name before this is in effect". The product owner self-assigned the old-app decommission runbook (2026-09-07).
3. **The 28 `spinr-*` agents are reviewers, not owners.** They are invoked on a diff; none can be paged, approve a merge, or hold a production credential. Two surfaces have no reviewer at all: `shared/` logic (CARTO-002) and Maps & Routing (CARTO-003).
4. **Human-only roles are unfilled in the repo record:** migration owner (HIST-006 "owner deferred"), privacy officer (named in `data-breach.md` but not identified), accountant/tax counsel (every tax question has been open since 2026-08-13), SGI/municipal liaison, CI owner (quality.md §13), SOS on-call responder (TSF-001).

**Consequence.** Every row below whose "Accountable today" column says "unowned" falls back, in practice, to the admin account — one person approving everything. That is the single-point-of-failure the operating model (`06-operating-model.md` §6) has to fix before any cadence can be real.

**Legend.** *CODEOWNERS:* "specific" = a path-specific rule exists (still the same two accounts); "default" = only the `*` rule matches. *Accountable today:* who the repo record names; "unowned" if nobody. *Proposed role:* the role in `risk-register.md`'s legend that should own it (PROPOSED).

## §1 L2 epics (18, from `01-inventory/epics.md`)

| Epic | Main code paths | CODEOWNERS | Review agent(s) | Runbook(s) | Accountable today | Proposed role | Open risk rows |
|---|---|---|---|---|---|---|---|
| Ride Booking & Matching | `routes/rides/`, `services/dispatch_service.py` | specific | dispatch-reviewer, edge-case-reviewer, performance-sla-reviewer | `driver-not-receiving-rides.md` (no owner line) | unowned | BE-DISP | RR-94–RR-96, RR-15 |
| Ride Fulfillment | `routes/drivers/ride_flow.py`, `routes/rides/lifecycle.py`, `socket_manager.py` | partly specific (`routes/rides/`) | dispatch-reviewer, realtime-reliability-reviewer, insurance-period-auditor | `websockets.md` (owner: `backend`) | unowned (`backend` is a label) | BE-DISP | RR-14, RR-91, RR-104 |
| Ride Completion & Payments | `services/payment_service.py`, `routes/payments*`, `routes/webhooks.py`, `services/fare_service.py` | specific (payments, fare) | money-auditor, surge-auditor | `stripe-webhook-failure.md` (none), `stripe-reconciliation.md` (`backend` + `finance`) | unowned | BE-PAY | RR-42–RR-49 |
| Driver Earnings & Payouts | `routes/drivers/earnings.py`, `routes/drivers/payouts.py`, `utils/auto_payout.py`, `utils/t4a_pdf.py` | default | money-auditor | none specific | unowned; payout schedule decision unfiled (STRAT-012) | BE-PAY + FINANCE | RR-47, RR-54, RR-109, RR-17 |
| Corporate / B2B Billing | `routes/corporate_*`, `services/corporate_*` | specific | corporate-billing-reviewer, corporate-reporting-reviewer | `corporate-compensating-transaction.md`, `corporate-guest-booking.md` (no owner lines) | unowned | BE-PAY | RR-50–RR-52, RR-66 |
| Authentication & Authorization | `routes/auth*`, `dependencies/`, `utils/crypto.py`, `routes/admin/auth.py` | specific | security-auditor, admin-rbac-reviewer | `auth-tokens.md` (`backend`), `otp-lockout-false-positive.md` (none), `secret-rotation.md` (`devops` + `security`, placeholder dates) | unowned | BE-AUTH | RR-16, RR-21–RR-27 |
| Admin Dashboard & Operations | `admin-dashboard/`, `routes/admin/` | default | admin-rbac-reviewer, design-consistency-reviewer, ui-ux-critic | `admin-rollback.md` (on-call or release owner), `admin-pwa.md` | unowned; no PRD footprint (CARTO-001) | ADMIN | RR-53, RR-68, RR-111–RR-113 |
| Safety, Trust & Fraud | `routes/rides/safety.py`, `utils/safety_paging.py`, `routes/admin/safety.py` | specific (`routes/safety*` only) | safety-sos-reviewer, fraud-auditor | `sos-incident.md` (`security` + `compliance` + `product`; describes a system that does not exist, TSF-001), `security-incident.md` | **unowned — no responder is paged for a real SOS** | T&S | RR-116–RR-128 |
| Notifications & Messaging | `features.py` push, `utils/push_retry.py`, notification inboxes (forked) | default | notification-ux-reviewer | `transactional-outbox.md` (none) | unowned | BE-PLAT + MOBILE | RR-20, RR-131 |
| Maps & Routing | `routes/maps_proxy.py`, `service_areas.py`, `h3_heatmap.py`, venues | default | **none** (CARTO-003) | `route-pipeline-diagnostics.md`, `trip-route-integrity.md` (no owner lines) | unowned | BE-DISP | RR-63, RR-100, RR-114 |
| Promotions & Loyalty | `routes/promotions.py`, `routes/quests.py`, `utils/referral_payout.py` | default | fraud-auditor | none | unowned | BE-PAY + T&S | RR-117, RR-122 |
| Observability & Monitoring | `utils/metrics.py`, `utils/loop_alert.py`, `metrics-agent/`, `monitoring/` | default | observability-reviewer, sentry-triage-investigator, ops-triage-investigator | `synthetic-monitoring.md` (`devops`, scaffolding only), `capacity-scaling.md` (stale) | unowned | SRE | RR-12, RR-67, RR-85, RR-86 |
| Integrations & Webhooks | `sms_service.py`, `services/zoho_desk_integration.py`, `utils/email_provider.py`, `ai/providers/` | default | ai-guardrail-reviewer (AI only) | none per vendor | unowned | BE-PLAT | RR-98–RR-102, RR-39, RR-40 |
| Legacy Import & Data Migration | import services, legacy playbooks | default | migration-reviewer (schema only) | `legacy-migration-playbook.md`, `old-app-decommission.md` (product owner, self-assigned 2026-09-07), `dual-run-driver-roster-policy.md` (**explicitly unassigned**) | product owner (decommission only) | FOUNDER + BE-PAY | RR-17 |
| AI Assistant | `backend/ai/`, `routes/ai.py`, `rider-app/app/ai-assistant.tsx` | default | ai-guardrail-reviewer | none | unowned | BE-PLAT + PRIVACY | RR-101, RR-130 |
| Engineering Gates & CI/CD | `.github/workflows/`, `.claude/` | specific for `security-gates.yml` only | cicd-infra-reviewer, tooling-hygiene-reviewer, test-coverage-reviewer | `dependency-audit.md`, `dependency-update.md` (`backend`/`frontend`) | **unowned** (quality.md §13: no CI owner) | CI + REPO-ADMIN | RR-01–RR-07, RR-29, RR-37, RR-132–RR-134 |
| Shared Frontend Foundation & Design System | `shared/` (93 files) | default | accessibility-reviewer, design-consistency-reviewer (visual only) | none | **unowned for logic** (CARTO-002) | MOBILE | RR-09, RR-137–RR-140 |
| Platform Foundation & Schema | `backend/migrations/`, `repositories/`, `core/` | specific (`migrations/`, `core/config.py`, `core/middleware.py`) | migration-reviewer | `migration-conflict-detection.md`, `safe-data-migration-playbook.md`, `deploy-migration-*.md`, `pitr-restore.md` (`data` + `infra`) | **unowned — "migration owner" is an open question since 2026-08-21** | DB-OWNER | RR-08, RR-13, RR-36, RR-51 |

## §2 Services, vendors and infrastructure

| Service / vendor | What it holds or does | Where configured | Accountable today | Proposed role | Notes / risk rows |
|---|---|---|---|---|---|
| Fly.io (`yyz`, intended primary) | Backend compute | `backend/fly.toml`, Fly secrets | unowned (runbook says `infra`) | SRE | Deploy-capable token; not listed in the vendor register (RR-39) |
| Railway (warm standby, US) | Backend compute | `railway.json`, Railway dashboard | unowned | SRE | Runs 4 workers vs Fly 2 (RR-90) |
| Render (US region blueprint) | A committed `render.yaml` defines a backend service; live status UNKNOWN | repo root | **unowned / unknown** | SRE + PRIVACY | Confirm in the Render console (E-S5) |
| Cloudflare | DNS failover, proxy, origin lock (not enforced, C131) | dashboard | unowned | SRE | RR-27 |
| Supabase (Postgres, Storage, Vault) | All durable data incl. SIN/bank (encrypted) | dashboard; `backend/migrations/` | admin account (holds the dashboard); migration owner unassigned | DB-OWNER | RR-08, RR-18, RR-23, RR-36 |
| Redis (three URLs) | Rate limits, OTP lockout, WS pub/sub, locks | env | unowned (runbook: `devops` + `backend`) | SRE | Plan tier unrecorded (RR-90) |
| Stripe (incl. Connect) | Charges, payouts (set to manual) | `app_settings` + dashboard | unowned; payout decision unfiled | FINANCE + BE-PAY | RR-54 |
| Twilio | SMS OTP | `app_settings` | unowned | BE-AUTH | RR-98 |
| Firebase (FCM, App Check) | Push, attestation | console | unowned | SRE + MOBILE | App Check enforcement unrecorded (RR-32) |
| AWS SES (`ca-central-1`) / Resend (fallback) | Transactional e-mail | env | unowned | BE-PLAT + PRIVACY | Resend region UNKNOWN (RR-40) |
| Google Maps Platform | Places, geocode, directions | `app_settings`, budget breaker | unowned | BE-DISP | RR-63, RR-100 |
| Zoho Desk | Support tickets incl. SOS-linked | `app_settings` | unowned (support lead in `payment-dispute-evidence.md` only) | SUPPORT | RR-99 |
| Sentry | Errors | Fly extension | unowned | SRE | Refresh-token-theft alert rule still not created (C2, open since July) |
| LogRocket | Session replay (on by default on iOS) | mobile `_layout.tsx` | unowned | MOBILE + PRIVACY | RR-19 |
| Meta (SDK + Conversions API) | Ad attribution and per-ride purchase events | app config + `app_settings` | unowned | FOUNDER + PRIVACY | RR-38 |
| PostHog | Product analytics | apps | unowned | PRIVACY | Missing from DPA register (RR-31) |
| AI providers (Anthropic, OpenAI, Gemini, OpenRouter) | Assistant | `app_settings` keys | unowned | BE-PLAT + PRIVACY | DPA status per provider UNKNOWN (security.md §10 Q9) |
| Vercel | Admin dashboard hosting | Vercel project | unowned | ADMIN | Connector scope drifted to three projects (RR-29) |
| Expo EAS | Mobile builds, OTA | `eas.json` | unowned | MOBILE | Builds only via `[build]` commits |
| Cloudinary | Wired dependency, zero call sites | `requirements` | unowned | BE-PLAT | RR-102 |
| GitHub (repo, Actions, branch protection) | Code, CI, merge gate | Settings | admin account | REPO-ADMIN | RR-01; Actions budget cause of the 2026-09-08 outage UNKNOWN |

## §3 Background loops (44 + watchdog; `backend/core/background_loop_registry.py`)

All loops are spawned from `backend/core/lifespan.py`. **CODEOWNERS:** only `payment_retry.py` and `surge_engine.py` have a path-specific rule; every other loop module matches only the default `*` rule. **Loop-level reviewer:** `spinr-realtime-reliability-reviewer` covers all loops; the domain agent is listed where one applies. **Alert path for every loop:** `loop_watchdog` → `ALERT_WEBHOOK_URL` (production value UNKNOWN, RR-86). **Accountable today: unowned for every loop** — no loop has a named human who receives its alert. "Threshold" repeats `reliability.md` §3 (⚠ = default 2 h, mistuned per REL-001).

| # | Loop | Module | Placement | Domain agent | Runbook mentioning it | Threshold | Proposed role |
|---|---|---|---|---|---|---|---|
| 1 | subscription_expiry (6h) | `routes/drivers/subscriptions.py` | deferred | money-auditor | none | ⚠ | BE-PAY |
| 2 | surge_engine (2min) | `utils/surge_engine.py` (specific CODEOWNERS) | deferred | surge-auditor | `saskatoon-launch.md` | tuned | BE-PAY |
| 3 | scheduled_dispatcher (60s) | `utils/scheduled_rides.py` | api | dispatch-reviewer | none | ⚠ | BE-DISP |
| 4 | payment_retry (5min) | `utils/payment_retry.py` (specific CODEOWNERS) | deferred | money-auditor | `corporate-guest-booking.md` (mention) | tuned | BE-PAY |
| 5 | preauth_capture (5min) | `utils/preauth_capture.py` | deferred | money-auditor | none | ⚠ | BE-PAY |
| 6 | referral_payout (5min) | `utils/referral_payout.py` | deferred | fraud-auditor, money-auditor | none | ⚠ | BE-PAY |
| 7 | driver_claim_reaper (60s) | `utils/driver_claim_reaper.py` | api | dispatch-reviewer | none | ⚠ | BE-DISP |
| 8 | document_expiry (12h) | `utils/document_expiry.py` | deferred | regulatory-compliance-checker | none | ⚠ | COMPLIANCE + BE-DISP |
| 9 | driver_onboarding_reminders (15min) | `utils/driver_onboarding_reminders.py` | worker_wave1 | notification-ux-reviewer | `transactional-outbox.md` (mention) | tuned | BE-PLAT |
| 10 | corporate_autotopup (10min) | `utils/corporate_autotopup.py` | deferred | corporate-billing-reviewer | none | tuned | BE-PAY |
| 11 | corporate_low_balance (1h) | `utils/corporate_low_balance.py` | deferred | corporate-billing-reviewer | none | tuned | BE-PAY |
| 12 | allowance_reset (1h) | `utils/allowance_reset.py` | deferred | corporate-billing-reviewer | `corporate-compensating-transaction.md` | tuned | BE-PAY |
| 13 | kyb_reverification (24h) | `utils/kyb_reverification.py` | deferred | corporate-billing-reviewer | none | ⚠ | BE-PAY |
| 14 | route_finalizer (15s) | `utils/route_finalizer.py` | deferred | none (Maps & Routing unowned) | `route-pipeline-diagnostics.md` | tuned | BE-DISP |
| 15 | route_gap_monitor (15s) | `utils/route_gap_monitor.py` | deferred | none | none | tuned | BE-DISP |
| 16 | route_deviation_alerter (30s) | `utils/route_deviation_alerter.py` | api | safety-sos-reviewer | none | ⚠ (safety loop) | T&S + BE-DISP |
| 17 | stale_intent_reconciler (15min) | `utils/stale_intent_reconciler.py` | api | dispatch-reviewer | none | ⚠ | BE-DISP |
| 18 | safety_checkin (30s) | `utils/safety_checkin_loop.py` | api | safety-sos-reviewer | none (not in `sos-incident.md`) | ⚠ (highest-priority mistune) | T&S |
| 19 | retention_purge (24h) | `utils/retention_purge.py` | deferred | regulatory-compliance-checker | `data-retention.md` (`backend`) | tuned | PRIVACY + DB-OWNER |
| 20 | data_export_purge (1h) | `utils/data_export_purge.py` | deferred | regulatory-compliance-checker | none | ⚠ | PRIVACY |
| 21 | reconciliation (daily 02:00 UTC) | `utils/reconciliation.py` | deferred | money-auditor | `stripe-reconciliation.md` (`backend` + `finance`), `ledger-alerts.md` | ⚠ (inner poll, not falsely stale) | BE-PAY + FINANCE |
| 22 | stripe_reconcile (24h) | `utils/stripe_reconcile.py` | deferred | money-auditor | `stripe-reconciliation.md` | tuned | BE-PAY + FINANCE |
| 23 | dispute_evidence_reminder (6h) | `utils/dispute_evidence_reminder.py` | deferred | money-auditor | `payment-dispute-evidence.md` (support lead + finance) | ⚠ | SUPPORT |
| 24 | ledger_projection (15min) | `utils/ledger_projection.py` | deferred | money-auditor | `ledger-alerts.md` | ⚠ | BE-PAY |
| 25 | distance_reconciliation (daily 04:00 UTC) | `utils/distance_reconciliation.py` | deferred | insurance-period-auditor | none | tuned | BE-DISP |
| 26 | period1_distance_finalizer (5min) | `utils/period1_distance_finalizer.py` | api | insurance-period-auditor | none | tuned | BE-DISP + COMPLIANCE |
| 27 | driver_daily_rollup (30min) | `utils/driver_daily_rollup.py` | deferred | money-auditor | none | tuned | BE-PAY |
| 28 | stale_p3_closer (15min) | `utils/stale_p3_closer.py` | api | insurance-period-auditor | none | tuned | BE-DISP + COMPLIANCE |
| 29 | insurance_period_reconciler (10min) | `utils/insurance_period_reconciler.py` | api | insurance-period-auditor | none | ⚠ | BE-DISP + COMPLIANCE |
| 30 | h3_index_reconciler (2min) | `utils/h3_index_reconciler.py` (via registry) | deferred | none (Maps & Routing unowned) | none | ⚠ | BE-DISP |
| 31 | t4a_annual_job (yearly Feb 28) | `utils/t4a_annual_job.py` | deferred | money-auditor, regulatory-compliance-checker | none | **never heartbeats — cannot be flagged** | BE-PAY + ACCOUNTANT |
| 32 | driver_statements (30min) | `utils/driver_statement_job.py` | deferred | money-auditor | none | ⚠ | BE-PAY |
| 33 | stuck_ride_sweeper (60s) | `utils/stuck_ride_sweeper.py` | api | dispatch-reviewer | none | ⚠ (dispatch recovery) | BE-DISP |
| 34 | stale_in_progress_ride_alerter (5min) | `utils/stale_in_progress_ride_alerter.py` | api | dispatch-reviewer | **none — no response-time runbook (DRIVER-005)** | ⚠ | SUPPORT + BE-DISP |
| 35 | retention_guard_monitor (6h) | `utils/retention_guard_monitor.py` | api | regulatory-compliance-checker | none | ⚠ (security control, falsely stale) | PRIVACY |
| 36 | orphaned_hold_reconciler (15m) | `utils/orphaned_hold_reconciler.py` | deferred | money-auditor | none | ⚠ | BE-PAY |
| 37 | offer_expiry_reaper (10s) | `utils/offer_expiry_reaper.py` | api | dispatch-reviewer | none | ⚠ (dispatch-SLA backstop) | BE-DISP |
| 38 | driver_readiness_reconciler (20s) | `utils/driver_readiness_reconciler.py` | api | dispatch-reviewer | none | ⚠ | BE-DISP |
| 39 | suspension_reactivation (10min) | `utils/suspension_reactivation.py` | deferred | admin-rbac-reviewer (adjacent) | none | ⚠ | ADMIN |
| 40 | push_retry (30s) | `utils/push_retry.py` | worker_wave1 | notification-ux-reviewer | `transactional-outbox.md` (mention) | tuned | BE-PLAT |
| 41 | zoho_desk_sync (10min) | `utils/zoho_desk_sync.py` | worker_wave1 | none | `transactional-outbox.md` (mention) | tuned | SUPPORT + BE-PLAT |
| 42 | support_sla_breach_sweep (5min) | `utils/support_sla.py` | deferred | none | none | ⚠ | SUPPORT |
| 43 | capacity_watchdog (60s) | `utils/capacity_watchdog.py` | api | performance-sla-reviewer | `capacity-scaling.md` (stale), `on-call.md` | tuned | SRE |
| 44 | auto_payout (1h, Sundays) | `utils/auto_payout.py` | deferred | money-auditor | none | ⚠ (lands under the 2 h floor) | BE-PAY + FINANCE |
| — | loop_watchdog (5min) | `core/lifespan.py` | api | realtime-reliability-reviewer | `capacity-scaling.md`, `on-call.md` | self | SRE |

**Summary.** 44 of 44 loops: accountable owner **unowned**. 32 of 44 have no runbook that mentions them. 5 have no domain review agent (route_finalizer, route_gap_monitor, h3_index_reconciler, zoho_desk_sync, support_sla_breach_sweep). Placement split is 14 `api`, 3 `worker_wave1`, 27 `deferred` — but with `SPINR_PROCESS_ROLE` unset every machine runs all of them (RR-84).

## §4 Runbooks (66 files in `docs/runbooks/`)

"Owner stated" is the header text verbatim in meaning (team labels, not people). "Drilled" means a dated record of the runbook being exercised exists in the repo.

| Runbook | Owner stated | Drilled / current | Accountable today |
|---|---|---|---|
| `admin-rollback.md` | on-call engineer or release owner | not drilled; current | unowned (no on-call roster) |
| `auth-tokens.md` | `backend` | not drilled | unowned |
| `commit-signing-setup.md` | `infra` + each environment owner | n/a | unowned |
| `data-breach.md` | Engineering Lead + Legal/Privacy Officer | not drilled | unowned (roles not identified) |
| `data-retention.md` | `backend` | automatic loop; manual path not drilled | unowned |
| `dependency-audit.md` / `dependency-update.md` | `backend` + `frontend` / `backend` | weekly "manual until automated" | unowned |
| `dual-run-driver-roster-policy.md` | **explicitly unassigned** | not in effect | unowned |
| `error-responses.md`, `rate-limits.md`, `websockets.md` | `backend` (rate-limits: + `mobile`) | contract docs | unowned |
| `old-app-decommission.md` | product owner (self-assigned 2026-09-07) | planned for 2026-10-31 (tentative) | **product owner** |
| `on-call.md` | `devops` + engineering leadership | not read in full by any lane; no roster evidence | unowned |
| `payment-dispute-evidence.md` | support lead + finance | stale per admin-ops §5 | unowned |
| `pii-key-rotation.md` | `infra` + `compliance` | **never executed** (SEC-R10-010) | unowned |
| `pitr-restore.md` | `data` + `infra` | **never drilled; core assumption self-flagged "likely false"** | unowned |
| `railway-fly-failover.md` | none stated (infra implied) | **never drilled** (C1); current 2026-09-21 | unowned |
| `redis-down.md` | `devops` + `backend` | no dated drill | unowned |
| `renewal-calendar.md` / `secret-rotation.md` | `devops` / `devops` + `security` | scaffolding, placeholder dates | unowned |
| `security-incident.md` | Engineering Lead + Legal | not drilled | unowned |
| `sos-incident.md` | `security` + `compliance` + `product` | **describes a system that does not exist** (TSF-001); links a missing trauma guide | unowned — **no responder is paged** |
| `stripe-reconciliation.md` | `backend` + `finance` | daily automatic; alert trust low (MONEY-013) | unowned |
| `supabase-down.md` | `devops` + `backend` | cites a PagerDuty rotation with no account (domain-safety B15) | unowned |
| `supabase-region-migration.md` | `infra` + `compliance` | trigger-only | unowned |
| `synthetic-monitoring.md` | `devops` | **scaffolding only — no external monitor exists** | unowned |
| `capacity-scaling.md` | none stated | **stale vs the 2026-09-16 fleet** (A3-005) | unowned |
| `saskatoon-launch.md` | launch lead, ops, engineering (audience) | stale topology guidance (admin-ops §14) | unowned |
| 38 other runbooks (migration and legacy playbooks, EAS/Android signing, Stripe legacy, route diagnostics, fly-mixed-fleet, ledger-alerts, MOBILE_SMOKE, c43 readiness, car-marker device verification, staging, DAST, etc.) | no owner line | mostly one-off or procedural | unowned |
| **Missing:** backend deploy rollback | — | does not exist (reliability.md §8) | unowned |
| **Missing:** stale in-progress ride response | — | does not exist (DRIVER-005) | unowned |
| **Missing:** customer outage communications | — | does not exist (SKB-004) | unowned |
| **Missing:** trauma-support guide | — | referenced, does not exist (SKB-003) | unowned |

## §5 Decision areas that need a named human (not engineering)

| Area | Accountable today | Needed by | See |
|---|---|---|---|
| Tax (PST, GST supplier of record, Part XX.1, T4A) | **unowned** — no accountant engagement recorded | 7-day deadline proposed; T4A before 2027-02-28 | `05-escalations.md` §B |
| SGI / municipal licensing and filings | **unowned** — no liaison recorded | before eligibility flags flip | `05-escalations.md` §C |
| Privacy officer (breach determinations, DPA register, LogRocket/Meta) | named as a role in `data-breach.md`; person not identified | now | `05-escalations.md` §A |
| Legal counsel (ToS, ICA, contractor status) | **unowned** — nine live legal texts without counsel review | before the next ToS version bump | `05-escalations.md` §C |
| SOS on-call responder and paging tool | **unowned** | now | `05-escalations.md` §D |
| Migration owner | **unowned** since 2026-08-21 | now (8+ pending migrations) | `05-escalations.md` §D |
| CI owner / branch protection | admin account (by default) | now | `05-escalations.md` §D |

## §6 What to do with this matrix (PROPOSED)

1. **Name a human per proposed role** — even if one person holds several roles, the point is that each alert and each decision has a recipient. Record it in CODEOWNERS (as individual accounts until teams exist) and in each runbook header. Target: every "unowned" cell in §1–§4 filled within two weeks (`ROADMAP.md` N21).
2. **Add a loop-owner column to the loop registry** (`background_loop_registry.py`) and route watchdog alerts per owner once `ALERT_WEBHOOK_URL` is confirmed (RR-85, RR-86).
3. **Give `shared/` logic and Maps & Routing a reviewer** by widening an existing agent's charter rather than adding new agents — the same two people maintain every agent's config (CARTO-002, CARTO-003; 08-hostile-review §1.8).
4. **Re-generate this file quarterly** from CODEOWNERS, runbook headers and the loop registry (a script, not hand edits — HIST-005's lesson).

*Written by R20 (W5), 2026-09-25. No code, config or data was changed.*
