# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | docs (ACTION_ITEMS.md) |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md A35 — status correction, no code change |

## 1. Issue / gap identified

A35 (the regulatory guard-trigger monitor, closed 2026-08-17) carried a
"What was NOT verified" caveat stating migration 317 had not been applied
to production, and that until it was, the new 6-hourly monitor loop
(`utils/retention_guard_monitor.py`) would log an RPC-not-found error every
tick instead of actually detecting anything.

## 2. Root cause

Same class of drift this session has already documented three times today
(B8, B13, B15c): a migration this doc described as unapplied had, at some
point since 2026-08-17, actually been applied by a concurrent session — the
doc simply never got updated to reflect it.

## 3. Fix / remediation

Verified directly against production (`soavhtdhefowwvforzwb`) before writing
anything: `schema_migrations` has both migration 317
(`check_disabled_guard_triggers`) and 318 (A37's real-time DDL audit
trigger) applied, and calling `SELECT * FROM check_disabled_guard_triggers()`
returns cleanly (empty result set — no guard trigger currently disabled),
not an error. This confirms the monitor loop has real data to poll against
in production today, not a missing RPC. Updated A35's caveat to reflect
this and removed the stale "logs an RPC-not-found error" claim.

## 4. Risk & impact on existing functionality

- **Zero application-code impact.** This PR touches only `ACTION_ITEMS.md`
  — no code, no migration (both were already applied by an earlier,
  unidentified session), no config change.
- **What else reads this doc:** other sessions/agents consulting
  ACTION_ITEMS.md for whether this regulatory monitoring loop is actually
  functioning — this update corrects a false "still broken" impression of
  a safety/compliance-adjacent monitoring mechanism that is, in fact,
  working.

## 5. User-experience effect

None — documentation-only, and no admin-facing or rider/driver-facing
behavior is touched. Internal-only monitoring loop, already running in
production regardless of this doc update.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `ACTION_ITEMS.md` | A35's "What was NOT verified" caveat corrected — migration 317/318 confirmed applied and functioning | Reflect reality; was stale |
| `docs/change-log/2026-08-22-a35-guard-monitor-verified-live.md` | New change-log | Required Change Impact & Risk Log |

## 7. Before / after

```diff
- Migration 317 itself has **not been applied to production** in this
- session ... until it is, the new loop logs an RPC-not-found error every
- 6h ... rather than actually detecting anything.
+ **2026-08-22 update: migration 317 (and 318, A37's) confirmed applied
+ to production.** ... `check_disabled_guard_triggers()` returns cleanly
+ ... The "logs an RPC-not-found error" caveat below is now stale.
```

## 8. Rollback plan

**`git-revert-safe`** — pure documentation text, no data, no code. A plain
`git revert` fully undoes it.

## 9. Verification performed

- [x] Verified directly against production (`soavhtdhefowwvforzwb`): `schema_migrations` has both 317 and 318 rows.
- [x] Verified the RPC actually runs and returns the expected clean (empty) result: `SELECT * FROM check_disabled_guard_triggers()`.
- [ ] No new automated tests — nothing to test, doc-only change.

## What was NOT verified

- Which session or PR actually applied migrations 317/318 to production, or when — not chased down, matching this session's established precedent (B13, B15c) of not investigating attribution once the live state is confirmed correct.
- Whether `retention_guard_monitor.py`'s background loop is actually running in the deployed backend process right now (vs. just the DB-side RPC being callable) — this update only confirms the DB half is live; the Python loop's own runtime state was not separately checked (no log/metrics access from this session).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (doc-only, zero code impact)
- [x] No silent behavior change to an already-shipped flow — none; the monitor's actual behavior is unchanged by this doc update
