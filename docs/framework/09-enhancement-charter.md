# Pillar 9 — Enhancement Charter (Swarm Mission Backlog)

> The standing mission for the `/spinr-swarm` protocol (Pillar 8): the full
> universe of enhancements Spinr intends to consider, organized into
> workable tracks, with each item dispositioned against what already exists
> in this repo — plus the risk register for operating the swarm itself.
>
> **This charter is deliberately NOT one prompt.** A 60-concern mega-prompt
> gives the swarm everything and therefore no priority, no verify step per
> item, and no way to say "done" — the exact failure modes Pillars 1 and 8
> forbid. Instead: the swarm runs `/spinr-swarm charter <track>` for one
> track at a time, each invocation producing normal ≤3-file, gated,
> verified cycles. The charter is the map; a cycle is the unit of work.

## How to run the charter

1. Pick a track below (or let discovery mode pick: launch-gating
   `ACTION_ITEMS.md` P0/P1 items always outrank charter work).
2. The first cycle of any track is an **assessment cycle**: verify the
   "exists today" column against current code, produce a gap list with
   effort/value scores, and file the chosen items into `ACTION_ITEMS.md`
   so the single-backlog rule holds. No implementation in cycle 1.
3. Subsequent cycles implement one filed item each, under the full Pillar 8
   loop (brainstorm A/B/C → debate → implement → adversary pass → validate
   → Change Impact Log).
4. Items marked **Process** need a human with dashboard/legal access — the
   swarm drafts, a human executes. Items marked **Research** end in a
   decision write-up + ADR, never in speculative code.

Disposition legend: **Enhance** (exists, improve it) · **Build** (new) ·
**Research** (investigate → ADR before building) · **Process** (human-run,
swarm-drafted).

---

## Track A — Security & Access

| Item | Exists today | Disposition |
|---|---|---|
| MFA beyond staff | Admin/staff MFA shipped (`routes/admin/auth.py`; rollout comms = C4) | **Enhance**: evaluate rider/driver step-up MFA for sensitive actions (payout changes, phone change) — not login friction on every ride |
| ABAC vs current RBAC | Module-grant RBAC (`require_module`, `AVAILABLE_MODULES`) with dedicated reviewer agent | **Research**: attribute-based rules (region, shift-time, data-class) layered *on* RBAC only if a concrete need is shown; wholesale ABAC replacement is high-risk churn on a working model |
| Access reviews (periodic recertification) | RBAC grants exist; no scheduled review of who holds what | **Build (Process-assisted)**: quarterly access-review report from the grant tables + audit log; human signs off |
| Full identity lifecycle (joiner/mover/leaver) | Onboarding + suspension/offboarding services exist per role | **Enhance**: single lifecycle doc + automated leaver sweep (tokens revoked, grants dropped, exports gated) |
| Encryption at rest | PII vault exists (`utils/vault_pii.py`, key-rotation runbook); Supabase disk encryption | **Enhance**: inventory which fields are vaulted vs plain; close gaps by data classification (`docs/data-classification.md`) |
| Encryption in transit | TLS everywhere (Fly/Cloudflare/Supabase/Stripe) | **Enhance**: verify internal hop coverage (Redis, metrics-agent) + document cipher floor |
| Encryption in use / in process | None (no confidential computing) | **Research**: almost certainly not warranted at this scale — write the ADR saying so with triggers that would change the answer, rather than leaving it unexamined |
| Core-tech security posture | Threat models ×4, security-gates CI, gitleaks, pre-commit hooks, `spinr-security-auditor` | **Enhance**: keep; wire the DAST scaffold live once staging URL exists (E6) |
| Secure storage & data normalization | Postgres + RLS; repositories layer owns query shaping | **Enhance**: normalization audit per new module; no new datastore without ADR |

## Track B — Compliance & Regulatory (beyond current SK/PIPEDA)

