# Spinr Full-App Audit Runbook (every surface + the cutover seam)

**How to use**: paste everything from the `---` below into a fresh Claude Code
session on this repo (or reference this file directly). This runbook supersedes
the earlier scratch-only cutover audit prompts (v1/v2, 2026-08-15). It has
**two parts with different cadences** — run the part the moment calls for:

- **PART A — Whole-app fleet audit**: every surface and sub-surface, all 21
  reviewer agents. Cadence: before launch, before any release cut, then weekly
  against `main` as a drift check.
- **PART B — Dual-run cutover seam audit**: the old-app ↔ new-app boundary only
  (pending money, identity, migration, decommission). Cadence: after every
  production cut lands, and before each decommission gate. Decommission target:
  **October 31, 2026 (tentative)**.

Do not treat either part as covering the other — that exact confusion already
happened once (2026-08-15). Part A never opens the old-app data; Part B never
audits the app's general surfaces.

---

## Session-wide operating rules (apply to BOTH parts)

1. **Audit-only.** Every agent reports findings; nothing executes, no files
   change, without the user's explicit, separate go-ahead. Established posture:
   dry-run first, filter-chain-verified numbers, Change Impact Log before any
   write (see `docs/change-log/2026-08-14-sk-pst-revert.md`,
   `2026-08-15-dual-run-monitoring-signals.md`).
2. **Every dollar figure states its exact filter chain** — source → each filter
   in order → row count → dollar sum. A number without one is a guess, not a
   finding. Applies to every agent touching money.
3. **Verified vs. assumed, every time.** Old-app-derived numbers are "as of the
   last production cut" — say so on every quote, with the cut date/batch id.
4. **No silent scope-narrowing.** If a check turns out bigger than expected, say
   so rather than quietly answering a smaller question.
5. **PII hygiene for the session's own output**: reports reference IDs and
   last-4 only — never full names, phones, emails, addresses, or raw GPS —
   matching the repo's never-log list, even though agents hold live Supabase
   read access.
6. **Don't re-derive what's pinned down.** Read the Prior-Findings Ledger below
   first; for anything it covers, the task is *"verify still true + report the
   delta since the ledger date"*, not a fresh derivation. Divergent duplicate
   reports are worse than none.
7. **Every "needs a decision" finding must land in the decision log** as
   *decision needed + owner + due date* (recorded in `ACTION_ITEMS.md` A34) —
   a surfaced-but-ownerless decision is how items rot until Oct 30.
8. **Bandwidth discipline**: agents run in parallel with tightly-scoped prompts;
   commit each phase's report before starting the next so context loss never
   loses work; two-layer reporting everywhere (plain-English summary up top,
   technical detail with `file:line`/filter chains underneath).

## Prior-Findings Ledger (as of 2026-08-15 — read these, don't re-derive)

| Topic | Status | Where |
|---|---|---|
| Cutover Phases 0–3 (pending money, collision risk, retention, identity, tax, corporate, migration mapping, decommission runbook) | Reported | `docs/audit/2026-08-15-dual-run-cutover/P0…P3` |
| Whole-app fleet audit, 21 reviewers, 17 ranked blockers + decision list | Reported | `docs/audit/2026-08-15-full-fleet-launch-readiness.md` |
| Consolidated blocker/decision index | Live | `ACTION_ITEMS.md` **A34** |
| Dual-run monitoring signals (first-go-online audit row + 2 labeled counters, all payout paths incl. auto-payout) | **Shipped**, flag `app_settings.dual_run_monitoring_enabled` | PR #3954, `backend/utils/dual_run_monitor.py`, change-log `2026-08-15-dual-run-monitoring-signals.md` |
| $276.59/20-driver payout correction (dry-run only; corrected `legacy_outstanding_correction` design; 5 buckets blocked on re-link) | Parked — extend, don't duplicate. **Note: its plan doc lives only on PR #3946's unmerged branch**, not `main` | PR #3946 |
| Legacy tax split (`tax_amount` = commission-GST only; `payout_gst_amount` never imported, $105.17; now preserved raw on new imports) | Confirmed; historical-GST figure for the 186 migrated rows is a **business/legal decision**, not a code fix | `docs/audit/2026-08-14-mongodb-legacy-extract-audit.md`, importer fix same-day |

