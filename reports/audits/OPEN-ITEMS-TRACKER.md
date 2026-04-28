# Open Items Tracker — All Modules

**Updated:** 2026-04-23 · Branch: `claude/review-pending-audits-Pu1aP`
**Scope:** every verified-open item (PARTIAL / PENDING / BLOCKED) plus the
new issues surfaced during driver verification (2026-04-23) that are not
yet in any remediation file.

Re-build this tracker after every audit run or verification pass.

---

## Legend

| Field | Meaning |
|---|---|
| **Status** | DONE / PARTIAL / PENDING / BLOCKED / UNVERIFIABLE / SUPERSEDED |
| **Blast radius** | self = single user · org = every rider/driver · regulator = filing deadline |
| **Owner** | one of: backend, driver-app, rider-app, shared, admin, infra, data, devops, legal, compliance, product, ext-* |
| **Regulations** | IDs from `audit-framework/regulatory-matrix.md` |
| **Risk score** | `severity × blast × likelihood` — see formula below |

### Risk-Score Formula (added 2026-04-24)

```
risk_score = severity_weight × blast_weight × likelihood_weight

severity_weight:   CRITICAL=16  HIGH=8  MEDIUM=4  LOW=2  RECOMMENDATION=1
blast_weight:      regulator=4  org=2   self=1
likelihood_weight: certain=4    likely=3  possible=2  rare=1
```

**Sprint thresholds:**
- **≥ 64** → P0 (fix now; e.g. CRITICAL × regulator × likely = 192)
- **32–63** → P1 (this sprint)
- **16–31** → P2 (before launch)
- **8–15** → P3 (hardening)
- **< 8** → P4 (backlog)

Dollar-at-risk column is optional but recommended when > 3 items compete for the
same sprint window.

---

## A · Driver App — 4 Open Items (from verification 2026-04-23)

Full evidence in `reports/audits/2026-04-23-driver-P{0..4}-verification.md`.

| ID | Status | Blast | Owner | Effort | Regs | Action |
|---|---|---|---|---:|---|---|
| **P0-5** | PARTIAL | org | backend | 4 h | SAFE-CRC, SAFE-DRV, SAFE-VEH, SGI, SK-TNC | Replace `{"$set": ...}` wrapper on Supabase update in `utils/document_expiry.py:115`; add `status != 'suspended'` filter to `find_nearby_drivers` RPC; fix past-expiry loop bound; regression test |
| **P3-4** | PARTIAL | self | backend + driver-app | 8 h | — | Raise `driver-app/jest.config.js` threshold from 30/20/30 → 50/40/50; add `--cov-fail-under=30` in `backend/pytest.ini`; step +5 pp/sprint; add CI "coverage must not drop" check |
| **P4-5** | PARTIAL | self | driver-app | 20 h | — | Add E2E specs for verify-OTP, complete-trip, payout (framework: Playwright-style to match existing specs, **not** Maestro as originally named); update `P4-future-features.md` to reflect framework choice |
| **P4-7** | PARTIAL | regulator | backend | 12 h | CRA | Complete T4A PDF generator + filing path; `/drivers/t4a/${year}` must return real `pdf_url`; schedule Feb-each-year generator job; integration test |

**Blocker before device test:** P0-5 (4 h).
**Blocker before beta:** none.
**Blocker before launch:** none (P4-7 blocks Feb 28 tax filing, not launch).

---

## B · Driver App — 17 New Issues Surfaced During Verification (not in any remediation file yet)

These need triage into remediation sprints or into the next audit cycle.
Logged here so nothing is lost between audit passes.

### B1. Backend / dispatch
| ID | Area | Summary | Owner | Regs |
|---|---|---|---|---|
| DV-1 | Dispatch | `find_nearby_drivers` RPC lacks `status != 'suspended'` filter (defense-in-depth beyond P0-5) | backend | SAFE-*, SK-TNC |
| DV-2 | DB helpers | Mongo-style `{"$set": ...}` wrapper on Supabase `update` in `utils/document_expiry.py` — same anti-pattern as source finding `[2-2]`; grep for other occurrences | backend | — |
| DV-3 | State machine | `COMPLETE_FROM_STATES = ("in_progress",)` vs `trip_in_progress` used elsewhere → full state-string sweep required | backend | SK-CPPA |
| DV-4 | Payments | Fallback Stripe idempotency key `intent-{user}-{amount}` collides on legitimate same-amount retry after 24 h → swap to UUID client token | backend | PCI-DSS |
| DV-5 | Notifications | P2-10 fire-and-forget PUT swallows 4xx/5xx; UI diverges from server → surface a toast | driver-app | CASL |
| DV-6 | Rate limiter | P2-4 in-memory fallback is per-process; alert SRE when "Redis unavailable" warning fires in prod | devops | PIPEDA |
| DV-7 | Secrets | pgsodium `drivers_pii_key` rotation cadence undocumented (SOC2) | infra / compliance | SOC2 |
| DV-8 | Retention | P3-8 soft-delete without scheduled purge at PIPEDA/CRA horizon → add cron | data | PIPEDA, CRA |
| DV-9 | Notifications | P3-9 notification deep-link has no default fallback for unknown types → silent no-op | driver-app | — |

