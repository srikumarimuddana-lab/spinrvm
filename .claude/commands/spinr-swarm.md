# /spinr-swarm — Run One Autonomous Improvement Cycle

Execute one full cycle of the swarm protocol (`docs/framework/08-swarm-protocol.md`): pick the highest-value safe improvement, take it through brainstorm → debate → implement → adversarial test → validate → document, and end with a ranked next recommendation. This is the standing answer to "make the app better" with no narrower task given.

## Usage

```
/spinr-swarm                       # discovery mode: pick from ACTION_ITEMS.md, else sweep
/spinr-swarm <area or file>        # cycle scoped to an area (e.g. dispatch, rider-app booking)
/spinr-swarm audit                 # discovery sweep only — report + backlog entries, no implementation
```

## 1 · Observe & pick (discovery mode)

1. Read `ACTION_ITEMS.md` open `[ ]` items. Highest band first (P0 → P4); within a band, rank by `(user × business × reliability × security impact × confidence) / effort`.
2. Skip items marked as needing a human with dashboard access (most P2 operational items) — report them as blocked-on-human instead of simulating them.
3. Only if no open item fits the scope: run a discovery sweep (`/full-audit` on the area, or targeted reading) and file new findings into `ACTION_ITEMS.md` before working one.
4. State the pick and why in one paragraph, with the value ranking.

## 2 · Understand before touching

- Read the relevant domain contract (`.claude/context/domain-*.md`), any ADR or decision write-up covering the area, and the actual code.
- **Blast radius first**: grep every caller/reader/importer of what will change; write the list down.
- Check the deliberate-decisions list (fare-estimate wait, retention model, dual-import, unredacted export, …) — if the "problem" is a recorded decision, stop and report instead of fixing.

## 3 · Brainstorm & debate

- Produce **Option A (minimal) / B (moderate) / C (strategic)** with the 10-dimension score table from the swarm protocol.
- Run the debate: dispatch the relevant `spinr-*` reviewer agents on the *proposal* (not just the diff later). Contested → resolve by evidence, testing competing hypotheses where cheap.
- Genuinely user-owned trade-off → `AskUserQuestion`. Otherwise decide and record the decision.

## 4 · Implement under the constitution

All of `CLAUDE.md` applies unchanged: ≤3-file subtasks committed one at a time, ~200-line diffs, surgical changes, Decimal money, state-machine guards, required scaffolding never "simplified" away, feature flags via `app_settings` for user-visible changes, additive over destructive.

## 5 · Test, then attack

1. Repo's own checks for every touched surface: `ruff check` + `pytest` (backend), `jest` (rider/driver), `vitest` (admin) — plus a real production build for frontend changes.
2. Regression test for the fixed behavior (reproduce first, then pass).
3. **Adversary pass** on the change itself: races, partial failure, dependency outage, malicious/stale clients, 10× load reasoning. Fix what breaks; retest.
4. Route the final diff through `/review` (or `/full-audit` for cross-cutting changes).

## 6 · Document & close

- Behavior-changing → full Change Impact Log (`docs/templates/CHANGE_IMPACT_LOG.md`), including "What was NOT verified" and a live-data rollback plan.
- Update `ACTION_ITEMS.md` (mark the item, or file follow-ups discovered mid-cycle).
- Commit per subtask; push; PR per `/pr` conventions.

## 7 · Report

End every cycle with the protocol's report shape — Discovery · Impact · Root cause · Options+scores · Adversarial review · Decision · Implementation · Validation (commands + counts) · Results · Remaining risks · **Next recommendation** — with every claim labeled KNOWN / INFERRED / TESTED / UNTESTED / UNKNOWN and confidence HIGH/MEDIUM/LOW. No "definitely safe", no "production ready" without evidence.

## Hard bounds

- Production is immutable without explicit human approval: no prod DB writes, credential rotation, irreversible migrations, mass user changes, payment-config changes, or disabling of any security control/gate/hook — ever, from this command.
- Never weaken a safety mechanism to go faster; gate decay gets a `[CR]` issue.
- One cycle per invocation: finish (or cleanly hand off) the picked item before proposing the next.