| Item | Exists today | Disposition |
|---|---|---|
| PIPEDA | Deeply implemented (Pillar 5): minimization, never-log list, rights flows, breach protocol | **Enhance** (continuous; B11 export dual-approval is the open gap) |
| Federal trajectory (CPPA / Bill C-27 successor state) | Not tracked | **Research**: monitor + gap-map so a federal privacy-law change is a diff, not a scramble |
| Other provinces (AB/BC/MB TNC + privacy regimes) | SK-only by design (`regulatory-sk.md`) | **Research**: pre-expansion regulatory matrix per province — *before* any expansion decision, not after |
| ISO 27001 / NIST CSF / SOC 2 alignment | `audit-framework/` dimensions cover much of the control substance; no formal mapping | **Build**: control-mapping doc (existing control → ISO/NIST CSF ID → evidence location). Mapping first; certification is a business decision (Process) |
| Incident response & reporting | `/incident`, 50+ runbooks, breach protocol, postmortem template | **Enhance**: add regulator/insurer notification matrix (who, threshold, deadline, channel) as one page |
| Reports to cities / municipalities | ADR-008 two report modes; SGI form-field mapping (`docs/reporting/`) | **Enhance**: extend the fixed-regulator-format mode per municipal requirement as they arise |
| Insurance-company reporting (SGI) | SGI quarterly (`docs/compliance/`), insurance-period audit trail (7 y) | **Enhance**: automate quarterly assembly from the period tables |
| Regulatory notifications | Manual via runbooks | **Build**: notification-obligation tracker (deadline-aware checklist generated at incident open) |

## Track C — Observability, Analytics & Intelligence

| Item | Exists today | Disposition |
|---|---|---|
| Detailed logging | Structured logging conventions + audit tables (Pillar 6) | **Enhance**: per-module log-coverage sweep against the "diagnosable at 3 AM" bar |
| Anomaly detection | Pointwise checks exist (GPS plausibility, payment/location integrity, fraud-reviewer rules) | **Build**: a modest runtime anomaly layer (rule-based first: fare outliers, impossible speed, login velocity) feeding admin alerts — heuristics before ML, PIPEDA-clean features only |
| Heuristic analysis | Fraud heuristics scattered in services | **Enhance**: consolidate into one documented heuristic registry with per-rule metrics |
| Data analytics & drill-down across surfaces | Admin heat map, KPI targets, metrics registry (ADR-010) | **Enhance**: admin drill-down paths (KPI tile → cohort → ride list → single ride) using existing paginated endpoints; **never** third-party analytics/ad SDKs (product guardrail) |
| Holistic operations view | `/status`, `/kpi`, dashboards | **Build**: one admin "ops cockpit" page composing existing metrics — composition, not new collection |
| Operations analysis | KPI table + SLA table | **Enhance**: weekly automated KPI digest vs targets (existing background-loop pattern) |

## Track D — Data Operations & Reporting

| Item | Exists today | Disposition |
|---|---|---|
| Bulk import | Extensive: driver/rider/booking/wallet/Stripe-mapping import services + runbooks | **Enhance**: unify validation/error-report UX in admin; every import path needs dry-run + row-level error export |
| Bulk export / download | Data Transfer export (ADR-009), corporate exports, T4A, earnings statements | **Enhance**: close B11 (dual-approval) first — export is the highest-exfil-risk surface in this track |
| Reporting module | ADR-008 dual-mode reports, corporate reporting reviewer agent | **Enhance**: report catalog page in admin listing every generatable report + audience |
| Migration tooling | `run_migrations.py`, migration-check CI, migration reviewer agent | **Enhance** only via existing conventions; no new migration framework |
| Layman-readable PRs & change docs | PR template + Change Impact Log (technical) | **Build**: add a one-paragraph "**In plain terms**" field to the PR template/Change Impact Log — what changed, for whom, written for a non-engineer; keeps technical tiers intact |

## Track E — UX, Design & Device Reality