## Facts snapshot (measured 2026-08-15 — correct any stale assumption against this)

- New app production Supabase: **187 rides total — 186 legacy-imported (batch
  `20260729184745`, imported 2026-07-29, trip dates 2026-04-01→2026-07-26) + 1
  organic cancelled ride**. Zero drivers online. Collision risk is *latent*, not
  yet live traffic on both sides.
- 211 drivers (189 legacy-marked, 22 unmarked from 07-26→07-31 — unexplained;
  150 active+verified i.e. dispatchable on go-online; 104 with
  `stripe_account_id`). 1,134 users, **none** legacy-marked (rider importer
  stamps nothing on `users`).
- **Open discrepancy: prior audits say 224 rides were imported; production holds
  186, zero soft-deletes.** Unexplained. Any per-cut reconciliation sits on this
  unexplained base until it's resolved — carry it as a P0 item in Part B.
- Two incompatible legacy-ID namespaces: rides carry Mongo ObjectIds, drivers
  carry 10-digit numeric IDs (0/62 cross-match). Final migration requires a
  crosswalk sourced from the old export.

---

# PART A — Whole-App Fleet Audit (every surface and sub-surface)

**Scope**: the entire application — `backend/` (all ~25 routers, services,
utils, 35+ background loops, migrations), `rider-app/`, `driver-app/`,
`admin-dashboard/`, `shared/`, `.github/workflows/` + deploy config, and the AI
surface (`backend/ai/**`, `/mcp`, `rider-app/app/ai-assistant.tsx`).

**Dispatch all 21 `spinr-*` reviewers in a single parallel batch** (per
`/full-audit` — no path-routing, no pre-filtering; a quiet agent reporting
"audited, clean" is a finding, an undispatched agent tells you nothing):

