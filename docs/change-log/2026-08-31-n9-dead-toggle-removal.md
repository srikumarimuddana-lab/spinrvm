# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (frontend follow-up, on behalf of vikas@ngitservices.com) |
| Surface(s) | rider-app / driver-app |
| Domain (Sentry tag) | rides (settings screen; not a rides-flow change, closest existing domain tag — no dedicated "settings"/"notifications" tag exists) |
| PR / commit link | (local commit, not pushed) |
| Related issue or gap ID | ACTION_ITEMS.md N9 (frontend follow-up) |

## 1. Issue / gap identified

N9's backend-only pass (2026-08-11) determined 3 of 5 `notification_preferences`
columns (`sms_enabled`, `safety_alerts`, `promotions`) are dead — no safe backend
call site exists to gate on them — but left the corresponding rider-app/driver-app
settings-screen toggles in place, so riders/drivers could flip a switch that does
nothing (or, for `safety_alerts`, one that would be actively misleading if it
existed).

## 2. Root cause

The columns were added speculatively ahead of a wiring decision; N9's investigation
concluded no safe wiring exists for these three (see ACTION_ITEMS.md N9 for the
full per-column reasoning), but the frontend toggles that write to them were never
removed in the same pass — flagged explicitly as a follow-up, not done.

## 3. Fix / remediation

Removed the UI toggle, its local state, and its sync-effect wiring for each column
that had one:
- `sms_enabled` — rider-app only had this toggle ("SMS Notifications" in
  `app/settings.tsx`). Removed.
- `promotions` — driver-app only had this toggle ("Promotions & Offers" in
  `app/driver/settings.tsx`). Removed (not repointed to
  `marketing_preferences` — that stays an undecided product call per N9's own
  text).
- `safety_alerts` — grepped both apps' `app/`, `i18n/`, `store/` trees; no
  toggle exists in either app's UI for this column. Nothing to remove.

Backend (`notification_preferences` table/columns, `db_supabase.py`, any Python
route) is untouched — out of scope per task and per N9's own backend/frontend
split.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two settings screens.** Grepped both apps'
  `app/`, `i18n/`, `store/`, and `shared/hooks/queries` trees for
  `sms_enabled`, `safety_alerts`, `promotions`, `smsEnabled`, `setPromotions`
  before and after the change — no other reader/writer of these fields exists
  in either app. `useUpdateNotificationPreferences` is a generic
  `{[key]: value}` PATCH pass-through (`shared/hooks/queries/notificationQueries.ts`)
  used by every toggle on both screens; removing two of its callers doesn't
  change its behavior for the remaining callers (`push_enabled`,
  `email_enabled`, `ride_updates`, `earnings_summary`).
- No other screen reads or writes `sms_enabled`/`promotions` — `rider-app`'s
  `/promotions` and `/notifications` routes are unrelated features (a
  promo-code list and a notification *feed*, respectively — not preference
  toggles), confirmed by reading both files.
- Removing the toggle stops the app from ever sending
  `{sms_enabled: ...}` / `{promotions: ...}` PATCH bodies going forward. The
  backend columns and any existing stored values are untouched and remain
  harmless dead data, per N9's own backend note ("nothing reads them").
- No ride state, money, auth, or safety code path touched.

## 5. User-experience effect

- Rider-facing: the "SMS Notifications" row disappears from rider-app
  Settings → Notifications. It previously did nothing (no backend call site
  reads `sms_enabled`), so this removes a switch that gave a false sense of
  control, not a working feature.
- Driver-facing: the "Promotions & Offers" row disappears from driver-app
  Settings → Notifications. Same reasoning — previously inert.
- Visible mid-session only in the trivial sense that a driver/rider with the
  Settings screen open before an app update would see the row on next visit;
  no state is lost since the column was never actually gating anything.
