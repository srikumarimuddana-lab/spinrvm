# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude (agent session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin (reads `driver_insurance_periods`, a `drivers`/`safety`-adjacent regulatory table, through an admin-only route) |
| PR / commit link | (local worktree at commit time, branch `claude/spinr-mongodb-migration-u9y6iz`) |
| Related issue or gap ID | `ACTION_ITEMS.md` A41, Oct 30 checklist item #5(b) in `docs/runbooks/legacy-migration-playbook.md` |

## 1. Issue / gap identified

Migration 332 added `driver_insurance_periods.is_reconstructed` (true when a period
span's boundaries were backfilled from timestamps during legacy migration rather than
logged live) and it was already wired into the regulator-facing
`scripts/compliance_export.py` export, but no admin-dashboard screen surfaced it —
an admin looking at a driver's insurance-period history in the app had no way to
tell a reconstructed span from a contemporaneously-logged one.

## 2. Root cause

`is_reconstructed` was added to the `driver_insurance_periods` table and to the
regulator export in a prior pass, but the admin-dashboard side of the same task
(checklist item #5(b)'s second half) was never picked up — confirmed by grep, no
`is_reconstructed` reference existed anywhere in `admin-dashboard/src/` before this
change.

## 3. Fix / remediation

No new screen was added. `backend/routes/admin/drivers.py`'s
`admin_driver_daily_activity` and `backend/routes/admin/driver_distance.py`'s
`admin_driver_distance_logs` were the two existing admin routes that already read
`driver_insurance_periods`; only the latter (`GET /drivers/{id}/distance-logs`)
lists individual spans one row at a time (`logs[]`, one entry per span clipped to
the Regina day) — the former aggregates spans into per-phase second/km totals
(`period_seconds` summed across all matching rows) and has no natural per-row slot
for a per-span boolean. `admin_driver_distance_logs` was therefore extended with an
additive `is_reconstructed` field per log entry, sourced directly from the
underlying span row (`s.get("is_reconstructed", False)`), and the admin-dashboard
`DayLogs` component (inside
`admin-dashboard/src/app/dashboard/drivers/_components/driver-distance.tsx`, the
per-day drill-down already rendered under the Distance tab's day rows) renders a
small "Reconstructed" badge next to the existing phase badge when the flag is true,
styled to match the existing "Imported" badge convention
(`docs/change-log/2026-08-19-legacy-migration-transparency-admin-dashboard.md`:
`bg-muted`, `text-[10px]`, `rounded px-1.5 py-0.5`).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one backend route, one frontend component, one test
  file.** Grepped for every other consumer:
  - Backend: `grep -rn "distance-logs\|admin_driver_distance_logs" backend --include=*.py`
    → only `backend/routes/admin/driver_distance.py` (the route itself) and
    `backend/tests/test_admin_driver_distance.py` (its tests). No other route or
    background loop reads this endpoint's response.
  - Frontend: `grep -rn "distance-logs\|distanceLogs\|DistanceLogs" admin-dashboard/src`
    → only `admin-dashboard/src/lib/api/drivers.ts` (`getDriverDistanceLogs`, the
    thin fetch wrapper) and `admin-dashboard/src/app/dashboard/drivers/_components/driver-distance.tsx`
    (its sole caller, `DayLogs`). No other page or component imports `DayLogs` or
    calls `getDriverDistanceLogs`.
  - The route is already gated behind `require_module("drivers")` in
    `backend/routes/admin/__init__.py` (same gate as the driver list/detail routes)
    — no new gating was added or needed, and no other module claim can now reach
    this field that couldn't already reach the rest of the response.
  - `driver_insurance_periods` itself was not written to — this change only reads
    an existing column. The append-only trigger and every writer of that table
    (`stale_p3_closer.py`, `insurance_periods.py`, `routes/users.py`,
    `services/data_transfer/bundle_document_uploader.py`, the ride-flow completion
    path, migration 332's backfill) is untouched.
- **Additive-only response shape.** No existing field in `logs[]` was renamed,
  reordered, or changed in meaning; one new key was appended per entry. A caller
  that doesn't know about `is_reconstructed` (there are none besides the one
  frontend component above) is unaffected.
- No ride state, dispatch, payment, or wallet code path was touched.

## 5. User-experience effect

- **Internal-admin-facing only** — visible solely inside the admin dashboard's
  Drivers → Distance tab → per-day drill-down, gated behind the `drivers` admin
  module. No rider, driver, or corporate-admin-facing surface changed.
- Not visible mid-session to any rider or driver already using the consumer/driver
  apps — this table has no reader outside admin.
- The badge is diagnostic/audit information only — it does not change any number,
  total, or ordering already shown; it is purely additive next to an existing badge.
  No copy/notification change to review against the customer-centric tone standard
  (this is not customer-facing copy).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/driver_distance.py` | Added `"is_reconstructed": bool(s.get("is_reconstructed", False))` to each entry in `admin_driver_distance_logs`'s `logs[]` response | Additive field on the existing per-span drill-down response |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-distance.tsx` | Added `is_reconstructed: boolean` to the `LogRow` interface; render a "Reconstructed" badge (with `title` + `aria-label` text alternative) next to the phase badge in `DayLogs` when true | Read-only diagnostic signal for admins, matching the existing "Imported" badge visual convention |
| `backend/tests/test_admin_driver_distance.py` | Extended the existing spans-join test with `is_reconstructed is False` assertions on all three log rows; added a new test asserting `is_reconstructed` passes through per-span (mixed true/false in one day) | Regression coverage for the new field |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-distance.test.tsx` (new) | Two tests: badge renders with correct `aria-label` when `is_reconstructed: true`; no badge renders when `false` | New — no prior test file existed for this component; mirrors the existing `driver-statements-panel.test.tsx` pattern (vitest + Testing Library + `@/lib/api` mock) |
| `docs/runbooks/legacy-migration-playbook.md` | Appended a dated `[RE-VERIFIED 2026-08-20 ...]` annotation to checklist item #5, marking (b) fully done and leaving (a) explicitly open | Keep the playbook's per-item status accurate |
| `ACTION_ITEMS.md` | Added a new "eighth pass" FIXED bullet under A41 documenting this change; updated the earlier "3 of 10 partially addressed" summary line to point at it | Keep A41's tracked status accurate |

## 7. Before / after

**`admin_driver_distance_logs` response entry (`backend/routes/admin/driver_distance.py`):**
```python
# Before
logs.append(
    {
        "from": c_start.isoformat(),
        "to": c_end.isoformat() if s_end or c_end < win_end else None,
        "seconds": int((c_end - c_start).total_seconds()),
        "phase": _PHASE_LABELS[period],
        "period": period,
        "ride_id": s.get("ride_id"),
        "ride_code": ride_codes.get(s.get("ride_id") or "", "") or None,
        "distance_km": float(dist["distance_km"]) if dist and dist.get("distance_km") is not None else None,
        "distance_source": dist.get("source") if dist else None,
        "open": s.get("ended_at") is None,
    }
)

# After
logs.append(
    {
        "from": c_start.isoformat(),
        "to": c_end.isoformat() if s_end or c_end < win_end else None,
        "seconds": int((c_end - c_start).total_seconds()),
        "phase": _PHASE_LABELS[period],
        "period": period,
        "ride_id": s.get("ride_id"),
        "ride_code": ride_codes.get(s.get("ride_id") or "", "") or None,
        "distance_km": float(dist["distance_km"]) if dist and dist.get("distance_km") is not None else None,
        "distance_source": dist.get("source") if dist else None,
        "open": s.get("ended_at") is None,
        "is_reconstructed": bool(s.get("is_reconstructed", False)),
    }
)
```

**`DayLogs` phase cell (`admin-dashboard/.../driver-distance.tsx`):**
```tsx
// Before
<td className="px-3 py-1.5">
    <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${PHASE_TINT[l.period] || ""}`}>{l.phase}</span>
</td>

// After
<td className="px-3 py-1.5">
    <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${PHASE_TINT[l.period] || ""}`}>{l.phase}</span>
    {l.is_reconstructed && (
        <span
            className="ml-1 inline-block rounded px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground bg-muted"
            title="Backfilled from timestamps during legacy migration — not logged live"
            aria-label="Reconstructed: backfilled from timestamps during legacy migration, not logged live"
        >
            Reconstructed
        </span>
    )}
</td>
```

## 8. Rollback plan

Purely additive, read-only, no data written and no schema change (the
`is_reconstructed` column already exists from migration 332, applied in a prior
pass). Rollback is a plain code revert of the three source files above — no feature
flag, config value, or migration rollback is needed:
- Revert `backend/routes/admin/driver_distance.py` — the `logs[]` response drops the
  extra key; the one caller (`DayLogs`) simply stops receiving `is_reconstructed`
  (defaults to `undefined`/falsy in the JSX, no crash).
- Revert `admin-dashboard/.../driver-distance.tsx` — the badge stops rendering.
- No live data, ride state, wallet balance, or Stripe charge is affected by either
  direction of this change, so `git revert` alone is a sufficient rollback plan here
  (the CLAUDE.md caveat about `git revert` not being sufficient applies to changes
  already applied to live *data* — this change never writes any).

## 9. Verification performed

- [x] Automated tests run (unit): `pytest backend/tests/test_admin_driver_distance.py -q`
      — 8 passed (2 new/extended assertions for `is_reconstructed`), against the
      `mock_supabase_client`-style `AsyncMock` pattern already used in that file (no
      real Supabase or Postgres in this session).
- [x] Frontend unit test added/run: `admin-dashboard/.../driver-distance.test.tsx`
      (vitest + Testing Library) — 2 passed (badge renders with the correct
      `aria-label` when `is_reconstructed: true`; no badge renders when `false`).
- [x] `npx tsc --noEmit` on `admin-dashboard` — clean, no errors.
- [x] `npm run build` (real production Next.js build, Turbopack) on
      `admin-dashboard` — **completed successfully** ("Compiled successfully",
      73/73 static pages generated, no errors/failures in the build log).
      `node_modules` was absent in this worktree at task start; `npm install`
      was run first (took ~28s), then the build.
- [x] `ruff check` on both touched Python files
      (`backend/routes/admin/driver_distance.py`,
      `backend/tests/test_admin_driver_distance.py`) — clean except one pre-existing
      `F841` (`ws_iso` unused, line 222) confirmed present on `origin/main` before
      this change via `git stash` + re-run; not introduced by this diff, not touched
      by it.
- [ ] Manual repro / staging check — **not performed**, no staging environment or
      live Supabase credentials available to this session (consistent with every
      other A41 pass this session).
- [x] Blast-radius grep performed — see section 4 above (commands and results
      listed inline).
- [x] Reviewed against relevant CLAUDE.md conventions: append-only rule for
      `driver_insurance_periods` (no write path added — grepped confirmed read-only);
      admin-module RBAC gating (route already behind `require_module("drivers")`,
      unchanged); PIPEDA "never log/expose" list (this field is a boolean derived
      from migration provenance, not PII, GPS, or any listed-forbidden field).
- [ ] Feature-flagged — **not flagged**. Judged unnecessary: this is a read-only,
      internal-admin-only, purely additive diagnostic field with a single caller,
      not a new validation rule, new user-facing copy, or a shared component used
      by 3+ pages (`DayLogs` has exactly one call site). Per CLAUDE.md rule 3, flags
      are for "user-visible and non-trivial" changes on shared surfaces — this is
      neither shared nor a behavior change to an already-working flow (it only adds
      a badge that was previously never rendered).

**Whether a real production build was run:** Yes — `npm run build` (Next.js/
Turbopack production build) was run for `admin-dashboard` and completed
successfully, in addition to `tsc --noEmit` and the vitest unit suite above.

## 10. What was NOT verified

- **No visual/snapshot regression tooling exists in this repo** for the
  admin-dashboard (confirmed by the earlier 2026-08-19 "Imported" badge change-log
  making the same statement, and no such tooling was added since). The visual
  change here (one small muted badge, ~1.5rem tall, appended after the existing
  phase badge) was reasoned about from the Tailwind classes and the existing
  "Imported" badge's known rendered appearance — it was **not** screenshotted.
- **No manual click-through in a running admin-dashboard dev server or staging
  environment** — verified only via the unit test's jsdom render, not a real
  browser.
- **Not tested against live Supabase** — the backend test uses the same
  `AsyncMock`-based mocking pattern as every other test in
  `test_admin_driver_distance.py`; a real `driver_insurance_periods` row with
  `is_reconstructed = true` from an actual migration-332 backfill run was not
  queried (no live database access from this session).
- Part (a) of checklist item #5 (re-running migration 332's reconstruction against
  `driverlocationlogs.csv`'s real phase-boundary timestamps) was explicitly out of
  scope for this pass and was not attempted, touched, or verified in any way.

## 10a. Mandatory manual review (spinr-insurance-period-auditor / spinr-admin-rbac-reviewer / spinr-accessibility-reviewer)

No subagent-dispatch tool was available in this session's toolset (the `Agent`/
`Task` tool referenced by CLAUDE.md's PR-review section was not present), so each
reviewer's own criteria (`.claude/agents/spinr-*.md`) were applied directly by hand
against this diff instead of dispatching a separate agent run. Flagged here rather
than silently skipped, per this project's "escalate, don't silently ship" norm.

- **spinr-insurance-period-auditor** — VERDICT: SAFE TO MERGE. This diff only reads
  `driver_insurance_periods.is_reconstructed`; it contains no INSERT/UPDATE/DELETE
  against that table, no `go_online` change, and no ride-state transition. Rules
  1–5 (period definitions, append-only, document-expiry gating, retention,
  transition completeness) are all not applicable — nothing in this diff writes a
  period row or classifies a period. IMPACT CROSS-CHECK: skipped, no PR exists yet
  (out of scope per the task — another process opens the PR).
- **spinr-admin-rbac-reviewer** — VERDICT: SAFE TO MERGE. No new router was mounted
  and `backend/routes/admin/__init__.py`/`staff.py`'s `AVAILABLE_MODULES`/
  `ROLE_PRESETS` were not touched. The one route extended
  (`admin_driver_distance_logs`) keeps its pre-existing
  `require_module("drivers")` gate unchanged — confirmed still present in
  `routes/admin/__init__.py`. The new field is a boolean migration-provenance
  marker, not PII/financial/impersonation data, so a module gate (not
  `require_super_admin`) remains the correct sensitivity tier per check #3 — no
  escalation needed.
- **spinr-accessibility-reviewer** — VERDICT: LIKELY COMPLIANT. The new
  "Reconstructed" badge is a static `<span>` (not a new interactive element, so no
  new touch-target/keyboard-nav surface), carries visible text plus an `aria-label`
  and `title` (not color-only signaling), and reuses the same `bg-muted`/
  `text-muted-foreground` pairing already shipped for the existing "Imported" badge
  elsewhere in this codebase (no new color pairing introduced). No live region is
  needed — the value is static once the row is expanded, not a ticking/live update.
  Tooling-gap note carried over from section 10 above: no automated accessibility
  or visual-regression tooling exists in this repo, so this is code-level reasoning,
  not a verified screen-reader/contrast-checker pass.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain revert of 3 files, no data
      written by either direction).
- [x] Blast radius is stated, not assumed (see section 4).
- [x] No silent behavior change to an already-shipped flow — the affected flow
      (`DayLogs`) previously never rendered a "Reconstructed" badge at all; the
      field being added is documented as diagnostic, not a change to any existing
      number/behavior.
