# Handoff — Automation & Doc-Integrity Hardening

_Created 2026-08-01. Source: multi-lens architecture/security/process review run on branch `claude/run-status-w3fxp1`._

This document contains **copy-paste-ready prompts** for executing a set of small,
independent fixes. Each phase is written to be pasted into a **fresh Claude Code
session with no prior context** — every prompt restates the evidence it needs.

---

## How to use this document

1. Open a new session.
2. Copy **one** phase prompt verbatim into it (the fenced block under "Prompt").
3. Let it complete, review the diff, merge.
4. Move to the next phase.

**Do not paste multiple phases into one session.** They are deliberately scoped to
one logical change each, per the `CLAUDE.md` rule "one logical change per commit".

Phases 1–4 are independent and may run **in any order or in parallel branches**.
Phases 5–6 are research/design deliverables. Phases 7–10 are follow-on work.

---

## Global rules that apply to every phase

Paste-ready block — already embedded in each prompt below, repeated here for reference:

- Work on the branch your session designates. If none is designated, create
  `claude/<short-slug>` off the latest `main`.
- Follow `CLAUDE.md`. Notably: subtasks of ≤3 files, one logical change per commit,
  commit before starting the next subtask.
- **Phases 1–4 and 7–10 touch tooling, docs, and CI only — they do NOT touch any
  live-tested runtime surface (rides, dispatch, payments, auth, corporate, safety).**
  A Change Impact & Risk Log entry is therefore **not** required for them. Do not
  invent one. (Phases 5–6 produce documents only and likewise need none.)
- Do not modify ride state-machine code in `backend/routes/rides/` in any of these phases.
- Do not "fix" adjacent things you notice. Note them and report at the end.

---

## Why these phases exist (one paragraph of context)

A review found that Spinr's documentation is unusually thorough **and** drifting
from the code faster than it is corrected. Because the team relies heavily on
AI-assisted development — and every AI session reads `CLAUDE.md` and
`.claude/context/*.md` — doc accuracy is a **production dependency**, not hygiene.
Every phase below either corrects a verified drift or adds automation that makes
that class of drift fail loudly in future.

---

# Phase 1 — Sprint staleness guard (SessionStart hook)

**Priority: highest. Effort: XS.**

### Verified evidence

- `.claude/hooks/session-start.sh` extracts the "## Sprint goal" line from
  `.claude/context/sprint-current.md` and prints it as **"🚕 Spinr sprint in flight"**
  unconditionally.
- `.claude/context/sprint-current.md` was last status-updated **2026-05-06** and its
  body states **"Sprint COMPLETE"**.
- Today is 2026-08-01. The hook therefore announces a ~3-month-old completed sprint
  as the active goal at the start of every session.
- This actively misdirected a real session on 2026-08-01.

### Prompt

```
Fix a stale-context bug in the Spinr SessionStart hook.

BACKGROUND (verified 2026-08-01):
`.claude/hooks/session-start.sh` reads `.claude/context/sprint-current.md`,
extracts the line under "## Sprint goal", and prints it under the banner
"🚕 Spinr sprint in flight" every time a session starts. It has no staleness
check. The sprint file was last status-updated 2026-05-06 and its body says
"Sprint COMPLETE", so every session currently opens by announcing a finished,
~3-month-old sprint as the live objective. This has already misdirected a session.

TASK:
Modify `.claude/hooks/session-start.sh` so that it distinguishes a live sprint
from a stale one before printing.

REQUIREMENTS:
1. Treat the sprint as STALE if either is true:
   a. The file's last git-commit date is more than 21 days ago
      (prefer `git log -1 --format=%ct -- <file>`; fall back to filesystem mtime
      if git is unavailable, and do not let either failure abort the hook).
   b. The file body contains "Sprint COMPLETE" (case-insensitive).
2. If STALE, print a warning banner instead of the in-flight banner. It must state
   the age in days, say the priorities may be stale, and point at ACTION_ITEMS.md.
   Example shape:
     ⚠️  Spinr sprint context looks stale (87 days old, marked COMPLETE)
         Priorities may be out of date — check @ACTION_ITEMS.md for open [ ] items.
3. If NOT stale, keep the existing "🚕 Spinr sprint in flight" behaviour byte-for-byte.
4. Preserve every existing early-exit path: missing file, template-only content,
   and empty goal must all still exit 0 silently.
5. The hook must never fail a session. Keep `set -euo pipefail` but ensure the
   date/git lookups cannot cause a non-zero exit.

CONSTRAINTS:
- Only `.claude/hooks/session-start.sh` changes. Do not edit sprint-current.md,
  settings.json, or any other file.
- This is tooling only, touches no live-tested runtime surface, so NO Change Impact
  & Risk Log entry is required. Do not create one.

VERIFY BEFORE COMMITTING:
- `bash .claude/hooks/session-start.sh` with the current (stale) file prints the
  WARNING form.
- Temporarily test the fresh path (e.g. a copy of the file with "Sprint COMPLETE"
  removed and a recent date) prints the ORIGINAL in-flight form. Revert any test edit.
- `bash -n .claude/hooks/session-start.sh` passes.

Commit with a conventional-commit message and report what you changed.
```

