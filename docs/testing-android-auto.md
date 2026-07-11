# Getting Android Auto working — build & test guide

**Audience:** whoever needs to see the Spinr Driver map on a real car screen.
**Companion doc:** `docs/carplay-android-auto.md` (architecture & library decision).

## Why it looks "broken" today

Android Auto support lives in a **native module** (`@iternio/react-native-auto-play`).
Native modules only exist inside an APK built by EAS — they are **not** in Expo Go and
**not** in any APK built before the car dependency landed. On such a binary,
`driver-app/lib/androidAuto/register.ts` deliberately catches the missing module and
disables car support (instead of crashing the phone app), logging:

```
[android-auto] native module unavailable — Android Auto disabled for this session.
```

So: **Expo Go / `yarn start` alone can never show Android Auto.** You need a fresh EAS
build, and (until Play review passes) a phone with Android Auto developer mode enabled.

## Step 1 — Build an installable APK

```bash
cd driver-app
eas build --profile preview --platform android
```

- The `preview` profile produces a **standalone APK** (JS bundle embedded) — right for
  in-car testing without a dev server.
- Use `--profile test` instead only when iterating with a live Metro server (it's a
  dev-client build).
- When the build finishes, open the build link on the phone and install the APK.

**Before building, verify the EAS environment variables** (Maps and backend). A missing
maps key = blank map on both phone and car screen:

```bash
eas env:list --environment preview
# must include: EXPO_PUBLIC_GOOGLE_MAPS_API_KEY, EXPO_PUBLIC_BACKEND_URL
```

## Step 2 — Phone setup (one time)

1. Install/update the **Android Auto** app (built into Android 10+; find it under
   Settings → Connected devices → Android Auto, or install from Play).
2. Open Android Auto settings → scroll to **Version** → tap it **10 times** →
   accept "Enable developer settings".
3. Open the ⋮ menu → **Developer settings** → enable **Unknown sources**.
   (Required because the Spinr car app hasn't passed Google's Car App review yet —
   without this, the car launcher silently hides it.)
4. Give Spinr Driver **location permission** ("While using the app" is enough while
   the phone is connected to the car).

## Step 3 — Test in a real car

1. Connect the phone to the car (USB or wireless Android Auto).
2. On the car screen, open the app launcher — **Spinr Driver** appears with the app icon
   (navigation-category apps show in the main row).
3. Tap it: a live map with your car marker and zoom buttons should render.
4. Log in and go online **on the phone**. When a ride offer arrives, the car screen
   raises an **Accept / Decline** alert; during a ride the header shows
   **Arrived / Start on phone / Complete trip**, plus a **Navigate** button that hands
   off turn-by-turn to Google Maps.

## Step 3b — No car? Use Google's Desktop Head Unit (DHU)

The DHU emulates a car screen on a computer:

1. Install the DHU: Android Studio → SDK Manager → SDK Tools → **Android Auto Desktop
   Head Unit Emulator** (or `sdkmanager "extras;google;auto"`).
2. Phone: Android Auto **Developer settings** → **Start head unit server**
   (appears in the notification shade).
3. Computer (phone plugged in via USB, USB debugging on):
   ```bash
   adb forward tcp:5277 tcp:5277
   cd $ANDROID_HOME/extras/google/auto && ./desktop-head-unit
   ```
4. The car screen opens in a window; test exactly as in Step 3.
   Docs: https://developer.android.com/training/cars/testing/dhu

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Spinr Driver missing from the car launcher | **Unknown sources** not enabled (Step 2.3), or the installed APK predates the car module — reinstall the fresh build. Confirm via `adb logcat \| grep android-auto`: the "native module unavailable" line means stale binary. |
| Works in Expo Go? | It never will — native module. Always test the EAS build. |
| Map is blank / gray tiles | `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` missing from the EAS environment at build time, or the key lacks "Maps SDK for Android" enablement in Google Cloud. |
| App opens on car but no offers | You're not online / not authenticated on the phone — the car screen is a mirror of the same store; fix the phone session first. |
| Phone app crashes at startup after OTA update | Should not happen (the require is guarded), but check Crashlytics for `androidAuto / native_module_unavailable` non-fatals. |

## Step 4 — Shipping to real drivers (production)

Sideloading + developer mode is **test-only**. For drivers' phones:

1. Play Console → **App content** → **Android Auto** declaration: declare the car app,
   category **Navigation** (explicitly allows ride-hailing driver apps).
2. Pass **Car App Quality** review (driver-distraction rules — our template-only UI is
   designed for this).
3. Until approval, Android Auto hides the app on normal phones; plan for review lead time.

## iOS CarPlay (not in scope)

CarPlay is intentionally dormant: it needs an Apple-granted CarPlay entitlement
(apply at https://developer.apple.com/carplay — weeks of lead time) plus scene wiring.
Nothing in this guide enables it; see `docs/carplay-android-auto.md` § "iOS CarPlay — dormant".
