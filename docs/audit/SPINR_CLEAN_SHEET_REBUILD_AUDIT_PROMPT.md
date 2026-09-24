# Spinr Clean-Sheet Rebuild Audit — Master Orchestration Prompt

**What this is:** a reusable, role-based prompt that makes a Claude session (plus the
existing `spinr-*` reviewer fleet) sweep **every unit of Spinr** — vision down to a single
function — establish the true current state, attack it adversarially, benchmark it
against Uber/Lyft, and answer one question per unit:

> *"If Uber or Lyft had to rewrite this from scratch for Spinr's market, what would they
> keep, what would they do differently, why, how, who owns it, and when?"*

**Sibling prompts (same family, narrower scope — reuse, don't redo):**
`docs/audit/ADMIN_DASHBOARD_AUDIT_PROMPT.md` (admin surface),
`docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md` (map/fare/receipt/notification).
This prompt is the **umbrella** above both — where they already produced a report
(`docs/audit/ride-experience/`, `docs/audit/admin-dashboard/`), consume it as input.

**Companion files (load on demand, not all at once — token discipline):**

| File | Load when |
|---|---|
| `docs/audit/clean-sheet-prompt/roles.md` | Launching any role/lane — every role's charter, adversary stance, owned files, mapped `spinr-*` agent |
| `docs/audit/clean-sheet-prompt/sweep-catalog.md` | Doing the actual sweep — domain checklists, ride-share edge-case catalog, CRA/regulatory checks |
| `docs/audit/clean-sheet-prompt/operating-model.md` | Phase 6 — weekly audits, issue/PR/CR lifecycle, incident loop, agent loops, access guardrails |
| `docs/audit/clean-sheet-prompt/greenfield-extensions.md` | Phases 3–5 and W4/W5 lanes — required matrices, feature-completeness dimensions, invariants, simulation/chaos, compliance-as-code, agent control plane, dispute prevention, keep/modify/replace/remove, model routing, decision tie-breakers |
| `docs/audit/clean-sheet-prompt/standards-and-scale.md` | Inventory of techniques/standards in place, gaps, standards to measure against (ASVS, MASVS, NIST CSF, SLSA, …), the tagging taxonomy, per-SaaS capacity tripwires, new techniques to research |
| `docs/audit/clean-sheet-prompt/one-hour-run.md` | **Only one hour?** A time-boxed rapid baseline: model plan, 8 parallel lanes, timeline, kickoff prompt |
| `docs/audit/clean-sheet-prompt/research-sessions.md` | After the baseline — five parallel 1-hour research sessions (dispatch, pricing/payments/CRA, trust & safety, mobile & realtime, security/data/infra), each producing a techniques radar |
| `docs/audit/clean-sheet-prompt/industry-stack-benchmark.md` | What Uber/Lyft/Grab run vs Spinr's stack (INFERRED, secondary sources), primary licence-page sources to verify, and a keep/modify/assess/trial/hold verdict per pattern |

**Framework alignment:** `audit-framework/ground-rules.md` (evidence + severity rules),
`audit-framework/dimensions/01–24`, `audit-framework/modules/*`, `CLAUDE.md` (release
gates, conventions — they override anything here if the two disagree).

---

## 0. How to run this (read first)

1. **This is report-and-recommend, not fix.** No code changes during Phases 0–5.
   Per `ground-rules.md`: do not silently fix. Findings become `ACTION_ITEMS.md` entries
   and PRs *after* the report is accepted.
2. **Run in waves of parallel lanes** (§6). Each lane writes to **its own output file
   only** — no two lanes ever write the same file, so there are no merge conflicts.
3. **Every claim needs evidence** — `path:line`, a commit SHA, a PR/issue number, a
   migration filename, or a primary-source URL for regulatory/tax claims. "Looks fine"
   is not a finding; neither is a tool's own output standing in for verification.
4. **Read-only access only** (see `operating-model.md` §5). No production writes, no
   Stripe/Supabase mutations, no deploys, no Sentry resolves.
5. **Budget:** one orchestrator session + ≤ 8 concurrent subagents per wave (the repo's
   default workflow-size guideline). Delegate heavy reading; carry forward summaries, not
   file dumps.

---

## 1. Mission, audience, deliverable

| Item | Value |
|---|---|
| **Goal** | A single, evidence-backed picture of Spinr as-is, plus a clean-sheet "Spinr v-Next" blueprint that is at parity with Uber/Lyft on table-stakes and ahead where Spinr's model (0% commission, Saskatchewan-first, corporate SaaS) gives it an edge |
| **Audience** | Founder/product owner (decisions), engineering (roadmap), ops/support (runbooks, KB), legal/finance (compliance, CRA) |
| **Deliverable** | `docs/audit/clean-sheet/` — see §8 layout. One `EXECUTIVE_SUMMARY.md` (≤ 2 pages, plain language) on top of domain reports, a traceability matrix, and a phased roadmap |
| **Not the deliverable** | A rewrite. The blueprint says *what* a rebuild would change and *which of those changes are worth doing incrementally now*. Most value lands as incremental fixes, not a rewrite |

---

## 2. Unit-of-analysis hierarchy (the "every single unit" requirement)

Every item found is placed in exactly one node of this tree, and every node links up and
down. This is what makes the sweep exhaustive **and** navigable.

```
L0 Vision / North star          ("fair ride-share: driver keeps 100%")
 └ L1 Objective / KPI           (match rate ≥ 85%, payment success ≥ 99% — CLAUDE.md KPI table)
    └ L2 Epic                   (e.g. "Ride booking", "Driver payouts", "Corporate billing")
       └ L3 Feature / Capability(e.g. "Fare estimate", "Scheduled ride")
          └ L4 User story       ("As a rider, I see the full price incl. surge before I book")
             └ L5 Scenario      (happy path + every edge/failure case from sweep-catalog §3)
                └ L6 Task / Sub-task / Code unit
                                (route, service fn, migration, screen, hook, loop, workflow)
                   └ L7 Evidence (path:line, test, SHA, PR, incident, change-log entry)
```

**Seed the L2 epic list from real code, not memory:** `docs/PRD.md`,
`docs/audit/module-map.md`, `backend/routes/` (39 entries), `backend/routes/admin/`,
`backend/services/`, `backend/core/lifespan.py` `_WATCHDOG_LOOP_NAMES` (42 loops),
`rider-app/app/`, `driver-app/app/`, `admin-dashboard/`, `.github/workflows/`.

**Orphan rule:** a code unit with no L4 story above it is either dead code, undocumented
scope, or a missing requirement — each is a finding. A story with no code unit below it
is an unbuilt or silently dropped requirement — also a finding.

---

## 3. Phases

| # | Phase | Output | Exit criterion |
|---|---|---|---|
| 0 | **History mining** — how did we get here | `00-history.md` | Recurring-bug families named with evidence (see §3.0) |
| 1 | **Current-state inventory** — what exists | `01-inventory/` + `traceability.csv` | Every L6 unit mapped to an L4 story or flagged orphan |
| 2 | **Adversarial review** — what breaks | `02-findings/<domain>.md` | Every domain in `sweep-catalog.md` §2 swept; every edge case in §3 marked Handled / Partial / Unhandled with evidence |
| 3 | **Industry benchmark** — where we stand | `03-benchmark.md` | Each L3 feature scored Maturity 1–5 + GREEN/YELLOW/RED vs Uber/Lyft (Dimension 24) |
| 4 | **Clean-sheet blueprint** — what a rewrite would change | `04-blueprint.md` | Each L2 epic has a Rebuild Delta card (§7.3) |
| 5 | **Synthesis & roadmap** | `EXECUTIVE_SUMMARY.md`, `ROADMAP.md` | Findings de-duplicated against `ACTION_ITEMS.md`; phased plan with owners + rollback |
| 6 | **Operating model** — keep it good | `06-operating-model.md` | Weekly audit cadence, lifecycle SLAs, agent loops, access guardrails defined (`operating-model.md`) |

### 3.0 Phase 0 — history mining (do this first; it decides where to dig)

Sources, in order: `ACTION_ITEMS.md` (open `[ ]` and closed `[x]`), `docs/change-log/`
(~1,470 entries), `docs/incidents/`, `docs/audit/` (prior audit reports), `git log`
(note: the session clone may be shallow — say so, and use the GitHub MCP
`list_commits`/`search_pull_requests` for full history), closed PRs, `[CR]` issues,
`docs/known-forks.md`, `.claude/context/memory.md`.

Produce:
1. **Recurrence families** — the same root cause fixed piecemeal more than once (known
   example: float-on-NUMERIC money bugs across B28/B29/B30/B35/B36; CarMarker fork
   divergence ×5). For each: occurrences, files, why the systemic fix never landed.
2. **Hotspot map** — files with the most fix commits / change-log entries per surface.
3. **Decay signals** — gates that went silently red or dormant (e.g. Codex review silent
   since 2026-07-30, `claude-review.yml` off by design, RLS policies dormant per C108).
4. **Doc-vs-code drift** — every place a doc (CLAUDE.md, PRD, runbook) states something
   the code contradicts. CLAUDE.md has already corrected itself ≥ 6 times; assume more.

---

## 4. Ground rules for every role

- **Severity** (`audit-framework/templates/run-audit.md`): CRITICAL / HIGH / MEDIUM / LOW /
  PASS / RECOMMENDATION. Quantitative priority = severity × blast radius × likelihood
  (`ground-rules.md` §6).
- **Competitive position** (Dimension 24): Maturity 1–5 + Gap GREEN/YELLOW/RED. Report
  both where both apply — a maturity-4 feature can still carry a CRITICAL defect.
- **Evidence label on every statement:** VERIFIED (observed directly — read the code
  path, ran it, or cited a primary source), INFERRED (reasoned from evidence, not
  executed), ASSUMED (needed to proceed; must be confirmed by a human or primary
  source), PROPOSED (future-state recommendation), UNKNOWN (must be obtained — list who
  can answer). Never let INFERRED or ASSUMED read as VERIFIED.
- **Tie-breakers** when two goals conflict: reliable over clever; understandable over
  feature-rich; controlled automation over autonomous; safe over fast; operationally
  robust over elegant; understand why incumbents do it before copying or rejecting it;
  **incremental on the live product over rewrite** (Spinr is in live testing).
- **Regulatory/tax/legal claims** must cite a primary source (CRA, SGI, Government of
  Saskatchewan, OPC, municipal bylaw). If none was fetched, the claim is ASSUMED and
  goes to the legal/finance escalation list — never presented as settled.
- **De-duplicate before filing:** grep `ACTION_ITEMS.md` for the ID/keyword first. A
  re-found open item is evidence the fix is stuck — report *why*, don't re-file it.
- **PII:** never paste real rider/driver data, phone numbers, GPS coordinates, or
  addresses into any output (CLAUDE.md PIPEDA list).
- **Forks:** any file in `docs/known-forks.md` is audited together with its sibling.

---

## 5. The adversary mandate

Each role runs three passes over its domain, in this order:

1. **Steelman** — what does the current design get right, and why was it built this way?
   (Read the ADRs, change-log, and code comments first. Many "weird" choices are
   deliberate — e.g. the 3.5 s fare-estimate wait is an accepted SLA exception.)
2. **Attack** — as each adversary in `roles.md` §3 (fraudster, abusive rider, colluding
   driver, malicious insider, flaky network, regulator, auditor, plaintiff's lawyer,
   competitor), how do I break it, cheat it, or make it look bad?
3. **Rebuild** — if I owned this from zero with today's knowledge, what would I do the
   same, different, or delete — and is the delta worth it *now* vs in a rewrite?

A finding without a proposed remediation and a stated alternative ("chose X over Y
because …") is incomplete (CLAUDE.md gate 10).

---

## 6. Execution waves (parallel lanes, no file overlap)

Each lane = one role (or two related roles) = one subagent = one output file.
Lanes in the same wave share no output file and no write target.

| Wave | Lanes (run concurrently) | Depends on |
|---|---|---|
| **W0** | Historian (Phase 0) · Cartographer (Phase 1 inventory + `traceability.csv`) | — |
| **W1** | Product Strategist · Rider Journey Owner · Driver Journey Owner · Corporate/B2B Owner · Admin/Ops Owner | W0 |
| **W2** | Dispatch & State Machine · Payments/Money & CRA · Security & Data Protection · Trust, Safety & Fraud · Compliance & Regulatory | W0 |
| **W3** | Reliability/SRE & Observability · Quality/Test · Integrations & Vendors · Support/KB/Training · UX & Accessibility | W0 (W1/W2 read-only) |
| **W4** | Chief Architect (blueprint) · Competitive Analyst (benchmark) | W1–W3 |
| **W5** | Synthesizer (exec summary, roadmap, dedupe) · Operating-Model Designer | W4 |

W1, W2, and W3 can run in any order or together once W0 is done — they read the same
inputs and write disjoint files. Map each lane to its existing `spinr-*` agent(s) per
`roles.md` so the lane reuses that agent's domain rules instead of re-deriving them.

---

## 7. Output card formats (every lane uses exactly these)

### 7.1 Finding card

```markdown
### <DOMAIN>-<NNN> — <one-line title>
- Hierarchy: L2 <epic> › L3 <feature> › L4 <story id> › L5 <scenario>
- Severity: <CRITICAL|HIGH|MEDIUM|LOW|RECOMMENDATION>   Priority score: S×B×L = <n>
- Status: <VERIFIED|INFERRED|ASSUMED>   Existing item: <ACTION_ITEMS id or "new">
- Adversary: <which persona breaks it>
- Evidence: `path:line`, SHA/PR, incident, test name
- What happens (plain language): <what a rider/driver/admin experiences>
- Root cause: <why, not the symptom>
- Recommendation: <fix>   Alternative considered: <Y> — rejected because <reason>
- Blast radius: <other callers/readers — grep result, not "looks fine">
- Rollout: <flag? migration? additive?>   Rollback: <not "git revert" for live data>
- Verification to close: <test/command/manual step>
```

### 7.2 Scenario card (for L5 edge cases)

```markdown
| Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status (Handled/Partial/Unhandled) | Dispute risk |
```

### 7.3 Rebuild Delta card (Phase 4, one per L2 epic)

```markdown
## Epic: <name>
- Verdict per inherited pattern: KEEP / MODIFY / REPLACE / REMOVE — with evidence
- Keep (already best-in-class): …
- Uber/Lyft do: … (source)       Spinr today: … (evidence)
- Clean-sheet Spinr would: …      Why (the edge it creates): …
- How (architecture/pattern): …   Who (role/owner): …   When (Now / Next / Later / Rewrite-only)
- Incremental path from today (no big-bang): step 1 → 2 → 3, each shippable & flagged
- Cost/effort (S/M/L) · Risk · Reversibility · Build / Buy / Partner / Open source
- Advantage type: feature (easy to copy) / data / operational / network / cost / trust — is it defensible?
- "Why not?": why doesn't this exist already; could something simpler get the same result?
```

---

## 8. Deliverable layout (created by the executing session)

```
docs/audit/clean-sheet/
├── EXECUTIVE_SUMMARY.md       # W5 — plain language, ≤ 2 pages, top 10 decisions
├── ROADMAP.md                 # W5 — Now/Next/Later, owners, flags, rollback
├── 00-history.md              # W0 Historian
├── 01-inventory/epics.md      # W0 Cartographer — L0–L4 tree
├── traceability.csv           # W0 Cartographer — L2..L7, one row per code unit
├── 02-findings/<domain>.md    # W1–W3, one file per lane
├── 03-benchmark.md            # W4 Competitive Analyst
├── 04-blueprint.md            # W4 Chief Architect — Rebuild Delta cards
├── 05-escalations.md          # W5 — legal/finance/founder decisions only a human can make
├── matrices/                  # Tier A required, Tier B if time — greenfield-extensions.md §2
└── 06-operating-model.md      # W5 Operating-Model Designer
```

`traceability.csv` columns: `epic,feature,story_id,story,scenario,code_unit,path,test,evidence,status,finding_ids,maturity,gap`.

---

## 9. The master prompt (paste this to start)

```text
You are the Orchestrator for the Spinr Clean-Sheet Rebuild Audit.

Read, in order: CLAUDE.md; docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md (this
file); docs/audit/clean-sheet-prompt/roles.md. Load sweep-catalog.md and
operating-model.md only when a lane needs them.

Rules: report-and-recommend only — no code, config, or data changes. Read-only
connectors only. Every claim cites evidence; every regulatory/tax claim cites a
primary source or is marked ASSUMED. De-duplicate against ACTION_ITEMS.md. No PII in
any output. Each lane writes only its own file under docs/audit/clean-sheet/.

Step 1 — Frame: restate the goal, audience, and deliverable in 5 lines. List any
decision that is genuinely mine (the user's) and ask it via AskUserQuestion before W0.
Step 2 — Run wave W0 (Historian, Cartographer) as parallel subagents, each with its
role card from roles.md. Summarize their output in ≤ 30 lines before continuing.
Step 3 — Run W1, W2, W3 (≤ 8 concurrent subagents per wave). Give each lane: its role
card, its sweep-catalog sections, its mapped spinr-* agent(s), the W0 summary, and its
single output path. Require the §7 card formats.
Step 4 — Run W4 (Architect + Competitive Analyst) over W1–W3 output.
Step 5 — Run W5: synthesize, de-duplicate, score, write EXECUTIVE_SUMMARY.md (plain
language for a non-engineer), ROADMAP.md, 05-escalations.md, 06-operating-model.md.
Step 6 — Self-correct: assume the blueprint is wrong. Re-review it as a hostile
competitor, driver, rider, fraudster, regulator, SRE, CFO, and COO. Re-verify a random
10% sample of VERIFIED findings with a different model/agent than the author and report
the error rate. Fix contradictions, unsupported claims, and scope creep before reporting.
Step 7 — Report to me: top 10 findings, top 5 rebuild deltas, escalations needing my
decision, what was NOT verified, and the next 3 work items that can run in parallel
without touching the same files. End by answering: if Uber/Lyft had to rebuild from
scratch tomorrow, what would a Spinr-native platform do differently — structurally,
economically, operationally, technically, experientially — split into obvious,
meaningful, difficult, genuinely novel, and "sounds good but should NOT be built".
```

---

## 10. What was NOT verified while writing this prompt

- Regulatory and tax statements in `sweep-catalog.md` §4 are **checklist questions for
  the executing session to verify against primary sources** — they were not fetched
  from CRA/SGI/municipal sources while this prompt was written.
- Counts quoted here (39 route entries, 42 loops, ~1,470 change-log files, 28 agents)
  were read from the repo on 2026-09-24 and will drift — re-count, don't trust.
- No competitor feature claims were verified; the Competitive Analyst must cite sources.

## 11. Maintenance

Re-run Phase 0 + Phase 2 quarterly, or after any launch in a new city. Re-run a single
domain lane whenever that domain has a CRITICAL incident. When a lane's checklist gains
an item from a real incident, add it to `sweep-catalog.md` with the incident link.
