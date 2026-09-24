# Clean-Sheet Audit — Greenfield Extensions

Companion to `docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md`. On 2026-09-24 we
reviewed an external 90-section "greenfield ride-hailing rebuild" master prompt. This
file keeps the parts that add value and adapts them to Spinr. It also records what we
deliberately left out, and adds items that neither prompt covered.

**Why we adapted it instead of pasting it in:** the external prompt covers a lot of
ground but isn't specific to Spinr. It cites no repo paths, knows nothing of the
existing `spinr-*` agents or `ACTION_ITEMS.md`, and gives no execution plan. It also
asks one context to play ~80 roles across 90 sections and produce 18 matrices at once.
In practice that yields the generic, hand-waved document the prompt itself forbids.
Its ideas are useful when each lane applies them to its own scope.

---

## 1. Model routing (which model runs which lane)

| Work | Model tier | Why |
|---|---|---|
| Orchestrator, Chief Architect (R19), Synthesizer (R20), adversarial "Attack" passes, self-critique | Highest-reasoning model available in the session (e.g. Fable or Opus class) | Judgment, trade-offs, and synthesis across lanes |
| Inventory and breadth sweeps (R2 Cartographer, grep-heavy checklists) | Fast/cheaper model via `Explore` agents | High volume, low judgment; saves budget for reasoning lanes |
| Domain lanes W1–W3 | Mid or high tier, running each lane's mapped `spinr-*` agent | Domain rules already live in the agent files |
| 10% re-verification sample (master prompt, Step 6) | **A different model** from the one that wrote the finding | Reduces correlated errors: the same model tends to repeat its own mistakes |

The structure matters more than which model you pick. Any model given the whole
programme in one prompt will skim it.

---

## 2. Required matrices (tiered — don't try to build all 18 at once)

**Tier A: required** (write to `docs/audit/clean-sheet/matrices/`):

| Matrix | Owner lane | Seed from |
|---|---|---|
| Requirements traceability | R2 | `traceability.csv` |
| Feature completeness (§3) | R2 + domain lanes | traceability rows |
| Edge-case matrix (§4) | R4, R5, R8, R9, R11 | `sweep-catalog.md` §3 |
| Risk register | R20 | all findings |
| Threat model | R10 | `audit-framework/dimensions/21-threat-model.md` |
| Data classification | R10, R12 | `docs/data-classification.md` |
| Ownership (feature/service/loop/runbook → owner) | R20 | CODEOWNERS, runbooks |
| Operational readiness (§7) | R13 | `docs/runbooks/` |

**Tier B: build if time allows.** Integration, API inventory, event/WS inventory, agent
permission (§6), dependency graph, test coverage, documentation coverage, compliance,
incident, and tech-debt register (prioritized by impact × probability × cost of delay).

---

## 3. Feature completeness dimensions

Every L3 feature gets one row. Each cell is Yes / No / N/A with a reason; blank cells
are not allowed.

`UX · API · backend · DB · events/WS · authz · audit log · privacy · compliance ·
analytics · metric+alert · tests (real, not stubbed) · support article · docs ·
training · incident runbook · rollback/flag · owner`

A feature isn't complete just because its UI works.

---

## 4. Edge-case chain (extends the Scenario card)

Every edge case is traced end to end, not just listed:

`TRIGGER → DETECTION → SYSTEM STATE → USER EXPERIENCE → BUSINESS RULE → RECOVERY →
ESCALATION → AUDIT RECORD → TEST THAT PROVES IT`

Add three questions to every distributed step (R8, R9, R13):
- Can this run twice?
- What happens if the network fails after it succeeds?
- What happens if events arrive duplicated or out of order, and how is a partial
  success compensated?

---

## 5. Invariants → property-based tests

Candidate properties to encode. Each one is VERIFIED only when a real test exists.
The Schemathesis GET-only pilot (`test_schemathesis_fuzz.py`) is the precedent.

- The ledger balances: no money is created or lost across charge → refund → payout.
- Payout per ride ≤ amount collected for that ride; tips are never double-paid.
- A rider has at most one active ride.
- A trip can't reach `cancelled` after `in_progress`, and there are no unknown statuses.
- `is_available ⇒ is_online`.
- Insurance Period 3 ⇒ linked `in_progress` ride; period rows are append-only.
- Applied surge ≤ 2.5× on every fare path, and 1.0× on corporate-paid rides.
- A completed trip's fare is never changed silently (only through an audited adjustment).
- A non-admin actor can never read or mutate another user's ride, wallet, or documents.

---

## 6. Agents: control plane and permission matrix

This covers both **product AI** (`backend/ai/`) and **dev agents** (`.claude/agents/`,
`agents/`). For each agent, record: objective, tools, permissions (read / propose /
write), data it can see (PII?), budget/rate limit, approval gate, kill switch, audit
trail, eval suite, and failure modes.

Rule: no agent has more authority than its job needs, and every consequential action
has a human approval gate and a way to undo it. "Use an LLM" is never a design: name
the tools, permissions, and evaluation.

---

