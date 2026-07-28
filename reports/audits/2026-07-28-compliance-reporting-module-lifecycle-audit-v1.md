# Compliance & Tax Reporting Module — Structured Lifecycle Audit v1

**Date:** 2026-07-28
**Scope:** `backend/routes/admin/compliance.py`, `backend/utils/report_branding.py`,
`backend/migrations/263_compliance_export_events.sql`,
`admin-dashboard/src/app/dashboard/compliance/`, associated tests.
**Trigger:** module was built across PRs #2650 (kickoff), #2675 (hotfix),
#2680 (rendering fix + logo) via an ad-hoc, conversation-driven process —
no formal requirements/design phase preceded development. This audit is
the retrospective structural check the user asked for, using the same
lens as `audit-framework/` and `docs/threat-model/` apply to other
surfaces.
**Author:** Claude (self-audit of work performed in this session)

> **Read this as a self-audit, not a third-party one.** I built this
> module. Findings below include real defects I introduced and only
> caught because the user pushed for real verification (staging DB
> checks, a live-portal round trip, a visual PDF inspection). That's the
> central lesson of this report: the ad-hoc process worked only because
> verification was pushed harder than the default. A repeatable process
> needs to bake that pushing in, not rely on it being asked for each time.

---

## 1. Coverage snapshot

| Scope | Coverage | Bar (CLAUDE.md) | Status |
|---|---|---|---|
| `backend/utils/report_branding.py` | **98%** | "Admin routes, utilities: ≥ 70%" | ✅ well above bar |
| `backend/routes/admin/compliance.py` | **57%** | "Admin routes, utilities: ≥ 70%" | ❌ below bar |
| Full backend suite (`pytest -m "not slow"`) | *(running at time of writing — see addendum)* | `--cov-fail-under=60` (pytest.ini) | — |

**Why `compliance.py` is only 57% despite 39 passing reporting-module tests:**
every test in `test_compliance_reports.py` calls the module's internal
helper functions directly (`_gst_pst_rows`, `_insurance_period_rows`,
`_log_compliance_export`, `_check_truncated`) with `db_supabase.get_rows`
patched — none of them go through the actual FastAPI route handlers
(`get_gst_pst_remittance`, `get_insurance_period_audit`) as HTTP requests.
The untested lines are: query-param validation via `Query(...)`, the
`try/except` → `HTTPException(503)` wiring at the route level, and the
dispatch into `_render_tabular_report` for each of the 4 formats. This is
exactly the "unit tests only, no integration test" gap the original PR
body flagged (`Integration tests: not-applicable`) — now quantified.

**Why this matters concretely, not just as a number:** the two real bugs
found via live-portal testing (`drivers.full_name` typo, PDF overlap) were
both in code paths a route-level integration test would have exercised
end-to-end. Helper-level unit tests validated the aggregation *logic* was
right; they never validated that calling the actual endpoint produces a
working response. Coverage percentage is a proxy here for a real,
specific gap — not a target to chase for its own sake.

---

## 2. Lifecycle phase-by-phase assessment

