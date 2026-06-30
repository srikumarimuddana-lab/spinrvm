# Runbook — Live Ride Activity go-live

Turn on the live ride-status surface (Android ongoing notification + iOS Live
Activity) for real devices. All backend code is on `main` and **ships dark** —
nothing happens on phones until the steps below are done. Design + decisions:
`docs/live-ride-activity-plan.md`.

| Surface | What it shows | Tech |
|---|---|---|
| Android | Ongoing notification, updated while alive (local) + backgrounded/killed (FCM) | notifee |
| iOS | Live Activity (Lock Screen + Dynamic Island), updated by backend APNs push | Voltra + ActivityKit |

**Backend is already live** (auto-deploys from `main`). It only *acts* once: (a)
a rider app build registers a token, and (b) for iOS, the `apns_*` keys are set
and `voltra_templates.json` is generated. Order below is safe to do incrementally.

---

## 0. Prerequisites

- Apple Developer account with access to **Keys** (APNs) and **Identifiers** (App Groups), for bundle `com.spinr.user`.
- An **Apple Push Notifications service (APNs) `.p8` Auth Key** (or create one in step 3).
- A physical **iOS 16.4+** device (Live Activities do **not** run in the Simulator) and an Android device.
- EAS access (`eas build`). A Mac is required to finalize the iOS Voltra files (step 1).

---

## 1. Finalize the iOS Voltra scaffolds (Mac, against installed types)

Three files were committed as scaffolds with `BUILD-TIME-VERIFY` markers because the
Voltra v2 API isn't fully documented. **If the API names are wrong the token never
registers and iOS push silently no-ops** — verify before building.

```bash
cd rider-app && yarn install          # pulls @use-voltra/ios-client
# Open the installed types and confirm the names used in our code:
#   node_modules/@use-voltra/ios-client/...   (and @use-voltra/ios)
```

Confirm/adjust in these files (search for `VERIFY`):
- `rider-app/services/rideVoltraLiveActivity.ts` — `startLiveActivity` / `endLiveActivity` /
  `addVoltraListener('activityTokenReceived', …)` + the token payload key (`pushToken` vs `token`).
- `rider-app/components/VoltraRideActivity.tsx` — Voltra primitives + style props; add the
  Dynamic Island regions (left as a TODO) once the island-region API is confirmed.
- `rider-app/scripts/render-voltra-templates.mjs` — `renderLiveActivityToString` import + signature
  from `@use-voltra/ios-server`.

---

## 2. Generate the iOS push templates

