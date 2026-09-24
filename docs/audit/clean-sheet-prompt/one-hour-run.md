# Clean-Sheet Audit — 60-Minute Rapid Baseline Run

This is a time-boxed version of `docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md`
for when you have **one hour**.

**Be clear about what this is:** a rapid baseline — the top findings per area, a
verified standards inventory, a tagging plan, a capacity table, and a roadmap. It is
**not** the full 20-lane programme, which takes several sessions. Everything this run
skips is listed in `DEFERRED.md`, so nothing is silently dropped.

---

## 1. Model plan

| Role | Model (Agent tool `model:`) | Why |
|---|---|---|
| Orchestrator (the session itself) | **Fable** | Planning, merging, synthesis, the final judgment calls |
| Research lanes A2, A3 (web research + code) | `fable` | Weighing new techniques needs judgment |
| Code-audit lanes A4–A8 | `sonnet` | Fast and strong on code reading; keeps the run within the hour |
| Inventory lane A1 | `haiku` | High-volume grep and verification |
| Verifier V (10% re-check) | `opus` | Deliberately a **different** model from the lanes' authors |

If Fable isn't selectable for the session, use Opus as the orchestrator. The plan still
holds.

## 2. Timeline

| Clock | Step | Parallel? |
|---|---|---|
| 0–5 min | Orchestrator reads this file, the master prompt §4–§7, and `standards-and-scale.md`. Uses the §4 defaults (no blocking questions). Creates the output folder | — |
| 5–35 min | **Wave A**: 8 lanes launched in **one message** as background subagents | 8 in parallel |
| 35–45 min | **Wave B**: Architect (top-10 rebuild deltas) and Verifier V (re-checks a random 10% of VERIFIED findings; dedupes against `ACTION_ITEMS.md`) | 2 in parallel |
| 45–55 min | Orchestrator writes `EXECUTIVE_SUMMARY.md`, `ROADMAP.md`, `ESCALATIONS.md`, and `DEFERRED.md` | — |
| 55–60 min | Commit, push, open a draft PR, report back | — |

**Hard rules for the time box:**
- **Lane cap:** each lane gets 25 minutes, at most 12 findings, CRITICAL/HIGH first.
- **Late lanes:** at 35 minutes the orchestrator takes whatever lanes have returned.
  A lane that hasn't returned goes into `DEFERRED.md`. Nobody waits.
- **Single writer:** lanes **return** findings text; only the orchestrator writes files,
  one file per lane. That means no merge conflicts, and read-only reviewer agents work
  too.
- **Web search budget:** at most 6 searches per research lane, primary sources first,
  and every claim cited.
- **Live systems:** use connectors only if they are connected, and read-only. Sentry
  and Stripe MCP connections have failed in recent sessions, so no lane may depend on
  them. Static code and docs come first.
- **Network:** WebFetch to some company domains (e.g. `lyft.com`, `grab.com`, `uber.com`)
  has been blocked by the environment's network policy. If a fetch is blocked, use
  WebSearch results instead, label the claim INFERRED, and list the blocked host in
  `DEFERRED.md`. Don't retry blocked hosts.

## 3. Wave A lanes (all start together)

| Lane | Model | subagent_type | Scope | Rules to load |
|---|---|---|---|---|
| **A1** Inventory & history | haiku | Explore | Verify each row of `standards-and-scale.md` §1 (correct / wrong / partial, with `path:line`). Name the top 5 recurrence families from `ACTION_ITEMS.md` + `docs/change-log/`. Draft an L2/L3 epic list | master §3.0 |
| **A2** Security, crypto & data protection | fable | general-purpose | Map to the OWASP ASVS L2 / API Top 10 / MASVS-L1 headline controls: JWT (HS256), OTP, pgsodium status, secrets, RLS dormancy, PII in logs, cert-pinning decision. Research new techniques from §6 security | `.claude/agents/spinr-security-auditor.md`, `docs/threat-model/` |
| **A3** Transmission, compression, caching & SaaS capacity | fable | general-purpose | Fill the §5 capacity table (limits cited from vendor docs/plans in the repo). Response compression, WS payload size, caching, 10× break points. Research new techniques from §6 transmission. Verify and correct `industry-stack-benchmark.md` against primary sources where the network allows | `.claude/agents/spinr-performance-sla-reviewer.md`, `spinr-cicd-infra-reviewer.md`, `industry-stack-benchmark.md` |
| **A4** Dispatch, state machine & realtime | sonnet | spinr-dispatch-reviewer | Count the state-guard implementations; WS emit on every transition; replay-safety of the 42 loops; offer/timeout races | `spinr-realtime-reliability-reviewer.md` |
| **A5** Money, ledger & CRA | sonnet | spinr-money-auditor | Float leaks, ledger vs Stripe reconciliation, receipt tax lines, payout ≤ collected, CRA items in `sweep-catalog.md` §4.1 (as questions; primary-source status) | `spinr-regulatory-compliance-checker.md` |
| **A6** Observability & tagging | sonnet | spinr-observability-reviewer | Coverage of the `standards-and-scale.md` §4 keys today (Sentry tags, metric names, log fields, table comments, CODEOWNERS); SLA → metric → alert gaps; a tagging rollout plan | CLAUDE.md Observability |
| **A7** Surfaces: rider, driver, admin, website | sonnet | spinr-edge-case-reviewer | The top 10 edge cases from `sweep-catalog.md` §3 across the apps; offline/poor-network behaviour; bundle/startup performance; missing loading/empty/error states; a11y headline issues | `spinr-accessibility-reviewer.md`, `spinr-design-consistency-reviewer.md` |
| **A8** Trust, safety, fraud & privacy | sonnet | spinr-safety-sos-reviewer | SOS end to end; fraud patterns in `sweep-catalog.md` §3.4 (prevent / detect / neither); PIPEDA retention and deletion; insurance periods | `spinr-fraud-auditor.md`, `spinr-insurance-period-auditor.md` |

