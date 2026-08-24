# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude (session), on behalf of vikas@ngitservices.com |
| Surface(s) | CI (`.github/workflows/pr-checks.yml`) — not rider-app/driver-app/admin-dashboard/backend |
| Domain (Sentry tag) | admin (closest fit — internal tooling; not a domain in the CLAUDE.md list) |
| PR / commit link | branch `claude/c40-fix-pr-checks-duplicate-append` (see PR opened this session) |
| Related issue or gap ID | `ACTION_ITEMS.md` C40 |

## 1. Issue / gap identified

`pr-checks.yml`'s "Expand conditional template sections" job re-appends a
blank Tier 5 (UI change details) and/or Tier 6 (bug-fix notes) block to a
PR body on `pull_request.edited` events even when a filled section already
exists earlier in the body. Observed 3x this session (PRs #4469, #4470,
#4481).

## 2. Root cause

The `add(marker, heading, bullets)` helper inside the job's
`actions/github-script` step gated purely on
`body.includes(marker)`, where `marker` is an invisible HTML comment
(`<!-- spinr-expanded:ui -->` etc.) placed as the first line of the
appended block. When an author fills in a Tier 5/6 section by hand — via
the GitHub UI or an `update_pull_request` call — and in the process drops
that invisible comment (easy to do, since it renders as nothing in the
PR-body editor), the step's next run on `pull_request.edited` sees no
marker, concludes the section was never appended, and appends a second,
blank copy at the end of the body — even though a fully-filled section
with that exact heading already exists mid-body.

## 3. Fix / remediation

Added a `sectionHasContent(heading)` helper that locates the `## <heading>`
block already in the current (freshly-fetched) PR body and determines
whether it holds anything beyond the blank `` `[ ]` ``/"or N/A" placeholder
text baked into the templates. `add()` now skips appending when *either*
the marker is present (previous behavior, unchanged) *or* the heading
already has real content (new). This runs through the same `add()` call
for all seven templated sections (Tier 5 money/UI/auth/background-job/RLS/
safety, Tier 6 bug-fix, Tier 7 high-risk), so the fix is uniform across all
of them, not just the UI case that was directly observed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one step** (`expand-sections` job,
  single `actions/github-script` block) inside `pr-checks.yml`. Grepped
  the rest of the workflow file: the other jobs in this file
  (`auto-label`, `size-advisory`, merge-conflict detection, `auto-summary`,
  required-fields check implied elsewhere) each read `pr.body` or
  `fresh.body` independently and don't call `add()` or
  `sectionHasContent()` — no shared state or exported function touched.
- No other workflow file (`migration-check.yml`, `deploy-*.yml`,
  `visual-regression` etc.) references this step's logic or the marker
  comments it emits.
- Does not touch rides, dispatch, payments, auth, corporate, or safety
  code paths — this is CI/PR hygiene tooling only, explicitly out of scope
  of the live-tested backend/app surfaces the stricter gates in `CLAUDE.md`
  target. Still logging a full Change Impact entry per the instructions
  for this task and out of caution since it changes existing CI behavior.
- Failure mode if the new check has a bug: worst case is the same
  pre-existing behavior (a duplicate blank section gets appended again),
  not a new failure — `sectionHasContent()` only ever makes `add()` *more*
  conservative about appending (an additional `return` before the
  `sections.push`), it never causes a section to be skipped that the old
  code would have appended and the marker check alone wouldn't already
  have skipped. It cannot cause the step to throw either: `body` is
  already-fetched fresh PR body text (a string, possibly empty), so
  `body.indexOf(...)` and the regex `.replace()` chain always return a
  string, never throw.

## 5. User-experience effect

Internal-admin/engineering-facing only (PR authors and reviewers on this
repo). No rider/driver/corporate-admin visibility. Effect: PR bodies on
affected PRs stop accumulating duplicate blank Tier 5/6 blocks on every
edit, so the description stays clean for human reviewers. Not visible
mid-session to anyone using the live apps.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/pr-checks.yml` | Added `sectionHasContent(heading)` helper and one extra guard line in `add()` inside the `expand-sections` job's script step | Stop re-appending a blank Tier 5/6/7 section when a filled one already exists but lost its idempotency marker comment |
| `ACTION_ITEMS.md` | C40 entry: checkbox → `[x]`, added Status: FIXED note, rewrote "What was NOT verified"/"Acceptance" | Close out the tracked finding with what was actually done and its verification boundary |
| `docs/change-log/2026-08-24-c40-pr-checks-fix.md` | New file (this log) | Required Change Impact Log per `CLAUDE.md` for any fix/behavior change |

## 7. Before / after

```js
// Before
const sections = [];
const add = (marker, heading, bullets) => {
  if (body.includes(marker)) return; // already appended
  sections.push([
    marker,
    '## ' + heading,
    '',
    ...bullets,
  ].join('\n'));
};
```

```js
// After
const sectionHasContent = (heading) => {
  const idx = body.indexOf('## ' + heading);
  if (idx === -1) return false;
  const rest = body.slice(idx);
  const nextHeading = rest.slice(3).search(/\n## /);
  const block = nextHeading === -1 ? rest : rest.slice(0, nextHeading + 3);
  const stripped = block
    .replace(/^## .*/, '')
    .replace(/`\[ \]`/g, '')
    .replace(/\bor N\/A\b/gi, '')
    .replace(/^- \*\*[^*]+\*\*[^:]*:/gm, '')
    .replace(/^[-\s]*$/gm, '')
    .trim();
  return stripped.length > 0;
};

const sections = [];
const add = (marker, heading, bullets) => {
  if (body.includes(marker)) return; // already appended
  if (sectionHasContent(heading)) return; // filled in by hand, marker lost
  sections.push([
    marker,
    '## ' + heading,
    '',
    ...bullets,
  ].join('\n'));
};
```

## 8. Rollback plan

Pure `git revert` is sufficient and safe here: this step only edits
GitHub PR-body text via `pulls.update`, not any live data (no Stripe
charges, wallet deltas, or ride state involved). Reverting the commit
restores the old marker-only check; worst case is the pre-existing
cosmetic duplicate-append behavior returns, which was already the
observed (non-blocking) status quo. No feature flag or migration needed —
this is workflow YAML, redeployed simply by merging.

## 9. Verification performed

- [ ] Automated tests run — none exist for `github-script` steps in this
      repo; not applicable.
- [ ] Manual repro steps followed in staging — not possible; GitHub
      Actions cannot be triggered from this sandbox.
- [x] Blast-radius grep performed — searched `.github/workflows/pr-checks.yml`
      for other `add(`/`sectionHasContent(`/marker-comment usages; confirmed
      isolated to the one `expand-sections` job/step. Searched other workflow
      files for references to the Tier 5/6/7 marker comments or this job's
      output; none found.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — this is CI
      tooling, not a state-machine/money/RLS/PIPEDA change; reviewed
      against the general "surgical changes" and "do not silently swallow
      errors" conventions (the new helper degrades to `false` on a
      not-found heading rather than throwing, preserving prior behavior).
- [ ] Feature-flagged if user-visible and non-trivial — not applicable;
      internal CI-only change with a trivial, fully-reversible blast
      radius (see rollback plan).
- [x] Traced the exact logic against four hand-built PR-body fixtures in a
      standalone Node script (scratch-only, not committed): filled Tier 5
      with marker lost (correctly skipped — this is the reported bug,
      fixed), no Tier 5 present (correctly appended), marker present with
      blank template (correctly skipped, unchanged), and heading present
      but still blank with no marker (still appends a duplicate — a known,
      pre-existing, and out-of-scope edge case; see "What was NOT
      verified" in `ACTION_ITEMS.md` C40 and section 4 above).

## 10. What was NOT verified

This cannot be exercised by a live GitHub Actions run in this sandbox —
no `pull_request` webhook trigger is available here, so
`actions/github-script` was never actually executed against a real PR.
Verified instead by direct code reading plus the standalone-script trace
described above. Also not verified: GitHub's actual PATCH-body behavior
when `pulls.update` writes the new body (assumed to behave as documented);
whether any other bot/integration on this repo also edits PR bodies in a
way that could interact with this step (not investigated — out of scope
of this fix, which only touches read-then-conditionally-write logic
already isolated to this one step).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live
      data touched)
- [x] Blast radius is stated, not assumed — isolated to one step in one
      workflow file
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — UX effect (section 5) stated explicitly; this
      only reduces a previously-observed noisy/duplicate CI side effect,
      does not change what the templates require
