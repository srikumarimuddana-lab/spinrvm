# Contributor, PR/CR/issue governance & repo-professionalization baseline (2026-09-25)

**What this is.** A consolidated map of who has done what in Spinr, how work is currently tracked end-to-end (PR → CI → review → merge → decision → rollback), and what to fix so the repo is consistently professional, non-drifting, and reversible. This **inherits and extends** the 2026-09-24 rapid baseline audit (`docs/audit/clean-sheet/rapid-baseline-2026-09-24/`, especially `EXECUTIVE_SUMMARY.md`, `tagging-plan.md`, `ESCALATIONS.md`, `DEFERRED.md`) rather than re-running it — that audit is one day old and nothing in it has been invalidated. New in this report: the contributor/PR/identity mapping, the sanitization plan, and the full PR-lifecycle governance checklist.

**Aliasing.** Per `docs/CONTRIBUTOR_ALIASES.md`: `srikumarimuddana-lab` = **Spinrmk** (owner/admin), `ittalenthireca-sketch` = **Spinrmv** (PR-authoring collaborator), both together = **TeamSpinr**. Real GitHub handles stay unchanged in CODEOWNERS/git for enforcement reasons — see that file for the full explanation and one open question (commit/PR attribution footer) that needs your answer before it changes.

**Evidence discipline.** Aggregate counts below are VERIFIED against the live GitHub API on 2026-09-25. Per-PR detail is a sample, not an exhaustive read of ~2,600 PRs — that would require a scripted export, not a chat-scale review. Anything not directly checked is marked ASSUMED/INFERRED, matching the inherited audit's own labeling convention.

---

## 1. How work is currently logged (as-is, verified)

| Mechanism | What it captures | Where |
|---|---|---|
| PR template (7 tiers) | Type, risk, blast radius, schema/API/flag/rollback plan, compliance flags, tests, and (conditionally) migration/UI/auth/bug-fix/high-risk-stop-condition detail | `.github/pull_request_template.md` |
| Change Impact & Risk Log | Mandatory for any fix/gap-close/behavior-change on a live-tested surface: issue, root cause, fix, blast radius, UX effect, before/after, rollback, verification, what wasn't verified | `docs/templates/CHANGE_IMPACT_LOG.md`, instances in `docs/change-log/YYYY-MM-DD-<slug>.md` (114+ files, still actively used — 5 new entries dated 2026-09-25 alone) |
| Path/risk labeling | `surface:*`, `area:*`, `risk:low/medium/high` auto-applied by `pr-checks.yml` | `.github/labeler.yml`; known race with fast-merge documented in CLAUDE.md (CR-2026-034) |
| CODEOWNERS | Review routing by money/schema/auth/dispatch/safety path, OR-semantics across the 2 real collaborators | `.github/CODEOWNERS` |
| CI required-checks + Claude Approvals gate | Automated review verdict, blocking on Approved/Passed | `.github/workflows/*.yml`, `ci-guardrails.yml` |
| ACTION_ITEMS.md | 102 open `[ ]` items today, each carrying a dated status line, some updated as recently as 2026-09-24 | root `ACTION_ITEMS.md` |
| `docs/audit/`, `docs/incidents/`, `docs/runbooks/` | Point-in-time investigations, incident records, and operational runbooks — append-style, dated filenames | repo-wide, ~150+ files |
| `spinr-*` reviewer fleet (26 agents) | Domain-scoped adversarial review (money, security, migrations, dispatch, corporate billing, etc.), invoked proactively per CLAUDE.md | `.claude/agents/spinr-*.md` |

This is a genuinely mature system on paper. The gap isn't "no tracking exists" — it's **enforcement drift**: the rapid-baseline audit's #2 and #8 findings (automated review off ~2 months on cost grounds, though Codex resumed 2026-09-16 per the executive summary's own errata; 3 of 8 performance SLAs unmeasured) are exactly this — a documented process that silently stopped running.

## 2. Contributor activity map

### Aggregate (VERIFIED via GitHub search API, 2026-09-25)