### Phase 1 — Inception: **skipped**
No problem statement, business case, or Go/No-Go decision exists for this
module beyond the initial chat prompt ("reporting module take a lead").
**Reasoning this matters, not just as process box-ticking:** the scope
pivoted mid-build — from "general reporting module" to "compliance & tax
reporting" to "must not duplicate the existing DSAR export" — three times
in the first hour, each requiring rework. A half-page problem statement
("SGI/CRA-facing exports currently require manual data pulls; admins need
self-serve, audit-logged reports") would have anchored scope before code
started and avoided at least one of those pivots. **Recommendation:** for
future modules of this size, write the 5-question inception answer
(problem, success criteria, why-this-approach, top risks, approver) as
the *first* message in the build, even informally — cheap insurance
against scope churn.

### Phase 2 — Requirements: **informal, not written down**
Functional scope (GST/PST remittance, insurance-period audit) was decided
conversationally and is now only recoverable by reading PR descriptions.
No non-functional requirements were ever stated: no P95 latency target
(reasonable — this isn't a KPI-tracked path per CLAUDE.md's SLA table,
but that exemption was never *written*, so a future reviewer can't tell
"deliberately out of scope" from "forgotten"), no defined row-volume
expectation (the `_ROW_LIMIT = 10000` constant was chosen by me, not
derived from an actual admin usage estimate), no accessibility target
beyond "same component kit as the rest of the app" (asserted, not
verified — flagged already in PR #2650's Tier 5 as `[ ]` unchecked).

**Concrete, high-value gap found here — threat model mismatch:**
`docs/threat-model/admin-panel.md:85` already documents an **open P1
threat**, `AI-3`: *"Admin exports all users → offline PII leak"*, with
the stated mitigation *"dual-approval on any export > 1,000 rows"*
(`docs/threat-model/admin-panel.md:126,161`). The insurance-period-audit
endpoint can return up to `_ROW_LIMIT = 10000` rows of driver-identifying
data (names, ride IDs, timestamps) in one unauthenticated-beyond-JWT
request, with **no dual-approval gate** — a direct instance of AI-3 that
was never cross-referenced against the existing threat model during
requirements. This is not a hypothetical: the mitigation for exactly this
risk was already designed and documented before this module existed, and
the new module didn't apply it.
**Reasoning why this is the right fix vs. alternatives:** the two
alternatives are (a) leave it, accepting the same risk profile as the
already-open AI-3 finding, or (b) build a new, module-specific
approval flow. Neither is right — (a) ships a known gap silently, (b)
duplicates work AI-3 already scoped. The correct fix is to route this
module's exports through whatever AI-3's eventual implementation becomes
(a shared dual-approval check available to any admin export endpoint),
and in the meantime, add this module explicitly to AI-3's tracked scope
in the threat model file rather than leaving it undiscovered.

### Phase 3 — Design: **partial, undocumented**
Real design decisions were made — the `REPORT_FORMAT_REGISTRY`
branded-vs-fixed_format split, the choice to build a shared
`report_branding.py` layer rather than per-report styling, reusing
`fpdf2`'s native `Table` API over hand-rolled cell math — but **none are
captured as an ADR**. `docs/adr/` has 8 entries for architecture
decisions of comparable-or-lesser scope (e.g. `004-redis-in-process-fallback.md`).
**Reasoning why an ADR is the right artifact here, not just "add a
comment":** the branded/fixed_format split is the kind of decision a
future engineer *will* get wrong by default — it's tempting to "just
brand the SGI form too" for consistency, and only an ADR explaining *why
that would violate the regulator's exact-format requirement* prevents
that regression outside of tribal knowledge. Code comments in
`report_branding.py` explain the *what*; an ADR is where the *why, and
what we rejected* belongs, discoverable independent of reading that one
file.

No data model diagram, no explicit non-functional design (caching?
none needed at current volume, but not stated), no cost estimate (N/A at
this scale, but again not stated as a deliberate non-goal).

### Phase 4 — Development: **mostly followed CLAUDE.md's stated gates, with one systemic near-miss**
What went right, concretely:
- Money-touching code was routed through `spinr-money-auditor` before
  commit (PR #2650, subtask 7) — caught a real HST-misclassification bug
  before merge.
- Security-sensitive code was routed through `spinr-security-auditor`
  before commit (PR #2650, subtask 8) — caught a real crash-on-import bug
  before merge.
- CSV-injection sanitization was carried over from the established
  `services/data_transfer/tabular_writer.py` convention rather than
  reinvented.
- Migration follows `backend/migrations/CLAUDE.md`'s append-only + RLS-
  in-same-migration + indexed-for-query-pattern rules, verified for real
  against a live Supabase project (not just reviewed).

What's a systemic gap, not a one-off mistake: **two of the three real
bugs found in this module (the `drivers.full_name` typo, the PDF
overlap/fill-color bug) were only caught because the user manually tested
the live portal and shared real output.** Neither the money-auditor nor
the security-auditor review (both of which ran and correctly caught what
they were scoped to catch) would ever have caught a wrong column name or
a visual rendering defect — those aren't in their remit. **This is not
"the reviews failed"; it's that no review step in the actual PR process
was scoped to catch "does this code path even run against the real
schema" or "does this render legibly."** That's a real, structural gap
in the development-phase gate list, not a fluke.

### Phase 5 — Testing: **unit-tested well, integration/E2E/visual-regression absent**
- Unit tests: 39 tests, well-reasoned, several are genuine regression
  tests for bugs actually found (HST misclassification, `Optional`
  import, em-dash PDF crash, fill-color leak). This is real, not
  padding — each traces to a specific defect.
- Integration tests: **none.** Confirmed by the 57% route-handler
  coverage number above.
- E2E tests: **none.** `admin-dashboard/e2e/` has 28 spec files for other
  admin pages; the compliance page has zero.
- Visual/accessibility regression: **no tooling exists for this at all in
  the repo** (already flagged as a standing gap in CLAUDE.md's guidance,
  not new to this module) — but this module is a fresh, concrete example
  of the cost: a real, user-visible rendering defect shipped to the live
  admin portal and was only caught by a human looking at a downloaded
  file.
- Performance/load: not applicable per CLAUDE.md's SLA table (admin
  reporting isn't a listed critical path) — this is a reasonable
  non-finding, but should be stated as a deliberate exemption in the
  module's own documentation rather than left implicit.

### Phase 6 — Deployment: **followed convention correctly, one real gap in rollback verification**
Both merges (`#2650`→`af1c105`, `#2675`→`08f4024`) were squash-merged only
after CI guard rails were green, matching repo convention. Rollback plans
were stated (`git-revert-safe`) and are structurally true. **What was
never actually done: the rollback command was verified against a
disposable local clone, but never against the real staging project** —
the migration was *applied* to staging and thoroughly verified, but its
`DROP TABLE IF EXISTS compliance_export_events;` rollback was not
re-run there. Low risk given the table is still empty of real report
runs, but "verified" and "verified once, on a different database" are
different claims, and the PR body said the former when only the latter
was true at staging-application time.

### Phase 7 — Launch/Hypercare: **no formal hypercare, informal version happened anyway**
No canary/staged rollout — both PRs went straight to 100% via squash
merge (reasonable at this scale/risk, but not a stated decision). What
functioned as ad-hoc hypercare: the user manually verified the live
portal within hours of merge, surfaced 2 real defects, and both were
fixed same-day. That's *better* than the PR's own "not verified" flag
implied would happen, but it worked because the user drove it, not
because a hypercare process existed.

### Phase 8 — Operations: **the most under-built phase**
- **No runbook.** If the GST/PST report starts returning wrong totals in
  production, there is no document telling on-call what to check first.
  The PR body's Tier 7 "stop condition" (log pattern to watch) is the
  closest thing that exists, and it's a paragraph in a merged PR, not a
  discoverable operational document.
- **No retention/purge wiring for `compliance_export_events`.** The
  migration's own comment claims *"Retention: 7 years"* — but grep
  confirms zero references to this table outside `compliance.py` itself;
  it's never touched by `retention_purge_loop` or any other lifecycle
  job. The table will grow unbounded with no enforcement of the claimed
  policy. **Reasoning on the right fix:** don't build an active purge
  job yet — a 7-year-retention *audit* table is *supposed* to accumulate
  for its full retention window, unlike PII tables purge_pii_retention
  targets. The real gap is narrower: the migration's claim should be
  either (a) backed by a dated reminder / ACTION_ITEMS.md entry to add
  purge logic once the table approaches 7 years of age (2033), or (b) the
  comment should say "no purge job yet; add one before year 7" rather
  than implying retention is already enforced.
- **No monitoring/metrics.** CLAUDE.md's observability conventions call
  for state-transition metrics and domain-tagged Sentry capture on
  user-visible errors. `compliance.py` has neither — DB failures are
  `logger.error`'d (correct, not swallowed) but never reach Sentry with a
  `domain` tag, so a production failure here is invisible to whatever
  alerting/triage process watches Sentry by domain.
- **No rate limiting.** `auth.py`, `data_transfer_export.py`, `faqs.py`,
  `rides.py`, and `staff.py` in the same `routes/admin/` directory all
  use the repo's SlowAPI-based rate limiter; `compliance.py` has none.
  Low exploitability (admin-only, JWT-gated) but inconsistent with
  sibling routes, and the GST/PST endpoint does a full table scan over
  up to 10,000 rides per call — an admin (or a compromised admin session)
  could hammer it.
- **Feature is not actually grantable to non-super-admins.** Concrete,
  verifiable bug: `admin-dashboard/src/app/dashboard/staff/page.tsx`'s
  `ALL_MODULES` list (the source of truth for what modules a
  non-super-admin role can be assigned) **does not include `"compliance"`**.
  Since `require_module("compliance")` bypasses the check only for
  `role == "super_admin"`, **no other admin role can currently be granted
  this module through the product's own admin-management UI** — directly
  contradicting PR #2650's own Tier 2 claim that access is *"granted
  per-admin via the existing admin-management UI."* That claim was wrong
  at the time it was written; this audit is the first place it's been
  caught. Until `"compliance"` is added to `ALL_MODULES` (one line), this
  is a super-admin-only feature in practice, whatever the code's intent.

---

## 3. Gap register (prioritized)

| # | Gap | Severity | Where | Fix effort | Recommended fix |
|---|---|---|---|---|---|
| G1 | `"compliance"` missing from `ALL_MODULES` — feature ungrantable to non-super-admins | **High** (functional, contradicts shipped PR claim) | `admin-dashboard/src/app/dashboard/staff/page.tsx:14-34` | 1 line | Add `{ key: "compliance", label: "Compliance & Tax Reporting" }` to `ALL_MODULES` |
| G2 | No dual-approval/threshold gate on large exports — direct instance of open threat-model finding AI-3 | **High** (documented security risk, not hypothetical) | `docs/threat-model/admin-panel.md:85`, `compliance.py`'s two endpoints | Medium — depends on AI-3's eventual shared implementation | Add this module to AI-3's tracked scope now; when AI-3's dual-approval mechanism ships, wire both endpoints through it rather than building a one-off |
| G3 | Route handlers untested at HTTP level — 57% coverage, below CLAUDE.md's 70% admin-route bar | **Medium** | `routes/admin/compliance.py` | Small–medium | Add `TestClient`-based integration tests for both endpoints (success, 503-on-DB-failure, format validation) using the existing `mock_supabase_client` fixture pattern from `conftest.py` |
| G4 | No E2E test for `/dashboard/compliance` | **Medium** | `admin-dashboard/e2e/` | Small | One Playwright spec: login → navigate → download each format → assert file downloaded, mirroring the existing 28 specs' pattern |
| G5 | No Sentry/metrics instrumentation | **Medium** | `compliance.py` | Small | Add `sentry_sdk.capture_exception` with `domain="admin"` tag (or petition for a dedicated `compliance` domain tag in CLAUDE.md's list) on the existing `except Exception` blocks; add `spinr_admin_compliance_export_total{report_type,outcome}` counter per `utils/metrics.py`'s naming convention |
| G6 | No rate limiting, inconsistent with sibling admin routes | **Low–Medium** | `compliance.py` | Small | Apply the same SlowAPI limiter decorator used in `routes/admin/rides.py`/`faqs.py` |
| G7 | No ADR for the branded/fixed_format design split | **Low** (documentation debt, not a runtime risk) | `docs/adr/` | Small | Write `docs/adr/008-report-branding-fixed-vs-branded.md` capturing the decision and rejected alternatives (e.g. "brand everything including SGI forms" — rejected because it would violate regulator format requirements) |
| G8 | `compliance_export_events`' claimed "7-year retention" has no enforcement or reminder | **Low** (long time horizon, real gap) | `backend/migrations/263_compliance_export_events.sql` | Trivial | Add an `ACTION_ITEMS.md` P3/P4 entry: "add purge logic for `compliance_export_events` before 2033," or soften the migration comment to state no purge job exists yet |
| G9 | No audit-framework module scope file — this surface won't be swept by future systematic audits | **Low** | `audit-framework/modules/` | Small | Add `audit-framework/modules/compliance-reporting.md` (or fold into a broader `reporting.md`) so future audit sweeps include it automatically instead of by luck |
| G10 | Rollback command verified locally, never against the real staging project it was applied to | **Low** (table is empty, low current risk) | staging Supabase project `spinrmobileapp` | Trivial | Optional: re-verify `DROP TABLE IF EXISTS compliance_export_events;` against staging directly, or accept the local-clone verification as sufficient given zero real data currently in the table |

---

## 4. What this module does *not* need (explicitly, to avoid over-correcting)

Per CLAUDE.md's own guardrails against gold-plating, some things a
generic SDLC checklist would flag are **not** worth doing here, and
saying so explicitly matters as much as the gap list:

- **No canary/blue-green deploy strategy** — this is a low-traffic admin
  report endpoint, not a consumer-facing hot path; the existing
  squash-merge-after-green-CI process is proportionate.
- **No load/performance testing** — not a KPI-tracked path per CLAUDE.md's
  SLA table; the `_ROW_LIMIT` truncation-detection mechanism already
  bounds worst-case query cost.
- **No OpenAPI spec authored by hand** — FastAPI generates this
  automatically from the route signatures already in place; a
  hand-maintained spec would just drift.
- **No dedicated Terraform/IaC** — no new infrastructure was provisioned,
  only application code and a migration against existing Supabase/Fly.io.

---

## 5. Recommended next steps, in order

1. **G1 (one-line fix)** — unblocks the feature for its intended users;
   do this first regardless of anything else.
2. **G2 and G3** together — G2 because it's a documented, open security
   finding being silently extended; G3 because writing the integration
   test for G3 will naturally exercise the same code path G2's gate would
   sit in front of.
3. **G5 and G6** — small, mechanical, brings this module in line with
   every sibling admin route's baseline.
4. **G4, G7, G9** — documentation/coverage debt, valuable but not
   time-sensitive; batch into a single follow-up PR.
5. **G8, G10** — register in `ACTION_ITEMS.md` as low-priority tracked
   items rather than fixing now; both have long time horizons and low
   current risk.

---
_Generated by Claude Code as a self-audit of work performed in this
session (PRs #2650, #2675, #2680)._