### B2. Auth
| ID | Area | Summary | Owner | Regs |
|---|---|---|---|---|
| DV-10 | Auth | Firebase-path audience check (driver vs rider) open — see rider-P1-12 | backend | PIPEDA |

### B3. Frontend layout drift
| ID | Area | Summary | Owner | Regs |
|---|---|---|---|---|
| DV-11 | Panels | `ActiveRidePanel.tsx` + `TripCompletedPanel.tsx` moved `components/panels/` → `components/dashboard/`; remediation text stale | driver-app | — |
| DV-12 | Screens | `report-safety.tsx` and `legal.tsx` at `app/` root not under `app/driver/` → confirm nav wiring | driver-app | PIPEDA |

### B4. Product-decision drift
| ID | Area | Summary | Owner | Regs |
|---|---|---|---|---|
| DV-13 | Cancellation | P1-1 implementation uses $5 fee (not hard block) when driver arrived → update `P1-before-beta.md` | product | SK-CPPA |
| DV-14 | OTP | P1-10 native keypad used (not custom) → remove from checklist | product | — |
| DV-15 | E2E | P4-5 framework changed (Maestro → Playwright-style) → update `P4-future-features.md` | product | — |

### B5. Compliance follow-ups
| ID | Area | Summary | Owner | Regs |
|---|---|---|---|---|
| DV-16 | Privacy | Gemini cross-border disclosure (P4-1): add as sub-processor in privacy policy | legal | PIPEDA |
| DV-17 | DSAR | P4-6 emit audit-log row on request + enforce 30-day PIPEDA SLA | backend + compliance | PIPEDA |

**Triage recommendation:** DV-1, DV-2, DV-3, DV-4 are P1 technical debt worth addressing in the next engineering sprint. DV-6, DV-7, DV-8, DV-16, DV-17 are compliance/ops preconditions for public launch — move to P2. DV-5, DV-9 are P3 polish. DV-10 is tracked under rider. DV-11 through DV-15 are documentation syncs — 1 h total, do at start of next audit cycle.

---

## C · Rider App — Pending Verification

Rider has 92 pre-populated remediation items across P0–P4 with `rider-`
prefix in `reports/remediation/`. Verification against HEAD has not been
run yet. Execute in 5 sprint-sized calls:

1. `reports/remediation/rider-P0-critical-fix-now.md` (8 items)
2. `reports/remediation/rider-P1-before-beta.md` (33 items)
3. `reports/remediation/rider-P2-before-launch.md` (33 items)
4. `reports/remediation/rider-P3-hardening.md` (8 items)
5. `reports/remediation/rider-P4-future-features.md` (10 items)

Prompt: `reports/audits/2026-04-23-rider-app-remediation-verification-prompt.md`
(created in this session).

Preliminary expectations from the rider audit (`2026-04-19-rider-app-v1.txt`):

| Rider item | Expected status |
|---|---|
| rider-P0 (8 items) | Unknown — verify first |
| rider-P1-10 (i18n library) | Likely PENDING — big effort |
| rider-P1-12 (Firebase audience) | Shared with DV-10 — PARTIAL/PENDING expected |
| rider-P1-22..26 (tests) | Likely PARTIAL — depth gaps |
| rider-P2-47+ (notifications, support, perf, a11y) | Mixed |
| rider-P4 (10 items) | Roadmap — likely ALL PENDING |

After verification, populate a rider-side twin of section A above and
triage rider-side new issues into section B.

---

## D · Backend API — Pending Audit

Plan: `reports/audits/2026-04-23-backend-api-audit-plan-v1.md`
Phases A → E (dims 01–04, 07–08, 09–12, 14, 17–22) — 17 dimensions.
Expected: per-phase `===FINDINGS-YAML===` block, rolled into one v1.txt.

After audit, derive `reports/remediation/backend-P{0..4}-*.md` (mirror of
driver/rider layout) and schedule remediation sprints.

No open items yet — all findings live downstream of audit execution.

---

## E · Admin Panel — Pending Audit

