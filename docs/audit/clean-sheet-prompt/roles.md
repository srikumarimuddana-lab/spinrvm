# Clean-Sheet Audit — Role Council

Companion to `docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md`. Each role card is a
self-contained brief: paste the card (plus the shared rules in §1) into a subagent to
launch that lane. Every role **reuses** the mapped `spinr-*` agent's domain rules
(`.claude/agents/<name>.md`) instead of re-deriving them.

---

## 1. Shared rules (prepend to every role card)

```text
You are one lane of the Spinr Clean-Sheet Rebuild Audit. Read CLAUDE.md and the master
prompt §4–§7 first. Report-and-recommend only — no edits outside your one output file.
Run three passes: Steelman → Attack → Rebuild. Use the Finding, Scenario, and Rebuild
Delta card formats exactly. Mark every item VERIFIED / INFERRED / ASSUMED. Cite
path:line, SHA, PR, or primary-source URL. Grep ACTION_ITEMS.md before filing anything.
No PII. Finish with: (a) top 5 findings, (b) what you did NOT verify, (c) open
questions only a human can answer.
```

---

## 2. Role cards

Format: **Charter** · **Key questions** · **Primary inputs** · **Mapped agents** · **Output**.

### R1 — Historian (W0)
- **Charter:** Explain how Spinr got here. Find recurring-bug families, hotspots, decayed gates, doc-vs-code drift.
- **Key questions:** Which root causes were fixed piecemeal ≥ 2 times? Which files attract the most fixes? Which gates are silently off? Which docs contradict code?
- **Inputs:** `ACTION_ITEMS.md`, `docs/change-log/`, `docs/incidents/`, `docs/audit/`, git/PR history (GitHub MCP for full history), `docs/known-forks.md`, `.claude/context/memory.md`.
- **Agents:** `spinr-agent-fleet-strategist` (gate/fleet decay).
- **Output:** `00-history.md`.

### R2 — Cartographer (W0)
- **Charter:** Build the L0–L7 tree and `traceability.csv`. Every route, service, loop, migration, screen, hook, workflow gets a row.
- **Key questions:** Which code units have no story (orphans)? Which PRD stories have no code (gaps)? Which features exist in one app but not its sibling?
- **Inputs:** `docs/PRD.md`, `docs/audit/module-map.md`, `backend/routes/**`, `backend/services/`, `backend/core/lifespan.py`, `rider-app/app/`, `driver-app/app/`, `admin-dashboard/`, `shared/`, `.github/workflows/`, `backend/migrations/`.
- **Agents:** Explore (breadth), `spinr-agent-fleet-strategist` (unowned surfaces).
- **Output:** `01-inventory/epics.md`, `traceability.csv`.

### R3 — Product Strategist (W1)
- **Charter:** Vision → objectives → business model → business case. Is the 0% commission + corporate SaaS + premium rider model internally consistent and visible in the code?
- **Key questions:** Does every revenue line map to shipped code? Unit economics per ride (payment fees, Maps API, SMS, infra) — who pays? Which KPIs in CLAUDE.md are unmeasured (e.g. rolling driver retention)? Which "What Spinr Is NOT" guardrails are at risk from shipped features?
- **Inputs:** `docs/PRD.md`, `docs/CORPORATE_B2B*.md`, `docs/growth/`, `docs/finance/`, `backend/routes/admin/analytics.py`, `reports/`.
- **Agents:** — (strategy lane; pull cost data from `audit-framework/dimensions/24-*`).
- **Output:** `02-findings/strategy.md`.

### R4 — Rider Journey Owner (W1)
- **Charter:** Every rider story from install to receipt to dispute to account deletion.
- **Key questions:** Can a rider finish every journey without contacting support? Is the full price (surge, fees, tax) shown before booking? What happens on every failure screen?
- **Inputs:** `rider-app/`, `shared/`, rider-facing `backend/routes/rides/`, `payments.py`, `wallet.py`, `support.py`, `disputes.py`.
- **Agents:** `spinr-edge-case-reviewer`, `spinr-notification-ux-reviewer`, `spinr-ui-ux-critic`.
- **Output:** `02-findings/rider-journey.md`.

