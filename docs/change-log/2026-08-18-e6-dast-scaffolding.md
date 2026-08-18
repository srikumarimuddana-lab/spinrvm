# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | vikas@ngitservices.com |
| Surface(s) | infra (CI), docs |
| Domain (Sentry tag) | n/a — no runtime code touched |
| PR / commit link | claude/e6-dast-scaffolding |
| Related issue or gap ID | ACTION_ITEMS.md E6 (blocked in part on E1) |

## 1. Issue / gap identified

SAST (Semgrep/Bandit/ESLint-security) already runs in CI on every PR, but
nothing exercises the actually-running application (DAST), and no external
penetration test has been booked before public launch — `ACTION_ITEMS.md` E6.

## 2. Root cause

No DAST tooling was ever wired up because there is no staging environment to
point it at yet (`ACTION_ITEMS.md` E1 is still open). The external pentest
was never booked because it's a procurement/budget decision, not a
code-review gap.

## 3. Fix / remediation

This is scaffolding only, not a live scan:

- New workflow `.github/workflows/dast-zap-baseline.yml` — OWASP ZAP
  baseline scan via `zaproxy/action-baseline`, triggered manually
  (`workflow_dispatch`) or weekly (`schedule`, Monday 03:00 UTC). It never
  runs on `pull_request`. It reads the scan target from a `STAGING_URL`
  repo variable/secret that does not exist yet; if unset, the first step
  logs a clear "STAGING_URL not configured, skipping" message and exits 0
  rather than failing.
- New runbook `docs/runbooks/dast-and-pentest.md` documenting what the ZAP
  job covers once `STAGING_URL` exists, its limitations (baseline scan only,
  not a full active scan), and that the third-party pentest remains a
  separate human/procurement action.
- `ACTION_ITEMS.md` E6 updated (kept open, `- [ ]`) with a dated note
  pointing at both new files and stating the remaining blockers.

No application code, no existing workflow, and no runtime behavior changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** This is a brand-new workflow file that no
  other workflow, script, or job references. It does not modify
  `security-gates.yml` or any other `.github/workflows/*.yml` file.
- Grepped for existing consumers of a `STAGING_URL` variable/secret and of
  `zaproxy/action-baseline` across the repo — none found; this is the first
  use of both.
- The new workflow **never gates a PR** (`workflow_dispatch` + `schedule`
  only) — it cannot block or slow down anyone's merge.
- It **no-ops until `STAGING_URL` is configured** — until then, running it
  (manually or on schedule) does nothing but log a message and exit 0. No
  live target exists today, so there is no possibility of it scanning
  anything real, staging or production, right now.
- Once `STAGING_URL` is eventually configured (out of scope for this PR —
  tracked under E1), the workflow will start making real HTTP requests
  against that URL on the weekly schedule. That's a deliberate future
  behavior change this entry flags in advance: whoever wires up
  `STAGING_URL` should re-read `docs/runbooks/dast-and-pentest.md` first.
- No background loop, DB table, ride/dispatch/payment state, or shared
  component/hook/utility is touched. `ACTION_ITEMS.md` and a new runbook are
  the only content changes outside `.github/workflows/`.

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin facing surface is
touched. This is CI/docs scaffolding only, not visible to any app user, and
has no mid-session effect on anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/dast-zap-baseline.yml` | New workflow: ZAP baseline DAST scan, manual + weekly trigger, graceful no-op when `STAGING_URL` unset | Scaffold the DAST half of E6 without gating PRs or requiring a staging env to exist yet |
| `docs/runbooks/dast-and-pentest.md` | New runbook documenting the ZAP job's scope/limitations and the separate pentest procurement action | Give the workflow an operational home; existing `security-incident.md` covers breach response, not DAST |
| `ACTION_ITEMS.md` | Appended a dated note to the E6 entry; item stays open | Record what scaffolding now exists and what still blocks it (E1, and the human pentest booking) |

## 7. Before / after

Not applicable — purely additive (new workflow file, new doc, an appended
note to an existing open action item). No existing behavior changed.

## 8. Rollback plan

`git revert` of this commit is a complete rollback: it deletes the new
workflow file and the new runbook, and un-appends the `ACTION_ITEMS.md` note.
No data, no deployed state, no live scan history is affected — the workflow
has never run against anything real (no `STAGING_URL` exists), so there is
nothing to clean up beyond the files themselves. This is one of the rare
cases where a plain `git revert` genuinely is sufficient, because nothing
here writes to live data (no Stripe charges, wallet deltas, or ride state
are anywhere near this change).

## 9. Verification performed

- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/dast-zap-baseline.yml'))"` — passed, YAML is syntactically valid.
- Confirmed `zaproxy/action-baseline` is a real, actively maintained public
  GitHub Action (cloned it read-only via the session's anonymous git proxy
  access) and resolved its latest release tag `v0.15.0` to commit SHA
  `de8ad967d3548d44ef623df22cf95c3b0baf8b25` via `git ls-remote --tags`,
  matching the pinning convention closed under `ACTION_ITEMS.md` C18
  (`uses: <owner>/<repo>@<sha> # <tag>`).
- Reused the same commit-SHA pin for `actions/checkout`
  (`3d3c42e5aac5ba805825da76410c181273ba90b1 # v7`) already established
  elsewhere in this repo's workflows (`security-gates.yml`), rather than
  re-deriving a new one.
- Reviewed against the `spinr-cicd-infra-reviewer` checklist
  (`.claude/agents/spinr-cicd-infra-reviewer.md`) before finalizing — no
  automated Codex/Claude PR review is running (CLAUDE.md C7/C9), and this
  session has no `Task`/`Agent` tool available to dispatch the subagent as a
  separate process, so its full checklist (service-container health checks,
  secrets handling, required-check consistency, Fly/Railway parity,
  Dockerfile, guardrail-gate wiring, trigger scope) was applied directly.
  One real finding: the `STAGING_URL` check step originally interpolated
  `${{ secrets.STAGING_URL }}` straight into the `run:` shell script —
  GitHub's documented script-injection risk for `run:` blocks. Fixed by
  passing it through `env:` and referencing the env var in the script
  instead (the `zaproxy/action-baseline` `with: target:` input is a YAML
  mapping value, not a shell script, so that reference was already safe).
  All other checklist sections were not applicable (no service containers,
  no Dockerfile/Fly/Railway/required-check changes, workflow correctly never
  gates a PR).
- Read `docs/runbooks/security-incident.md` and confirmed it covers breach
  response, not DAST scanning, before deciding to create a new runbook
  rather than append to it.

## 10. What was NOT verified

- The workflow has **not** been run — not even manually via
  `workflow_dispatch` — because there is no `STAGING_URL` to point it at and
  this task explicitly excludes scanning any real URL or arranging any
  actual pentest. Its no-op path (STAGING_URL unset → log + exit 0) was
  verified by reading the step logic, not by an actual Actions run in this
  session.
- The `zaproxy/action-baseline` action itself was not exercised end-to-end
  (no live target) — only its `action.yml` inputs/outputs and README were
  read to confirm the parameters used here (`target`, `cmd_options`,
  `allow_issue_writing`, `fail_action`, `artifact_name`) are real and
  correctly typed.
- No staging environment exists, so there is no way to verify the scan
  actually produces a useful report against a real Spinr deployment yet —
  that verification can only happen once E1 lands.