Existing floor: `brand-spinr.md` (colors/typography are **already defined** —
"fresh but muted, calming" proposals must reconcile with the brand system,
not invent a palette per feature), `spinr-design-consistency-reviewer`
(loading/empty/error states mandatory), `spinr-accessibility-reviewer`
(WCAG 2.1 AA floor), and the honest no-visual-regression status (B38).

| Item | Disposition |
|---|---|
| Minimalist, non-overwhelming surfaces | **Enhance**: per-screen simplification cycles; the metric is taps-to-complete and elements-per-screen, reviewed persona-by-persona (admin density ≠ rider simplicity) |
| Gamified tiles / engagement | **Enhance carefully**: loyalty, quests, promotions routes already exist. HARD CONSTRAINT: driver-facing gamification must never nudge toward control-of-work (mandatory shifts, offline penalties) — contractor-classification risk, legal review required (Pillar 5). Rider-side gamification is the safer surface |
| Device/OS/screen-size compatibility, scaling, rendering | **Enhance**: Expo + responsive patterns exist; add a device-matrix test checklist (small/large phones, tablets, OS versions, font-scale 200%, reduced motion) to the UI-change gate |
| Recovery & resilience UX | **Enhance**: poor-connectivity behavior per critical journey (Pillar 8 journey walks); every async action needs retry + offline state, already the design-reviewer's bar |
| Visual regression baseline | **Prerequisite**: seed B38 baselines before large visual work, or every change stays "reasoned about, not screenshotted" |

## Track F — Enablement, Support & Knowledge

| Item | Exists today | Disposition |
|---|---|---|
| AI assistant | **Exists**: `rider-app/app/ai-assistant.tsx`, `backend/ai/`, admin AI console, guardrail reviewer + eval requirement | **Enhance**: extend to driver app + FAQ/knowledge-base retrieval; every new tool follows the `spinr-ai-tool` skill contract; assistant must refuse legal/insurance advice beyond published docs |
| FAQ | Compliance FAQs seeded via migrations 365–367; SK driver FAQ doc | **Enhance**: surface in-app with search; single content source |
| Terms, privacy & legal docs | 23 docs in `docs/legal/`, `legal_documents.py` route, publication-checklist gating + legal-readiness reviewer | **Enhance**: in-app rendering/versioned-consent already conventioned; keep the reviewer gate |
| Knowledge base | Docs exist repo-side; no user-facing KB | **Build**: user-facing KB fed from the same content source as FAQ/AI assistant (one source, three surfaces) |
| Interactive rider/driver onboarding | **Gap verified**: no tutorial/onboarding walkthrough screens in either app | **Build**: first-run guided flows + contextual hints ("?" affordances) per key screen; skippable, re-openable, WCAG-compliant |
| Training material | `agents/roles/` (internal), driver docs scattered | **Build**: driver training module (safety, WAV/service-animal obligations, app usage) — contractor-safe framing: education offered, never mandated shifts/quotas |
| Hints & page-level help | None systematic | **Build**: lightweight help layer (tooltip/coach-mark inventory per screen) — part of the onboarding cycle |

## Track G — Innovation Research (standout, evidence-gated)

| Item | Status | Disposition |
|---|---|---|
| **H3 hexagonal geospatial indexing** | **Verified absent** from the codebase; dispatch is radius-based, surge is per service-area | **Research → likely Build**: strongest candidate in this charter. Evaluate H3 for surge zoning granularity, supply-demand heat mapping, and dispatch candidate pre-filtering. Cycle 1 = benchmark spike (h3-py / h3-js on real Saskatoon/Regina coordinates) + ADR with before/after query cost; only then an implementation plan (additive: H3 columns alongside existing geometry, dual-read window) |
| Other candidates | — | **Research**, one ADR each, only with a named KPI they move: predictive driver positioning (utilization KPI), smarter ETA (cancellation KPI), scheduled-ride pooling for corporate (B2B revenue), WAV supply forecasting (regulatory service level). Reject any candidate that only adds "wow" without a KPI |
| Integration points | Stripe, Twilio, Firebase, Zoho Desk, Meta conversions exist | **Enhance**: integration inventory + health checks; new integrations need the vendor-register/DPA process (Process) |