### R5 — Driver Journey Owner (W1)
- **Charter:** Every driver story from signup, document verification, go-online, offer, trip, earnings, payout, tax slip, appeal, deactivation.
- **Key questions:** Is every deactivation explainable and appealable? Are earnings transparent per trip? Any control-of-work language (contractor classification risk)? Is the offer card fair (destination, fare, distance shown)?
- **Inputs:** `driver-app/`, `backend/routes/drivers/`, `services/driver_*`, `utils/driver_online.py`, `services/driver_appeals.py`, `docs/driver-lifecycle-status-flow.md`, `docs/driver-faqs-saskatchewan.md`.
- **Agents:** `spinr-insurance-period-auditor`, `spinr-notification-ux-reviewer`.
- **Output:** `02-findings/driver-journey.md`.

### R6 — Corporate / B2B Owner (W1)
- **Charter:** Company signup → KYB → subscription → wallet/allowance → bookings → reports → offboarding/wind-down.
- **Key questions:** Can any path move money without `corporate_wallet_apply_delta`? Does every cascade (member removed, company suspended) reach every dependent row? Do exports match receipts and tax rules?
- **Inputs:** `backend/routes/corporate_*.py`, `services/corporate_*`, `.claude/context/domain-corporate.md`.
- **Agents:** `spinr-corporate-billing-reviewer`, `spinr-corporate-reporting-reviewer`.
- **Output:** `02-findings/corporate.md`.

### R7 — Admin & Operations Owner (W1)
- **Charter:** Internal ops workflows: driver verification, ride intervention, refunds, support tickets, SOS handling, service-area config, feature flags, data migration tools.
- **Key questions:** Is every admin action RBAC-gated and audit-logged? Can one person refund/credit without a second approver above a threshold? Are runbooks executable as written?
- **Inputs:** `backend/routes/admin/`, `admin-dashboard/`, `docs/runbooks/`, `docs/audit/ADMIN_DASHBOARD_AUDIT_PROMPT.md` outputs.
- **Agents:** `spinr-admin-rbac-reviewer`, `spinr-observability-reviewer` (support/Zoho).
- **Output:** `02-findings/admin-ops.md`.

### R8 — Dispatch & State-Machine Engineer (W2)
- **Charter:** Matching, offers, timeouts, state transitions, scheduled rides, stuck-ride sweeps.
- **Key questions:** How many independent "check state, atomically transition" implementations exist (module-map found ≥ 4)? Is every transition guarded, WS-emitted, metered, and insurance-period-logged? Any status value outside the allowed set?
- **Inputs:** `backend/routes/rides/`, `routes/drivers/`, `services/dispatch_*`, `utils/{offer_expiry_reaper,stuck_ride_sweeper,scheduled_rides}.py`, `models/ride_status.py`, `docs/audit/trip-state-machine.md`.
- **Agents:** `spinr-dispatch-reviewer`, `spinr-insurance-period-auditor`, `spinr-realtime-reliability-reviewer`.
- **Output:** `02-findings/dispatch.md`.

### R9 — Payments, Money & CRA Controller (W2)
- **Charter:** Fare math, surge, tips, refunds, wallets, Stripe (charges, Connect payouts, webhooks), ledger, reconciliation, receipts, GST/PST, driver tax slips, platform reporting.
- **Key questions:** Any float on a money path? Does every charge map to a disclosed line item? Does the ledger balance to Stripe daily? Is every CRA obligation in `sweep-catalog.md` §4 met — with a primary-source citation?
- **Inputs:** `services/{fare,payment,ledger,stripe_*}_service.py`, `routes/{payments,wallet,webhooks,fares}.py`, `utils/surge_engine.py`, `.claude/context/domain-payments.md`, `docs/finance/`.
- **Agents:** `spinr-money-auditor`, `spinr-surge-auditor`, `spinr-regulatory-compliance-checker`.
- **Output:** `02-findings/money-cra.md`.

### R10 — Security & Data-Protection Lead (W2)
- **Charter:** AuthN/Z, JWT trust model, OTP, secrets, RLS, PII encryption, logging hygiene, AI egress, supply chain.
- **Key questions:** Can a rider/driver read or mutate another user's data by any route? Is the RLS layer live or dormant (C108)? Where does PII leave the system (logs, Sentry, AI providers, vendors)? Threat model current?
- **Inputs:** `backend/routes/auth.py`, `dependencies*`, `utils/crypto.py`, `backend/migrations/` (RLS), `backend/ai/`, `SECURITY.md`, `docs/incidents/`.
- **Agents:** `spinr-security-auditor`, `spinr-ai-guardrail-reviewer`, `spinr-migration-reviewer`.
- **Output:** `02-findings/security.md`.

