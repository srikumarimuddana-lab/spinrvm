# Runbook: Android SHA-1 Fingerprint Mismatch (Play Console vs Expo/EAS)

**What this covers:** Diagnosing a SHA-1 certificate fingerprint reported by the
Google/Play/Firebase consoles that does not match the one Expo (EAS) reports, and
deciding whether that mismatch is normal (it usually is) or an actual upload-key
failure. Also covers where every SHA-1 must be registered so Maps, Firebase, and
App Check work in **both** Play-distributed and EAS-distributed builds.

**Severity:** P2 normally (a console value looks wrong, nothing is broken).
P1 if it presents as grey maps, Play upload rejection, or App Check failures on a
build already in front of live testers.

**Prerequisites:**
- Play Console access to `com.spinr.user` / `com.spinr.driver`
- Google Cloud Console access to project `spinrapp-6e464` (Credentials page)
- Firebase Console access to project `spinrapp-6e464`
- `eas` CLI logged in, or expo.dev web access to the Credentials page

**Related:** `.github/workflows/generate-upload-keystore.yml` (upload key reset),
`docs/change-log/2026-08-16-android-auto-hardware-validation.md` §2.1 (the
`B7:F7:…` vs `D7:51:…` incident), audit finding `[23-6]` (keystore custody),
`docs/runbooks/MOBILE_SMOKE.md` §8 ("Map renders grey").

---

## 1. The mismatch is expected — read this before changing anything

With **Google Play App Signing** enabled (the default for every app created since
August 2021, and what these packages use), there are **two different certificates**
and they are *supposed* to have different SHA-1 fingerprints:

| Certificate | Who holds the private key | Where you see its SHA-1 | What it signs |
|---|---|---|---|
| **Upload key** | Us — the keystore stored in EAS | `eas credentials -p android`, expo.dev → Credentials | The AAB/APK we hand to Play; and **directly** every EAS internal-distribution APK |
| **App signing key** | Google | Play Console → App integrity → App signing | The APK Play actually serves to a device |

Play strips our upload signature and re-signs with its own key. So:

- A build a tester installs **from Play** (internal test, closed, open, production)
  presents the **app signing key** fingerprint.
- A build a tester installs **from an EAS link** (`preview` / `test` profiles in
  `rider-app/eas.json` and `driver-app/eas.json`, both `buildType: apk`) presents
  the **upload key** fingerprint.

**Do not try to make the two values equal.** The fix is almost always to register
*both* fingerprints everywhere a fingerprint is checked (§4), not to change a key.

---

## 2. Identify which value is which

Do this first — every later step depends on it.

```bash
# The keystore EAS holds. Its "SHA1 Fingerprint" is the UPLOAD key.
cd driver-app   # or rider-app
eas credentials -p android
```

Then in **Play Console → Test and release → Setup → App integrity → App signing**,
read the two fingerprints Google shows: *App signing key certificate* and
*Upload key certificate*.

Now compare:

| Check | Meaning |
|---|---|
| EAS SHA-1 **==** Play's *Upload key certificate* | Healthy. Skip to §4 — nothing to fix in Play; you only need to register fingerprints in Cloud/Firebase. |
| EAS SHA-1 **==** Play's *App signing key certificate* | Play App Signing is off for this package, or you are reading the wrong row. Re-check before acting. |
| EAS SHA-1 **!=** either Play value | **Real problem.** EAS holds a keystore Play does not recognise → §3. |

> The console SHA-1 you were handed may be either of Play's two values. Confirming
> *which row it came from* is the whole diagnosis — a fingerprint quoted without
> its row is not enough to act on.

---

## 3. Real failure: EAS holds a keystore Play does not accept

**Symptom:** `eas submit` / Play upload fails with *"Your Android App Bundle is
signed with the wrong key … expected fingerprint SHA1: <X> … found: <Y>"*.

This already happened once on `com.spinr.driver` (registered upload key `B7:F7:…`
had no custodian; EAS held `D7:51:…`). It was resolved by an upload key reset, not
by code.

**Fix — Play upload key reset:**

1. Run `.github/workflows/generate-upload-keystore.yml` (workflow_dispatch, confirm
   input `generate`). Requires repo secret `KEYSTORE_PASSWORD`, ≥12 chars, vaulted
   *before* the run — it is unrecoverable afterwards.
2. Download the run artifact (`upload-keystore.jks` + `upload_certificate.pem`).
3. Play Console → App integrity → App signing → **Request upload key reset** →
   upload **only** `upload_certificate.pem`. Never upload the `.jks` to Google.
