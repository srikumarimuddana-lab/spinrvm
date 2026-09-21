# /sentry-triage — Sentry Error Discovery, Root-Cause Fix & Weekly Report

Discover new/regressed/climbing Sentry issues in the `spinr-backend` Sentry org (project `crimson-smoke-7445`), root-cause each one against the actual code (never Seer's guess alone), ship a reviewed, guardrail-carrying fix PR for anything confidently confirmed, and produce a weekly report of everything found, fixed, and still open. This is the automated version of the fix loop already proven out manually in this repo (find → implement → adversarial review → PR with Change Impact Log) — it does not skip any of those steps, it just runs them starting from a Sentry issue instead of a `/full-audit` finding.

Consistent with the pilot guardrail in `docs/audit/2026-09-08-agentic-tooling-atlas.md` and `.claude/context/connector-scoping.md`'s Sentry row: **no fix is ever auto-merged.** Every fix ships as a normal PR, reviewed the same way any other PR in this repo is reviewed, with a human deciding whether to merge it.

## Usage

```
/sentry-triage                    # full run: since last recorded run (or 7 days if none), fix + report
/sentry-triage --report-only      # discovery + report, no fixes implemented or PRs opened
/sentry-triage --window 14d       # override the lookback window
/sentry-triage issue <short-id>   # investigate and (if confirmed) fix one specific issue on demand
```

## 1 · Discover the window

- Check `docs/audit/sentry-triage/` for the most recent `YYYY-MM-DD-weekly-report.md` to find the last run's end date; use that as the window start. If none exists, default to 7 days back (or whatever `--window` overrides to).
- Confirm the Sentry connector is authorized before doing anything else — call `mcp__Sentry__find_organizations()`. If it errors or requires OAuth, stop and tell the user plainly: the connector needs to be authorized via claude.ai connector settings before this can run. Don't silently produce an empty report that looks like "no errors found."

## 2 · Investigate — dispatch the investigator agent

Launch `spinr-sentry-triage-investigator` (Agent tool) with the window and any `--window`/`issue <id>` scoping from the invocation. Wait for its structured report (see that agent's own Output format). Do not re-derive its findings yourself — it already did the discovery/identify/analyze/investigate/recommend legwork; your job from here is to act on its report.

## 3 · Triage the investigator's findings

For each issue in the investigator's report:
- **Confidence `high`** → proceed to implement (step 4), unless it touches a live-tested surface (rides/dispatch/payments/auth/corporate/safety) genuinely ambiguously — in that case, treat it like `medium`.
- **Confidence `medium`** → use `AskUserQuestion` to confirm the approach before implementing, per CLAUDE.md's "escalate, don't silently ship" rule — don't guess on the user's behalf for anything not fully confirmed.
- **Confidence `low`** → do not implement. List it in the weekly report under "needs human triage" with the investigator's hypothesis, and stop there.
- Any `BLOCKERS` the investigator raised (org/project mismatch, a PII leak found in a payload, etc.) → surface to the user immediately, don't fold into the weekly report as just another finding.
- If `--report-only` was passed, skip implementation entirely for every issue — produce the report (step 7) from the investigation alone.

## 4 · Remediate — implement the fix

For each issue proceeding to a fix:
- Implement the investigator's recommended fix, minimal and surgical (CLAUDE.md's simplicity-first principle) — resist scope creep even if you notice other things nearby.
- **Add the guardrail the investigator recommended** in the same change: a regression test that fails without the fix (mandatory — CLAUDE.md's test-coverage conventions require this for any state-transition/fare-calc/auth/webhook fix, and it's good practice for everything else), plus whichever of an alert rule / metric / tightened validation applies.
- **Edge-case and error-handling discipline**: per CLAUDE.md's "do not silently swallow errors" rules — the fix must not introduce a new silent fallback, must surface DB/auth/payment/dispatch errors loudly, and must handle the actual edge case the Sentry issue exposed (not just the one reproduction case) where that's knowable from the investigation.
- Write the Change Impact Log entry (`docs/change-log/YYYY-MM-DD-<slug>.md`) per CLAUDE.md's mandatory template — this is not optional for a fix touching a live-tested surface, and good practice for the rest.

## 5 · Adversarial review before committing

Dispatch the domain-appropriate `spinr-*-reviewer` agent(s) for the surface touched (e.g. `spinr-dispatch-reviewer` for a dispatch fix, `spinr-security-auditor` for anything auth-adjacent) — same pattern as every other fix shipped in this repo. Do not commit until the review comes back clean or its findings are addressed. If the fix touches a surface with no obviously-matching reviewer, use `/code-review` at medium+ effort instead.

## 6 · Ship the PR

- One PR per issue (never bundle multiple unrelated Sentry issues into one PR — CLAUDE.md's batch-size rule applies here same as anywhere else), unless two issues share the exact same root cause, in which case say so explicitly in the PR body.
- Fill out the repo's PR template in full (all required Tier 1/2/4 fields) — a PR missing required fields fails CI's own check, as this session has hit before.
- Reference the Sentry issue's short-id and a link in the PR body (never paste the raw stacktrace/payload — link only, and only IDs/tags in prose, per the investigator's PII discipline).
- Subscribe to the PR's activity and drive it to green per this repo's standing PR-babysitting rules — this loop isn't finished until the PR merges or is explicitly closed.

## 7 · Weekly report

Regardless of whether any fixes were shipped this run, produce `docs/audit/sentry-triage/YYYY-MM-DD-weekly-report.md` (create the directory if it doesn't exist) via `scripts/observability/generate_sentry_weekly_report.py` — pass it the investigator's structured findings plus this run's outcomes (which issues got a fix PR, which needed human triage, which were blockers). The script renders the Markdown; do not hand-write the report inline, so the format stays consistent run over run. The script must tolerate a fully-empty week (no issues) and say so plainly rather than producing a blank or misleading file.

Open the report as its own small PR (draft is fine) if any fix PRs weren't also opened this run to carry it; if fix PRs were opened, the report can ride along in one of them or its own PR — use judgment, but never skip publishing the report just because there was nothing to fix.

Also carry forward, every week, until closed: the status of `ACTION_ITEMS.md` **C2** (missing Sentry alert rule for refresh-token reuse) — one line in the report, not re-investigated from scratch. (C55, a similar-sounding insurance-period alerting item, is fully CLOSED as of 2026-09-04 — don't re-flag it.)

## Do NOT

- Do not auto-merge any fix PR — a human merges, always.
- Do not implement a `medium`- or `low`-confidence finding without confirming with the user first (medium) or at all (low, report only).
- Do not bundle multiple unrelated Sentry-issue fixes into one PR.
- Do not paste raw Sentry payload text (stacktraces, breadcrumbs, event bodies) into a PR body, commit message, or the weekly report — reference the issue by short-id/link only, tags and IDs in prose.
- Do not skip the adversarial review step because "it's just a Sentry fix" — same gate as every other change.
- Do not treat an unauthorized/expired Sentry connector as "no errors this week" — that's a tool failure, not a clean signal, and must be reported as such.

## See also

`.claude/agents/spinr-sentry-triage-investigator.md` for the investigation step's own detailed rules and output format. `backend/routes/admin/sentry.py` for the existing human-facing issue browser (this command complements it, doesn't replace it). `docs/audit/2026-09-08-agentic-tooling-atlas.md` and `.claude/context/connector-scoping.md` for the Seer/Sentry-connector guardrail history this command is built to stay consistent with.