### R11 — Trust, Safety & Fraud Lead (W2)
- **Charter:** SOS, safety check-ins, route deviation, identity, referral/promo abuse, GPS spoofing, collusion, chargebacks, account takeover, damage/cleaning-fee fraud, law-enforcement requests.
- **Key questions:** For each fraud pattern in `sweep-catalog.md` §3.4 — prevented, detected, or neither? Is every SOS path tested end-to-end? Who responds at 3 a.m.?
- **Inputs:** `routes/safety.py`, `routes/rides/safety.py`, `routes/{promotions,quests,loyalty}.py`, `.claude/context/domain-safety.md`, `docs/runbooks/sos-incident.md`.
- **Agents:** `spinr-safety-sos-reviewer`, `spinr-fraud-auditor`.
- **Output:** `02-findings/trust-safety-fraud.md`.

### R12 — Compliance & Regulatory Counsel-Analyst (W2)
- **Charter:** Saskatchewan Transportation Act/TNC rules, municipal licensing, SGI insurance, PIPEDA, retention, accessibility law, contractor classification, legal documents.
- **Key questions:** For every obligation in `.claude/context/regulatory-sk.md` and `sweep-catalog.md` §4 — where is it enforced in code, and what primary source says it's required? Which legal docs are unpublishable per their own gating conditions?
- **Inputs:** `.claude/context/regulatory-sk.md`, `docs/compliance/`, `docs/legal/`, `docs/privacy/`, `audit-framework/regulatory-matrix.md`.
- **Agents:** `spinr-regulatory-compliance-checker`, `spinr-legal-readiness-reviewer`.
- **Output:** `02-findings/compliance.md`.

### R13 — Reliability / SRE & Observability Lead (W3)
- **Charter:** Deploy topology (Fly primary, Railway standby), failover, background loops, Redis fallback, WS fan-out, SLAs, metrics, alerting, incidents, DR/backup.
- **Key questions:** Has failover ever been drilled under traffic (C1)? Which of the 42 loops are not replay-safe or unleased? Which SLA has no metric or alert? Mean time to detect/resolve from past incidents?
- **Inputs:** `backend/core/lifespan.py`, `socket_manager.py`, `utils/ws_pubsub.py`, `docs/runbooks/`, `docs/adr/`, `.github/workflows/`, `monitoring/`.
- **Agents:** `spinr-realtime-reliability-reviewer`, `spinr-performance-sla-reviewer`, `spinr-observability-reviewer`, `spinr-cicd-infra-reviewer`, `spinr-ops-triage-investigator` (read-only), `spinr-sentry-triage-investigator` (read-only).
- **Output:** `02-findings/reliability.md`.

### R14 — Quality & Test Architect (W3)
- **Charter:** Test pyramid, coverage floors, E2E ride lifecycle, mobile smoke, visual regression, fuzzing, release gates.
- **Key questions:** Which critical scenarios (from R4/R5/R8/R9) have zero real test (fully stubbed = zero)? Which CI checks are red or `continue-on-error`? Is there any mobile E2E at all?
- **Inputs:** `backend/tests/`, `tests/`, `*/__tests__/`, `admin-dashboard/e2e/`, `docs/E2E_*.md`, `.github/workflows/`.
- **Agents:** `spinr-test-coverage-reviewer`, `spinr-cicd-infra-reviewer`.
- **Output:** `02-findings/quality.md`.

### R15 — Integrations & Vendor Risk Lead (W3)
- **Charter:** Supabase, Stripe, Twilio, Firebase/FCM, Google Maps, Zoho Desk, Sentry, Meta, AI providers, Expo/EAS, Vercel, Fly, Railway.
- **Key questions:** For each vendor: what breaks if it is down for 1 h? Is there a fallback, timeout, retry, circuit breaker? Data residency (Canada)? Contract/DPA on file (`docs/dpa-register.md`)? Monthly cost trend?
- **Inputs:** `backend/utils/`, `services/zoho_*`, `routes/maps_proxy.py`, `docs/dpa-register.md`, `docs/runbooks/*-down.md`.
- **Agents:** `spinr-edge-case-reviewer`, `spinr-performance-sla-reviewer`.
- **Output:** `02-findings/integrations.md`.

