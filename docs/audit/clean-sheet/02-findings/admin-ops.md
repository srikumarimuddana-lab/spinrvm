# R7 — Admin & Operations Owner — Findings

Status: COMPLETE (60-120 min budget lane, W1)

## §0 Method & evidence scope
VERIFIED unless noted. Read: `CLAUDE.md`; audit prompt ground rules
(`docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md` §4-§7); `roles.md` §3
(adversary personas) and R7's own card; `greenfield-extensions.md` §3
(feature-completeness dims) and §7 (readiness definitions); `W0-SUMMARY.md`;
`sweep-catalog.md` §2.2/§2.8/§2.9/§3.6; the Admin Dashboard & Operations epic's
415 rows in `traceability.csv`; `backend/routes/admin/__init__.py` (full, 355
lines); `backend/routes/admin/staff.py` (RBAC core, ~470 lines); `backend/
routes/admin/{wallet,rides,support,disputes,settings,drivers,vehicle_fleet,
incentives}.py` (targeted reads); `backend/routes/disputes.py` (live refund
executor); `backend/utils/audit_logger.py`; `docs/runbooks/{admin-rollback,
saskatoon-launch,payment-dispute-evidence}.md`; grepped every `routes/admin/
*.py` for mutating-verb decorators and `log_admin_action` calls (70 files);
grepped 10 admin editor files for staleness/version-check patterns; cross-
referenced `ACTION_ITEMS.md` for B10/B11/AI-3/C23/W3/W4/A-P3-5/A-P3-6 threads
before filing anything new. Did **not** independently execute code, run
tests, or query a live DB — this is a static/code-reading pass. Given the
scope (~230 mutating admin handlers across 70 router files, per the grep
count below), §3's table is router-granularity with named exceptions pulled
out into full findings, not a literal 230-row enumeration — see §13.

## §1 Steelman
This module is materially more mature than the failure mode this role's
charter warns about, and most of that maturity is *documented in the code
itself* as the direct fix for a past incident — worth citing rather than
re-discovering:

- `routes/admin/__init__.py`'s own docstring records the 13-of-14-sub-
  routers-had-zero-auth incident and the fix: `admin_router` now carries
  `dependencies=[Depends(get_admin_user)]` at the **router** level, so a new
  sub-router is authenticated-admin-gated by default the moment it's
  `include_router()`'d — opt-out, not opt-in. Every one of the 70 sub-
  routers mounted today has at least this gate; none were found mounted
  with zero auth.
- The exact "unreachable module string" failure mode this role's brief
  centers on (`data_transfer_export_router` / `bulk_operations`) has
  **already been found and fixed** (ACTION_ITEMS.md B11/R-A, 2026-07-28):
  `bulk_operations` was never in `AVAILABLE_MODULES`, so the module gate was
  replaced with an explicit `require_super_admin` at all 5 Data Transfer
  routers, the same fix pattern applied to `compliance_router`
  ("compliance" absent from `AVAILABLE_MODULES`/`ROLE_PRESETS`, decision log
  2026-08-19 §2 option B) and to `sgi_forms_router`/`export_approvals_router`
  (ACTION_ITEMS.md B10). All ~20 super_admin-mounted routers carry an
  inline comment explaining *why* (money, PII, impersonation, chat-history,
  irreversible-external-write) rather than leaving the boundary implicit.
- Every one of those super_admin routers is documented as re-checking the
  role **inside** the handler too (`stripe_import.py`, `stripe_payout_sync.py`,
  `booking_import.py`, `sentry.py`, `dispute_evidence_submission.py`, ...) —
  defense-in-depth against a future re-mount mistake, exactly what
  `spinr-admin-rbac-reviewer`'s check #4 asks for. Spot-checked 6 of these;
  all matched.
- Staff-management RBAC (`staff.py`) has real teeth beyond a checkbox list:
  a "custom" role whose module set equals `AVAILABLE_MODULES` is detected as
  super-admin-equivalent and gated behind the actor's own password
  re-confirmation (W4, 2026-09-10), the same gate applies to an explicit
  promotion to `role="super_admin"` (A-P3-6), demoting the last active
  super_admin is blocked (count check), and any role/module change bumps
  `token_version` + revokes refresh tokens so a stale 1-hour admin JWT can't
  keep operating on the old grant (H6).
- Two inert module strings (`heatmap`, `surge`/`pricing`) were found
  granting a false sense of restriction/permission and were **removed**
  from `AVAILABLE_MODULES` with an explanatory NOTE rather than silently
  left to rot — the sidebar is now driven by the module that actually gates
  the backend route.
- A dual-approval-for-large-exports mechanism already exists end-to-end
  (`settings.dual_approval_exports_enabled`, `_APPROVAL_GATE_ROW_THRESHOLD`
  =1,000 rows, self-approval blocked server-side) — dark-launched, but built,
  tested, and wired into both Compliance and Data Transfer exports (B10).
- The newest money-adjacent admin feature shipped, Stripe dispute-evidence
  submission (`dispute_evidence_submission.py`, C23 item 5), is close to a
  textbook execution of CLAUDE.md's pre-merge gates in one file: dark-launch
  flag, explicit `confirm: true`, super_admin at mount **and** handler,
  atomic idempotent claim taken *before* the external call with rollback on
  failure, and an audit-log row for every outcome including "flag was off."
- Sampled UX surfaces (staff page) have real `AlertDialog` confirmations on
  delete/MFA-reset, not `window.confirm`; list endpoints sampled
  (`/admin/rides`, `/admin/staff`) carry bounded `limit`/`offset` with a
  `le=` cap, not "return everything." `settings.py`'s update path is a
  field-level diff (only submitted keys are written) and its audit row
  records changed *key names*, never values — correct PIPEDA hygiene for a
  table that holds Stripe/Twilio/Maps secrets.