## Track H — Engineering Hygiene (continuous, already mandated)

Edge cases, error handling, industry best practices are **not a track to
schedule — they are the standing law of every cycle** (Pillars 1, 4, 8:
edge-case reviewer, error-handling conventions, adversary pass). The
charter adds nothing here except the reminder that no track above may
trade them away for speed.

---

## Risk register — what using the swarm itself could break

Your explicit question: *is there risk in using the swarm, and what impact?*
Yes. Named, with mitigations and residuals:

| # | Risk | Impact | Mitigation (in place) | Residual |
|---|---|---|---|---|
| R1 | **Change velocity vs live users** — more changes/week on a live-tested product = more regression surface, regardless of per-change quality | Rider/driver-facing breakage mid-session | Release gates, flags-first, additive-over-destructive, Change Impact Logs | Real. Cap concurrent in-flight tracks (suggest: 2) and keep money/rides/auth changes to one at a time |
| R2 | **Human review bottleneck** — swarm outproduces the human's ability to meaningfully review, especially with automated PR review down (C7/C9) | Rubber-stamped merges; the 4582 merge-within-seconds pattern is the warning sign | Draft PRs, plain-language PR field (Track D), small diffs | Real. Restore one automated reviewer (C9/C7) early; agree a max-open-PR count |
| R3 | **Plausible-but-wrong agent findings** driving unneeded "fixes" | Churn; correct code "fixed" into incorrect code | Adversary pass, epistemic labels, deliberate-decisions checklist in `/spinr-swarm` step 2 | Low-moderate; the labels only help if the human reads them |
| R4 | **Product-identity drift** — gamification, analytics, engagement features decaying into the things Spinr is NOT (driver control, data harvesting, hidden monetization) | Legal reclassification risk (drivers), brand/trust damage, PIPEDA exposure | "Is NOT" filter is a hard gate; Track E/F constraints above; legal review triggers | Moderate on gamification specifically — treat every driver-facing incentive as legal-review-required |
| R5 | **Compliance overreach by new modules** — anomaly detection/analytics ingesting PII, AI assistant answering legal/insurance questions, bulk export widening exfil | P0 privacy incident; wrong advice liability | Never-log list, AI guardrail reviewer + PII scrubbing, B11 gate, assistant refusal rules | Moderate until B11 closes — sequence export hardening *before* export expansion |
| R6 | **Backlog divergence** — swarm inventing a parallel priority universe | Launch-gating P0/P1 work starved by novel charter work | Charter rule: findings file into `ACTION_ITEMS.md`; P0/P1 always outrank charter tracks | Low if the rule is followed; check at each assessment cycle |
| R7 | **Cost/time burn** — fleet reviews + adversary passes are token-expensive | Budget without proportional product gain | One-cycle-per-invocation; assessment-before-implementation; Process items handed to humans | Low; visible per session |
| R8 | **Expectation gap on Process items** — 25 of 57 open items need human dashboard/legal access; the swarm cannot "do" them | Illusion of progress; drills/alerts still unexercised (C1/C5/C2/C4) | Charter labels Process items explicitly; swarm drafts, human executes | Real until the human loop closes them — the scorecard's #1 finding stands |
| R9 | **Doc drift** — charter/framework claims decaying as code moves | Framework loses its "verified truth" property | Date-stamped claims; assessment cycle re-verifies before each track | Low with the re-verify rule |

**Net answer:** the swarm is safe to use *as governed* — every mitigation
above already exists in the framework — but R1/R2/R4 need active handling,
which the operating agreement below makes binding rather than advisory.

