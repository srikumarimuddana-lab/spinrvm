# 06 — Operating model: how to stop re-finding the same bugs

**Lane:** R20 Operating-Model Designer · **Wave:** W5 · **Date:** 2026-09-25 · **Mode:** report-only. Built from `clean-sheet-prompt/operating-model.md` (the target), `00-history.md` §1 (17 recurrence families) and §3 (38 decay signals), and every lane's human-only questions. Each section states what exists today (VERIFIED unless marked), the gap, and the proposal (PROPOSED).

## §0 Diagnosis in five sentences

1. **Velocity without shared memory.** About 36 PRs a day, mostly written by agent sessions that do not share working memory (00-history "velocity context"), so the same class of bug is found and fixed by different sessions, one instance at a time; 13 backlog entries are literally phrased "found while fixing the previous one".
2. **Local fixes, prose rules.** Each fix is applied where the bug was seen (CLAUDE.md's "surgical change" rule working as written); the systemic rule ("never swallow errors", "Decimal only", "derive the insurance period") lives in prose that a new session may not read or may read after a stale paragraph.
3. **Gates that do not gate.** The merge gate is advisory in practice (RR-01), 21 `continue-on-error` lines exist, several monitors run on placeholder data, and silence is read as green (C9's own lesson).
4. **Docs that are snapshots.** The same volatile fact (loop count, review-bot status, JWT trust model) is hand-copied into 5–10 files, and CLAUDE.md fixes itself by appending corrections rather than removing the number.
5. **One person owns everything.** CODEOWNERS has two accounts and no teams, and on most PRs only one approval can count; every alert, decision and drill falls to the same person or to nobody (`matrices/ownership.md` §0).

**The two mechanisms that have actually stopped a family** (00-history §6): a **static test that resolves re-exports** (`tests/test_loguru_call_conventions.py`, which closed HIST-008) and a **contract test between forks** (`CarMarkerParity.test.ts`, the auth real-IP guard). The whole model below is: for every recurring family, build one of these two, make it blocking, and give it an owner.

**Rule adopted from the target model:** a finding that recurs in two consecutive audits is escalated from "fix" to "systemic root cause" and gets an architecture item in `04-blueprint.md`. Under this rule, **all 17 HIST families already qualify**, because each has at least two same-root-cause fixes.

---

## §1 Recurrence family → the mechanical gate that ends it

| Family | What keeps it recurring | Gate that ends it (type) | Status today | Roadmap | Owner role |
|---|---|---|---|---|---|
| HIST-001 float money | Repository accepts untyped dicts; pre-commit check 6 warns only; CI rule has 7 exclusions and cannot see column types; three `rides` money columns are FLOAT8 | Money-column registry generated from `information_schema` + repository write guard that rejects `float` for registered columns (flag `money_write_guard`); pre-commit check 6 made blocking or deleted; extend the CI rule to `earnings.py`, `features.py`, `referral_payout.py` | Not built; 5 new instances found this wave (MONEY-006/-008, MONEY-001) | N19, X5 | BE-PAY |
| HIST-002 / HIST-015 forks | "Surgical change" rule; parity guard diffs props only; pre-commit check 11 warns | **Rule change** (E-F14): reconcile or contract-test a fork before the second fix; check 11 blocking with commit-trailer override; every `known-forks.md` row gets a reconciliation date; register the admin track-page marker | Registry exists (4 rows, 1 copy unregistered); guard warn-only | X16, E-F14 | MOBILE + REPO-ADMIN |
| HIST-003 legacy cohort | JSON marker filtered by hand at 59 call sites | Generated boolean column + view; a test that every `rides` aggregate filters on it or declares `include_legacy=True` | Not built; DRIVER-002 still live | L14 | BE-PAY |
| HIST-004 silent swallow | Rule is prose; only the loguru *call shape* is checked, not whether the error re-raises | Blocking semgrep/AST rule: `except` on DB/Stripe/Redis calls in webhooks, repositories, redis client, payment service with no `raise`/`return`/`unclaim` → fail (one week in warn mode first); quarterly inventory of `continue-on-error` lines, each with an owner and expiry | Not built; C135 open | X20 | BE-PLAT |
| HIST-005 doc drift | Volatile facts copied by hand; corrections appended, not replaced | `scripts/docs/facts.py` generates loop count, router count, migration head, review-bot status, flag inventory; a test fails if CLAUDE.md contains a literal for a generated fact | Not built | X20 | BE-PLAT |
| HIST-006 migrations | Hand numbering across parallel sessions; manual apply; no owner | Named owner (E-F9); nightly `--status` alert; then migrate-on-deploy gated on CI evidence (the Fly deploy-gate pattern of 2026-09-23); timestamp prefixes; scheduled read-only advisor diff against repo intent | Collision check (CHECK B) exists and blocks; apply is manual | X9 | DB-OWNER |
| HIST-007 tracker ids | 29k-line file; ids chosen by whichever session writes | ID allocator (a `NEXT_ID` file updated atomically, or GitHub issues as source with ACTION_ITEMS generated); CI check `uniq -d` on ids is empty | Not built | X20 | REPO-ADMIN |
| HIST-008 loguru misuse | Two logging APIs | **Closing** — static test works; extend it with the "ERROR log must bind a domain tag" rule (RR-12) | Test exists and blocks | N18 | BE-PLAT |
| HIST-009 RLS dormant | Backend uses the service-role key only | Keep the `tests/rls` tier; recorded decision (C108); scheduled advisor diff catches drift | Decided | X9 | DB-OWNER |
| HIST-010 insurance periods | Period recorded as a side effect at each call site | Finish the v2 claim RPC; delete v1 call sites one per PR; reconciler is the only repair path | In progress (flag dark) | X13 | BE-DISP |
| HIST-011 check-then-act | Read-decide-write is the natural helper shape | `compare_and_swap(table, id, expect, set)` helper + a lint for read-then-`update_one` on the same id without a status filter; optimistic `version` on admin editors | Not built | X20, X7 | BE-PLAT |
| HIST-012 PII egress | Per-sink redaction; three FCM exclusion sets | One `egress.redact(payload, channel)`; a test that every `send_push`/`send_to_*`/AI/Zoho/exception path passes through it | Not built; a fourth unfiltered push found (SKB-001) | N13, X15 | BE-PLAT + PRIVACY |
| HIST-013 CI silently not running | No meta-check that a required workflow ran | Branch protection read and recorded (RR-01); a "did required checks run" meta-check; summary jobs fixed (QUAL-001) | `main-branch-guard.yml` backstop is notify-only | N4 | REPO-ADMIN + CI |
| HIST-014 `main` red after merge | Merge does not wait for `backend-test` | Same as HIST-013 plus merge queue or "require up to date" | Open (C21) | N4 | REPO-ADMIN |
| HIST-016 timezone | Timezone is an optional request field | Server-side default from the service area; reject missing timezone for scheduled rides | Not built | L15 | BE-DISP |
| HIST-017 OTP ×4 | No shared short-code primitive | One `utils/otp.py` (hash, counter, lockout, dev bypass) + test that the dev bypass literal appears once | Not built | X8 | BE-AUTH |

**Emerging family to watch** (00-history "not yet a recurrence"): WS event ordering and stale HTTP responses in `rideStore.ts` (102 commits). One more same-root-cause fix promotes it to a family; the gate would be the WS event enum + schema test proposed in `04-blueprint.md` §2 H6.

**Definition of done for a family** (PROPOSED): the gate exists, it is **blocking** in CI, it was shown to fail on a scratch branch that re-introduces the original bug, it has a named owner, and the family's row in this table is updated with the PR link.

---

## §2 Decay watch — keeping gates from going silently dark

`00-history.md` §3 lists 38 decay signals. They share one shape: a control that exists on paper and is dormant, advisory, unprovisioned or on placeholder data, where silence reads as "no findings". Proposals:

| Rule (PROPOSED) | Signals it covers | Mechanism | Owner |
|---|---|---|---|
| **Every `continue-on-error: true` line has an owner, a reason and an expiry date** in a comment; a CI check fails on a line without them; a monthly report lists all 21 | #6, #7, #8, #9, #31–#33 | grep-based CI check + monthly Routine | CI |
| **Every scheduled monitor must prove it can alert.** A monitor running on placeholder data (secret-rotation, renewal calendar, Supabase capacity, billing usage) reports "NOT CONFIGURED" as a failure, not a pass | #16, #18–#21 | change the monitors' no-op branch to fail loudly; one issue per unconfigured monitor | SRE |
| **Absence of a signal is a finding.** The weekly health note (§3) includes "which reviewers, monitors and loops produced zero output this week, and is that expected?" | #3, #4 (Codex silent 48 days), #10 (3,206 zero-duration runs), #15 | weekly note template | Eng lead (unowned today) |
| **Every dark flag has an owner, a failure mode and a sunset date.** Generated flag registry; a flag older than 60 days without a decision is listed in the weekly note | "built, dark" rows: RR-20, RR-70, RR-75, RR-96, v2 dispatch, ledger flags, outbox | `04-blueprint.md` §2 H7 registry + annotation test | BE-PLAT |
| **Drills produce dated evidence.** Failover, PITR restore, key rotation and SOS paging each get a drill log; an undrilled runbook is RED in the readiness matrix | #25, #26, RR-87, RR-23 | quarterly drill calendar (§3) | SRE |
| **Every stale doc paragraph is replaced, not appended to.** A "Correction (date):" paragraph must delete the wrong sentence in the same edit | HIST-005, CLAUDE.md self-corrections | review checklist line + the generated-facts test | Doc owner |

---

## §3 Audit cadence — target vs today

| Cadence | What runs (target) | Today | Gap | Proposal |
|---|---|---|---|---|
| Daily | `/ops-triage` (Railway, Sentry, vendor status); `/sentry-triage` daily scope (payments, auth, dispatch, safety) | Commands and agents exist (C129, 2026-09-21). Whether they run on a schedule is **UNKNOWN**; no `docs/audit/daily/` notes exist | Documented, not scheduled | Schedule both as Routines; output one short note per day; Sentry-found fixes go through the normal Change Impact Log gate |
| Weekly | `/sentry-triage` full window; ledger-vs-Stripe reconciliation review; open CRITICAL/HIGH review; CI gate health | Reconciliation loop runs, but its alert is not trusted (MONEY-013); no weekly note exists | No review ritual; reconciliation signal untrustworthy | Weekly health note (template in §3.1); fix reconciliation populations first (X5) |
| Bi-weekly | One lane of this audit re-run on changed files only | None | — | Rotate lanes in the order of `risk-register.md` §10; write into the same `02-findings/<domain>.md` |
| Monthly | `/fleet-check`, `/tooling-check`, connector-scope verification, admin module access review, `continue-on-error` report | `/tooling-check` ran once (2026-09-08) and the loop count drifted again by 09-20 | Not scheduled | Schedule; the connector review must call each connector's own list tool, not read the scoping doc |
| Quarterly | Full Phase 0 + Phase 2; failover drill (C1); PITR restore test; regulatory source re-check; threat-model refresh | Never run: C1 "never exercised"; PITR never drilled; threat models v1.0 from April | All RED | First quarterly cycle before 2026-12-31 (drill window in E-F8) |

### §3.1 Weekly health note (template)

1. What changed since last week (merged PRs touching money, auth, dispatch, safety, migrations — count and links).
2. Open CRITICAL/HIGH rows in `risk-register.md`: moved, stuck (and why), new.
3. Gate health: any required check red on `main`; any new `continue-on-error`; any monitor that produced no output.
4. Dark flags older than 60 days without a decision.
5. Reconciliation: discrepancies reported vs explained.
6. Incidents and near-misses; what check was added to `sweep-catalog.md` because of them.
7. Decisions needed from the founder this week (link to `05-escalations.md` rows).

---

## §4 Issue / PR / CR lifecycle — resolution, not merge

**Target flow** (from the target model): Detect → Triage (S×B×L, owner, duplicate check) → Plan (≤ 3 files per subtask, a verify step each) → Implement (flag if user-visible) → Review (mapped `spinr-*` agents + `/code-review`; forks checked) → Change Impact Log (rollback plan before merge) → CI green (no new `continue-on-error`) → merge → staged rollout (dark → staging → canary → on) → verify in production (the metric/query named in the log) → close with evidence; update KB/FAQ/runbook.

**What exists:** the Change Impact Log discipline is real (1,470 entries in 61 days, each with rollback and "not verified" sections), the pre-commit hook, `/plan`, `/review`, `/impact-log`, the `app_settings` flag mechanism. **What breaks the flow:** merges before CI finishes (RR-01), no staging confirmed (RR-89) so "staged rollout" often means "production with a flag", no production verification step recorded in most closures, and duplicate tracker ids (RR-07).

### §4.1 Fix-time targets vs measured reality (sample)

Targets (to be confirmed by the founder; the rapid baseline used the same): CRITICAL triaged < 1 h and fixed or mitigated < 24 h; HIGH < 1 week; MEDIUM next sprint; LOW backlog with quarterly review.

Measured from `ACTION_ITEMS.md` open dates, as of 2026-09-25 (a sample of open items, not a full census — a full census needs the tracker to carry a machine-readable opened date, which is itself a proposal):

| Item | What | Severity class | Opened | Age (days) | Target | Gap |
|---|---|---|---|---|---|---|
| C21 | `main` required checks never audited | HIGH | 2026-08-12 | 44 | 7 | +37 |
| C13 | Required workflows silently never fire | HIGH | 2026-08-10 | 46 | 7 | +39 |
| C11 | Metrics aggregation and alerting not implemented | HIGH | 2026-08-02 | 54 | 7 | +47 |
| B25 | Mobile E2E wired but never fires | HIGH | 2026-08-11 | 45 | 7 | +38 |
| G9 | PST applicability unconfirmed | HIGH (tax) | 2026-08-22 (question open since 08-13) | 34 | 7 | +27 |
| G4 | Company-level insurance not confirmed | HIGH | 2026-08-21 | 35 | 7 | +28 |
| C43 | RLS disabled on 4 tables (now closed live, open in repo) | HIGH | deferred 2026-08-25 | 31 | 7 | +24 (and the record is wrong) |
| C2 | Sentry refresh-token-theft alert rule ("~5 min in the Sentry UI") | MEDIUM | July 2026 | ~60+ | next sprint | +45 or more |
| C70 / C90 | Marker fixes unverified on real devices | MEDIUM | 2026-09-04 / 09-07 | 21 / 18 | next sprint | at or past target |
| C131 | Cloudflare origin lock not enforced | MEDIUM | 2026-09-21 | 4 | next sprint | within target |

**Pattern:** the items that miss by the widest margin are all ones whose last step is **a human with console access** (GitHub settings, Grafana account, Sentry UI, accountant, broker, device). Code-only items generally close within days. The lifecycle therefore needs a **"blocked on human" state with a named person and a date**, reviewed in the weekly note, rather than "open".

### §4.2 Proposed lifecycle rules

1. **Triage within one working day** for anything touching money, auth, dispatch, safety or personal data; the triager sets S×B×L, owner role and named person.
2. **Every item has a named person, not a role label**, or is explicitly `blocked-on-human: <role>, needs <access>, asked <date>`.
3. **Closing requires evidence**: the PR link, the production check named in the Change Impact Log, and (for families) the scratch-branch proof that the gate fails.
4. **A HIGH item stuck > 14 days on a human step becomes a founder escalation** in `05-escalations.md`.
5. **One ID allocator** (HIST-007) and a machine-readable `opened:` field so §4.1 can be computed by script every week.

---

## §5 Incident loop — target vs today

| Step | Target | Today | Proposal |
|---|---|---|---|
| Detect | Alert, Sentry spike, support surge, SOS | Loop and capacity alerts depend on one webhook of UNKNOWN state (RR-86); SOS pages nobody (RR-116); the 2026-07-30 exposure was found by a person, ~3.5 months late; the 2026-09-10 crash was triaged from a PR comment before anyone read Sentry (reliability.md §7) | E-F4 paging decision; confirm the alert webhook; outside-in synthetic checks (E-F8 d) |
| Declare | `/incident`, severity, commander, channel | `/incident` command exists; no roster | Name an incident commander rota (even of one) |
| Mitigate | Flag off / rollback / failover | Flags work well (`app_settings`); admin rollback runbook exists; **no backend rollback runbook** (RR-88); failover never drilled (RR-87) | Write the backend rollback runbook; drill failover + PITR in one window |
| Communicate | In-app notice; regulator/OPC within 72 h for a breach | No customer-facing templates or status page (RR-129); breach runbook exists | Pre-approved templates (E-F13) |
| Resolve & learn | Post-mortem in `docs/incidents/`; actions into the backlog; new check into `sweep-catalog.md` | 3 incident docs; none about a loop stall, Redis outage or bad deploy, so MTTD/MTTR for those classes is UNKNOWN, not zero | Every incident adds one sweep-catalog check and one gate (§1 "definition of done") |
| Proactive triage | Weekly review of rising warnings | Not done | Weekly note item 3 |

---

## §6 Ownership and on-call

Today one account is the only approver that can count on most PRs and the implicit owner of every alert (`matrices/ownership.md` §0). Proposals, smallest first:

1. **Name a person for each role in the risk-register legend** — one person may hold several. Record them in CODEOWNERS as individual accounts until teams exist, and in each runbook header (ownership matrix §6).
2. **Add an owner column to the loop registry** and route watchdog alerts per owner once the webhook is confirmed.
3. **A second human reviewer** for money, auth, migrations and safety paths — the CODEOWNERS file itself notes that the PR-author account cannot approve its own PRs, so sensitive PRs effectively have one possible approver.
4. **An on-call rota**, even if it is one person with a backup, covering SOS pages, loop alerts and payment alerts, with a recorded acknowledgement time.
5. **Reviewer agents for the two unowned surfaces**: `shared/` logic and Maps & Routing (CARTO-002, CARTO-003).

---

## §7 Agent loop for new features, and its guardrails

**Target loop:** Intake (`/spinr-feature`) → PRD check → `/plan` (subtasks + verify steps) → adversarial pre-review (one alternative named; mapped agent reads the plan) → implement subtask → `/review` → fix → commit (≤ 200-line diff) → Change Impact Log → draft PR → CI → **human approval** → flag-dark deploy → canary check → flag on → KB/FAQ/training updated → close.

**Guardrails in the target model:** agents never merge, never flip production flags, never mutate live data or vendor state; a human approves every merge; automated fix suggestions (Codex, Seer, agents) go through the same gate as human code.

**Today (VERIFIED, SEC-R10-012):**

| Guardrail | Today | Gap | Proposal |
|---|---|---|---|
| Agents never merge / push to `main` | `.claude/settings.json` pre-approves `git push origin main` for agents; the deny list blocks `--force`, `rm -rf`, `git reset --hard`, key/pem and `.env` writes, and some workflow writes | A prompt-injected agent can push to `main` inside the allowlist; branch protection contents unknown | Remove `git push origin main` (keep `claude/*` branches) — repo admin (N5) |
| Least privilege for reviewer agents | Four auditor agents declare Bash | Reviewers can run arbitrary commands | Reviewer agents get `Read, Grep, Glob`; Bash only with narrow patterns (e.g. `pytest*`) where a test run is the job |
| Read-only production connectors | Account-level Supabase connector has full write on production (time-boxed exception to 2026-10-31); Vercel connector drifted back to three projects | Write access during audits; scope drift | Read-only Supabase at the deadline, with a separate explicit path for the one write consumer (migration apply); re-narrow Vercel; verify each by the connector's own list call monthly |
| Agent actions are logged | `agent_action_log` table exists (migration 429); the logger has no production caller | The table implies an audit trail that does not exist | Wire it from `/full-audit`, `/security-check` and any write-capable skill, or drop the table |
| Fly deploy token not reachable from audits | Target says Fly is not used in audits; the session token is deploy-capable | Any reach is itself a finding | Keep Fly out of agent sessions; record in connector scoping |
| Automated fixes follow the same gate | Stated in CLAUDE.md for Codex and Seer | Relies on the merge gate that is advisory (RR-01) | Fixing RR-01 is the prerequisite |

---

## §8 Project-scoped access guardrails — target vs today

| Tool | Target scope | Today | Action |
|---|---|---|---|
| GitHub | Read for audits; agents push only to their own branch; branch protection on `main` | Push to `main` pre-approved; branch protection unread | N4, N5 |
| Supabase | Read-only SQL/advisors during audits | Full write via account-level connector until 2026-10-31 | Narrow at or before the deadline |
| Stripe | Restricted read key | Connector failed to connect in this audit; scope unverified | Verify with the connector's list call |
| Sentry | Read only; no resolve/assign by agents | Connector failed to connect in this audit | Verify |
| Vercel | Admin project only | Drifted to three projects (2026-09-21) | Re-narrow |
| Railway | Read (status, logs, metrics) on the one service | Available; not exercised by this audit | Keep read-only |
| Fly.io | Not used in audits | Deploy-capable token exists in sessions | Remove from agent environments |
| Expo/EAS | Read builds and crashes | Builds only via `[build]` commits by humans | Keep |
| Twilio, Firebase | Read logs/usage | Twilio connector failed to connect | Verify |

---

## §9 Documentation and tracker hygiene

1. **Generated facts** (HIST-005): loop count, router count, migration head, flag inventory, review-bot status come from a script; CLAUDE.md links to the generated file instead of stating numbers.
2. **Replace, don't append**: when a doc is wrong, delete the wrong sentence in the same edit that adds the correction. CLAUDE.md's deployment paragraph (about 20 lines of superseded theories) is the example to clean up first — with the owner's approval, since CLAUDE.md edits are the owner's call.
3. **Adopt this audit's matrices as the current records**: `matrices/threat-model.md` as threat-model v2.0; `matrices/data-classification.md` replacing the per-table section of `docs/data-classification.md` (SEC-R10-013/-014).
4. **ACTION_ITEMS.md**: an ID allocator; one status line per item; "blocked-on-human" as an explicit state; re-file the lost forward reference for the admin FCM gap (SKB-001) under its own id.

---

## §10 Exit checklist for the whole programme (from the target model §6) — status today

| Criterion | Status 2026-09-25 | Evidence |
|---|---|---|
| Every L6 code unit mapped to an L4 story; orphans resolved or documented | **Not met** — 769 orphan rows; 16 "gap" rows are keyword-collision noise | `01-inventory/epics.md` §4–§5, CARTO-004 |
| Every §3 edge case has a status and, if Unhandled, a finding | **Partly met** — each lane marked its edge cases; several marked UNKNOWN (minor rider, extreme cold, battery) | lane scenario tables |
| Every regulatory/tax item has a primary source or is on the escalation list | **Met by escalation** — none has a primary source; all are in `05-escalations.md` §B–§C | this audit |
| Every KPI and SLA has a live metric and an alert | **Not met** — 3 of 8 SLAs emit nothing; 0 of 8 alert; 3 KPIs have no query | RR-65, RR-67 |
| Every CRITICAL/HIGH finding has an owner, a flag/rollback plan and a date | **Partly met** — owner *roles*, flags and rollbacks are in `ROADMAP.md`; named people and dates are not (roles unfilled) | `matrices/ownership.md` |
| Every recurrence family has a systemic fix planned, not another point fix | **Met on paper** — §1 above; none built yet | §1 |
| KB/FAQ/training updated for every behaviour change shipped from the roadmap | **Not started** | — |
| Cadence in §3 is scheduled (not just documented) and the first cycle has run | **Not met** | §3 |

---

## §11 First 30 days of the operating model (PROPOSED sequence)

| Week | Do | Why first |
|---|---|---|
| 1 | Name people for the roles (at least: repo admin, SRE, DB owner, privacy officer, T&S on-call); repo admin reads and records branch protection; remove `git push origin main` from the agent allowlist; confirm the alert webhook and SOS paging target | Nothing else can be owned, alerted or gated until these exist |
| 2 | Schedule daily ops/Sentry triage and the weekly health note; ship the first two family gates that are pure tests (HIST-004 no-swallow rule in warn mode; RR-12 domain-tag test with a baseline); `continue-on-error` owner/expiry check | Cheapest gates with the largest recurrence history |
| 3 | Generated-facts script and the CLAUDE.md literal test; flag registry with owner/sunset; tracker ID allocator | Stops doc and tracker drift from undoing the rest |
| 4 | First bi-weekly lane re-run (money, per `risk-register.md` §10); first monthly connector and `continue-on-error` report; schedule the quarterly failover + PITR drill | Proves the cadence runs, not just exists |

*Written by R20 (W5), 2026-09-25. No code, config or data was changed.*