Plan: `reports/audits/2026-04-23-admin-panel-audit-plan-v1.md`
Dimensions: 01–04, 07 (partial: admin impacts on ride state), 09–12, 14, 15,
17–22 — 16 dimensions.
Severity ladder: one step up from consumer equivalents (blast radius =
org-wide).

No open items yet — all findings live downstream of audit execution.

---

## F · Operational / Governance Gaps (cross-module)

These come from the **dims 17–22** checklists. Audit them per module but
track consolidated here because they are shared infrastructure concerns.

| Gap | Module(s) | Owner | Severity est. |
|---|---|---|---:|
| ~~Structured JSON logging with `request_id` end-to-end~~ | ~~backend + mobile~~ | DONE — backend: loguru `serialize=True` + `RequestIDMiddleware`; mobile: `shared/api/client.ts` sends `X-Request-ID` on every request | ~~HIGH~~ |
| SLO doc + alerting rules committed to repo | infra | infra + devops | HIGH |
| PITR restore drill evidence (past 90 days) | data | data | MEDIUM |
| Vendor inventory file `docs/vendor-inventory.md` | cross | compliance | HIGH |
| DPAs on file for Supabase, Stripe, Firebase, Twilio, Google Maps, Gemini | cross | legal | HIGH |
| Supabase region = Canadian (PIPEDA) | data | data + compliance | CRITICAL if non-CA |
| Gemini US cross-border disclosure in privacy policy | legal | legal | HIGH |
| 7-loop liveness alerts | backend + devops | devops | HIGH |
| Daily reconciliation cron (Stripe ↔ DB ↔ wallet) | backend | backend + finance | HIGH |
| Threat-model doc per module, re-review cadence | cross | security | HIGH |
| SBOM generation + signed releases | infra | infra | MEDIUM |
| Break-glass admin account + JIT escalation | admin | backend + security | HIGH |
| Immutable audit log for admin actions | admin | backend | HIGH |
| Bulk-mutation admin dual-approval | admin | backend + product | HIGH |

Most of these will surface during backend + admin Phase E audit. This
section is the "known issues to stress-test" hint for the auditor.

---

## G · Regulatory Deadlines (calendar-bound)

Items tied to calendar events — miss them and there's a legal/financial
consequence.

| Deadline | Item | Module | Open? |
|---|---|---|---|
| Feb 28 each year | CRA T4A slip issuance (≥$500/yr drivers) | backend | **YES** (P4-7 PARTIAL) |
| Annually | Accessibility plan publication (ACA) | product + legal | Unknown — verify |
| 72 h from vendor breach notice | OPC notification chain (PIPEDA) | incident response | Runbook not yet audited |
| 30 days from DSAR | Driver/rider data export (PIPEDA s.9) | backend | Endpoint exists (P4-6 DONE); SLA enforcement not verified |
| Quarterly | ASV scan (PCI-DSS v4) | backend | Scope depends on SAQ level — verify |
| At launch + 3 y | Accessibility progress report (ACA) | product + legal | Not audited |
| Continuous | CASL unsubscribe ≤ 10 biz days | backend | Not audited yet |

---

## H · Effort Roll-Up

| Category | Items | Effort |
|---|---:|---:|
| Driver verified-open (section A) | 4 | 44 h |
| Driver new-issues triage (section B) | 17 | ~40 h estimated |
| Rider verification (section C) | (run 5 prompts) | 1–2 h per sprint × 5 |
| Backend audit (section D) | (run 5 phase prompts) | Phase A–E execution |
| Admin audit (section E) | (run 5 phase prompts) | Phase A–E execution |
| Cross-module gaps (section F) | 14 | ~80 h estimated |
| Regulatory deadlines (section G) | 7 | varies |

Total open engineering work once all verifications are run: **150–200 h**
spread across backend, driver-app, rider-app, admin, infra, devops, data,
legal, compliance, product. Largest owners: **backend** (40–60 h) and
**mobile apps** (40–50 h combined).

---

## Closing Checklist — When Is This Tracker Empty?

The product is audit-ready for public launch when:

- [ ] Section A: all items `DONE` (driver)
- [ ] Section B: each item either remediated or moved into a sprint file
- [ ] Section C: rollup produced, items moved into structured sections
- [ ] Section D: backend v1 audit done, findings remediated through P1 (at minimum)
- [ ] Section E: admin v1 audit done, P0 CRITICAL cleared, P1 majority cleared
- [ ] Section F: each gap either closed or consciously deferred with sign-off
- [ ] Section G: every calendar-bound item has an owner and target date
- [ ] Last audit for every module ≤ 90 days old at launch date

Maintain this tracker as the single source of truth. When it's empty,
the minimum-viable-audit coverage is satisfied.