### Acceptance
- Stale file → warning banner naming age + pointing to `ACTION_ITEMS.md`.
- Fresh file → original banner unchanged.
- All existing silent-exit paths intact.

---

# Phase 2 — Correct two factual errors in CLAUDE.md

**Priority: high (one is a security control). Effort: XS.**

### Verified evidence

| Claim in `CLAUDE.md` | Reality (verified) |
|---|---|
| Line 217: "access tokens: 15 min (rider/driver), **12 hr (admin)**" | `backend/core/config.py:55` → `ADMIN_ACCESS_TOKEN_TTL_HOURS: int = 1`. The rider/driver value is correct (`ACCESS_TOKEN_EXPIRE_MINUTES: int = 15`, config.py:52). **Only the admin figure is wrong.** |
| Observability section: "`utils/metrics.py` is the source of truth" for `spinr_*` metric names | `backend/utils/metrics.py` is a 186-line generic in-process counter registry and defines **no** `spinr_*` names. Actual metric names are emitted at call sites: `services/dispatch_service.py`, `services/payment_service.py`, `utils/stripe_reconcile.py`, `services/data_transfer/observability.py`. |

`.claude/context/sprint-current.md` records A-P0-2 as "Admin access token TTL 1 h
confirmed end-to-end" — i.e. the sprint file was right and `CLAUDE.md` was never updated.

### Prompt

```
Correct two verified factual errors in Spinr's CLAUDE.md.

ERROR 1 — Admin token TTL (security control stated incorrectly)
CLAUDE.md line 217 currently reads:
  "**Token lifetimes** — access tokens: 15 min (rider/driver), 12 hr (admin)."
The actual value in code is 1 hour: `backend/core/config.py:55` defines
`ADMIN_ACCESS_TOKEN_TTL_HOURS: int = 1`. The 15-minute rider/driver figure IS
correct (`ACCESS_TOKEN_EXPIRE_MINUTES: int = 15`, config.py:52) — do not change it.
`.claude/context/sprint-current.md` confirms ticket A-P0-2 reduced the admin TTL
to 1h and it shipped in PRs #95/#97; CLAUDE.md was simply never updated.

Fix: change "12 hr (admin)" to "1 hr (admin)".

Before editing, re-read `backend/core/config.py` around line 55 and confirm the
value is still 1. If it is NOT 1, stop and report the discrepancy instead of guessing.

ERROR 2 — metrics.py "source of truth" claim
The Observability Conventions section states that `utils/metrics.py` is the source
of truth for the `spinr_<domain>_<metric>_<unit>` metric names. It is not:
`backend/utils/metrics.py` is a generic in-process counter/gauge registry that
defines no `spinr_*` names at all. The names are emitted at their call sites —
verified in `backend/services/dispatch_service.py`,
`backend/services/payment_service.py`, `backend/utils/stripe_reconcile.py`, and
`backend/services/data_transfer/observability.py`.

Fix: reword so it says the exposition format and helpers live in
`utils/metrics.py` while the metric names themselves are defined at their emitting
call sites. Keep the existing list of canonical metric names and keep the existing
warning that the older dotted `spinr.<domain>...` spelling is NOT what the code emits.

CONSTRAINTS:
- Only CLAUDE.md changes. Do not modify config.py, metrics.py, or the sprint file.
- Make these two edits as ONE commit (both are "correct CLAUDE.md to match code").
- Docs only, no live-tested runtime surface touched, so NO Change Impact & Risk Log
  entry is required. Do not create one.

Report both before/after strings in your summary.
```