- No change to `safety_alerts` UX since no such toggle existed to begin with.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/settings.tsx` | Removed `smsEnabled` state, its sync-effect line, and the "SMS Notifications" `SettingToggle` row | Toggle wrote to dead `sms_enabled` column |
| `rider-app/__tests__/settingsScreen.test.tsx` | Removed/updated 4 test assertions referencing the SMS toggle; updated a stale comment | Keep tests matching the screen |
| `rider-app/i18n/en-CA.json` | Removed `settings.sms_notifications` / `settings.sms_notifications_subtitle` keys | Orphaned by the toggle removal |
| `rider-app/i18n/fr-CA.json` | Same as above, French locale | Same |
| `driver-app/app/driver/settings.tsx` | Removed `promotions` state, its sync-effect line, and the "Promotions & Offers" `renderToggle` row | Toggle wrote to dead `promotions` column |
| `driver-app/__tests__/app/driverSettingsScreen.test.tsx` | Removed the `promotions` case from the parametrized toggle test, updated the hydration test and doc comment | Keep tests matching the screen |
| `driver-app/i18n/en.json`, `fr.json`, `es.json` | Removed `settings.promotions` / `settings.promotionsDesc` keys | Orphaned by the toggle removal |
| `ACTION_ITEMS.md` | Marked N9's three "frontend follow-up" notes done/checked with what was found | Close the loop N9 left open |

## 7. Before / after

```tsx
// Before — rider-app/app/settings.tsx
const [pushEnabled, setPushEnabled] = useState(true);
const [emailEnabled, setEmailEnabled] = useState(true);
const [smsEnabled, setSmsEnabled] = useState(false);
...
<SettingToggle icon="chatbubble" iconColor="#10B981" iconBg="#ECFDF5"
  title={t('settings.sms_notifications')} subtitle={t('settings.sms_notifications_subtitle')}
  value={smsEnabled} onToggle={handleNotificationToggle('sms_enabled', setSmsEnabled)} />
```

```tsx
// After
const [pushEnabled, setPushEnabled] = useState(true);
const [emailEnabled, setEmailEnabled] = useState(true);
// (SMS Notifications row removed)
```

```tsx
// Before — driver-app/app/driver/settings.tsx
const [promotions, setPromotions] = useState(false);
...
{renderToggle(t('settings.promotions'), t('settings.promotionsDesc'), promotions, handleToggle('promotions', setPromotions), 'gift', colors.primaryDark)}
```

```tsx
// After
// (promotions state and row removed)
```

## 8. Rollback plan

Pure `git revert` of this commit is sufficient and complete — no data was
touched, no migration ran, no live-tested surface state changed. The
`notification_preferences.{sms_enabled,safety_alerts,promotions}` columns
still exist server-side untouched, so re-adding the toggle later (or
reverting this commit) restores identical prior behavior.

## 9. Verification performed

- [x] Automated tests run: `yarn test __tests__/settingsScreen.test.tsx` (rider-app,
      22/22 passed) and `yarn test __tests__/app/driverSettingsScreen.test.tsx`
      (driver-app, 30/30 passed).
- [x] `yarn tsc --noEmit` run clean in both `rider-app` and `driver-app`
      (real TypeScript compile, not just dev-server).
- [x] Blast-radius grep performed: `sms_enabled`, `safety_alerts`, `promotions`,
      `smsEnabled`, `setPromotions` across both apps' `app/`, `i18n/`, `store/`
      trees, and `shared/hooks/queries` — no other consumer found.
- [ ] No manual on-device/simulator run performed (no active visual-regression
      tooling exists for rider-app/driver-app per CLAUDE.md gate 6 — flagged,
      not newly discovered).
- No snapshot tests exist for either settings screen (confirmed via
  `find __tests__ -iname "*snap*"` in both apps, no results), so nothing to
  update there.

## 10. What was NOT verified

- Not run on a real device/simulator — verified via `tsc --noEmit` and Jest
  only, consistent with CLAUDE.md's standing note that neither app has active
  visual-regression coverage.
- Did not verify whether any already-installed app build currently has a
  cached/stale `sms_enabled`/`promotions` value sitting in a user's local
  notification-prefs query cache; this is a UI-only removal and such state
  simply stops being read from now on — no migration of client-side cache
  needed.
- Did not touch or verify the backend `notification_preferences` table/columns —
  out of scope per task instructions and per N9's backend/frontend split.