None of this excuses the findings below — but the pattern is "a genuinely
mature module with specific, nameable gaps," not the systemic-zero-auth
starting point the role charter describes. Several gaps below are the
*next* layer of the same discipline (dual-approval exists for exports, not
yet for money; per-handler re-check is a named pattern, one handler breaks
it) rather than a new category of problem.

## §2 Findings

### ADMIN-OPS-001 — Money-moving admin actions (wallet credit/debit, dispute refunds) have no dual-approval or cumulative cap, reachable by non-super_admin role presets
- Hierarchy: L2 Admin Dashboard & Operations › L3 Refunds & wallet adjustments › L4 Admin credits/debits a wallet or resolves a dispute › L5 single-admin, uncapped-over-time money movement
- Severity: HIGH   Priority score: S×B×L = 3×3×2 = 18
- Status: VERIFIED   Existing item: new (extends the *pattern* B10/B11 already fixed for exports; grepped ACTION_ITEMS.md for "dual approval"/"second approver"/"four eyes" — both hits are export-scoped, neither covers wallet or dispute-refund money movement)
- Adversary: malicious insider; over-privileged support agent (per this role's own charter's key question 2)
- Evidence:
  - `backend/routes/admin/wallet.py:53-76,128-228` — `POST /admin/wallet/credit`: `AdminCreditRequest.amount` capped `le=Decimal("10000")` **per transaction**, no daily/cumulative cap, no second-approver check anywhere in the handler. Mounted `require_module("earnings")` (`routes/admin/__init__.py`), and `"earnings"` is in the `"finance"` `ROLE_PRESETS` bundle (`staff.py:114`) — reachable by any admin holding the `finance` preset, not just `super_admin`.
  - `backend/routes/admin/wallet.py:231-337` — `POST /admin/wallet/debit`, same gate, same absence of a second approver.
  - `backend/routes/disputes.py:206-317` `admin_resolve_dispute` — Stripe refund bounded only by `req.refund_amount > original_fare` (line 223-227); no absolute dollar ceiling, no second-approver. Mounted `require_module("disputes")` in `routes/admin/__init__.py`, and `"disputes"` is in the `"support"` `ROLE_PRESETS` bundle (`staff.py:113`) — reachable by a day-to-day support agent, not just `super_admin`/`finance`.
  - No check anywhere in `admin_credit_wallet`/`admin_debit_wallet` that `req.user_id` isn't a rider/driver account the acting admin controls themselves (self-dealing guard absent).
  - Contrast: the export path for full-fidelity PII (`data_transfer_export`, `compliance` GST/PST reports) already has a built, if dark-launched, dual-approval gate above a 1,000-row threshold (B10) — money movement has no equivalent, despite being the more directly monetizable insider-abuse surface.
- What happens (plain language): a support-team admin (not super_admin, not even finance-team) can single-handedly approve a Stripe refund up to a ride's full fare, or credit up to $10,000 to any wallet, as many times as they want, with nobody else's sign-off required before the money moves. The only artifact is an after-the-fact audit-log row.
- Root cause: the dual-approval mechanism built for AI-3/B10/B11 was scoped narrowly to "export request," not generalized to "any admin action that moves real money or PII," so wallet credit/debit and dispute-refund approval were never wired through it.
- Recommendation: extend the existing `admin_export_approvals` service/schema (already built for B10) to a generic "admin action requiring second approval" queue, and route `admin_credit_wallet`/`admin_debit_wallet`/`admin_resolve_dispute` through it above a threshold (e.g. any credit/debit, and any refund > some fixed CAD amount or > N per day per admin). Ship dark behind a new flag exactly as B10 did.   Alternative considered: a hard per-admin daily cap with no approval queue — rejected because it caps blast radius but does nothing for the "who signed off" audit trail a regulator/plaintiff's-lawyer persona would ask for, and the approval-queue mechanism already exists and is proven.
- Blast radius: `services/admin_export_approvals.py`, `routes/admin/export_approvals.py`, `admin-dashboard/src/app/dashboard/export-approvals/page.tsx` are the only current consumers of the approval-queue pattern — extending it to wallet.py/disputes.py touches those two backend files plus whatever admin-dashboard screens call them (`admin-dashboard/src/app/dashboard/wallet*`, `disputes/page.tsx` — not independently confirmed which exact frontend files call these two backend routes in this pass, see §13).
- Rollout: additive, flagged (`settings.money_action_dual_approval_enabled` or similar), dark by default — matches B10's own precedent exactly.
- Rollback: flip the new flag off; no data migration, no live-data mutation to undo (the queue table is new/additive).
- Verification to close: a test asserting a non-super_admin `finance`/`support`-preset admin's credit/debit/refund call is queued (not applied) once the flag is on and above threshold, plus the existing B10 self-approval-blocked test extended to the new action types.