### Acceptance
- `CLAUDE.md` states 1 hr admin TTL; 15 min rider/driver untouched.
- Metrics wording no longer claims `metrics.py` defines the names.
- No code files modified.

---

# Phase 3 — Fix 6 stale doc paths + add a doc-path validator

**Priority: high. Effort: S.**

### Verified evidence

All paths below were existence-checked on 2026-08-01.

**Stale references (6):**

| File | Cites (does not exist) | Actual path |
|---|---|---|
| `.claude/context/domain-dispatch.md` | `backend/routes/rides.py` | `backend/routes/rides/` (package) |
| `.claude/context/domain-dispatch.md` | `backend/routes/drivers.py` | `backend/routes/drivers/` (package) |
| `.claude/context/domain-safety.md` | `backend/routes/chat.py` | `backend/routes/rides/chat.py` |
| `.claude/context/domain-safety.md` | `backend/services/insurance_period_service.py` | `backend/utils/insurance_periods.py` |
| `.claude/context/domain-safety.md` | `rider-app/src/screens/SOSScreen.tsx` | `rider-app/app/report-safety.tsx` (no `src/screens/` dir exists) |
| `.claude/context/domain-safety.md` | `driver-app/src/screens/SOSScreen.tsx` | `driver-app/app/report-safety.tsx` (no `src/screens/` dir exists) |

**Clean — do not touch:** `.claude/context/domain-payments.md` (9/9 paths valid) and
`.claude/context/domain-corporate.md` (10/10 paths valid).

Cause: the "god-file split" refactor moved `routes/rides.py` and `routes/drivers.py`
into packages, and the mobile apps use Expo Router (`app/`) rather than `src/screens/`.
The docs were not updated with those PRs.

### Prompt

