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
| EAS SHA-1 **!=** Play's *Upload key certificate* | **Real problem.** EAS signs with a key Play will reject → §3. |

The *App signing key certificate* is a **third, different** fingerprint on that same
Play page. It is never expected to equal the EAS value and is not part of this
diagnosis — but you do need it for §4, so copy it while you are there.

> The console SHA-1 you were handed may be either of Play's two values. Confirming
> *which row it came from* is the whole diagnosis — a fingerprint quoted without
> its row is not enough to act on.

---

## 3. Real failure: EAS holds a keystore Play does not accept

**Symptom:** `eas submit` / Play upload fails with *"Your Android App Bundle is
signed with the wrong key … expected fingerprint SHA1: <X> … found: <Y>"*.

Seen twice on this project:

- **`com.spinr.driver`, 2026-08:** registered upload key `B7:F7:…` had no custodian;
  EAS held `D7:51:…`. Resolved by an upload key reset, not by code.
- **`com.spinr.user`, 2026-09:** Play's upload key certificate is
  `D3:C7:7E:B0:…:43:7A`; EAS's single build-credentials set (`xa8QcCZa5i`, alias
  `9eebacda…c6451`, JKS, uploaded 2026-04-09) is `26:39:10:88:…:7C:DB`. No second
  credentials set exists in EAS to switch to → §3b.

Fingerprints are public information — recording them here is deliberate, so the next
mismatch can be compared against history instead of re-derived.

### 3a. First — is the key Play wants still sitting in EAS, just not active?

expo.dev → Project credentials → the application identifier → **Build credentials**.
If more than one credentials set is listed, one of them may hold the keystore whose
SHA-1 matches Play. Making that set the default is the whole fix — no reset, no
waiting on Google.

If there is exactly **one** set and its SHA-1 still does not match Play, that key is
not in EAS. Check any off-EAS custodian (vault, a prior developer's machine, an old
CI secret) before assuming it is lost — a recovered `.jks` uploaded to EAS is
strictly cheaper than a reset. A key alias that is a bare 32-hex string is EAS's
auto-generated format, i.e. that keystore was minted by EAS rather than uploaded by
a human — a hint that the Play-side key came from some earlier, separate tooling
path and was never in EAS at all.

### 3b. Upload key reset — reuse the keystore EAS already has

**Do not mint a new key just to satisfy the reset.** Play only needs a public
certificate, and the keystore EAS already holds is a valid one to register. Fewer
moving parts than generating a fresh key, and EAS needs no change at all.

```bash
cd rider-app   # or driver-app
eas credentials -p android
# → select the build profile → Keystore → "Download existing keystore"
# EAS writes the .jks and prints the keystore password + key alias.

keytool -export -rfc \
  -keystore <downloaded>.jks \
  -alias <alias printed by EAS> \
  -storepass <password printed by EAS> \
  -file upload_certificate.pem
```

1. Play Console → App integrity → App signing → **Request upload key reset** →
   upload **only** `upload_certificate.pem`. Never upload the `.jks` to Google.
2. Wait 1–2 business days for Google to apply it.
3. Rebuild and resubmit. **Nothing changes in EAS** — it keeps signing with the same
   keystore, which Play now recognises.
4. Vault the downloaded `.jks` + password and delete the local copy. The custody gap
   audit `[23-6]` raised is what produces this failure in the first place.

**Only if EAS's keystore is also unusable** (corrupt, or you want a clean key) run
`.github/workflows/generate-upload-keystore.yml` instead: workflow_dispatch, confirm
input `generate`, repo secret `KEYSTORE_PASSWORD` ≥12 chars vaulted *before* the run
(unrecoverable afterwards). Download the artifact, submit the `.pem` to Play, upload
the `.jks` to expo.dev as new build credentials and make it active, then delete the
artifact. Note its `-dname` hardcodes `CN=Spinr Driver Upload Key` — change it if you
run this for `com.spinr.user`.

### 3c. After any reset

**Record the new upload SHA-1 and redo §4.** The old upload fingerprint is dead
everywhere it was registered — any Maps or Firebase entry naming it must be updated
or EAS-distributed builds start failing. The reset does **not** touch the app signing
key, so Play-distributed installs and anything registered against the app signing
fingerprint are unaffected.

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