The backend pushes a pre-rendered Voltra UI blob (it can't render JS). Generate the
template map and commit it (CI should re-run this on any `VoltraRideActivity.tsx` change):

```bash
cd rider-app
node scripts/render-voltra-templates.mjs       # writes backend/utils/voltra_templates.json
git add ../backend/utils/voltra_templates.json && git commit -m "chore(rides): generate voltra live-activity templates"
```

Until real keys exist in this file, `apns_client._render_ui_json` returns `None` and
**no iOS push is sent** (intentional dark mode).

---

## 3. Provision APNs key + App Group (Apple Developer)

- **APNs Auth Key:** Certificates, IDs & Profiles → **Keys** → create a key with
  *Apple Push Notifications service (APNs)* enabled → download `AuthKey_XXXXXXXXXX.p8`
  (one download only). Note the **Key ID** (10 chars) and your **Team ID** (10 chars).
- **App Group:** Identifiers → App Groups → create `group.com.spinr.user`; ensure the
  rider app id `com.spinr.user` is a member (the Voltra plugin uses this `groupIdentifier`).
  **Also add the Live Activity extension App ID `com.spinr.user.SpinrLiveActivity` to the
  same App Group** (Identifiers → App IDs → that id → enable **App Groups** → assign
  `group.com.spinr.user`). The Voltra plugin writes
  `com.apple.security.application-groups = [group.com.spinr.user]` into the *extension's*
  entitlements, so if the extension App ID isn't in the group the EAS-generated profile won't
  contain it and the archive fails with:
  > Provisioning profile "…SpinrLiveActivity…" doesn't support the group.com.spinr.user App
  > Group … doesn't match the entitlements file's value for the
  > com.apple.security.application-groups entitlement.
  After enabling it, **regenerate the cached profiles** or EAS reuses the stale ones:
  `cd rider-app && eas credentials -p ios` → Production → remove the App Store provisioning
  profile(s) (keep the distribution cert); the next `eas build -p ios` reissues both the app
  and extension profiles with the App Group included.

---

## 4. Configure admin settings (no redeploy)

Admin dashboard → **Settings**. Set all four (the key is masked after save; use a
**textarea** so the multi-line PEM keeps its newlines):

| Field | Value |
|---|---|
| `apns_key_id` | the 10-char Key ID |
| `apns_team_id` | your Apple Team ID |
| `apns_bundle_id` | `com.spinr.user` (topic becomes `com.spinr.user.push-type.liveactivity`) |
| `apns_p8_key` | the full `-----BEGIN PRIVATE KEY----- … -----END PRIVATE KEY-----` PEM |

The backend stays dark until **all four** are non-blank. A mis-pasted PEM logs a single
`error` (`apns: .p8 key malformed`) — re-paste; it won't flood.

---

## 5. EAS builds (native — OTA can't add these)

Both surfaces need a fresh native build (notifee on Android; Voltra on iOS). Trigger
via a commit containing `[build]`, or:

```bash
cd rider-app
eas build --platform android      # ships @notifee/react-native
eas build --platform ios          # ships Voltra (needs the App Group + 16.4 target)
```

Install the builds on the test devices.

---

## 6. Device test (full ride)

Run a real ride (or staging dispatch) through every transition and watch the surface:

| Transition | Expect |
|---|---|
| `driver_accepted` | Notification (Android) / Live Activity (iOS) appears; rider registers the token |
| `driver_arrived` | Headline updates in place |
| `in_progress` | "On your way…" |
| `completed` / `cancelled` | Ends / dismisses |

**Lock the phone and force-quit the app**, then advance a transition — the update must
still land (Android via FCM background handler; iOS via APNs). On iPhone 14 Pro+ confirm
the Dynamic Island. Token rotation: background long enough for ActivityKit to rotate, then
confirm updates continue (the re-register upserts the new token).

---

## 7. Verify / observe

- **Backend logs:** `live_activity dispatch` (info, per transition, no PII) and, for iOS,
  any `apns:` warnings (e.g. `no Voltra template`, `rejected malformed push token`,
  APNs `:status` + `reason`).
- **Metric:** `spinr_rides_live_activity_apns_sent_total{event,outcome}` increments on iOS sends.
- **DB:** a registered ride shows a `ride_live_activities` row; it gets `ended_at` on the
  terminal event or on an APNs dead-token (`410`/`BadDeviceToken`).

---

## 8. Rollback / kill

- **iOS only:** blank `apns_p8_key` in admin Settings → instantly dark (no push), no redeploy.
- **Android:** the ongoing notification is driven by the app build + backend FCM; to stop
  backend-driven updates, the `live_activity` FCM is best-effort and harmless to leave.
- **Full:** revert the rider app to the prior EAS build. Backend code is inert without tokens
  + keys, so no backend rollback is required.

---

## Open product/UX decisions (from the security audits — not blockers)

- Android ongoing notification is lockscreen-`PUBLIC` and the foreground content can include
  the full dropoff address + pickup PIN (pre-existing behaviour). Tighten to a `PRIVATE`
  channel + redacted public version if desired.
- iOS pushed (locked/killed) content is **generic per status** (Option C) — the driver
  name/vehicle show on-device while the app is alive, not on a pushed update. Swap
  `apns_client._render_ui_json` to a Node sidecar (Option A) for full per-ride fidelity.
- Tick the **"third-party SDK added"** box (notifee, Voltra) with a privacy justification when
  raising the PR.
