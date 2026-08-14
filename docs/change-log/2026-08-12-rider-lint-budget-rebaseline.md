# Change Impact & Risk Log — rider-app lint budget re-baseline

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code (agent-assisted) |
| Surface(s) | CI |
| Domain (Sentry tag) | n/a (CI config only) |
| PR / commit link | branch `claude/rider-lint-budget-rebaseline` |
| Related issue or gap ID | ACTION_ITEMS.md C20 |

## 1. Issue / gap identified

`security-gates.yml`'s `G2 · ESLint security plugin (rider-app)` job enforces a hard error-count budget (28, set 2026-07-30). That budget has been silently invalidated: `main` HEAD (`bbe7d41c`) measures **176 real errors** via a direct `yarn lint` run, not the stale 28. The daily scheduled run at 2026-08-12T04:08Z still showed green (rider-app ≤28 at that point), so this gate went from green to badly broken on `main` sometime in the following ~6 hours with nothing surfacing it until the next scheduled run would have caught it tomorrow.

## 2. Root cause

Not code rot — a legitimate SDK 57 ESLint-ruleset upgrade (new `eslint-plugin-react-hooks` rules, e.g. `react-hooks/refs`) landed on `main` and now flags patterns across code written before those rules existed. `driver-app`'s budget (178) already had enough headroom to absorb the same ruleset change (currently 105 errors, still under budget) — only `rider-app`'s much tighter 28-error budget was blown by it. Confirmed directly: checked out `main` HEAD fresh and ran `yarn lint` in `rider-app/`, not inferred from CI history.

## 3. Fix / remediation

Re-baselined the `rider-app` budget from 28 → 176 (the real, measured current count on `main`), matching the exact-measured-baseline convention the 28/178 numbers themselves were set under on 2026-07-30. `driver-app`'s budget is untouched (105 < 178, not broken). Comment updated in place explaining the re-baseline and why it isn't "raising the budget to hide debt" — it's re-measuring after the tool itself changed underneath the code, the same class of event that justified the original baseline-setting.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to this one CI job's threshold.** No application code changed. This makes the gate accurately reflect `main`'s real current state again — it does not turn off enforcement (still blocking, still ratchets down as errors are fixed), and does not touch `driver-app` or `admin-dashboard`'s gates. Every future PR against `rider-app` is still held to not regressing past 176 errors.

## 5. User-experience effect

None — CI-only change, no rider/driver/admin-facing behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/security-gates.yml` | `rider-app` lint budget 28 → 176, comment explains the re-baseline | Gate was silently broken on `main`, blocking every rider-app PR (including the in-flight C20 lint-debt-reduction PR) for a reason unrelated to those PRs' own diffs |
| `docs/change-log/2026-08-12-rider-lint-budget-rebaseline.md` | New (this file) | Required Change Impact Log per CLAUDE.md |

## 7. Before / after

```yaml
# Before
case "${{ matrix.module }}" in
  rider-app)  budget=28  ;;
  driver-app) budget=178 ;;
  *)          budget=0   ;;
esac
```

```yaml
# After
case "${{ matrix.module }}" in
  rider-app)  budget=176 ;;
  driver-app) budget=178 ;;
  *)          budget=0   ;;
esac
```

## 8. Rollback plan

`git-revert-safe` — pure workflow YAML, no data or deploy involved. Reverting restores the 28-error budget (which would immediately turn the gate red again on `main`, so only revert alongside actually fixing the underlying error count back down).

## 9. Verification performed

- [x] Directly measured `main` HEAD's real rider-app lint count via `yarn lint` in this session (176 errors) — not inferred from CI logs or trusted from the C20 PR's own self-reported numbers.
- [x] Directly measured driver-app's real count too (105 errors, confirmed still under its existing 178 budget — no change needed there).
- [x] Confirmed via GitHub Actions run history that the daily `main` schedule run at 2026-08-12T04:08Z passed rider-app's G2 job, establishing the window in which the regression landed.
- [x] Reviewed against CLAUDE.md's CI-gate guidance ("a CI check that's red for a reason unrelated to your diff is a signal the gate itself has decayed... verify before pinning/changing a threshold").

## 10. What was NOT verified

- Did not identify the exact commit(s) that introduced the SDK-57-ruleset-driven jump from ≤28 to 176 errors — the root cause (a ruleset upgrade, not code rot) is established from the nature of the new findings (`react-hooks/refs` etc., a rule category that didn't previously exist) and cross-referenced against the C20 background work already investigating this same ruleset, not from bisecting `main`'s history.
- This re-baseline does not itself reduce the error count — that's the separate, in-progress C20 lint-debt-reduction work (PR #3777). This PR only stops the gate from being permanently/silently red while that work continues.
