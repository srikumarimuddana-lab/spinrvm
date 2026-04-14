# 04 — Mobile App Builds (EAS)

**Goal:** produce production iOS + Android builds of the rider and
driver apps, submit them to App Store Connect and Google Play
Console, and wire EAS OTA updates for future releases.

**Time:** ~90 minutes active; 2-7 days including first store review.

**Pre-reqs:**

- Backend live at `https://api.<domain>` ([`02-backend-fly.md`](./02-backend-fly.md)).
- Firebase Android + iOS apps created; `google-services.json` and
  `GoogleService-Info.plist` downloaded.
- Google Maps API key generated and restricted.
- Apple Developer Program active. Google Play Console paid.
- `eas-cli` installed: `npm install -g eas-cli`.
- Two physical devices (one Android, one iOS) for smoke-testing.

---

## Step 1 — Log in to EAS

```bash
eas login
# Use your Expo / Spinr organization account, NOT a personal account.
eas whoami
```

Switch to the Spinr organization:

```bash
eas organization:use spinr
```

---

## Step 2 — Initialize both apps

```bash
cd rider-app
eas init --id <paste-the-rider-project-id-from-expo-dashboard>
# or `eas init` without an ID to create a new project — only if one
# doesn't already exist. Re-using the existing project ID is the norm.

cd ../driver-app
eas init --id <paste-the-driver-project-id>
```

Verify `app.config.ts` / `app.json` in each has:

- `name` — `"Spinr Rider"` / `"Spinr Driver"`
- `slug` — `"spinr-rider"` / `"spinr-driver"`
- `ios.bundleIdentifier` — `"com.spinr.rider"` / `"com.spinr.driver"`
- `android.package` — `"com.spinr.rider"` / `"com.spinr.driver"`
- `extra.eas.projectId` — matches the EAS project ID.

---

## Step 3 — Place Firebase config files

Copy the files you downloaded from Firebase into each app:

```bash
cp ~/Downloads/google-services-rider.json rider-app/google-services.json
cp ~/Downloads/GoogleService-Info-rider.plist rider-app/GoogleService-Info.plist

cp ~/Downloads/google-services-driver.json driver-app/google-services.json
cp ~/Downloads/GoogleService-Info-driver.plist driver-app/GoogleService-Info.plist
```

These files are NOT secret (they're bundled into every client build
and can be extracted by anyone with an APK), but they MUST match
the Firebase project — a mismatch means silent FCM delivery failure.

Confirm `app.config.ts` references them:

```typescript
// rider-app/app.config.ts
ios: {
  googleServicesFile: "./GoogleService-Info.plist",
  ...
},
android: {
  googleServicesFile: "./google-services.json",
  ...
}
```

Both files are in `.gitignore` already; do not commit them to the
public repo.

---

## Step 4 — Register EAS secrets

The repo already includes `scripts/setup-eas-secrets.sh` for the
Google Maps key. Run it now with your restricted production key:

```bash
cd ~/Spinr
export EXPO_PUBLIC_GOOGLE_MAPS_API_KEY='AIzaSy...<your-restricted-key>'
bash scripts/setup-eas-secrets.sh
```

The script invokes `eas secret:create --scope project` for both
the rider and driver apps.

Register the production backend URL as a secret too (per-app):

```bash
cd rider-app
eas secret:create --scope project --name EXPO_PUBLIC_BACKEND_URL \
  --value https://api.yourdomain.app --force

cd ../driver-app
eas secret:create --scope project --name EXPO_PUBLIC_BACKEND_URL \
  --value https://api.yourdomain.app --force
```

List to confirm:

```bash
eas secret:list
```

Secrets are injected into EAS Build runtime; they're NOT visible in
built apps unless prefixed `EXPO_PUBLIC_`. For `EXPO_PUBLIC_*` secrets,
they ARE bundled into the JS — treat every `EXPO_PUBLIC_*` value as
public.

---

## Step 5 — Review `eas.json`

Each app has an `eas.json` with `development`, `preview`, `production`
profiles. For the first production build, you want:

```json
{
  "build": {
    "production": {
      "distribution": "store",
      "autoIncrement": true,
      "channel": "production",
      "env": {
        "EXPO_PUBLIC_BACKEND_URL": "$EXPO_PUBLIC_BACKEND_URL",
        "EXPO_PUBLIC_GOOGLE_MAPS_API_KEY": "$EXPO_PUBLIC_GOOGLE_MAPS_API_KEY"
      }
    }
  },
  "submit": {
    "production": {
      "ios": {
        "appleId": "<your-apple-id-email>",
        "ascAppId": "<your-app-store-connect-app-id>",
        "appleTeamId": "<your-team-id>"
      },
      "android": {
        "serviceAccountKeyPath": "./play-service-account.json",
        "track": "internal"
      }
    }
  }
}
```

