# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (agent session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | safety |
| PR / commit link | (this commit) |
| Related issue or gap ID | Follow-up to the same gap closed for `route_deviation_alert_enabled` (#5285) — same UI gap, different pre-existing flag |

## 1. Issue / gap identified

`app_settings.stale_in_progress_ride_alert_enabled` (the kill switch for
`utils/stale_in_progress_ride_alerter.py`, which pages ops when an
`in_progress` ride's driver has gone silent for 10+ minutes) has no toggle
on the admin Settings page — same class of gap as `route_deviation_alert_enabled`
before #5285, just on an older, already-live alert instead of a new one.

## 2. Root cause

Same root cause as #5285: the Settings page hardcodes one Switch per exposed
`app_settings` key, and this particular flag never got one when its loop was
added. Not a bug introduced here — a pre-existing gap the user asked to close
as an explicit follow-up to #5285.

## 3. Fix / remediation

Added one Switch to the existing "Kill Switches" card (Operations tab),
directly below "Corporate billing" — that card is the right home here
(unlike `route_deviation_alert_enabled`, which went in the "Safety alerts"
card): this flag defaults `True` and is framed exactly like the card's other
four entries ("pause a risky automatic subsystem in seconds... all default
on — flipping one off stops new work only"), whereas `route_deviation_alert_enabled`
defaults `False` and is a dark-launch flag, not a pause switch.

Checked-state note: this card's other four switches use `!!settings.<key>`
(renders OFF while `settings` is still loading, even though their real
default is `True`). For this flag I instead used
`settings.stale_in_progress_ride_alert_enabled !== false` — matching the
pattern this same file already uses for other `default: true` flags (the SOS
panel's `sos_show_share_trip`/`sos_show_report_issue`), so the switch reads
correctly as ON both before the settings fetch resolves and after, rather
than flashing OFF on load. Did not change the existing four switches'
`!!` pattern — out of scope, not something this task touched.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped `admin-dashboard/` for
  `stale_in_progress_ride_alert_enabled` — the only match, before and after
  this change, is the one line added here.
- Same generic `settings` state + `update(key, value)` helper as every other
  toggle on the page; the helper itself is unmodified.
- No background loop, ride-state, or money path touched. The
  `stale_in_progress_ride_alerter` loop itself is untouched — this only
  gives admins a UI path to the flag it already reads.
- **Visual-regression: reasoned through and expected to pass, same as
  #5285.** `/dashboard/settings` opens on `<Tabs defaultValue="integrations">`
  (`settings/page.tsx:298`); the Kill Switches card lives inside
  `TabsContent value="operations"` (`settings/page.tsx:1007`), not the
  default tab. The visual-regression spec screenshots the page on load
  without switching tabs, so this change should be structurally outside
  what the seeded `dashboard-settings` baseline captures — same reasoning
  verified structurally for #5285's toggle, and this PR's own CI run will
  confirm it directly rather than leaving this as an assumption.

## 5. User-experience effect

- **Internal admin only.** No rider/driver/corporate-admin-facing change.
- Visible only to admins who open Settings → Operations → Kill Switches.
  Not visible mid-session to anyone already using the rider/driver apps.
- No behavior change until an admin actually flips this switch — the
  alerter loop's current (already-live, default-on) behavior is unaffected
  by merging this PR.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Added one label/description/`Switch` block bound to `settings.stale_in_progress_ride_alert_enabled` inside the existing "Kill Switches" card | Give ops a UI path to pause this already-live alert, matching the fix already shipped for `route_deviation_alert_enabled` in #5285 |
| `docs/change-log/2026-09-12-stale-ride-alert-admin-toggle.md` | New Change Impact Log entry (this file) | Required for any change touching a live-tested surface (safety) per `CLAUDE.md` |

## 7. Before / after

Purely additive.

```tsx
<Switch
    id="stale-ride-alert-enabled"
    aria-label="Stale in-progress ride alert enabled"
    checked={settings.stale_in_progress_ride_alert_enabled !== false}
    onCheckedChange={(v) => update("stale_in_progress_ride_alert_enabled", v)}
/>
```

## 8. Rollback plan

`git revert` is fully sufficient: boolean `app_settings` flag through the
page's existing generic save path, no migration, no data mutation. The
already-live alerter loop and its current flag value are unaffected either
way. An admin can also just toggle it back and Save — no deploy needed.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean (repo-wide, no new errors)
- [x] `npx eslint src/app/dashboard/settings/page.tsx` — 0 errors, same 5
      pre-existing warnings the file already had, none introduced by this
      change (the new paragraph's apostrophe is already `&apos;`-escaped)
- [x] **Real production build**: `npm run build` — succeeded (exit code 0)
- [x] Blast-radius grep: `stale_in_progress_ride_alert_enabled` across
      `admin-dashboard/` — one match (this change) before and after
- [x] Confirmed the backend already declares this field in
      `SettingsUpdateRequest` (`backend/schemas.py:514`) — not a new field,
      already accepted by the existing save endpoint
- [ ] Not manually clicked through in a running browser session in this
      sandbox (no live admin auth/session available here) — reasoned
      through from the identical, already-working pattern of the card's
      other four switches, not screenshotted
- [ ] Not verified against a real Supabase project — the save round-trip
      (`PUT /api/admin/settings`) is unchanged code

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, or toggle
      back + Save — no deploy required either path)
- [x] Blast radius is stated, not assumed (isolated; grep performed and
      documented above)
- [x] No silent behavior change to an already-shipped flow — this only adds
      a new, previously-inaccessible control; the loop's current (live,
      default-on) behavior is unchanged by this PR
- [ ] **Visual-regression: expected to pass** (structurally reasoned, same
      basis as #5285's confirmed-passing result) — this PR's own CI run will
      confirm directly; if it unexpectedly fails, treat that as a real
      finding to investigate, not dismiss.
