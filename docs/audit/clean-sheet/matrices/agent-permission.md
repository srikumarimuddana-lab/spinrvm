# Agent Permission Matrix (Tier B matrix, part 1/2)

**Lane:** ad hoc Tier-B matrix build (this session) · **Written:** 2026-09-25 · **Report-only.**
Evidence labels: VERIFIED / INFERRED / UNKNOWN per master convention. Per the brief: quote rule
strings, never secret values.

## §0 Method

1. Read every `.claude/agents/*.md` file's YAML frontmatter (`name`, `description`, `tools`, `model`) —
   28 files, all read directly (`awk` between the two `---` fences, not a summary/grep).
2. Read `.claude/settings.json` in full (permissions allow/deny, `autoMode.allow`, hooks).
3. Read the two `PreToolUse` hook scripts (`pre-migration-write.sh`, `pre-domain-write.sh`) and the
   `PostToolUse` one (`post-python-write.sh`) to determine what they actually enforce (block vs. nudge)
   and what tool-matcher they fire on, since a hook that only matches `Write|Edit` has a specific,
   checkable blind spot against `Bash`-based file writes.
4. Read `.claude/context/connector-scoping.md`'s live scoping table for the three agents that carry MCP
   tools beyond `Read, Grep, Glob, Bash`, plus `.mcp.json`'s per-server sandboxing notes, to state what
   each of those tool grants actually reaches (project/org-scoped vs. account-level).
5. Read `.github/CODEOWNERS`'s header (real-org-membership note) and cross-referenced `security.md`
   SEC-R10-012 (already covers this exact surface in depth) and `W0-SUMMARY.md` §1 (PR #5048's 47-second
   merge with a red check) rather than re-deriving the human-review-gate weakness independently — this
   file restructures those findings into a per-agent table and adds the specific hook-bypass mechanism
   (§3 above) as new content.
6. No agent was run this session; every claim below is a static read of its declared configuration, not
   an observed action. "Can" statements are about what the configuration *permits*, not what any agent
   has *done*.

## §1 Every `.claude/agents/*.md` agent — tools, model, write/push/Bash/production reach

All 28 agents declare `model: sonnet`. None declare an explicit `Write` or `Edit` tool in frontmatter —
this matches the "audit-only by convention" self-description in `spinr-sentry-triage-investigator.md`'s
own frontmatter ("this repo's 25 other spinr-* agents are all audit-only by convention"). **Every one of
the 28 does carry `Bash`**, which is not read-only (see §2) — this is the crux of SEC-R10-012's finding,
restated here per-agent rather than as a single repo-wide observation.