## Operating agreement — binding handling for R1/R2/R4

These are standing rules, not suggestions. The swarm enforces its side at
cycle time; the human side is stated so the scorecard can grade it.

**R1 — throughput control (velocity vs live users)**
1. At most **2 charter tracks in flight** at any time.
2. At most **1 open PR** touching a regulated surface (money, rides/dispatch,
   auth, insurance periods, corporate billing, safety) at any time.
3. After a regulated-surface merge: a **24-hour soak window** — watch the
   KPI/SLA tables and Sentry for that domain before the next regulated
   cycle starts. Docs, tests, and dark (flag-off) changes are exempt.
4. Rationale: per-change gates don't control *compounded* regression risk;
   only rate limits do, and a numeric cap is the only "slow down" an
   autonomous loop reliably obeys.

**R2 — review economics (human bottleneck)**
1. At most **3 open swarm PRs**; at the cap, the swarm stops opening new
   ones and works review feedback, docs, or Process drafts instead —
   back-pressure, not pile-up.
2. **Two-tier merge rule**: docs-only and dark-flagged diffs may merge on a
   light pass; a regulated-surface diff requires a real review (reading the
   diff and its Change Impact Log — a same-minute ready-to-merged event on
   such a PR is a scorecard finding, not a convenience).
3. Every code-changing PR carries the **"In plain terms"** paragraph
   (Track D) plus its auditor-agent evidence, so a real review costs
   minutes, not hours.
4. **First Process item worked overall: restore one automated PR reviewer**
   (diagnose C9 or re-key C7) — the cheapest multiplier on review capacity.

**R4 — identity keel (drift prevention)**
1. Every cycle's brainstorm step answers a fixed **transparency test**:
   *can a rider or driver see what this does and why, before it affects
   them?* A feature that fails it is redesigned or dropped — regardless of
   engagement upside.
2. No cycle may weaken a code-enforced transparency invariant (surge cap
   and pre-booking visibility, receipt line-item mapping, no hidden fees,
   append-only audit trails); new user-facing behavior adds to the
   *disclosed* surface, never the hidden one.
3. Any **driver-facing incentive** (quests, streaks, bonuses, tiles) is
   legal-review-required **before its implementation cycle starts**, not
   before merge — by merge, design momentum already exists. Contractor
   autonomy language is checked at proposal time.
4. Every cycle names the **KPI it moves** and what the user will see;
   automation that can't answer both doesn't run. The quarterly scorecard
   re-grade is the drift detector, and P0/P1 backlog always preempts
   charter novelty.

## New features vs enhancements?

Both, deliberately weighted: of the ~45 charter items, roughly **two-thirds
are Enhance** (the platform already has more than the wishlist assumed —
imports, exports, AI assistant, gamification routes, regulator reporting,
PII vault, incident machinery) and **one-third Build/Research** (interactive
onboarding, help layer, knowledge base, access reviews, anomaly layer,
ISO/NIST mapping, notification tracker, ops cockpit, plain-language PR
field, H3). The swarm's assessment cycles will correct these dispositions
against code truth before any build starts.

---

## Standing mission prompt

Use this as the argument to the swarm — it is intentionally short, because
the charter above carries the content:

> `/spinr-swarm charter <track-letter>` — Operate under Pillar 8 on
> Enhancement Charter Track <X> (docs/framework/09-enhancement-charter.md).
> First invocation on a track: assessment cycle only — re-verify the
> "exists today" column, score the gaps, file chosen items into
> ACTION_ITEMS.md, recommend the first implementation cycle. Later
> invocations: one filed item per cycle, full loop, all gates. Honor every
> disposition label (Research ends in an ADR; Process ends in a
> human-ready draft). P0/P1 backlog items preempt charter work. The goal
> is to exceed incumbent ride-share expectations **without** becoming
> busy or overwhelming for riders, drivers, or admins — when a cycle adds
> UI surface, it must also state what it removed or simplified.