## 7. Readiness definitions (map to CLAUDE.md gates)

| Definition | Must be true |
|---|---|
| Ready | Story + acceptance criteria incl. failure states; blast radius named; alternative considered |
| Done | Real tests; Change Impact Log; reviewer agent run; prod build run for app changes |
| Operationally ready | Metric + alert, runbook, support article, owner, training note, rollback/flag |
| Safe to release | Flag dark → staging → canary checks passed; post-release verify step named |

After release: `VERIFY → COMPARE TO BASELINE → ERRORS → BUSINESS METRICS → UX →
OPS/SAFETY → CLOSE OR ROLL BACK`. A deployment finishing doesn't mean the feature works.

---

## 8. Dispute prevention (design out ambiguity instead of refunding later)

For each dispute category, find where the ambiguity starts and remove it before the
trip. Categories include surprise fee, cancellation fee, wrong pickup, route taken,
wait time, cleaning fee, lost item, rating, and payment mismatch.

Examples to evaluate: an all-in price before booking; a visible cancellation-fee
countdown; pickup-pin confirmation with a street-side hint; a visible wait timer for
both parties; route explanations; photo evidence for fees; a trip PIN for the right
rider.

**New experience KPIs** to propose (unmeasured today; mark ASSUMED until built):
contacts per 100 trips, surprise-charge rate, dispute rate by category, post-trip CSAT.

---

## 9. Simulation and chaos (sized for Spinr's scale)

- **Proportionate now:** a replay harness that runs anonymized historical trips
  through changed dispatch/surge/fare code before merge, plus chaos drills. The
  drills break Supabase, Redis, Stripe, Twilio, Maps, FCM, the WS connection, and GPS,
  and verify each degrades gracefully. Build on `loadtest/` and the runbooks.
- **Later:** a full marketplace simulator or city digital twin. Mark it
  "Rewrite-only / Later" unless the city count or dispatch complexity justifies it.

---

## 10. Compliance-as-code

`POLICY → IMPLEMENTATION → TEST → MONITOR → AUDIT → EVIDENCE`. First candidates:
- the document-expiry gate on `go_online`
- the surge cap
- the retention/purge jobs
- tax lines on receipts
- insurance-period logging
- consent version on signup

Record the city/province rule set as **configuration** (service area → rules), so
Regina, Saskatoon, and a future province don't need code forks.

---

## 11. Blueprint verdicts (R19)

- KEEP / MODIFY / REPLACE / REMOVE for every inherited pattern, with evidence.
- CURRENT → PROBLEM → ROOT CAUSE → PROPOSED → WHY → TRADE-OFF → IMPLEMENTATION →
  MEASUREMENT for every change.
- Build / buy / partner / open source per subsystem (lock-in, cost, ops burden).
- An explicit **"sounds good, should NOT build"** list. Screen each idea against
  "What Spinr Is NOT", scale, and complexity.
- A formal deprecation lifecycle: still needed, used, safe, economical — or remove.

---

## 12. Gaps neither prompt covered (added here)

Each lane checks these as additional sweep items:

| Item | Lane |
|---|---|
| Driver fatigue / hours-online limits and rest nudges (safety without control-of-work) | R5, R11, R12 |
| Winter operations mode: storm/−40 °C pickup rules, stranded-rider priority, battery-saver | R4, R5, R13 |
| Insurance claim workflow after a collision (first notice of loss, evidence pack, SGI hand-off) | R11, R12 |
| Law-enforcement / subpoena data-request process and a transparency report | R10, R12 |
| Fairness audit: dispatch, ETA, and pricing outcomes by neighbourhood | R8, R9, R18 |
| Driver earnings transparency: per-trip breakdown, weekly statement, true hourly after costs | R5, R9 |
| Deaf/hard-of-hearing drivers; seniors and low-digital-literacy riders | R17 |
| Rural connectivity: offline trip completion and fare reconciliation after reconnect | R8, R13 |
| App-store policy compliance (payments, background location, account deletion) | R14, R12 |
| Vendor exit plan per critical vendor (how we'd switch in 30 days) | R15 |
| Cost of the audit itself: token/time budget per wave, stop rules | Orchestrator |
| Audit quality: precision of findings on the 10% re-verification sample | R20 |

---

## 13. What we deliberately did NOT adopt from the external prompt

- **Its 11-state ride machine** (REQUESTED … RIDER_VERIFIED … ARRIVING … SETTLED) as a
  mandate. It's an input for R8/R19 to evaluate against the real machine in
  `backend/models/ride_status.py`. Adding states changes a live-tested surface, so it
  needs gate 4's dry run and a migration plan.
- **Greenfield by default.** Spinr has live users, so rebuild ideas must show an
  incremental, flagged path.
- **Pooling, FX, multi-country fleets, and a generic 90-day plan** are out of scope
  unless `docs/PRD.md` includes them. The external prompt itself warns that a plan with
  no staffing assumptions gives false precision.
- **Playing ~80 roles in one context.** Replaced by 20 lanes with separate contexts
  (`roles.md`).
