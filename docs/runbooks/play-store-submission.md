# Runbook — submitting an Android build to Google Play

**What this covers:** how a Spinr AAB actually reaches Play, which credential
does it, and how to read the two failures that account for every unsuccessful
submission so far. Written 2026-09-09 after the rider app's first-ever Play
submission attempt; before this the repo documented none of it.

**Severity:** P2 — blocks a release, breaks nothing already shipped.

---

## 1. The credential model

Two separate Google credentials are involved. Confusing them wastes hours.

| Credential | What it is | Where it lives | What it does |
|---|---|---|---|
| **Upload keystore** | Signs the AAB | EAS (`eas credentials -p android`) | Proves the binary is ours. See `android-signing-fingerprint-mismatch.md`. |
| **Play service account** | A Google Cloud service account | Repo secret `PLAY_SERVICE_ACCOUNT_JSON` (base64) | Authenticates the *upload* to the Play Developer API |

**The EAS project holds NO stored Google Service Account key.** Every
`eas.json` Android submit profile therefore sets
`serviceAccountKeyPath: ./play-service-account.json`, and every workflow that
submits must decode the secret into that path first. Removing the path breaks
submission with *"Google Service Account Keys cannot be set up in
--non-interactive mode"* — a real regression that shipped and was reverted the
same day (`docs/change-log/2026-09-09-play-submit-credential-correction.md`).

> Do not infer the presence of a stored key from the Expo MCP connector's
> error *"conflict between exclusive peers [googleServiceAccountKeyId,
> googleServiceAccountKeyJson]"*. That connector contributes the duplicate
> field itself; it is not evidence about EAS credentials. That misreading is
> what caused the regression above.

## 2. The two working paths

| Workflow | Scope | Notes |
|---|---|---|
| `submit-play-store.yml` | Either app, any track, submit-only | Takes an existing build ID; no rebuild |
| `deploy-driver-play-testing.yml` | Driver only, `android-auto` profile | Builds *and* submits to the Auto closed track |

`eas-native-build.yml` can also auto-submit as a side effect of building.

Locally: `eas submit --platform android --profile production --id <BUILD_ID>`
from the app directory, after `yarn install` (the config eval loads local
plugins that need `node_modules`).

## 3. Failure: "The caller does not have permission"

Full text, from fastlane under Expo:

```
Google Api Error: Invalid request - The caller does not have permission
The service account is missing the necessary permissions to submit the app
```

This is **never** a code or credential-file problem — the key authenticated
fine or you would have seen a different error. It means the service account
is not authorised on *that package*.

**Diagnose by contrast.** Run `submit-play-store.yml` with `app: both`. If one
app succeeds and the other fails, the key is valid and the problem is
app-scoped. That is exactly what happened on 2026-09-09: `com.spinr.driver`
submitted successfully while `com.spinr.user` failed in the same run, minutes
apart, with the same key.

The workflow's decode step prints the service account email and GCP project.
Use that to find the principal — do not guess which one it is.

**Fix, in order of likelihood:**

1. **Different developer accounts.** Check Play Console's account switcher. A
   service account can only be granted on apps inside the developer account
   it was invited to. If `com.spinr.user` and `com.spinr.driver` live in
   different accounts, no grant in one fixes the other — invite the same
   service account email in the *other* account, or use a second key and
   secret. Plausible here: the rider listing predates the rewrite (v1.0.0,
   2026-01) while the driver app's Play presence is newer.
2. **App not in the principal's app list.** Users and permissions → the
   service account → App permissions. Account-level access does not imply
   every app.
3. **Insufficient permissions on the app.** Needs at least *Create and edit
   draft releases* and *Release to testing tracks*; add *Release to
   production* to promote from CI. Copying the driver app's existing grant is
   the reliable move.
4. **Propagation delay.** Play permission changes commonly take 5–10 minutes,
   occasionally up to 24 hours. Re-running immediately after a grant can
   reproduce the error even once it is correct.

## 4. Failure: version code already used

Play rejects an AAB whose `versionCode` is ≤ one already uploaded. `eas.json`
sets `appVersionSource: remote` with `autoIncrement`, so EAS's counter is
authoritative — but it does not know about builds uploaded outside EAS.

Check Play Console → the app → App bundle explorer for the highest existing
code. As of 2026-09-09: `com.spinr.user`'s highest was **3** (v1.0.2), and the
new builds were **15** (rider) and **25** (driver).

## 5. Before promoting to production

Install from the internal track and confirm the app gets past login and loads
data. App Check is enforced in production (`enforcement_enabled=is_production`
in `backend/core/middleware.py`) and returns **401 on every `/api/*` route**
when attestation fails; the client's `getAppCheckToken()` returns `null`
silently, so a broken Play Integrity link presents as an app that simply
hangs, with no error. Play-distributed installs are signed with Google's app
signing key, so this path is only exercised by a real Play install — an
internal-track install tests it, a sideloaded APK does not.