Verify before continuing.

---

## Step 6 — First production build

Run one at a time (they take 15-30 min each):

```bash
cd rider-app
eas build --profile production --platform all
```

EAS will ask about credentials the first time:

- **iOS:** "Generate a new Apple Distribution Certificate?" → Yes, if
  this is your first iOS build. EAS handles provisioning profile
  creation.
- **Android:** "Generate a new Keystore?" → Yes. **After the build
  succeeds, download a backup of the keystore**
  (`eas credentials`) and store it in your secrets vault. Losing the
  keystore means you can never push an update to that app without a
  new package name.

Repeat for the driver app:

```bash
cd ../driver-app
eas build --profile production --platform all
```

Build status and downloadable IPA/AAB files appear in the Expo
dashboard.

---

## Step 7 — Submit to the stores

### Apple App Store

```bash
cd rider-app
eas submit --profile production --platform ios --latest
```

`--latest` uses the most recent successful build.

Post-submission:

1. Log in to https://appstoreconnect.apple.com.
2. Go to **Apps** → Spinr Rider → **TestFlight** tab.
3. Wait ~10-30 minutes for Apple to process the build.
4. Add yourself and a few beta testers. Install via TestFlight on
   your iOS device.

Repeat for driver app.

### Google Play

```bash
cd rider-app
eas submit --profile production --platform android --latest
```

Post-submission:

1. Log in to https://play.google.com/console.
2. App → **Testing** → **Internal testing** → review.
3. Add a tester Gmail account; open the install link on the test
   device.

Repeat for driver app.

### First-time store review

Both stores take 24-72 hours to approve a brand-new app for
external distribution. TestFlight / Internal Testing are ~immediate.
Use them for pre-launch QA.

---

## Step 8 — Smoke-test on real devices

Both apps pointed at `https://api.yourdomain.app`:

- [ ] Rider: sign up with a real phone; receive OTP (Twilio); set
      profile; request a ride.
- [ ] Driver: sign up; complete onboarding (upload one document);
      go online; accept the rider's request.
- [ ] Driver: receive FCM push when backgrounded.
- [ ] Rider: receive FCM / APNs push when driver arrives.
- [ ] Rider: payment intent succeeds against a real Stripe test
      card.
- [ ] Driver: earnings export CSV downloads from tax screen.

Any failure at this stage means the corresponding third-party
service is misconfigured. Go back to that section and fix before
shipping to the stores.

---

## Step 9 — Wire EAS OTA updates

For JS-only bug fixes you don't need to re-submit to the stores.
Ship OTA via EAS Update:

```bash
cd rider-app
eas update --branch production --message "Fix ride completion navigation"
```

Caveats:

- Native changes (new dependencies, native module upgrades) REQUIRE
  a new store build. EAS blocks OTA updates with a mismatched
  native SDK.
- Staged rollout: `eas update --branch production --message "…" --rollout 10`
  ships to 10% of devices. Monitor crash-free rate before promoting.

---

## Step 10 — Rotate mobile secrets pre-launch

Before public launch:

1. **Rotate `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`.** The key committed
   during development may be in git history. Google Cloud Console →
   Credentials → delete old key, create new one restricted to your
   real bundle IDs and package names. Re-run
   `scripts/setup-eas-secrets.sh`. Re-build both apps.

2. Double-check every `EXPO_PUBLIC_*` var is intentional public
   material. Anything sensitive should move to the backend and be
   fetched via an authed endpoint.

---

## Done when…

- [ ] Rider + driver production builds are in TestFlight and Play
      Internal Testing.
- [ ] Both apps install on physical devices via those channels.
- [ ] Full ride lifecycle completes on real devices against
      production backend.
- [ ] EAS Update OTA channel is `production` for both apps.
- [ ] Keystore + iOS distribution cert are backed up in vault.
- [ ] Old development Google Maps key is revoked.

Next: [`05-third-party-services.md`](./05-third-party-services.md) if
you haven't already set up the rest of the service integrations, then
return to [`CHECKLIST.md`](./CHECKLIST.md) for final sign-off.