### ADMIN-OPS-002 — `PUT /admin/staff/{staff_id}` enforces its super_admin requirement in-body instead of via the `Depends()` pattern its 4 sibling handlers use
- Hierarchy: L2 Admin Dashboard & Operations › L3 Staff management › L4 Edit staff member role/modules › L5 defense-in-depth consistency
- Severity: MEDIUM   Priority score: S×B×L = 2×3×2 = 12
- Status: VERIFIED   Existing item: new
- Adversary: a future engineer refactoring staff.py's checks into a shared dependency by pattern-matching the 4 handlers that already use `Depends(require_role("super_admin"))`
- Evidence: `backend/routes/admin/staff.py:304-308` — `update_staff` declares `admin: dict = Depends(get_admin_user)` and does the super_admin check as an in-body `if admin.get("role") != "super_admin": raise HTTPException(403, ...)` — compare its 4 siblings, `list_staff:171`, `create_staff:206`, `get_staff:289`, `reset_staff_mfa:399`, `delete_staff:458`, every one of which declares `admin: dict = Depends(require_role("super_admin"))` structurally, visible in the function signature and FastAPI's dependency graph. `update_staff` is currently **correctly enforced** (the in-body check runs first, before any mutation), so this is not a live vulnerability today.
- Root cause: the in-body check predates (or was never migrated to) the `require_role()` helper the other 4 handlers use; nothing forces the two styles to be reconciled.
- Recommendation: change the dependency to `Depends(require_role("super_admin"))` and drop the redundant in-body check, matching all 5 siblings.
- Alternative considered: leave as-is since it's currently correct — rejected because this is precisely the "per-handler re-check survives a re-mount, but only if every handler follows the same shape" gap CLAUDE.md's own `routes/admin/__init__.py` docstring philosophy warns about for the super_admin-mount class; an in-body check that looks different from its siblings is exactly what a future "de-duplicate the RBAC checks" pass would miss.
- Blast radius: isolated to this one handler; no other caller of `update_staff`.
- Rollout: trivial same-PR fix, no flag needed (behavior-preserving).
- Rollback: revert the one-line dependency change.
- Verification to close: existing staff RBAC tests (`test_admin_rbac.py`, `test_admin_module_list_parity.py` per grep) should already cover a non-super_admin 403 on this route — confirm, add a signature-shape assertion if not present.

### ADMIN-OPS-003 — Two parallel audit-log write paths; the direct-`insert_one` path (≈8 routers) never sets `request_id`, breaking the Sentry/log correlation the `log_admin_action()` helper provides
- Hierarchy: L2 Admin Dashboard & Operations › L3 Audit logging › L4 Every mutating admin action writes an `audit_logs` row › L5 incident/regulator correlation
- Severity: MEDIUM   Priority score: S×B×L = 2×3×1 = 6
- Status: VERIFIED   Existing item: new
- Adversary: regulator/auditor asking "who did this and why"; on-call operator at 3 a.m. trying to join an `audit_logs` row to the Sentry event/log lines for the same request
- Evidence: `backend/utils/audit_logger.py:56-111` `log_admin_action()` explicitly sets `request_id = get_request_id() or None` on every write "so a SOC investigation join an `audit_logs` row to its Sentry event / log lines" (its own docstring, lines 73-80). Grepped every `routes/admin/*.py` for mutating-verb decorators vs. audit calls: `wallet.py`, `users.py`, `staff.py`, `ai_console.py` (and `settings.py`, though its shape is otherwise clean — see §1) write `audit_logs` via a **direct** `db_supabase.insert_one("audit_logs", {...})` (e.g. `wallet.py:180-199`) whose dict has no `request_id` key at all — every wallet credit/debit, every staff create/update/delete, every users.py mutation, and every AI-console (impersonation/chat-history) action logs with `request_id` implicitly NULL.
- Root cause: `log_admin_action()` was added later than these routers' original direct-insert code and was never backfilled into them — a second, parallel audit-write mechanism exists with no enforcement that one is used over the other.
- Recommendation: either (a) route all direct-insert call sites through `log_admin_action()`, or (b) if the extra fields those sites capture (e.g. wallet's `old_balance`/`new_balance`) don't fit `log_admin_action()`'s `details` shape, add `request_id` to each direct-insert dict via a shared helper. Add a static check (mirroring `test_loguru_call_conventions.py`'s pattern) that fails if a new `routes/admin/*.py` file inserts into `audit_logs` without `request_id`.
- Alternative considered: leave both mechanisms and just document the difference — rejected, this is exactly the "duplication-by-default" pattern W0 (00-history.md finding 2) already flagged repo-wide; codifying it as "intentional" here would add a ninth instance instead of closing one.
- Blast radius: `wallet.py`, `users.py`, `staff.py`, `ai_console.py`, `settings.py` (5 files with direct inserts, of the ~8 the grep flagged — 3 more not individually re-verified in this pass, see §13). None of these are wrong today (rows are written, actor/action/entity are captured), the gap is the missing correlation key only.
- Rollout: additive — adding a column value to an insert dict, no schema change (column already exists, just unset).
- Rollback: none needed; purely additive.
- Verification to close: a test asserting every `audit_logs` row written during a request carries a non-null `request_id`.