4. expo.dev → Credentials → Android → the package → Add new build credentials →
   upload the `.jks`, make it active.
5. Vault the `.jks` + password; delete the workflow run artifact.
6. Wait 1–2 business days for Google to apply the reset, then rebuild + resubmit.
7. **Record the new upload SHA-1** and redo §4 — the old upload fingerprint is now
   dead everywhere it was registered.

If instead the app signing key itself is wrong, stop and escalate: it cannot be
rotated without Play support and it invalidates every device-side signature check.

---

## 4. Register fingerprints everywhere they are checked

A fingerprint mismatch that is *normal* (§1) still breaks things if only one of the
two values is registered. Each service below validates the signature of the
**installed** app, so each needs **both** values for both packages.

### 4a. Google Maps — Google Cloud Console → APIs & Services → Credentials

Open the key behind `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` → *Application restrictions*
→ **Android apps**, and add one row per combination:

| Package name | SHA-1 |
|---|---|
| `com.spinr.user` | app signing key |
| `com.spinr.user` | upload key |
| `com.spinr.driver` | app signing key |
| `com.spinr.driver` | upload key |

Missing rows present as a **grey map with markers but no tiles** — the symptom
already listed in `MOBILE_SMOKE.md` §8. `PROVIDER_GOOGLE` is Android-only here
(iOS deliberately uses Apple Maps — see the comment in each `app.config.ts`), so
this is an Android-only failure mode.

> ⚠️ **A Google Cloud API key can carry only ONE application-restriction type** —
> *Android apps* **or** *HTTP referrers*, never both. The same key string is also
> consumed from the web by `admin-dashboard/src/app/track/[rideId]/page.tsx` (as
> `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY`, whose comment asks for `track.spinr.ca` as an
> authorised referrer) and by `driver-app/app/_layout.tsx`'s Maps JS `<script>` tag.
> Adding an Android restriction to that shared key **will break the web surfaces**.
> Split into separate keys — one Android-restricted for the two apps, one
> referrer-restricted for web — before applying any restriction.
> `docs/ENVIRONMENT_VARIABLES.md` already calls for per-app keys; this is the same
> point with a concrete failure attached.

### 4b. Firebase — Console → Project settings → Your apps → Android app → Add fingerprint

Needed for App Check / Play Integrity, and for anything using Google Sign-In or
Firebase Auth reCAPTCHA. Add all four rows from §4a.

**Current state, from the committed config:** both `rider-app/google-services.json`
and `driver-app/google-services.json` (byte-identical, project `spinrapp-6e464`)
carry `"oauth_client": []` for `com.spinr.user` *and* `com.spinr.driver` — i.e.
**no SHA-1 is registered against either Firebase Android app today.**

After adding fingerprints in the console, **re-download `google-services.json` and
commit it** for both apps. The console change alone does not reach the binary.

### 4c. App Check / Play Integrity

Play Integrity attests against the **app signing key** Google holds, so it works
only for Play-distributed builds. EAS internal-distribution APKs (`preview` /
`test`) will fail attestation by design — register debug tokens instead:
Firebase Console → App Check → Apps → overflow menu → *Manage debug tokens*. This
is the manual step already flagged in both `app.config.ts` files above the
`@react-native-firebase/app-check` plugin block, and the enforcement half of
`ACTION_ITEMS.md` C3.

---

## 5. Verification

1. `eas credentials -p android` for both packages — record both SHA-1s in the
   credentials vault entry alongside the custodian (audit `[23-6]` asked for this).
2. Install the **Play internal-test** build on a device: maps render tiles, push
   arrives, no App Check errors.
3. Install the **EAS `preview` APK** on a device: same three checks. This is the
   build that fails when only the app signing key is registered — the most common
   way this bug reaches a live tester.
4. Web surfaces still render maps: `track.spinr.ca` ride tracking, and the driver
   web build — the §4a restriction-type trap.

## 6. What this runbook does NOT cover

- iOS provisioning profiles / distribution certificates (other half of `[23-6]`).
- EAS build or `eas update` failures from JS/dependency resolution — see
  `docs/runbooks/eas-build-failure-triage.md`, whose §6 explicitly excludes
  credentials.
- Rotating the Play **app signing** key (requires Play support).

---

**Owner:** mobile team · update §4 whenever a new service starts validating an
Android signature, and re-run §5 after any upload key reset.