```
Two-part task: correct stale file paths in Spinr's domain context docs, then add a
guard so this class of drift fails loudly in future. Do these as TWO separate commits.

=== COMMIT 1 — correct the stale paths ===

These 6 references were existence-verified as broken on 2026-08-01:

.claude/context/domain-dispatch.md
  1. `backend/routes/rides.py`     -> now the package `backend/routes/rides/`
       (relevant modules: matching.py, booking.py, lifecycle.py)
  2. `backend/routes/drivers.py`   -> now the package `backend/routes/drivers/`
       (relevant modules: status.py, location.py)

.claude/context/domain-safety.md
  3. `backend/routes/chat.py`                          -> `backend/routes/rides/chat.py`
  4. `backend/services/insurance_period_service.py`    -> `backend/utils/insurance_periods.py`
  5. `rider-app/src/screens/SOSScreen.tsx`             -> `rider-app/app/report-safety.tsx`
  6. `driver-app/src/screens/SOSScreen.tsx`            -> `driver-app/app/report-safety.tsx`

Note for 5 and 6: neither app has a `src/screens/` directory at all — both use Expo
Router with screens under `app/`. Verify the replacement paths exist before writing them.

DO NOT modify domain-payments.md or domain-corporate.md — all of their cited paths
were verified valid.

Only correct paths. Do not rewrite the surrounding prose, rules, or tables.

=== COMMIT 2 — add the validator ===

Add a doc-path check to the EXISTING hook `.claude/hooks/pre-commit` (it is already
auto-installed into .git/hooks by a SessionStart hook in .claude/settings.json —
do not create a new hook file or a new install mechanism).

The check must:
- Scan `CLAUDE.md` and `.claude/context/*.md` for backtick-quoted tokens that look
  like repo file paths (contain a `/` and end in a real extension such as
  .py/.ts/.tsx/.sql/.md/.sh/.json, OR end in `/` denoting a directory).
- Report any that do not exist on disk, printing the citing doc and the bad path.
- Run in WARNING mode for now (print, exit 0). Add a clearly dated comment:
  "# TODO(2026-09-01): flip to blocking (exit 1) once the warning backlog is clear."
  A dated TODO is required — an undated 'temporary' state is exactly the failure
  mode this repo already has in .github/workflows/security-gates.yml.
- Keep runtime under ~2 seconds; skip anything under node_modules/ or .git/.
- Be resilient: if the scan errors for any reason, print a notice and exit 0.
  It must NEVER block a commit while in warning mode.

Deliberately tolerate false positives for now (illustrative or glob-style paths).
That is why it starts as warn-only.

VERIFY:
- After commit 1, running the checker reports ZERO bad paths across all four
  context docs and CLAUDE.md. If it still reports some, either fix the path or
  adjust the matcher — and say explicitly which you did and why.
- `bash -n .claude/hooks/pre-commit` passes.
- A normal commit still succeeds.

CONSTRAINTS:
- Tooling and docs only, no live-tested runtime surface touched, so NO Change Impact
  & Risk Log entry is required. Do not create one.
- Do not change any backend, mobile, or admin source file.
```

### Acceptance
- All 6 paths corrected; payments/corporate docs untouched.
- `pre-commit` reports zero bad paths afterward.
- Validator is warn-only with a **dated** flip-to-blocking TODO.

---

# Phase 4 — New agent: `spinr-ai-guardrail-reviewer`

**Priority: high. Effort: S.**

### Verified evidence

- `.claude/agents/` contains 8 domain review agents: corporate-billing, dispatch,
  insurance-period, migration, money, regulatory-compliance, security, surge.
- `backend/ai/` contains: `orchestrator.py`, `mcp_server.py`, `tools.py`,
  `tools_rides.py`, `tools_booking.py`, `tools_support.py`, `tools_driver.py`,
  `pii.py`, `embeddings.py`, `response_cache.py`, `conversations.py`, `catalog.py`,
  `prompts.py`, `support_assistant.py`, and provider adapters for Anthropic, OpenAI,
  and Gemini.
- Entry points: `backend/routes/ai.py` (193 lines), `backend/routes/admin/ai_console.py`
  (245 lines), and `rider-app/app/ai-assistant.tsx`.
- `ACTION_ITEMS.md` carries an AI guardrail backlog (AI1–AI13) from a 2026-07-28 audit;
  recent commits closed AI3 (parallel tool-call cap), AI4 (scheduled-time validation),
  AI9, and AI12 (admin console rate limit).
- **No agent covers this surface**, despite it being the newest code that can book
  rides and reach payment flows, and the only surface that sends data to external
  LLM providers.

### Prompt

```
Create a new Claude Code subagent for Spinr that reviews the AI/LLM surface.

WHY THIS IS NEEDED (verified 2026-08-01):
`.claude/agents/` has 8 domain reviewers (corporate-billing, dispatch,
insurance-period, migration, money, regulatory-compliance, security, surge) — but
NONE covers `backend/ai/`. That directory contains an orchestrator, an MCP server,
three external provider adapters (Anthropic/OpenAI/Gemini), a PII scrubber, and
tool modules that can book and mutate rides (`tools_rides.py`, `tools_booking.py`,
`tools_support.py`, `tools_driver.py`). It is the newest money- and safety-adjacent
code in the repo, the only surface that transmits data to third-party LLM providers,
and it has an open guardrail backlog (AI1–AI13 in ACTION_ITEMS.md). Every comparable
domain already has a dedicated reviewer; this one does not.

TASK:
Create `.claude/agents/spinr-ai-guardrail-reviewer.md`.

FIRST: read at least three existing agent files in `.claude/agents/` (suggest
spinr-security-auditor.md, spinr-money-auditor.md, spinr-corporate-billing-reviewer.md)
and mirror their frontmatter schema, tool list, tone, and section structure exactly.
Do not invent a new format.

SCOPE — the agent should trigger on changes to:
  backend/ai/**, backend/routes/ai.py, backend/routes/admin/ai_console.py,
  and rider-app/app/ai-assistant.tsx

IT MUST ENFORCE:
1. PII scrubbing (`backend/ai/pii.py`) is applied on EVERY provider-egress path —
   not just the one it was originally written for. Call out explicitly that Spinr
   has been bitten three times by the "two independent code paths that look like one
   check" pattern (documented in .claude/context/domain-corporate.md), so each tool
   module and each provider adapter must be verified separately.
2. CLAUDE.md's PIPEDA ban list is honoured in anything sent to a provider or logged:
   no raw GPS lat/lng, full phone numbers, full names, emails, government IDs, or
   exact pickup/dropoff addresses.
3. Any tool that MUTATES state (books, cancels, modifies a ride, touches wallet or
   payment) must be resistant to prompt injection carried in user-supplied content
   such as support-ticket text or chat messages. Untrusted text must never be able
   to escalate into a state-changing tool call unchecked.
4. Rate limiting and cost controls exist on every AI entry point (AI12 added them to
   the admin console — verify the rider-facing path has equivalents), plus the
   parallel tool-call cap from AI3.
5. No new tool ships without an eval case. If no eval harness exists yet, the agent
   must say so plainly and flag it as a blocking gap rather than passing silently.
6. Money rules still apply inside AI paths: Decimal-only arithmetic, no float, and
   fares/quotes produced by an AI path must go through the same fare service as
   every other path — never recomputed or approximated by the model.
7. Provider fallback behaviour is explicit: a provider outage must fail cleanly, not
   silently degrade to a different model with different guardrails.

STYLE:
Match the existing agents — concrete checks a reviewer can actually run, with
grep-able patterns where useful, not vague advice.

CONSTRAINTS:
- One new file only: `.claude/agents/spinr-ai-guardrail-reviewer.md`.
- Do not modify existing agents, backend/ai/ source, or CLAUDE.md in this phase.
- Tooling only, no live-tested runtime surface touched, so NO Change Impact & Risk
  Log entry is required. Do not create one.
```

### Acceptance
- New agent file matches existing agents' frontmatter/schema exactly.
- Covers all 7 enforcement areas.
- No other file modified.

---

# Phase 5 — Privacy Impact Assessment for the AI surfaces

**Priority: high — likely a legal prerequisite. Effort: M. Deliverable: a document.**

### Why

`backend/ai/` transmits ride, booking, and support data to **three external LLM
providers** (Anthropic, OpenAI, Gemini adapters all present). Under PIPEDA,
disclosing personal information to a new third-party processor requires a documented
purpose, consent basis, and residency assessment. `ai/pii.py` is a code control; a
PIA is the *documented assessment*, and Spinr does not appear to have one for this
surface. **This plausibly gates the rider-facing AI assistant going live.**

### Prompt

```
Produce a Privacy Impact Assessment (PIA) for Spinr's AI/LLM surface.

Use the `privacy-pia` skill. Load it first and follow its structure.

SCOPE — assess this surface only:
- `backend/ai/` (orchestrator, MCP server, tool modules, embeddings, response cache,
  conversations) and its provider adapters for Anthropic, OpenAI, and Gemini
- Entry points: `backend/routes/ai.py`, `backend/routes/admin/ai_console.py`
- Client surface: `rider-app/app/ai-assistant.tsx`

READ FIRST for the governing rules (do not restate them from memory):
- `CLAUDE.md` — the Compliance (PIPEDA) section, especially the list of what may
  never appear in logs/Sentry/analytics, and the data-residency requirements
- `.claude/context/regulatory-sk.md`
- `backend/ai/pii.py` — the existing scrubbing control, to assess what it does and
  does not cover

THE PIA MUST COVER:
1. What personal information actually reaches each provider, per tool module.
   Determine this by reading the code — do not assume.
2. Purpose and necessity for each data element (PIPEDA data-minimisation).
3. Consent basis, and whether existing consent language plausibly covers disclosure
   to third-party LLM providers. If it does not, say so directly.
4. Data residency: whether provider processing occurs outside Canada, and whether
   that is consistent with Spinr's stated residency commitments in CLAUDE.md.
5. Retention: what providers retain, what `response_cache.py` and `conversations.py`
   retain, and for how long.
6. Effectiveness of `ai/pii.py` — specifically whether it is applied on EVERY egress
   path or only some. Verify per tool module and per provider adapter; report which.
7. Risk rating per finding, with concrete mitigations.
8. An explicit go / no-go recommendation for launching the rider-facing AI assistant.

OUTPUT:
Write to `docs/compliance/pia-ai-surfaces-2026-08.md`.
Create the directory if needed.

CONSTRAINTS:
- This phase produces a DOCUMENT ONLY. Change no source code.
- If you find a live privacy defect (e.g. an unscrubbed egress path), do NOT fix it
  here — record it in the PIA with a severity, and list it as a follow-up item.
  Fixing it is a separate, properly-scoped change with its own review.
- Where you cannot determine something from the code (e.g. a provider's contractual
  retention terms), say "requires confirmation from <source>" rather than assuming.
  Do not guess at legal terms.
```

### Acceptance
- Document at `docs/compliance/pia-ai-surfaces-2026-08.md`.
- Per-tool-module egress inventory derived from code, not assumption.
- Explicit go/no-go, and unknowns flagged as unknowns.
- Zero source changes.

---

# Phase 6 — Observability / metrics aggregation design

**Priority: medium-high. Effort: M. Deliverable: a design document.**

### Verified evidence

- `backend/utils/metrics.py` (186 lines) is an in-process counter/gauge registry.
  Its own docstring states: **"Per-process only. Each backend replica keeps its own
  counters… we do NOT aggregate across replicas here."** There is no exporter sidecar.
- `CLAUDE.md` publishes a P95 SLA table (dispatch offer→notification < 2 s, fare
  estimate < 300 ms, WS fan-out < 100 ms, etc.) and a KPI table (match rate ≥ 85%,
  payment success ≥ 99%).
- **A cross-replica P95 cannot be computed from per-process counters** without
  scraping every replica or pushing to a central backend. As deployed (Fly.io, with
  Railway as a nominal standby), those published targets are not measurable today.

### Prompt

```
Design the production metrics and alerting architecture for Spinr's backend.

Use the `observability-monitoring` skill. Load it first and follow its structure.

THE VERIFIED PROBLEM:
`backend/utils/metrics.py` is a 186-line in-process counter/gauge registry. Its own
docstring states it is per-process only and explicitly does NOT aggregate across
replicas, and there is no exporter sidecar. Meanwhile CLAUDE.md publishes P95 SLA
targets (dispatch offer -> driver notification < 2 s, fare estimate < 300 ms,
WS fan-out < 100 ms) and KPI targets (match rate >= 85%, payment success >= 99%).
Those cannot be computed from per-replica in-process counters. The SLA table is
therefore currently aspirational rather than measured.

READ FIRST:
- `backend/utils/metrics.py` (understand the existing shape and `render_prometheus`)
- `CLAUDE.md` — Observability Conventions, Performance SLAs, KPI Targets
- Call sites that already emit `spinr_*` metrics:
  `backend/services/dispatch_service.py`, `backend/services/payment_service.py`,
  `backend/utils/stripe_reconcile.py`, `backend/services/data_transfer/observability.py`

DELIVER A DESIGN COVERING:
1. Aggregation approach — compare (a) Prometheus scraping every Fly instance,
   (b) push to a managed backend, (c) OpenTelemetry export. Recommend ONE, with
   reasoning that accounts for Fly.io autoscaling and a small team.
2. Whether histograms are needed for real P95s, and what changes in `metrics.py`
   to support them (it currently has counters and gauges).
3. A concrete alert-rule set, threshold + window per rule, tied to the existing
   SLA/KPI tables. Include at minimum: dispatch latency breach, payment failure
   rate, SOS unacknowledged, WS fan-out latency, background-loop stall.
4. How to avoid double-counting across the Fly primary / Railway standby topology.
   NOTE: Railway deploys are currently BLOCKED and drifting from main (ACTION_ITEMS
   item C5) — account for that reality rather than assuming a healthy standby.
5. A minimum-viable first increment that delivers real measurement in under a day,
   separated from the full build-out.
6. Cost estimate for the recommended managed option, if any.

OUTPUT:
Write to `docs/adr/` as a new ADR, following the numbering and format of the ADRs
already in that directory (read one first — do not invent a format, and check the
highest existing number before choosing yours).

CONSTRAINTS:
- DESIGN ONLY. Change no source code and add no dependency in this phase.
- Be explicit about what CANNOT be measured today, so the SLA table's current status
  is stated honestly rather than implied to be live.
```

### Acceptance
- New ADR matching existing numbering/format.
- One recommended approach with reasoning, not a survey.
- Alert rules with thresholds tied to the documented SLA/KPI tables.
- MVP increment separated from full build.

---

# Follow-on phases (7–10)

Lower priority. Same rules apply. Prompts are intentionally shorter here — run
Phases 1–6 first and re-scope these with fresh evidence when you reach them.

### Phase 7 — Corporate coverage CI gate
`CLAUDE.md` states corporate modules target ≥80% coverage and explicitly notes this
is **not yet enforced by a `--cov-fail-under` gate**. Current aggregate ~52%, with
`corporate_accounts.py` at 39% and `corporate_signup.py` / `corporate_rider.py` /
`corporate_company_kyb.py` at 32–33%. Add a scoped coverage gate **set at current
measured coverage, not the 80% target**, so it ratchets without blocking unrelated
PRs. Measure first; do not assume the numbers above are still current.

### Phase 8 — `spinr-fraud-auditor` agent
Grep found `fraud` in exactly one non-test backend file (`services/payment_service.py`),
while referrals (rider + driver), quests, promotions, incentives, and loyalty all
ship. Create an agent covering referral velocity/self-referral, promo stacking,
device + phone reuse at signup, and GPS plausibility between location pings.

### Phase 9 — `spinr-ai-tool` skill
Mirror the existing `.claude/skills/spinr-background-loop` skill (which encodes the
replay-safety contract for the 17 startup loops). The AI tool layer is the same
shape — a repeating pattern, easy to get subtly wrong, touching rides and money.
Encode: registration, mandatory PII scrubbing, rate limiting, the parallel-call cap,
the eval requirement, and safe MCP exposure.

### Phase 10 — Change Impact Log CI enforcement
`CLAUDE.md` mandates a Change Impact & Risk Log entry for changes to live-tested
surfaces, enforced today only by a human or AI remembering. Add a CI check: a PR
touching `backend/routes/rides/`, `backend/routes/payments.py`, `corporate_*`,
`safety`, or `auth` must add a `docs/change-log/` entry or carry a `no-impact` label.

---

# Explicitly OUT of scope

Recorded so a future session does not re-propose these:

- **Do not expand `.mcp.json`.** It is a scaffold with nothing wired to live
  credentials and carries deliberate per-server safety warnings (Stripe test-mode
  only, Redis staging-only, Twilio test-credentials only). Every wired MCP server is
  a live path from an AI session into a production system. Wire only Sentry
  (read-mostly) and Supabase (read-only) if anything, and not before launch.
- **Do not run the `sdlc-full-lifecycle` skill.** `CLAUDE.md`'s pre-merge release
  gates already cover more, Spinr-specifically, and `docs/LAUNCH_GATE_IMPLEMENTATION_PLAN.md`
  plus `docs/Spinr_Production_Readiness_Launch_Gate.docx` already exist.
- **Do not run `security-sast-dast`.** Five CI security gates already run (Bandit
  blocking, ESLint security, dependency audit, secret scanning, Trivy) alongside a
  `spinr-security-auditor` agent.
- **Do not run `cybersecurity-expanded`.** PCI scope is minimal (Stripe handles PANs;
  Spinr never touches card numbers). Revisit only if a health-authority or government
  contract demands a formal control mapping.
- **Do not build a `spinr-launch-gate` skill.** A launch gate is a one-time checklist,
  not a repeating pattern — skills should encode things done repeatedly. A doc is the
  right artifact, and one already exists.

---

# Open questions that change scope

These were raised in review and remain unanswered. Any of them materially changes
priority, so answer before committing to Phases 7–10:

1. **Launch timeline and first market.** Inside 3 months → run Phases 1–4 only and
   freeze everything else.
2. **Spinr Pass pricing and break-even driver count.** Determines whether the
   flat-subscription churn-cliff concern is real.
3. **Team size / on-call capacity.** Decides buy-vs-build on Phase 6's recommendation.
4. **Corporate pipeline (signed LOIs?).** If a health authority is in the pipeline,
   Phase 7 becomes launch-blocking rather than follow-on.