| | Spinrmk (`srikumarimuddana-lab`) | Spinrmv (`ittalenthireca-sketch`) | TeamSpinr total |
|---|---|---|---|
| Role | Admin / account owner | Write access, opens most PRs | — |
| PRs authored | 762 | 1,678 | — |
| Total PRs (repo-wide, all states) | — | — | 2,617 |
| Total issues (repo-wide, all states) | — | — | 2,822 |

Interpretation: Spinrmv authors roughly 2.2× as many PRs as Spinrmk. Per CODEOWNERS' own documented caveat, **Spinrmk's approval is the only one that counts on the PRs Spinrmv authors** (GitHub never counts a PR's own author as its approver) — meaning Spinrmk is the de facto sole human review gate for the majority of this repo's changes. That concentration is itself a governance risk worth naming: if Spinrmk is unavailable, there is no second human reviewer who can approve a change on money/auth/dispatch/safety paths, only the automated gate.

### Commit-graph window (local clone; shallow)

The checked-out clone only reaches back to 2026-09-21 (202 commits, all in the last 5 days: Spinrmk 261, Spinrmv 48, Claude Code co-author trailer 104 — note commit counts and PR counts aren't the same unit; one PR can bundle several commits). This does **not** represent the project's full lifetime — the PR/issue numbers above (into the 5,800s and 5,000s respectively) prove a much longer history than the local shallow clone shows. **A full contributor timeline needs a scripted export** (`gh api` paginated pull over all 2,617 PRs, or a non-shallow `git clone`), which I have not run — it's a multi-hour API job, not something to do inline. I can build that export script on request.

### Decisions and parked items, mapped (inherited from ESCALATIONS.md / DEFERRED.md, both 2026-09-24)

| Owner | Item | Age | Status |
|---|---|---|---|
| Spinrmk (founder decision) | E12: restore automated PR review gate for money/auth/dispatch/safety paths | Review gate off since ~2 months before 2026-09-24; Codex resumed 2026-09-16 | **Open** |
| Spinrmk | E14: move JWT signing to key-pair; set `OTP_PEPPER` now (zero-code) | Flagged 2026-09-24 | **Open** |
| Spinrmk | E17: enable loop process-role split (`SPINR_PROCESS_ROLE=api`) | Mechanism built, never switched on | **Open** |
| Spinrmk | E16: fill vendor plan-of-record table (Supabase/Redis/Sentry/Vercel/EAS tiers) | Recorded nowhere in-repo | **Open** |
| Accountant (external, routed via Spinrmk) | E1: SK PST applicability determination | **Open since 2026-08-13** — oldest unresolved item, compounding revenue-remittance exposure every day it stays open | **Open, urgent** |
| Accountant (external) | E2–E7: CRA supplier-of-record, small-supplier exemption, digital-platform reporting, T4A threshold, promo tax treatment, corporate ITC docs | Various, all ASSUMED with no primary source cited | **Open** |
| Legal/SGI (external) | E8–E11: Period-1 offline-driver interpretation, night-ride recording consent, device fingerprinting vs. PIPEDA minimization, certificate-pinning ADR | Flagged 2026-09-24 | **Open** |
| — | GitHub support purge request for the 2026-09-11 PII history rewrite | **Open since 2026-09-12** — draft not submitted, blocked on a human retrieving the pre-rewrite SHA list | **Open, unresolved for 13 days** |

### Age-bucketed view of open work (ACTION_ITEMS.md, 102 `[ ]` items)

Spot-checked statuses show a wide spread — from items updated the same day (`2026-09-24`, `2026-09-23`) to at least one item whose "Status" line is itself struck through as dead/superseded text, and items with no re-check since August. **Recommendation:** ACTION_ITEMS.md needs a machine-checkable `last-verified: YYYY-MM-DD` field per item (not just prose inside the status line) so staleness can be queried instead of read — see §4.

## 3. Sanitization actions taken today

- Added `docs/CONTRIBUTOR_ALIASES.md` — single source of truth for the Spinrmk/Spinrmv/TeamSpinr/noreply@spinr.ca aliasing, and an explicit record of what is deliberately **not** changed (git history, GitHub usernames, CODEOWNERS `@handles`) and why.
- Added a pointer comment in `.github/CODEOWNERS` to that file. Real `@handles` untouched — changing them would break review-routing enforcement.
- Searched the full repo for `info@spinr.ca`: **zero matches**. Nothing to sanitize there; the ask may have been based on an assumption rather than an actual repo string — flagging so we don't chase a phantom.
- Did **not** touch git commit author identity or GitHub account usernames — both are high-blast-radius/irreversible-in-practice given the still-open 2026-09-11 history-rewrite incident (see `docs/incidents/2026-09-12-github-purge-request-driver-pii-history-rewrite.md`), and you confirmed docs/labels-only scope.
- Did **not** yet change the commit/PR attribution footer (Claude/`noreply@anthropic.com` → `noreply@spinr.ca`/TeamSpinr) — this is a disclosure-policy decision, not a formatting one. See `docs/CONTRIBUTOR_ALIASES.md`'s open question; I need your pick of the three options there before applying it to future commits/PRs.
- Did **not** rewrite historical audit/incident/change-log documents that mention the real usernames in their own investigative record (e.g. the CODEOWNERS org-membership check, the PII history-rewrite incident docs). Rewriting a historical record to insert an alias after the fact reduces its evidentiary value as an audit trail — the opposite of "professional." New documents should use the alias from now on; old ones stay as the accurate record of what happened, when.

## 4. Naming, tagging, flagging, tracking — recommendations (extends `tagging-plan.md`)

The inherited audit's `tagging-plan.md` already scoped a `domain`/`surface`/`component`/`owner`/`tier`/`data_class`/`sla`/`flag`/`vendor`/`env`/`cost_center`/`scale_limit` taxonomy with a 3-phase rollout for **observability tags** (Sentry/logs). Extending that same discipline to **governance metadata**:

1. **Machine-readable status on every open item.** ACTION_ITEMS.md and ESCALATIONS-style docs should carry a parseable front-matter block (`status:`, `owner:`, `opened:`, `last-verified:`, `age-days:` computed, not hand-maintained prose) so staleness is a query, not a read-through. Today's 102-item file is unqueryable without reading it end to end.
2. **A single `owner:` tag distinct from CODEOWNERS.** CODEOWNERS is review-routing, not accountability-for-open-items. `E1` (PST) has no single named accountable party beyond "the accountant" — pick a human owner even for externally-blocked items, so it doesn't silently stay open for 43+ days (as E1 already has).
3. **PR labels: enforce, don't just apply.** `Auto-label by path`/`Apply risk label` aren't required checks (CR-2026-034) — a fast merge can land unlabeled. Recommendation: make them required checks now; it's a small config change with no functional risk, already flagged as a tracked follow-up (`#5279`, gated behind ACTION_ITEMS C21).
4. **Decision log, not just an escalations snapshot.** `ESCALATIONS.md` from the 2026-09-24 run is a point-in-time list; once E1 etc. are answered, that answer needs a permanent home (an ADR or a `docs/decisions/` entry) with the citation, not just a closed checkbox — otherwise the same question gets re-asked by the next audit (this has already happened once: PST has been "decided" three times on verbal signals per the audit's own finding).
5. **Consistent aliasing everywhere, per `docs/CONTRIBUTOR_ALIASES.md`.** One canonical source, referenced, not restated with variant spellings.
6. **Tie Change Impact Log entries to their PR number in both directions.** Today the convention is PR body *or* `docs/change-log/`, not always both with a cross-link. Recommendation: always create the `docs/change-log/` file **and** link it from the PR body (most recent PRs already do this correctly — e.g. #5782, #5805 above both link their change-log file. Standardize it as a template requirement, not a habit).

## 5. The full PR lifecycle loop — creation to closure, with guardrails

The template and CLAUDE.md's pre-merge gates already specify most of this; here's the loop made explicit end-to-end, with the gap at each stage named:

| Stage | Required today | Verified gap |
|---|---|---|
| **1. Intake** | Linked issue, type, risk tier | Fine as designed |
| **2. Design** | Alternative-approach note + reasoning for anything on a live-tested surface (CLAUDE.md gate #10) | Enforced by convention, not CI — no check confirms a PR actually contains this section filled meaningfully vs. boilerplate |
| **3. Implementation** | Surgical, scoped, Decimal-only money math, `_require_ride_in_state()` guards, dual-import pattern | Enforced by pre-commit hooks + `spinr-*` fleet |
| **4. Automated review** | `spinr-*` domain reviewer + Claude Approvals + Codex | **Was off ~2 months (E12); Codex resumed 2026-09-16 but the required-checks list is still stale (C73) — a 34-file rides/payments/auth change merged 47 seconds after opening.** This is the single highest-leverage fix in the whole report: it is the one gap that, left open, silently defeats every other guardrail listed here. |
| **5. Human review** | CODEOWNERS-routed approval | Structurally sound (§2 above), but single-point-of-failure on Spinrmk for majority of PRs |
| **6. Pre-merge gates** | Migration numbering (CHECK B), corporate coverage floor, visual regression (6 admin pages) | Solid, CI-wired, merge-blocking |
| **7. Merge** | Squash/merge per repo convention | Label race (CR-2026-034) — cosmetic, not blocking |
| **8. Post-merge verification** | Change Impact Log's "Verification performed" field | Self-reported by the PR author, not independently re-checked post-merge |
| **9. Rollback readiness** | Rollback plan required in template (`git-revert-safe`/`coordinated`/`not-revertible`) *before* merge | Good practice, present in sampled recent PRs (#5782, #5805 above both specify concrete rollback commands); no automated check that the stated rollback command was ever actually run/verified — both sampled PRs explicitly checkbox "not verified here" |
| **10. Closure / monitoring** | Stop-condition + unmerge trigger for `risk:high` PRs (Tier 7) | Present in sampled high-risk PR (#5782); no automated alert wired to the stated stop-condition metrics — it relies on a human noticing |

**Net assessment:** the *paper* process already implements almost everything you asked for (happy-path + sad-path coverage, guardrails, rollback plans, verification fields, drift/hallucination checks via the adversarial `spinr-*` fleet). The real risk isn't a missing control — it's **controls that exist but silently stop running** (review gate, required-checks list, rollback-command verification, stop-condition alerting). That pattern repeats across findings #2, #7, #8, #9, and #10 in this table and in the inherited audit's #2 and #8. Fixing that class of problem (not adding new paperwork) is the highest-value next step.

## 6. Priority recommendations

1. **Refresh the CI required-checks list now** and confirm Claude Approvals + Codex are both active gates on money/auth/dispatch/safety paths (closes E12; prevents a repeat of the 47-second merge).
2. **Send the SK PST question to an accountant with a hard deadline** (E1) — oldest open item, compounding daily.
3. **Make `Auto-label by path`/`Apply risk label` required checks** (closes the CR-2026-034 race; tracked in `#5279`/C21).
4. **Decide the attribution-footer question** in `docs/CONTRIBUTOR_ALIASES.md` so I can apply it consistently going forward.
5. **Commission a scripted contributor/PR export** if you want the full historical timeline (not just this session's sample) — I can write that as a one-off script rather than doing it via chat-scale API calls.
6. **Add machine-readable status metadata to ACTION_ITEMS.md** so staleness becomes queryable (§4.1) — this directly supports the "age of changes" mapping you asked for, which today can only be answered by reading prose.

## 7. What was NOT verified in this report

- No exhaustive read of the 2,617 PRs or 2,822 issues — aggregate counts only, sampled detail.
- No re-verification of the 2026-09-24 audit's own findings; they're inherited as-is (that report's own confidence caveats in `EXECUTIVE_SUMMARY.md` §"How much to trust it" still apply).
- No git history beyond the local shallow clone's 2026-09-21–25 window.
- No check of whether the still-open GitHub PII-purge support request (docs/incidents/2026-09-12-...) has since been submitted or resolved — worth an explicit status check given it's been open 13 days.