| Agent | Extra tools beyond `Read, Grep, Glob, Bash` | Can write via Bash? | Can `git push`? | Production connector reach | Domain |
|---|---|:-:|:-:|---|---|
| `spinr-accessibility-reviewer` | — | ✅ (Bash) | ✅ (settings.json allowlist, not agent-specific) | none | WCAG/a11y |
| `spinr-admin-rbac-reviewer` | — | ✅ | ✅ | none | Admin RBAC/module grants |
| `spinr-agent-fleet-strategist` | `WebSearch` | ✅ | ✅ | none (WebSearch is public internet only) | Meta: fleet coverage |
| `spinr-ai-guardrail-reviewer` | — | ✅ | ✅ | none | AI/LLM surface |
| `spinr-cicd-infra-reviewer` | — | ✅ | ✅ | none (reads workflow files only; no deploy-provider MCP) | CI/CD + infra config |
| `spinr-corporate-billing-reviewer` | — | ✅ | ✅ | none | Corporate wallet/allowance |
| `spinr-corporate-reporting-reviewer` | — | ✅ | ✅ | none | Corporate reporting/exports |
| `spinr-design-consistency-reviewer` | — | ✅ | ✅ | none | Visual/UX consistency |
| `spinr-dispatch-reviewer` | — | ✅ | ✅ | none | Ride dispatch/state machine |
| `spinr-edge-case-reviewer` | — | ✅ | ✅ | none | Cross-cutting failure modes |
| `spinr-fraud-auditor` | — | ✅ | ✅ | none | Referral/promo/incentive abuse |
| `spinr-insurance-period-auditor` | — | ✅ | ✅ | none | TNC insurance periods |
| `spinr-legal-readiness-reviewer` | — | ✅ | ✅ | none | Legal doc publication gating |
| `spinr-migration-reviewer` | — | ✅ | ✅ | none (reviews SQL files; no DB connector) | Migration safety |
| `spinr-money-auditor` | — | ✅ | ✅ | none | Fare/Stripe/wallet arithmetic |
| `spinr-notification-ux-reviewer` | — | ✅ | ✅ | none | Notification copy |
| `spinr-observability-reviewer` | — | ✅ | ✅ | none | Sentry tagging/metrics/logging conventions, Zoho support surface |
| `spinr-ops-triage-investigator` | `WebFetch`; **Sentry** (`find_organizations`, `find_projects`, `search_issues`, `search_events`); **Railway** (`environment-status`, `get-service-metrics`, `get-logs`, `list-deployments`, `get-deployment-diagnosis`) | ✅ | ✅ | **Sentry** (project-scoped, `crimson-smoke-7445`, read-mostly per `.mcp.json`); **Railway** (account-level connector, 2 projects on the account — `cooperative-harmony`/`spinrvm` is Spinr's, `beautiful-harmony` is unrelated and must never be touched — the connector itself has no hard per-project restriction, only the agent's own prompt discipline, per `connector-scoping.md`'s Railway row) | Live-ops health ("is anything on fire") |
| `spinr-performance-sla-reviewer` | — | ✅ | ✅ | none | Latency SLA table |
| `spinr-realtime-reliability-reviewer` | — | ✅ | ✅ | none | WS + background-loop reliability |
| `spinr-regulatory-compliance-checker` | — | ✅ | ✅ | none | SK Transportation Act / PIPEDA |
| `spinr-safety-sos-reviewer` | — | ✅ | ✅ | none | SOS/emergency UX |
| `spinr-security-auditor` | — | ✅ | ✅ | none | OWASP/auth/RLS/PII |
| `spinr-sentry-triage-investigator` | **Sentry** (`find_organizations`, `find_projects`, `search_issues`, `search_events`, `analyze_issue_with_seer`, `get_sentry_resource`); **`mcp__Supabase__execute_sql`**; **Railway** (`get-logs`) | ✅ | ✅ | **Sentry** (project-scoped, same as above); **`execute_sql` against the account-level Supabase connector, which per `connector-scoping.md`'s 2026-09-21 addendum holds full read/write/admin on confirmed-production `spinrmobileapp` — this agent's grant is "restricted only by its own written instructions [to a `SELECT`-only query against `audit_logs`] — not a real permission boundary" (that document's own words, quoted verbatim)**; **Railway** (`get-logs`, same account-level 2-project caveat as the row above) | Sentry error investigation + RCA correlation |
| `spinr-surge-auditor` | — | ✅ | ✅ | none | Surge pricing rules |
| `spinr-test-coverage-reviewer` | — | ✅ | ✅ | none | Test coverage minimums |
| `spinr-tooling-hygiene-reviewer` | **Vercel** (`list_teams`, `list_projects`); **Supabase** (`list_organizations`, `list_projects`) | ✅ | ✅ | Both are **list-only** calls (no `execute_sql`/`apply_migration`/project-mutation tools granted) against account-level connectors whose own scoping has **drifted open before** — `connector-scoping.md`'s Vercel row records the connector returning 3 projects (2 non-Spinr) as of 2026-09-21, "drifted back open"; a `list_projects` call from this agent would currently see that same over-broad set, though it cannot act on it with the tools it's been given | Claude Code / dev-tooling config drift |
| `spinr-ui-ux-critic` | — | ✅ | ✅ | none | Holistic visual/UX judgment |

## §2 `.claude/settings.json` — allow/deny rules (rule strings quoted verbatim, never secret values)

**`permissions.allow`** includes, among others:
```
"Bash(git status)"
"Bash(git diff*)"
"Bash(git log*)"
"Bash(git add*)"
"Bash(git commit*)"
"Bash(git checkout*)"
"Bash(git branch*)"
"Bash(git pull*)"
"Bash(git push -u origin claude/*)"
"Bash(git push origin claude/*)"
"Bash(git push origin main)"
"Bash(git push origin feature/*)"
"Bash(git push origin fix/*)"
"Bash(git push origin hotfix/*)"
"Bash(git push --force-with-lease origin claude/*)"
"Bash(git push --force-with-lease origin fix/*)"
"Bash(git push --force-with-lease origin feature/*)"
"Bash(git push --force-with-lease origin hotfix/*)"
"Bash(git push --force-with-lease origin HEAD:claude/*)"
"Bash(git push --force-with-lease origin HEAD:fix/*)"
"Bash(git push --force-with-lease origin HEAD:feature/*)"
"Bash(git push --force-with-lease origin HEAD:hotfix/*)"
"Bash(git push -u origin HEAD:claude/*)"
"Bash(git push -u origin HEAD:fix/*)"
"Bash(gh pr*)"
"Bash(gh issue*)"
```
plus broad `Write(...)` globs covering `backend/**`, `rider-app/**`, `driver-app/**`,
`admin-dashboard/**`, `shared/**`, `tests/**`, `docs/**`, `scripts/**`, `*.md`, `CLAUDE.md`, and —
notably — **`".claude/settings.json"` and `".claude/agents/**"` themselves** (this file and the agent
definitions it grants tools to are both in the pre-approved Write set, i.e. no per-call confirmation
prompt is required to edit either).

**`permissions.deny`** (the full list — short, everything else falls to prompt-or-allow):
```
"Bash(rm -rf*)"
"Bash(git push --force origin*)"
"Bash(git reset --hard*)"
"Write(.env*)"
"Write(*.pem)"
"Write(*.key)"
"Write(.claude/settings.local.json)"
"Write(.github/workflows/ci.yml)"
"Write(.github/workflows/deploy-backend.yml)"
"Write(.github/workflows/eas-build.yml)"
"Write(.github/workflows/test-env.yml)"
"Write(.github/workflows/apply-supabase-schema.yml)"
```

**`autoMode.allow`** includes a production-adjacent entry beyond the `$defaults` placeholder:
```
"Bash commands using curl to POST to https://api.fly.io/graphql for the Fly app \"spinr-backend-yyz\"
only — GraphQL queries (status/health/secrets-list checks) and the restartApp mutation to restart that
app. Does not cover any other Fly app, the Fly Machines API (api.machines.dev), or any secrets-set/
createRelease/deploy mutation — those still require explicit confirmation each time."
```
This is a **prose-scoped** allowance, not a mechanically-enforced one — nothing in `settings.json`'s
schema restricts *which* GraphQL body a `curl` to that URL carries; the restriction depends on the
running agent reading and honouring this sentence, the same "prose isn't an enforcement mechanism"
critique `security.md` SEC-R10-012 already makes of the four originally-Bash-granted auditor agents'
"you audit, you do not edit" framing (now generalized here to all 28, and again to this specific
production-restart allowance).

## §3 The hook layer is a nudge, not a gate — and has a Bash-shaped blind spot

`PreToolUse` fires two scripts, matched **only on `Write|Edit`**:
- `pre-migration-write.sh` — reminds about migration conventions when a new `backend/migrations/*.sql`
  file is written.
- `pre-domain-write.sh` — reminds (stderr only) when a write touches a money/dispatch/safety-sensitive
  path (fare/payments/wallet/fares/tips/payouts/payment_retry and more, per its `case` statement),
  pointing at the matching `spinr-*` reviewer agent. Its own header states the design intent plainly:
  *"Rationale: the spinr-* subagents ... only get used if a session remembers to invoke them"* and
  *"never block, exit 0 always) — the point is a nudge at write time, not a gate."*

**Both are matched on the tool name `Write|Edit`, not on the file path independent of tool.** A file
change made via `Bash` (`sed -i`, a heredoc redirect, `cat > file`, `python -c "open(...).write(...)"`)
does not invoke the `Write` or `Edit` tool at all, so **neither hook fires** — an agent with `Bash` (all
28 of them) can edit a fare-calculation file, a migration, or `.claude/settings.json` itself through a
shell redirect and receive zero nudge, where the same edit through the `Edit`/`Write` tool would have
printed the domain reminder. This is a mechanically-verified gap in the hook *matcher*, not a claim
about hook *content* (`pre-domain-write.sh`'s domain-file list itself was not exhaustively cross-checked
against every money/dispatch/safety path in this session — flagged as unverified breadth, separate from
the matcher gap, which is fully verified: `grep '"matcher"' .claude/settings.json` shows `"Write|Edit"`
for both `PreToolUse` entries, no `Bash` matcher exists anywhere in the hooks block).

`PostToolUse`'s `post-python-write.sh` has the same `Write|Edit`-only matcher — same blind spot for any
Python file change made via `Bash`.

The `git` `pre-commit` hook (installed by `SessionStart`, `.claude/hooks/pre-commit`) is the one control
in this repo that **does** fire regardless of how the working tree was modified, since it runs at
`git commit` time — its check 11 (`docs/known-forks.md` pair-staging warning) would catch a Bash-written
change to one side of a known fork not staged with its sibling. It is, however, a `WARN`, not a blocking
check (consistent with CLAUDE.md's own description: "a warning is not a review").

## §4 Human-review-gate context (cited, not re-derived)

- `.github/CODEOWNERS`'s own header: **2 collaborators, 0 GitHub teams**, and GitHub's own rule that a
  PR's author is never counted as its own approver — so on the majority of PRs (opened by
  `@ittalenthireca-sketch` per that file's own observed-history note), the *other* collaborator's
  approval is the only one that counts, and CODEOWNERS coverage is otherwise advisory.
- `W0-SUMMARY.md` §1: "the merge gate is advisory in practice ... PR #5048 merged in 47 s with a red
  check." Branch-protection *contents* for `main` remain a human-only open question (`W0-SUMMARY.md`'s
  own closing list; not re-resolved this session — no GitHub branch-protection API call was made).
- `security.md` SEC-R10-012 (the pre-existing, deeper version of this finding — read in full this
  session, not re-derived): recommends removing `"Bash(git push origin main)"` from the allowlist
  (keeping `claude/*`), splitting the four auditor agents it names to `Read, Grep, Glob` only (adding
  `Bash` back per-agent only where a test run is actually needed), narrowing the Supabase connector to
  `read_only=true` at its already-recorded 2026-10-31 deadline, re-narrowing the Vercel connector (drift
  already recorded in `connector-scoping.md`), and wiring or removing `agent_action_log` (0 rows live,
  per that finding's VERIFIED-LIVE check on 2026-09-24) so it stops implying an audit trail that does not
  exist. This file's own contribution is the per-agent table (§1), the settings.json rule-string
  transcription (§2), and the hook-matcher blind spot (§3) — the underlying risk and its recommended fix
  are SEC-R10-012's, cited rather than restated as new.

## §5 Risky-grant summary (flagged, cross-referenced SEC-R10-012)

1. **All 28 agents carry `Bash`**, and `Bash` is not read-only under this repo's own permission model —
   `git commit`/`git push origin main`/`git push origin feature|fix|hotfix/*` are all pre-approved
   patterns, so any agent invocation can, within its own turn, stage, commit, and push to a
   feature/fix/hotfix branch or directly to `main` with no per-call confirmation. **SEC-R10-012.**
2. **`spinr-sentry-triage-investigator`'s `mcp__Supabase__execute_sql` grant reaches confirmed production
   with full read/write/admin** via the account-level connector; the only restriction is the agent's own
   prompt telling it to run `SELECT`-only queries against `audit_logs` — `connector-scoping.md` states
   outright this is "not a real permission boundary." This is the single highest-severity grant in the
   fleet: a prompt-injected or simply mistaken tool call from this agent could write to production
   through a tool whose name (`execute_sql`) makes no distinction between a read and a write.
3. **`.claude/settings.json` and `.claude/agents/**` are themselves in the pre-approved `Write` allowlist**
   — no agent needs a human's explicit go-ahead to modify its own or a sibling agent's permission grant,
   or the top-level permission file that constrains all 28. A compromised or confused turn could widen
   its own future authority without triggering a confirmation prompt (though `git commit`/`push` would
   still be needed to persist that change past the current session for another turn to see, and the
   `known-forks.md` pre-commit check does not cover this specific file).
4. **The `PreToolUse`/`PostToolUse` hook nudges have a `Bash`-shaped blind spot** (§3) — every domain
   reminder and Python-write check in this repo's hook layer can be silently bypassed by using `Bash` to
   write the file instead of the `Write`/`Edit` tool, and every one of the 28 agents has that option.
5. **`spinr-ops-triage-investigator` self-documents its own risk** (a rare, positive pattern worth
   naming alongside the risky ones): its frontmatter description states plainly that its `Bash` grant
   makes the session's "ambient, deploy-capable Fly token" technically reachable even though the agent
   is never meant to use it, and instructs itself to flag the fact if it ever notices being in a
   position to. This is the one agent in the fleet whose own definition treats its `Bash` grant as an
   acknowledged risk rather than an implicit "audit-only" assumption — the other 27 rely on the fleet-wide
   convention holding, not on a per-agent acknowledgement.
6. **Railway and Supabase are both account-level connectors with no hard per-project/per-org boundary** —
   `connector-scoping.md` records the Railway account holding one unrelated project
   (`beautiful-harmony`) alongside Spinr's, and the Vercel connector having drifted back open to 3
   projects (2 non-Spinr) as recently as 2026-09-21. Every agent granted a tool against either connector
   (`spinr-ops-triage-investigator`, `spinr-sentry-triage-investigator`, `spinr-tooling-hygiene-reviewer`)
   inherits that account-wide reach regardless of its own tool list being narrowly scoped in appearance.

## §6 What was not checked

- No agent was actually invoked/run this session — this is a static configuration read, not a
  behavioral test of whether any agent has ever exercised the Bash-push or execute_sql reach described
  above. `agent_action_log` (0 rows live per SEC-R10-012's VERIFIED-LIVE check) is consistent with no
  agent action ever having been logged, not with no agent action ever having happened — the table simply
  isn't wired to anything that would populate it.
- Branch-protection rules on `main` were not queried (no GitHub branch-protection API call made this
  session, consistent with `W0-SUMMARY.md`'s existing "human-only question" framing).
- `agents/` (the separate Python multi-agent dev-automation SDK, explicitly non-production per
  CLAUDE.md) and `.codex/`, `.emergent/`, `.maestro/`, `.agents/` (the other AI-tool config directories
  CLAUDE.md's own table lists) were **not** inventoried here — this file's scope is `.claude/agents/*.md`
  plus `.claude/settings.json` specifically, per the brief. A full agent-permission matrix for the whole
  "Control Plane" (all AI-tool directories) would be a larger follow-up.
- `pre-domain-write.sh`'s own file-path coverage list was read but not exhaustively cross-checked
  against every money/dispatch/safety-sensitive path CLAUDE.md names — only the matcher mechanism (§3)
  was fully verified.
