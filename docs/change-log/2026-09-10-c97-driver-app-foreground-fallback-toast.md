# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code session |
| Surface(s) | driver-app (`hooks/useDriverDashboard.ts`) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | `ACTION_ITEMS.md` C97 (audit: `docs/audit/2026-09-10-driver-app-notification-delivery-audit.md`, plus its same-day correction) |

## 1. Issue / gap identified

`useDriverDashboard.ts`'s foreground FCM message handler explicitly branches on 5 known
notification types (`new_ride_assignment`, `auto_offline`, `ride_cancelled`,
`subscription_expiring`, `document_expiry_warning`), each showing either the ride-offer panel or
an in-app Alert/Toast. Any *other* type fell through to a final `else if (data?.type)` branch
that only did a `console.warn` and a silent `router.push('/driver/notifications')` — a driver
looking at any other part of the app while foregrounded would never notice anything happened.

## 2. Root cause

This is the narrower, corrected version of the C97 audit's client-side finding (see the audit
doc's own "Correction" section, added same-day after further reading): the backend sends a real
FCM `notification` block (title/body) for every type except `new_ride_assignment`/`live_activity`,
so background/killed-state display is handled by the OS automatically — not a gap. Android does
not auto-display a `notification`-block FCM message while the app is in the foreground by
default, though, so the app must handle that itself — which it does for 5 known types, but not
for anything outside that list, including any new type added to the backend later without a
matching client-side branch being added at the same time.

## 3. Fix / remediation

The fallback branch now also calls `showToast('info', title, body)`, preferring
`remoteMessage.notification?.title`/`.body` (populated for every type that reaches this branch,
per the root-cause finding above) and falling back to a generic line if a future payload ever
arrives without one. The existing `router.push` and `console.warn` are unchanged — this is purely
additive.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one `else if` branch inside one `useEffect` in one hook.** The 5
  existing, explicitly-handled branches (`new_ride_assignment` and the 4 `showAlert`/`showToast`
  cases) are untouched — this only changes what happens for a type that isn't any of those.
- **What else reads/writes this?** `showToast` is already imported and used 5+ times elsewhere in
  this same file (`useDriverDashboard.ts:981,1004,1018,1091`, etc.) with the identical
  `(type, title, message)` signature — no new dependency, no new import, matching an established
  in-file pattern exactly.
- **Could this regress a working flow?** No existing branch's behavior changes. The only new
  behavior is a toast appearing for a message type that previously produced no visible feedback
  at all — strictly additive, nothing to regress.
- **No duplicate-notification risk:** unlike a background-handler change (deliberately not made
  here — see the audit's correction), Android does not auto-display foreground FCM
  `notification` blocks, so there is no OS-level notification for this toast to duplicate.

## 5. User-experience effect

**Driver-facing, foreground-only.** A driver who receives a push notification of a type the app
doesn't explicitly recognize (e.g., a newly-added backend notification type, or any type this
audit didn't enumerate) now sees a toast while the app is open, instead of a silent screen
navigation they might not notice. This is a strict improvement — no existing driver-facing screen
or flow's behavior changes for any already-handled type.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/hooks/useDriverDashboard.ts` | Added a `showToast(...)` call to the foreground FCM handler's final unhandled-type fallback branch, alongside the existing `console.warn` and `router.push` | Close the narrowed C97 foreground gap: an unhandled notification type produced no visible feedback while the app was open |

## 7. Before / after

```typescript
// Before
} else if (data?.type) {
  console.warn('[Push] Unknown notification type — navigating to notifications list:', data.type);
  router.push('/driver/notifications' as any);
}
```

```typescript
// After
} else if (data?.type) {
  console.warn('[Push] Unknown notification type — showing fallback toast:', data.type);
  showToast(
    'info',
    remoteMessage?.notification?.title || 'New notification',
    remoteMessage?.notification?.body || 'Tap to view details in your notifications list.',
  );
  router.push('/driver/notifications' as any);
}
```

## 8. Rollback plan

`git revert`-safe. Reverting restores the prior silent-navigation behavior exactly — no stored
state, no backend interaction, no already-triggered notification is affected either way; this is
a pure client-side display-behavior change.

## 9. Verification performed

- [x] `tsc --noEmit` against the full `driver-app` project — clean, zero errors (confirms
      `remoteMessage.notification` is a valid access on the `RemoteMessage`-shaped parameter and
      `showToast`'s call signature matches).
- [x] Confirmed `showToast`'s exact signature (`(type: ToastType, title: string, message?:
      string)`) and that `'info'` is a valid `ToastType`, matching 5+ existing call sites in this
      same file.
- [x] Confirmed via direct reading of `backend/features.py::_deliver_push_now` that every type
      reaching this fallback branch does carry a real FCM `notification` block (title/body), so
      `remoteMessage.notification` is genuinely populated in practice, not just optionally
      present in the type signature.
- [ ] **No real device/emulator run, and no automated test exercises this callback** — see below.

## What was NOT verified

- **No test exists for this callback at all, before or after this change** — `useDriverDashboard.ts`'s
  entire `onForegroundMessage` effect (all 5 existing branches, not just the one this PR touches)
  has zero automated test coverage today; no test file mocks `@shared/services/firebase`'s
  `onForegroundMessage` to invoke this callback directly, and the one screen-level test that
  exists for the driver dashboard doesn't touch FCM handling. This is a pre-existing gap, not one
  introduced by this change — but it means this fix's correctness rests on `tsc` type-checking
  and direct code reading, not a passing test. Flagging explicitly per this repo's own
  "state the boundary, don't let silence imply full coverage" convention, rather than building a
  net-new hook-test harness (significant mocking surface: zustand stores, router, offer sound,
  the toast module, and the FCM listener itself) as an unscoped addition to this narrow fix.
- **Not run on a real device or emulator** — this environment has neither; verified by static
  analysis (`tsc`) and direct reading only, same disclosed boundary as other driver-app changes
  this session.
- **No visual-regression tooling exists for driver-app** (per `CLAUDE.md`'s own documented gap) —
  a toast's visual appearance was reasoned about, not screenshotted.