| Group | Agent | Independent angle |
|---|---|---|
| Money | `spinr-money-auditor` | Decimal-only math, Stripe idempotency, receipt line-item transparency, surge cap in fare math |
| Money | `spinr-surge-auditor` | 2.5× hard cap, tier table, never-retroactive, never-on-corporate, surge_source integrity |
| Money | `spinr-corporate-billing-reviewer` | wallet/allowance RPC idempotency + row-locking, cap discipline, settlement fallback ordering |
| Money | `spinr-fraud-auditor` | referral velocity/self-referral, promo stacking, signup reuse, GPS-ping plausibility on **every** ingestion path (v1, v2 outbox, WS) |
| Money | `spinr-corporate-reporting-reviewer` | per-company scoping, GST/PST as separate lines in every export, SK-local (not UTC) statement boundaries |
| Core | `spinr-security-auditor` | OWASP, JWT trust model, RLS posture, OTP/refresh discipline, PII-in-logs, secrets |
| Core | `spinr-dispatch-reviewer` | state-machine legality, WS emission, acceptance race guard, driver-liveness at accept |
| Core | `spinr-insurance-period-auditor` | Period 0–3 vs the *actual* batch-offer dispatch model (no `driver_assigned` in production), append-only, ride_id invariants |
| Core | `spinr-safety-sos-reviewer` | never-auto-dial-911, degraded-auth availability, emergency-contact storage vs `domain-safety.md`'s promises |
| Core | `spinr-realtime-reliability-reviewer` | WS auth/heartbeat/rate-limit contract, cross-replica fan-out, replay safety + watchdog coverage of ALL loops |
| Core | `spinr-migration-reviewer` | recent migrations vs conventions; prefix uniqueness; whether the CI gate actually blocks |
| Core | `spinr-admin-rbac-reviewer` | every mount gated, every module string grantable-and-real, super-admin boundaries explicit not by-omission |
| Core | `spinr-cicd-infra-reviewer` | SHA-pinned actions, health checks, secrets handling, required-check wiring, Fly/Railway parity (C5), hotfix path speed |
| Core | `spinr-edge-case-reviewer` | network-retry idempotency, app-lifecycle reconciliation, version skew, multi-device races, clock trust |
| Quality | `spinr-regulatory-compliance-checker` | SK eligibility gates enforced in code (license class, vehicle age, experience, CRC+VSC), retention jobs live for **every** geometry column, consent versioning, WAV/service-animal |
| Quality | `spinr-accessibility-reviewer` | WCAG 2.1 AA on rider/driver/admin; labels, focus management, touch targets; state plainly that findings are reasoned-from-code (N12: no visual tooling) |
| Quality | `spinr-design-consistency-reviewer` | brand tokens only (no superseded teal/amber), light/dark parity, loading/empty/**error** states on every async action |
| Quality | `spinr-ai-guardrail-reviewer` | PII scrubbing on EVERY provider-egress path incl. tool results and `/mcp`, injection resistance, cost caps, fare-service reuse |
| Quality | `spinr-performance-sla-reviewer` | the P95 SLA table, N+1s, inline third-party calls, unbounded list queries |
| Quality | `spinr-observability-reviewer` | audit-table coverage for every admin action, metric per state transition (incl. cancellations), Sentry tag discipline, log levels |
| Quality | `spinr-test-coverage-reviewer` | required-test list, per-module coverage floors vs the CI gate that actually enforces them, test theater, DB-level RLS testing |

**Per-agent output contract**: blockers first (max ~10–12 findings), each with
`file:line` + concrete failure scenario; then a NOT-verified list; then a domain
verdict (SAFE TO LAUNCH / FIX BLOCKERS / NEEDS HUMAN REVIEW) and one
plain-English paragraph. Rollup = worst verdict across all 21; never soften a
single agent's verdict.

**Baseline to verify against, not rediscover**: the 2026-08-15 fleet run's 17
ranked blockers and 7 open decisions
(`docs/audit/2026-08-15-full-fleet-launch-readiness.md`). For each, report
**fixed / still open / regressed** with evidence. New findings go below the
baseline table, clearly marked NEW.

**Deep-dive follow-ups**: when a single finding needs more than its fleet
reviewer gave it, use the repo's slash-command audits — `/fare-audit`,
`/security-check`, `/dispatch-check`, `/surge-check`, `/corporate-check`,
`/insurance-check`, `/compliance-check`, `/ai-check`, `/migration-check` — as
scoped second passes, not as substitutes for the fleet.

**Not agent-shaped — track, never claim**: load/chaos testing (E2), DAST/pentest
(E6), backup-restore drills (E7), real screen-reader/visual-regression passes
(N12). These need tooling or a human; the fleet must state them as standing
gaps, not silently absorb them.

---

# PART B — Dual-Run Cutover Seam Audit (v3)

**Context**: old vendor ride-share app (MongoDB, own Stripe usage, own hosting)
and new Spinr app run simultaneously until decommission (**Oct 31, 2026,
tentative**). No live old-app DB access, none expected. Periodic traffic-based
production cuts are migrated into Supabase via
`backend/services/booking_import_service.py` and sibling importers — **the
already-migrated data in production Supabase is this audit's source**. End state
before decommission: old app at zero pending payouts, zero pending refunds, zero
open disputes; then ALL historical data migrates with nothing pending.

**Stripe MCP**: not yet authorized → the live-Stripe portion of 0.3 is
**blocked-pending-access** (report it as such, never silently omit); re-run that
narrow piece the moment credentials are shared. The Supabase Stripe *mirror* is
available now and powers 0.0.

Work phases in order. Do not start P2 before P0 is fully reported.

## Phase 0 — Critical: money and regulatory exposure

### 0.0 — Stripe-mirror cross-check FIRST (highest priority in this prompt)
**Agent: `spinr-financial-migration-auditor`**, live Supabase read

Before trusting any "owed" figure — the standing lesson from the near-miss where
this plan almost recommended a real second Stripe transfer to an already-paid
driver.

**Step 0 — preconditions (do these before any matching):**
- a. Determine **which Stripe account the mirror covers** (old app's, new
  app's, or both) — from `docs/runbooks/stripe-legacy-migration.md`'s Scenario
  A/B and the mirror tables' provenance. If it covers only the new app's
  account, an "exact pair" proves nothing about old-app payment — the whole
  cross-check inverts. State the answer explicitly before proceeding.
- b. Check `MAX(synced_at)` on `driver_stripe_ledger` /
  `driver_stripe_payouts` — the mirror's freshness bounds every conclusion.

**Matching spec (all 20 driver buckets, not a sample):**
- Compare with **Decimal exactness**; state whether the target is **gross owed
  or net-of-Stripe-fees** and check both if ambiguous.
- Window: transfers within **±14 days** of the relevant ride(s); aggregate
  partial payments inside the window (two transfers summing to the amount count
  as a candidate match).
- Require a real link, not amount coincidence: attempt to tie
  `driver_stripe_ledger.id` / description / metadata to `rides.id` or
  `legacy_import_metadata->>'old_booking_id'`. If the schema simply can't link
  them, say so explicitly.
- **Ambiguity rule (default disposition): amount matches but no ID link →
  classify UNRESOLVED and HOLD — never "paid," never "owed."** Multiple
  candidate pairs → UNRESOLVED-HOLD likewise.
- Output: recompute the correction with its own filter chain, splitting every
  bucket into CONFIRMED-OWED / CONFIRMED-PAID / UNRESOLVED-HOLD. Produce a new
  figure; do not footnote the old $276.59.

### 0.1 — Per-cut acceptance checklist (repeatable, not a one-time essay)
**Agent: `spinr-financial-migration-auditor`**

For the **most recent** production cut (state its date + batch id as the "as
of" boundary on every number):
- Pending/due/unsettled payment+payout rows; open refunds; open
  disputes/chargebacks — full filter chains.
- **Reconciliation against the source**: per-collection row counts and Decimal
  dollar sums, export vs. landed; unmatched IDs listed, not counted.
- **Resolve or explicitly carry the 224-vs-186 discrepancy** — a reconciliation
  on an unexplained base is itself unexplained.
- Standing recommendation (needs user go-ahead to build): turn this section
  into an automated post-cut reconciliation script that writes a dated record —
  one-time human audits decay; a script is the gate that scales to Oct 31.

### 0.2 — Dual-run collision risk — **verify-delta only**
Answered 2026-08-15 (zero cross-system awareness; double-dispatch and
double-payout structurally possible; latent while 0 drivers online). Re-verify:
imported-driver online counts, whether any operational roster policy now
exists, and whether the shipped monitoring signals (PR #3954) are live and
emitting. Report deltas, not a re-derivation.

### 0.3 — Stripe reconciliation — narrowed
**Agent: `spinr-money-auditor`**
- Covered now via the mirror: everything 0.0 does.
- **Still blocked on live Stripe access**: confirming the mirror is current
  beyond `synced_at`, in-flight not-yet-synced transfers, account-level diffs.
  The ready 7-item checklist lives in the P0 report §0.3 — run it the day
  access arrives; it does not require repeating the rest of P0.

### 0.4 — Regulatory retention during dual-run — **verify-delta only**
Answered 2026-08-15 (GPS/trip intact for imported rides with true historical
clocks; vehicle-at-trip-time missing; insurance-period trail structurally
absent; **no final-export plan** = permanent loss of post-cut rides if the old
app dies). Re-verify: has a final-export plan/owner appeared; has the
insurance-period decision (reconstruct-and-flag vs documented exception — a
legal call) been made. Escalate both if still ownerless.

**P0 exit criterion**: one reconciled statement of pending money (post-0.0,
with UNRESOLVED-HOLD explicitly quantified), collision-risk delta, retention
delta — each with its cut-date boundary.

## Phase 1 — Financial and identity reconciliation

### 1.1 — Identity mapping, old ↔ new
**Agent: `spinr-financial-migration-auditor`**
Full-population map is **blocked without the old export** (the two ID
namespaces can't be joined inside Supabase — see Facts). Do what's possible:
per-cut delta of unmatched ride→driver links (3 known, $20.73), the 22
unmarked drivers, rider-marker absence. State the export as the unblocking
prerequisite every time.

### 1.2 — Corporate account continuity
**Agents: `spinr-corporate-billing-reviewer` + `spinr-corporate-reporting-reviewer`**
Unknown-not-cleared (the fleet/subscription/pass collections have never been
opened; the wallet RPCs cannot seed a legacy balance). Re-verify whether any
cut since has included them; otherwise restate the unknown loudly.

### 1.3 — Tax-field consistency — **carry forward, don't re-derive**
Confirmed finding stands (see Ledger). The open item is a **decision**: the
correct historical rider-facing GST for the 186 pre-fix rows (no source column
records it; `gst + payout_gst_amount` is a plausible guess, not verified).
Surface with owner + due date; also verify every *new* cut preserves
`old_payout_gst_amount` raw, per the importer fix.

### 1.4 — Fraud/duplicate-account exposure from dual-run — **verify-delta**
Baseline in P1 report §1.4 + fleet fraud report (referral velocity, un-backfilled
old customers as "new," `created_at = now()` on imported riders). Re-verify
against launch-week promo configuration specifically.

**P1 exit criterion**: identity-map delta with explicit unmatched/ambiguous
lists, tax decision logged with owner, fraud-guard delta.

## Phase 2 — Migration completeness

### 2.1 — Collection inventory — blocked-by-access, stated plainly
~23 of ~34 collections remain unopened and the raw export is not on the audit
machine. The inventory is the **first task of the fresh-export work**, not
completable before it. No silent narrowing: "probably other-tenant" requires a
tenant-field check, not an assumption.

### 2.2 — Migration mapping plan — **maintained, not redrafted**
The 14-category draft (fits / doesn't-fit flags, crosswalk-table design, RLS
and reversibility conventions) is in the P2 report. Task: keep it current
against any new cut; extend, don't rewrite.

### 2.3 — Retention post-migration — **verify against the mapping**
The retention cross-check table (GPS-on-cancelled-bookings and
`payout_gst_amount` named as the easiest silent drops; insurance-period rows
must never be fabricated) is in the P2 report. Re-verify on every mapping
change.

## Phase 3 — Operational readiness

### 3.1 — Monitoring — **shipped; verify live**
The three signals exist behind `dual_run_monitoring_enabled` (default on).
Verify: flag state in production `app_settings`, counters visible in
`/metrics`, an `audit_logs` query for `legacy_driver_first_go_online` returns
cleanly. Alerting/dashboards on those series remain open (E4).

### 3.2 — Decommission runbook — **maintain the draft**
The 11-step sequence with go/no-go gates and required-inputs list is in the P3
report. Task: assign owners and calendar dates to each gate (all currently
ownerless), and re-confirm C5 (Railway standby drift) status as a cutover-week
go/no-go item.

### 3.3 — NEW: Support & operations readiness
No agent owns this today — direct task, ops lens: dual-run support playbook
(refund routing when the ride was booked in the old app; "charged twice"
triage; which team answers which app's tickets), documented and staffed before
launch-week volume. Absence is a finding.

### 3.4 — NEW: Migration-facing user experience
Direct task + `spinr-design-consistency-reviewer` /
`spinr-accessibility-reviewer` on any migration-facing screens: forced-upgrade
and sunset messaging flow, what an old-app user actually sees and taps,
app-store transition copy timing (timeline entries exist in the runbook; the
*experience* has never been reviewed). Note the forced-upgrade gate's known
blind spot: pre-header app builds can't be forced to upgrade at all.

### 3.5 — Documentation truth-up
Update `ACTION_ITEMS.md` A34 and relevant `docs/change-log/` entries with this
run's findings, the blocker register, and the decision log (owner + due date on
every open decision). A run that doesn't update the ledger forces the next
session to start stale.

---

## Output format (both parts)

Per phase/part, one consolidated two-layer report, committed before moving on:
- **Findings** — filter chain (money) or verification method (non-money),
  `file:line` evidence, each marked NEW / STILL-OPEN / FIXED / REGRESSED
  against the ledger.
- **Blocker register** — ranked, with owner and due date per item.
- **Decision log** — every needs-a-decision item: the decision, its owner, its
  due date.
- **Verdict** — clean / needs remediation / needs a decision.
- **What was NOT verified** — the honest boundary, always. A stated boundary
  beats a false all-clear.

Do not propose or draft write-path code in the session unless the user
explicitly asks after reviewing the P0 (Part B) or blocker (Part A) report.