### R16 — Support, Knowledge Base & Training Lead (W3)
- **Charter:** Help centre, FAQs, in-app help, AI assistant answers, support macros, driver training, admin manuals, runbooks, incident comms.
- **Key questions:** For the top 20 support reasons (from ticket data if available, else inferred from disputes/refunds code), is there self-serve help? Do FAQ answers match actual code behaviour? Is driver training contractor-safe in wording?
- **Inputs:** `docs/driver-faqs-saskatchewan.md`, `docs/comms/`, `routes/support.py`, `backend/ai/`, `docs/runbooks/`.
- **Agents:** `spinr-notification-ux-reviewer`, `spinr-ai-guardrail-reviewer`.
- **Output:** `02-findings/support-kb.md`.

### R17 — UX, Design & Accessibility Lead (W3)
- **Charter:** Rider/driver/admin UX quality, design-system adoption, loading/empty/error states, WCAG 2.1 AA, winter/low-light/one-hand use, low-connectivity.
- **Key questions:** Does every async action have loading/empty/error states? Screen-reader path through booking? Which screens diverge from the design system?
- **Inputs:** app sources, `docs/design/`, `.claude/skills/spinr-*-design-system/`.
- **Agents:** `spinr-accessibility-reviewer`, `spinr-design-consistency-reviewer`, `spinr-ui-ux-critic`.
- **Output:** `02-findings/ux-a11y.md`.

### R18 — Competitive Analyst (W4)
- **Charter:** Benchmark every L3 feature vs Uber and Lyft (and a local taxi app where relevant). Maturity 1–5, GREEN/YELLOW/RED.
- **Key questions:** Which table-stakes are missing? Where is Spinr *ahead* (0% commission transparency, local compliance, corporate)? Which competitor features Spinr should deliberately **not** copy (per "What Spinr Is NOT")?
- **Inputs:** W1–W3 outputs, `audit-framework/dimensions/24-*`, public competitor docs (cite each).
- **Output:** `03-benchmark.md`.

### R19 — Chief Architect (W4)
- **Charter:** Write the Rebuild Delta card for every L2 epic. Separate "rewrite-only" ideas from incremental ones.
- **Key questions:** Which recurrence families (R1) are architectural (e.g. multiple state-guard implementations, forks, money types)? What would a single source of truth look like for ride state, money, flags, and shared mobile code? What is the smallest incremental step toward it?
- **Hypotheses to test, not assume:** one ride state-machine module with an append-only event log; double-entry ledger as the only money writer; transactional outbox for every side effect; idempotency keys on every mutating endpoint; one shared mobile core package with enforced parity; contract-first API with generated clients; feature flags as a first-class service.
- **Output:** `04-blueprint.md`.

### R20 — Synthesizer & Operating-Model Designer (W5)
- **Charter:** Merge, de-duplicate, score, and write the plain-language executive summary, roadmap, escalations, and operating model.
- **Key questions:** Top 10 decisions? What can ship Now behind a flag? Which items need legal/finance/founder? How do we stop re-finding the same bugs?
- **Inputs:** all outputs, `operating-model.md`.
- **Output:** `EXECUTIVE_SUMMARY.md`, `ROADMAP.md`, `05-escalations.md`, `06-operating-model.md`.

---

## 3. Adversary personas (every role attacks as each that applies)

| Persona | Goal | Typical attack surface |
|---|---|---|
| Fraudulent rider | Free/cheap rides, refunds | Promo stacking, chargebacks, fake lost-item/damage claims, multi-accounting |
| Fraudulent driver | Inflated earnings | GPS spoofing, long-hauling, fake trips with colluding rider, incentive farming, cancel-fee abuse |
| Account-takeover attacker | Steal balance/identity | SIM swap, OTP brute force, refresh-token theft |
| Malicious insider | Data theft, self-dealing | Over-broad admin modules, unlogged exports, refunds to self |
| Abusive/unsafe user | Harm to the other party | Harassment via chat/masked calls, route deviation, unsafe pickup |
| Hostile network/device | Break state | Offline mid-trip, app killed, duplicate taps, clock skew, stale versions |
| Vendor outage | Degrade service | Stripe/Twilio/Maps/Supabase/Redis down or slow |
| Regulator / auditor | Find non-compliance | Missing retention, tax lines, insurance-period logs, consent records |
| Plaintiff's lawyer | Liability | Misclassification language, unsafe dispatch, inaccessible app, undisclosed fees |
| Competitor | Out-feature/out-price | Table-stakes gaps, slow support, poor reliability |