### ADMIN-OPS-004 — No admin editor in `routes/admin/` has an optimistic-concurrency/staleness check — extends CONCURRENCY-001's blast radius from drivers.py to (at least) service-area/fare config, feature-flag settings, promotions, vehicle fleet, users, staff, venues, incentives, FAQs, and legal documents
- Hierarchy: L2 Admin Dashboard & Operations › L3 every config editor › L4 concurrent edit › L5 last-write-wins
- Severity: MEDIUM   Priority score: S×B×L = 2×3×2 = 12
- Status: VERIFIED   Existing item: extends `rapid-baseline-2026-09-24/A7-surfaces.md`'s CONCURRENCY-001 (`admin/drivers.py:1771`, MEDIUM, S×B×L=8), whose own "Blast radius" line said "other admin PUT/PATCH endpoints... not checked in this lane" — this lane checked them.
- Adversary: two internal admins (or an admin + a background reconciliation job) editing the same record concurrently
- Evidence: grepped `expected_updated_at|If-Match|version.*mismatch|409` across `service_areas.py`, `promotions.py`, `settings.py`, `vehicle_fleet.py`, `users.py`, `staff.py`, `venues.py`, `incentives.py`, `faqs.py`, `legal_documents.py` — zero hits for a staleness/version guard in all 10 (`vehicle_fleet.py`'s 3 hits are a delete-blocked-by-in-use-reference 409, unrelated to edit conflicts). `service_areas.py` is the one that matters most here: it's the admin surface for fare/surge boundary config CLAUDE.md marks as regulatory/reputational-risk-sensitive (surge override justification), and it has no conflict signal either.
- What happens (plain language): two admins edit the same service area's surge override, or the same platform-wide feature flag, at nearly the same time; whichever save lands second silently wins with no warning to either admin that their edit was based on stale data.
- Root cause: same as CONCURRENCY-001 — no admin editor in this codebase compares `updated_at`/a version column before writing.
- Recommendation: same as CONCURRENCY-001's — add an optional `expected_updated_at` (or a version column) to each editor's update payload, 409 on mismatch, additive/backward-compatible rollout. `settings.py`'s per-field diff write already narrows blast radius to only the fields actually submitted (not a full-row overwrite) — worth preserving that shape while adding the staleness check, since a full-row-replace fallback would make this worse, not better.
- Blast radius: 10 files checked in this pass; other admin editors not in this list (e.g. `promotions.py`'s own downstream consumers, `corporate_accounts.py` — out of this lane's file scope per the role card) not re-verified.
- Rollout: additive per editor, no flag needed (400/409 only fires when the new optional field is supplied and stale).
- Rollback: none needed; purely additive per editor.
- Verification to close: one regression test per editor simulating two sequential writes with a stale `expected_updated_at` on the second, asserting 409 — CONCURRENCY-001's own suggested test, replicated per file.

## §3 Mutating-endpoint RBAC + audit table

Router-granularity summary (grepped `@router.(post|put|patch|delete)` and
`log_admin_action` per file across all 70 files in `backend/routes/admin/`
that mount into `admin_router`). "Audit" column counts *any* audit
mechanism (helper **or** direct `insert_one`) — see ADMIN-OPS-003 for which
files use which. Full per-endpoint enumeration (~230 handlers) is out of
this lane's time budget; every row below was spot-checked for at least one
handler, and every file with `mut > audit` was individually re-checked (see
notes).

| Router file | mutating handlers | audit writes (either mechanism) | mount gate | module reachable via a grant path? | notes |
|---|---|---|---|---|---|
| `drivers.py` | 20 | 25 | `require_module("drivers")` | yes (`operations` preset) | some handlers write 2 audit rows (state + notify); no `mut > audit` gap |
| `support.py` | 15 | 17 | `require_module("support")` | yes (`support` preset) | dispute create/update/resolve write audit; `refund_amount` here is record-only, not the live Stripe executor (see F7 in §2's evidence discussion / ADMIN-OPS-001) |
| `rides.py` | 13 | 15 | `require_module("rides")` | yes (`operations` preset) | includes `waive`/`cancel`/`complete`/`send-invoice` money-adjacent actions, no dual-approval (bounded to a single ride's fare, lower blast radius than wallet/dispute paths, not separately carded) |
| `vehicle_fleet.py` | 12 | 14 | `require_module("vehicle_types")` | yes (`operations` preset) | delete-blocked-by-reference 409 present (good) |
| `support_tickets.py` | 10 | 11 | per-handler `require_module("support_tickets")` (no mount-level `dependencies=`, by design — see `__init__.py` comment) | yes | intentional exception to the mount-gate norm, documented |
| `service_areas.py` | 8 | 13 | `require_module("service_areas")` | yes (`operations` preset) | surge/fare-boundary config; no staleness check (ADMIN-OPS-004) |
| `subscriptions.py` | 5 | 8 | `require_module("earnings")` | yes (`finance` preset) | |
| `documents.py` | 5 | 7 | `require_module("documents")` | yes (custom-role grantable, not in any named preset) | |
| `users.py` | 4 | 0 via helper / 4 via direct insert | `require_module("users")` | yes (`operations`, `support` presets) | direct-insert audit path, no `request_id` (ADMIN-OPS-003) |
| `staff.py` | 4 | 0 via helper / 4 via direct insert | `require_module("staff")` + 4-of-5 handlers also `require_role("super_admin")` | "staff" nominally custom-grantable but AVAILABLE_MODULES comment says super_admin-only; 4/5 handlers enforce it via `Depends`, 1 in-body (ADMIN-OPS-002) | direct-insert audit, no `request_id` |
| `wallet.py` | 2 | 0 via helper / 2 via direct insert | `require_module("earnings")` | yes (`finance` preset) | ADMIN-OPS-001, ADMIN-OPS-003 |
| `ai_console.py` | 1 | 0 via helper / 1 via direct insert | `require_super_admin` (mount) + in-handler re-check (per its own comment) | no (super_admin only, by design) | correctly the strictest posture in the package (impersonation + chat-history reads); audit path just lacks `request_id` like the others |
| all `data_transfer_*`, `legacy_*`, `stripe_{import,payout_sync,connect_ledger,mode_audit,events}`, `booking_import`, `wallet_import`, `tax_id_import`, `sgi_forms`, `export_approvals`, `dispute_evidence_submission`, `pre_launch_flag`, `migration_*`, `compliance`, `sentry` (≈24 files) | 1-4 each | audit ≥ mut in every file checked | `require_super_admin` (mount, +handler re-check documented) | N/A — deliberately not grantable | already the correct, mature posture per §1; not re-audited line-by-line beyond confirming the mount |
| remaining ~30 files (`faqs`, `venues`, `incentives`, `messaging`, `maintenance`, `promotions`, `safety`, `legal_documents`, `driver_appeals`, `driver_statements`, `driver_dormancy`, `rider_import`, `monitoring`, `auto_payouts`, etc.) | 1-4 each | audit ≥ mut in every file checked | module-gated, each reachable via at least one preset or custom grant | yes | no `mut > audit` gaps found; not individually carded |

**Zero routers found mounted with no gate and no explanatory comment** (the
CRITICAL-class check this role exists to run) — the one router with no
`dependencies=` at mount (`support_tickets_router`) has a documented reason
and its own per-handler enforcement, matching the "rare, explained"
exception this role's brief allows for.

## §4 Money-moving admin actions and approval controls

| Action | Endpoint | Cap | Second approver? | Reachable by (non-super_admin) | Audit |
|---|---|---|---|---|---|
| Wallet credit | `POST /admin/wallet/credit` | $10,000/txn, no cumulative cap | No | `finance` preset (`earnings` module) | Yes (direct insert, no `request_id` — ADMIN-OPS-003) |
| Wallet debit | `POST /admin/wallet/debit` | $10,000/txn, no cumulative cap | No | `finance` preset | Yes (same gap) |
| Dispute refund (live Stripe call) | `PUT /disputes/{id}/resolve` (`routes/disputes.py`) | bounded by ride's `original_fare` only | No | `support` preset (`disputes` module) | Yes, via `log_admin_action` (has `request_id`) |
| Ride fee waive | `POST /admin/rides/{id}/held-for-review/waive`, force-complete waive | bounded by single ride's fare | No | `operations` preset (`rides` module) | Yes |
| PII/compliance export > 1,000 rows | `data_transfer_export`, `compliance` GST/PST/insurance reports | n/a (not money) | **Yes, but dark-launched, flag off by default** (B10) | super_admin only | Yes |
| Incentive bonus config | `POST/PUT /admin/incentives` | `bonus_amount` ≤ $500/incentive, `max_budget` optional | No | `operations` preset (`service_areas` module — incentives are configured per service area, consistent with how surge/pricing was folded into that module, see §1) | Yes |

**Answer to R7 key question 2 ("can one person refund or credit money
without a second approver above a threshold?"): yes, on every live
money-moving path found** (wallet credit/debit, dispute refund) — see
ADMIN-OPS-001. The one place a second-approver mechanism exists in this
codebase today is exports (non-monetary PII/compliance data), and even
that is not yet turned on.

## §5 Runbook executability table

| Runbook | Spot-check | Verdict | Evidence |
|---|---|---|---|
| `docs/runbooks/admin-rollback.md` | Vercel rollback steps, `docs/runbooks/pitr-restore.md` cross-reference, branch-protection table | Current (commands); branch-protection table **unverifiable from repo** | `pitr-restore.md` exists (confirmed); §5's GitHub branch-protection settings are a human-only question per W0 §21 — this runbook documents *required* config, not observed config, and the merge gate is documented elsewhere (00-history.md finding 1) as advisory in practice |
| `docs/runbooks/payment-dispute-evidence.md` | §0 "nothing is submitted to Stripe automatically... no one-click evidence pack" (lines 21, 200) | **STALE** | Both capabilities already shipped: `backend/routes/admin/dispute_pack_download.py:61` (`GET /rides/{ride_id}/dispute-pack` — the exact path the runbook's line 200 says still needs building) and `backend/routes/admin/dispute_evidence_submission.py` (dark-launched direct-to-Stripe submission, C23 item 5). An operator following this runbook today would manually re-derive already-built tooling and might not know to check the `dispute_stripe_evidence_submission_enabled` flag before hand-uploading via the Stripe Dashboard. |
| `docs/runbooks/saskatoon-launch.md` | §0 checklist framing (C-2 "Railway auto-deploy from main", C-4 "geofence... not in the codebase as of today") | **STALE** | CLAUDE.md's Deployment section (verified healthy 2026-09-21) documents Fly.io as primary / Railway as standby, not Railway-only; the product is already in live app testing per CLAUDE.md's own framing, and service-area gating clearly exists across many admin routers today (`service_areas.py`, `venues.py`, `h3_heatmap.py`) — the runbook is a pre-launch snapshot never updated post-launch. Not blocking (single-city pre-launch checklist, product already launched), but should be archived or clearly dated so an operator doesn't treat its topology section as current. |
| `docs/runbooks/data-retention.md`, `docs/runbooks/corporate-compensating-transaction.md` | not independently re-verified in this pass | not checked | out of budget — see §13 |

## §6 Feature-flag inventory (representative sample — see note)

`get_app_settings()`/`settings.get(...)` is read in ~150 distinct call
sites repo-wide; `SettingsUpdateRequest` in `routes/admin/settings.py`
alone exposes 45 boolean flags. Full enumeration is out of this lane's
budget (see §13); the admin-ops-relevant subset sampled:

| Flag | Default | Admin-UI editable? | Audit-logged on change? |
|---|---|---|---|
| `dual_approval_exports_enabled` | `false` (dark) | Yes, via `PUT /admin/settings` | Yes (`changed_keys` only, never values) |
| `dispute_stripe_evidence_submission_enabled` | `false`/unset (dark) | Yes, via `PUT /admin/settings` | Yes |
| `rideless_sos_enabled` | per migration 353 | Yes | Yes |
| `enforce_driver_eligibility_recheck` | `false` | Yes | Yes |
| `referral_payout_velocity_cap_per_day` | set value | Yes | Yes |
| `directions_proxy_enabled` | migration 415 | Yes | Yes |

All sampled flags share the same mechanism: `settings.py:765-838`
`admin_update_settings` — single `app_settings` row, field-diffed write
(only submitted keys change), audit row records **which keys** changed
(never the values, correct for the credential-bearing fields on this same
row), gated `require_module("settings")` at mount with a `_SUPER_ADMIN_ONLY_FIELDS`
carve-out for credential-equivalent fields (checked only when the value
actually differs from current, so unrelated non-super-admin saves keep
working). No staleness/version check (ADMIN-OPS-004 covers this row too).

## §7 Data tools

Legacy import / data-transfer / Stripe-migration tooling (24 sub-routers,
`routes/admin/{data_transfer_*, legacy_*, stripe_{import,payout_sync,
connect_ledger,mode_audit,events}, booking_import, wallet_import,
tax_id_import, sgi_forms, export_approvals, dispute_evidence_submission,
pre_launch_flag, migration_*, compliance, sentry}.py`) — this is the
best-governed corner of the module:

- **super_admin at both mount and (documented) handler** for every one of
  the 24, with an inline comment stating *why* each one earned that posture
  (money write, PII, external irreversible call) — not a blanket default.
- **Rate-limited**: confirmed on `data_transfer_export.py`
  (`data_transfer_export_limit`, SlowAPI) — not independently re-confirmed
  on all 24 in this pass.
- **PII scoped**: `include_ride_gps`/`include_document_bytes` opt-out flags
  (R-B), required `reason` field 10-200 chars (R-C), both already shipped
  per B11.
- **Open item, not new**: `dual_approval_exports_enabled` (B10) exists and
  is wired into these export paths but is still off by default — "the flag
  has not been flipped on anywhere... a separate, deliberate rollout
  decision" per B10's own acceptance note. Today, a single super_admin can
  still run a >1,000-row export with no second sign-off. Cited here as
  still-open, not re-filed.
- **Legal/consent follow-up still open** per B11: R-G's disclosure language
  is drafted in the repo source (`docs/legal/privacy-policy.md` Section 6,
  2026-09-11) but **not yet in the already-live production policy text** —
  cited here as still-open, not re-filed.

## §8 Concurrent-edit sweep

See ADMIN-OPS-004 (§2) for the full finding. Summary table:

| Editor | Version/staleness check? | Write shape |
|---|---|---|
| `drivers.py` (`admin_update_driver`) | No (CONCURRENCY-001, prior finding) | frontend diffs against opened-dialog snapshot, backend accepts raw field dict |
| `service_areas.py` | No | not independently characterized (full-row vs. diff) |
| `promotions.py` | No | not independently characterized |
| `settings.py` | No | field-level diff (only submitted keys written) — narrower blast radius than a full overwrite, still no conflict signal |
| `vehicle_fleet.py` | No | has delete-blocked-by-reference 409 (unrelated to edit conflicts) |
| `users.py`, `staff.py`, `venues.py`, `incentives.py`, `faqs.py`, `legal_documents.py` | No (all 6) | not independently characterized |

## §9 Admin UX at scale

Sampled, not exhaustive (see §13):

- **Pagination**: present and bounded on every list endpoint sampled —
  `GET /admin/rides` (`limit` default 25, `le=100`), `GET /admin/staff`
  (default 500, `X-Total-Count`/`X-Limit` headers, `le=1000`), `GET /admin/
  wallet/{user_id}` transactions (`le=200`). No unbounded "return
  everything" list endpoint found in the files read.
- **Destructive-action confirmations**: `admin-dashboard/src/app/dashboard/
  staff/page.tsx` uses real `AlertDialog` components (not
  `window.confirm`) for both delete-staff and MFA-reset, with a named
  target shown in the dialog body. Not independently checked on every
  other destructive admin-dashboard action (drivers, disputes, service
  areas, promotions) in this pass — see §13.
- **Loading/empty/error states**: not independently verified per-page in
  this pass (would require reading each `admin-dashboard/src/app/
  dashboard/*/page.tsx`, out of budget) — flagged to §13 rather than
  assumed. `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings`,
  `dashboard-rides` (4 of the 6 pages carrying a real Playwright visual-
  regression baseline per CLAUDE.md) were **not** re-read for
  loading/empty/error state logic in this pass; any visual change to those
  4 pages (plus `login`, `dashboard-home`) needs a human-recaptured
  baseline per CLAUDE.md's `update-visual-baselines.yml` note, not spurious-
  diff reasoning — no diffs were produced in this pass, this is a forward
  note for whoever next touches those pages.

## §10 Feature completeness

Per `greenfield-extensions.md` §3's dimension set. `Y`/`N`/`P` (partial)/`?`
(not independently verified this pass — see §13), one row per L3 feature in
this role's charter:

| L3 feature | UX | API | backend | DB | events/WS | authz | audit log | privacy | compliance | tests | runbook | rollback/flag | owner |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Driver verification/approval | Y | Y | Y | Y | N/A | Y | Y | ? | ? | ? | ? | N/A | this role |
| Ride intervention (cancel/complete/reassign/waive) | Y | Y | Y | Y | Y (WS events per CLAUDE.md state-machine rule) | Y | Y | N/A | ? | ? | ? | N/A | this role |
| Refunds & wallet credit/debit | Y | Y | Y | Y | ? | Y (but see ADMIN-OPS-001) | Y (partial, ADMIN-OPS-003) | ? | ? | ? | `payment-dispute-evidence.md` (STALE, §5) | N (see ADMIN-OPS-001) | this role |
| Support tickets (Zoho) | Y | Y | Y | Y | ? | Y | Y | ? | ? | ? | ? | N/A | this role |
| SOS handling (admin console) | ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | `sos-incident.md` (BROKEN per R11/TSF-001, cited not re-derived) | ? | R11, not re-audited here |
| Service-area / fare config | Y | Y | Y | Y | N/A | Y | Y | N/A | ? | ? | ? | N (ADMIN-OPS-004) | this role |
| Feature flags (`app_settings`) | Y | Y | Y | Y | N/A | Y | Y (key names only) | Y (secrets masked) | N/A | ? | ? | Y (flags ARE the rollback mechanism) | this role |
| Data migration / legacy import | Y | Y | Y | Y | N/A | Y | Y | Y (B11 scope flags) | P (B11 R-G legal disclosure still draft-only) | ? | multiple migration runbooks, not re-checked this pass | N/A (one-shot ops tooling) | this role |
| Data transfer / export | Y | Y | Y | Y | N/A | Y | Y | Y | P (dual-approval built, off by default — B10) | ? | ? | Y (flag) | this role |
| Staff management | Y | Y | Y | Y | N/A | Y (1 inconsistency, ADMIN-OPS-002) | Y (partial, ADMIN-OPS-003) | N/A | N/A | ? | ? | N/A | this role |

## §11 Rebuild Delta card

## Epic: Admin Dashboard & Operations (admin/ops control plane)
- Verdict per inherited pattern: **MODIFY** the RBAC/audit foundation (it's
  good — keep the shape), **BUILD** approvals-as-a-first-class-service (it
  exists for one action type only today).
- Keep (already best-in-class): router-level default-secure mount
  (`admin_router`'s `dependencies=[Depends(get_admin_user)]`); the explicit
  super_admin-with-documented-why pattern for money/PII/irreversible-
  external-call routes (24 routers, consistent, mount+handler
  double-checked); the "custom role reaching super_admin-equivalent module
  coverage gets gated the same as an explicit promotion" detection in
  `staff.py`; token_version bump + refresh revocation on any role/module
  change so a stale JWT can't outlive the change it should reflect.
- Uber/Lyft do: both maintain a centralized "trust & safety / ops actions"
  service with tiered approval workflows for refund/credit/account actions
  above configurable thresholds, and a unified audit event bus rather than
  a table two different code paths write into (industry-standard pattern,
  not independently sourced beyond general practice — flag as INFERRED, not
  a cited primary source).
  Spinr today: the approval-queue mechanism (B10) is real and well-built,
  but scoped to "export request" only; money-moving actions (the most
  obviously monetizable insider-abuse surface) have no equivalent
  (ADMIN-OPS-001); audit writes fork across two code paths
  (ADMIN-OPS-003).
- Clean-sheet Spinr would: treat "admin action requiring a second
  human's sign-off" as a property of the *action type* (configurable
  threshold, not hardcoded to exports), backed by one generic approvals
  table/service every sensitive mutation (export, wallet credit/debit,
  dispute refund, staff super_admin promotion) routes through, and one
  audit-write path (not two) that every one of those actions — approved or
  not — logs to with a guaranteed `request_id`. Why (the edge it creates):
  turns "who approved this and why" from a per-feature afterthought into a
  structural guarantee auditable in one query, which is exactly what a
  regulator/plaintiff's-lawyer persona (and a real fraud investigation)
  needs, and removes the "did this route remember to call the right audit
  function" class of bug entirely.
- How (architecture/pattern): generalize `services/admin_export_approvals.py`
  into `services/admin_approvals.py` keyed by `action_type` with a
  per-type threshold config (row count for exports, dollar amount for
  money actions); a single `record_admin_action()` wrapping
  `log_admin_action()`'s guarantees (actor, `request_id`, before/after)
  that every mutating admin handler calls, replacing the direct
  `insert_one("audit_logs", ...)` call sites. Who (role/owner): this role
  (R7) / whoever owns the admin backend surface post-rebuild. When: **Now**
  for the audit-write consolidation (low risk, additive); **Next** for
  extending the approval queue to money actions (needs a real threshold
  decision from product/finance, per CLAUDE.md's "escalate, don't silently
  ship" gate).
- Incremental path from today (no big-bang): 1) add `request_id` to the
  ~8 direct-insert call sites (one PR, no behavior change) → 2) add a
  static test forbidding a new `audit_logs` insert without `request_id`
  (closes the class, not just today's instances) → 3) extend
  `admin_export_approvals` schema with an `action_type` column and wire
  wallet credit/debit behind a dark flag at a first threshold (e.g. any
  amount) → 4) wire dispute-refund resolution the same way → 5) flip
  `dual_approval_exports_enabled` on for real, now that the same mechanism
  also protects money, closing B10's own "not yet done" gap in the same
  rollout.
- Cost/effort: S (step 1-2), M (step 3-5) · Risk: Low (additive/flagged
  throughout) · Reversibility: High (every step is a flag flip or additive
  column) · Build (not buy/partner) — this is bespoke internal-ops tooling
  with no vendor fit.
- Advantage type: operational + trust — not a rider/driver-facing feature,
  but a defensibility argument for enterprise/corporate customers and
  regulators ("every dollar an admin moves has a second human's sign-off
  above X") that's genuinely hard for a competitor to fake after the fact
  if Spinr can produce the query.
- "Why not?": the honest answer from the evidence is sequencing, not
  neglect — B10/B11 shipped the approvals mechanism for the highest-*volume*
  risk surface flagged at the time (bulk PII export, per an existing threat
  model, AI-3) and the money-action gap was never separately threat-modeled
  with the same rigor. A simpler fix than the full generalization above
  (e.g. a hardcoded daily per-admin dollar cap with a Slack alert instead of
  a queue) would reduce blast radius faster but doesn't produce the
  auditable "who signed off" record a queue does — worth doing as an
  interim Now step even if the full queue is Next.

## §12 Top 5

1. **ADMIN-OPS-001 (HIGH)** — wallet credit/debit and dispute-refund can be
   executed by a single `finance`/`support`-preset admin (not even
   super_admin) with no second approver and no cumulative cap, while the
   export path already has a (dark-launched) dual-approval mechanism for
   less directly monetizable PII data. Top priority: extend B10's approval
   queue to money actions.
2. **ADMIN-OPS-004 (MEDIUM)** — no admin editor has an optimistic-
   concurrency check; extends CONCURRENCY-001 across service-area/fare
   config, feature flags, and 8 other editors. Silent last-write-wins on
   surge/fare config is the highest-stakes instance.
3. **ADMIN-OPS-003 (MEDIUM)** — two audit-write mechanisms, one missing
   `request_id`, weakens incident/regulator correlation on wallet, staff,
   users, and AI-console actions specifically (the most sensitive action
   classes in the package).
4. **§5 runbook staleness** — `payment-dispute-evidence.md` tells an
   on-call operator to build something (`dispute-pack` endpoint) that's
   already shipped, and doesn't mention the newer, higher-risk
   direct-to-Stripe submission endpoint at all. An operator following it
   literally wastes time at best, double-submits evidence at worst if the
   flag is ever flipped without the runbook being updated.
5. **ADMIN-OPS-002 (MEDIUM)** — `PUT /admin/staff/{staff_id}`'s
   in-body super_admin check is a style outlier among 5 siblings; correct
   today, one pattern-matched refactor away from a real privilege-
   escalation gap on the single most sensitive admin route in the package
   (staff role/module editing).

## §13 NOT verified
- Literal per-endpoint enumeration of all ~230 mutating admin handlers
  (§3 is router-granularity with named exceptions, not a full table) —
  time budget.
- `corporate_accounts.py`/`corporate_wallet.py` admin-adjacent concurrent-
  edit and money-moving posture — explicitly out of this role's file scope
  per the charter (separate top-level router, own `require_module
  ("corporate_accounts")` gate), not re-checked; CONCURRENCY-001's own note
  already flagged corporate as unchecked too.
- All 45 `settings.py` boolean flags and the full ~150-site `app_settings`
  read surface — §6 is a representative sample, not a full inventory.
- Loading/empty/error states on any admin-dashboard page beyond the one
  spot-checked (`staff/page.tsx`) — §9's claim is limited to pagination and
  one confirm-dialog pattern.
- Which exact admin-dashboard frontend files call `wallet.py`'s
  credit/debit endpoints or `disputes.py`'s resolve endpoint — named as an
  open blast-radius question in ADMIN-OPS-001 rather than assumed.
- `docs/runbooks/data-retention.md`, `corporate-compensating-transaction.md`,
  and the remaining ~10 runbooks not listed in §5 — not spot-checked this
  pass.
- Whether `data_transfer_export.py`'s rate limiter is representative of
  all 24 super_admin-gated data-tool routers, or just that one file — only
  one file's rate-limit decorator was grepped.
- Whether SOS handling in the admin console (charter item) has its own
  distinct admin-side findings beyond the already-cited TSF-001
  (`sos-incident.md` runbook broken) — deferred entirely to R11's lane per
  this role's explicit instruction not to re-derive finished lanes.
- Full `test_admin_rbac.py`/`test_admin_module_list_parity.py` contents —
  referenced via grep hits, not read in full; their actual coverage of
  ADMIN-OPS-002's route was inferred, not confirmed by reading the test
  file.

## §14 Human-only questions
- Does Product/Finance want a dollar-threshold-based dual-approval gate on
  wallet credit/debit and dispute refunds (ADMIN-OPS-001), and if so, what
  threshold? This is a business-risk-tolerance decision, not an
  engineering one — CLAUDE.md's own "escalate, don't silently ship" gate
  applies directly here.
- Is the `finance` role preset (which currently grants unsupervised
  $10,000/txn wallet credit/debit authority) actually assigned to anyone
  today, and if so, to how many staff and under what individual
  accountability expectation? Answers whether ADMIN-OPS-001 is a live
  exposure or a currently-dormant one.
- Who owns `docs/runbooks/payment-dispute-evidence.md` and
  `saskatoon-launch.md` for the staleness fixes in §5 — same "no migration
  owner" gap W0 already flagged generally.
- Should `saskatoon-launch.md` be archived/dated now that the product is
  past that launch, to stop it from being read as current topology
  guidance?

## §15 Escalations
- **ADMIN-OPS-001** should be escalated to Product/Finance/Security before
  the next release touching `wallet.py` or `disputes.py` — per CLAUDE.md's
  pre-merge gate 9 ("escalate... when the change touches
  rides/payments/auth/corporate/safety and you're not confident of the
  full impact"), a HIGH-severity finding that a common role preset can move
  real money with zero second sign-off is exactly the class of thing that
  gate exists for, and this lane cannot itself decide the right dollar
  threshold — that's a business call.
- No CRITICAL findings in this lane (no unmounted-gate router, no live
  RBAC bypass, no frontend-only enforcement of a backend-absent gate were
  found) — nothing rises to "block the next deploy" on its own, but
  ADMIN-OPS-001 should not wait for a routine backlog slot given the
  charter's own key question 2 named this exact risk.