## 4. Defaults (edit before pasting if you disagree)

- Cities in scope: Regina + Saskatoon. Pooling: out. French: decision deferred.
- Fix-time targets: CRITICAL < 24 h, HIGH < 1 week, MEDIUM next sprint.
- Output folder: `docs/audit/clean-sheet/rapid-baseline-<today>/`.
- Report only: no code, config, or data changes.

## 5. Output files

```
docs/audit/clean-sheet/rapid-baseline-<date>/
├── EXECUTIVE_SUMMARY.md   # plain language, ≤ 2 pages
├── ROADMAP.md             # Now / Next / Later, owner role, flag, rollback
├── ESCALATIONS.md         # decisions for founder / legal / accountant
├── DEFERRED.md            # everything this 60-min run skipped, and why
├── standards-verified.md  # A1: §1 inventory with correct/wrong/partial
├── A2-security.md … A8-trust-safety.md
├── capacity-table.md      # A3: filled §5 table
├── tagging-plan.md        # A6: taxonomy coverage + rollout
├── blueprint-top10.md     # Wave B Architect
└── verification.md        # Wave B Verifier: sample, error rate, dedupe results
```

---

## 6. Kickoff prompt (paste into a NEW Claude Code session on `spinrvm`)

```text
Run the Spinr 60-minute rapid baseline audit. You are the Orchestrator.

Read, in this order and nothing else up front: CLAUDE.md;
docs/audit/clean-sheet-prompt/one-hour-run.md (this plan — follow it exactly);
docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md sections 4–7;
docs/audit/clean-sheet-prompt/standards-and-scale.md.

Constraints: report only — no code, config, or data changes. Read-only connectors
only; don't depend on Sentry/Stripe connectors. No PII in any output. Every finding
uses the master prompt §7.1 card, with an evidence label
(VERIFIED/INFERRED/ASSUMED/PROPOSED/UNKNOWN) and path:line or a cited primary source.
Tax, legal, and regulatory statements without a primary source are ASSUMED and go to
ESCALATIONS.md. Use the defaults in one-hour-run.md §4; don't stop to ask questions.

Minute 0–5: create docs/audit/clean-sheet/rapid-baseline-<today>/ and note the start
time.
Minute 5: in ONE message, launch lanes A1–A8 from one-hour-run.md §3 as background
Agent calls with the model and subagent_type in that table. Give each lane: its scope
row, the rule files to read, "return at most 12 findings in §7.1 card format,
CRITICAL/HIGH first, within 25 minutes; return partial results rather than nothing",
and "do not write files — return text".
As each lane returns, write its output to its own file immediately.
Minute 35: stop waiting. Record any missing lane in DEFERRED.md.
Minute 35–45: in ONE message, launch Wave B in parallel:
  (1) Architect (model fable): top-10 Rebuild Delta cards (master §7.3) from the
      findings, each with KEEP/MODIFY/REPLACE/REMOVE and an incremental, flagged path.
  (2) Verifier (model opus): re-check a random 10% of VERIFIED findings against the
      code, report the error rate, and dedupe everything against ACTION_ITEMS.md.
Minute 45–55: write EXECUTIVE_SUMMARY.md (plain language, for a non-engineer:
top 10 findings, top 5 rebuild moves, what we already do well), ROADMAP.md,
ESCALATIONS.md, DEFERRED.md. Self-check for contradictions and INFERRED presented as
VERIFIED.
Minute 55–60: commit (one commit per ≤ 3 files), push to this session's branch, and
open a draft PR using .github/pull_request_template.md. Then report to me: top 10
findings, the verifier's error rate, the decisions needing me, what was NOT verified,
and the 3 next work items that can run in parallel without touching the same files.
Also say which of the five research sessions in
docs/audit/clean-sheet-prompt/research-sessions.md should run first, and why,
based on these findings.
```
