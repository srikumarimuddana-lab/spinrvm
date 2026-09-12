# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (agent session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | safety |
| PR / commit link | (this commit) |
| Related issue or gap ID | Follow-up to #5269 (`feat(safety): add live route-deviation safety alert`) |

## 1. Issue / gap identified

PR #5269 added a new `app_settings` kill-switch, `route_deviation_alert_enabled`
(default `False`), gating the new route-deviation safety-incident background
loop. #5269 was backend-only by design ("Surfaces touched: backend, docs") —
it never added an admin-dashboard control for the flag. The user checked the
admin Settings page after that PR merged and confirmed the flag has no toggle
there, so today the only way to flip it on is a direct write to the
`app_settings` table (e.g. via Supabase console/SQL), not through the admin UI.

## 2. Root cause

Not a bug in #5269 — a known, disclosed gap in that PR ("needs a human to
verify in staging and flip on"), just not accompanied by the UI control that
"flip on" implies. This also isn't unique to this flag: the admin-dashboard
settings page hardcodes one Switch per exposed `app_settings` key (no generic
key/value editor), and several other existing background-loop kill switches
(e.g. `stale_in_progress_ride_alert_enabled`) have the same gap. This PR closes
it only for `route_deviation_alert_enabled`, since that's the one the user
flagged; the others are pre-existing and out of scope here.

## 3. Fix / remediation

Added one Switch control to the existing "Safety alerts" card on
`/dashboard/settings` (Email & Alerts tab), directly below the safety-alert
email recipients field. Uses the page's existing generic `settings` state +
`update(key, value)` helper and the existing "Save Changes" button/`PUT
/api/admin/settings` round-trip — no new API call, no new state shape. The
backend already declares this field in `SettingsUpdateRequest`
(`backend/schemas.py:520`, `extra="ignore"` model) so the existing save path
persists it correctly; verified this explicitly rather than assuming it,
since a 2026-09-03 bug (documented inline in `settings/page.tsx`) previously
let an UNdeclared field silently no-op on save.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped `admin-dashboard/` for
  `route_deviation_alert_enabled` — the only match, before and after this
  change, is the one line added here. No other component reads or writes
  this key.
- The generic `settings` state (`useState<any>(null)`) and `update()` helper
  are shared by every other toggle on this page (`scheduled_dispatch_enabled`,
  `surge_engine_enabled`, `admin_theme_v2_enabled`, etc.) — this change adds a
  new key to that same object, it does not modify the helper or any existing
  key's behavior.
- No background loop, ride-state, or money path touched. Flipping the new
  switch only changes what the existing `route_deviation_alerter` loop
  (already live from #5269, currently reading the flag as `False`) does on
  its next 30s tick — this PR does not change that loop's code at all.
- **Visual-regression note:** `/dashboard/settings` is one of the 6 pages with
  a seeded, CI-blocking Playwright visual-regression baseline
  (`e2e/visual-regression.spec.ts`, per `CLAUDE.md`'s Pre-merge gate #6). This
  is a real, intended UI change to that page, so the baseline screenshot for
  `dashboard-settings` **will** diff and the visual-regression CI check is
  expected to fail on this PR until a human re-runs
  `update-visual-baselines.yml` (this agent has no Actions-dispatch access to
  do that itself) — this is not a spurious/flaky failure, it's the new toggle
  rendering.

## 5. User-experience effect

- **Internal admin only.** No rider/driver/corporate-admin-facing change.
- Visible only to admins who open Settings → Email & Alerts → Safety alerts.
  Not visible mid-session to anyone already in the app (rider/driver apps
  untouched; this is an admin-only, admin-dashboard-only control).
- The underlying alert behavior does not change until an admin actually
  flips this new switch on and saves — same dark-launch state as #5269 left
  it in.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Added a `Separator` + one label/description/`Switch` block bound to `settings.route_deviation_alert_enabled` inside the existing "Safety alerts" card | Give ops a UI path to enable the #5269 alert after staging verification, instead of requiring a direct DB write |
| `docs/change-log/2026-09-12-route-deviation-alert-admin-toggle.md` | New Change Impact Log entry (this file) | Required for any change touching a live-tested surface (safety) per `CLAUDE.md` |

## 7. Before / after

Purely additive — no existing behavior changed. Before this change, the
"Safety alerts" card had one field (recipient emails) and no toggle for this
flag; after, it has that field plus:

```tsx
<Switch
    id="route-deviation-alert-enabled"
    aria-label="Route-deviation safety alert enabled"
    checked={!!settings.route_deviation_alert_enabled}
    onCheckedChange={(v) => update("route_deviation_alert_enabled", v)}
/>
```

## 8. Rollback plan

`git revert` is fully sufficient here: this change reads/writes only a
boolean `app_settings` flag through the page's existing generic save path —
no migration, no data mutation, nothing applied to live rider/driver state.
Reverting the commit removes the switch; the underlying flag (and the
already-live #5269 loop reading it) is unaffected either way. If a revert is
not wanted but the flag needs to go back off immediately, an admin can also
just toggle the switch off and Save — no deploy needed either way.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean (repo-wide, no new errors)
- [x] `npx eslint src/app/dashboard/settings/page.tsx` — 0 errors, the same 5
      pre-existing warnings the file already had, none near this change
- [x] **Real production build**: `npm run build` — succeeded (exit code 0),
      `/dashboard/settings` compiled as part of the full route manifest
- [x] Blast-radius grep performed: `route_deviation_alert_enabled` across
      `admin-dashboard/` — one match (this change) before and after
- [x] Reviewed against `CLAUDE.md`'s Settings-in-DB / feature-flag convention
      — confirmed the backend already accepts this key via
      `SettingsUpdateRequest` (`backend/schemas.py:520`) rather than assuming it
- [ ] Not manually clicked through in a running browser session in this
      sandbox (no live admin auth/session available here) — reasoned through
      from the existing, identical pattern of every other toggle on this same
      card/page, not screenshotted
- [ ] Not verified against a real Supabase project — the save round-trip
      (`PUT /api/admin/settings`) is unchanged code, exercised only by
      existing test coverage on that endpoint, not re-tested live here

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, or toggle
      off + Save — no deploy required either path)
- [x] Blast radius is stated, not assumed (isolated; grep performed and
      documented above)
- [x] No silent behavior change to an already-shipped flow — this only adds
      a new, previously-inaccessible control; every existing toggle/save
      behavior on the page is untouched
- [ ] **Visual-regression baseline will need re-seeding** — flagging this
      explicitly per `CLAUDE.md`'s gate #6 rather than assuming the diff is
      spurious: `dashboard-settings` is a seeded baseline page, this is a
      real UI change, and a human with Actions-dispatch access needs to run
      `update-visual-baselines.yml` after confirming the diff is exactly this
      new switch.
