# Dimension 23 — Mobile Binary / Release Artifact Audit

**Applies to:** rider-app, driver-app (not backend / admin)
**Why it exists:** A code-only audit has a blind spot — it never checks what
actually shipped. The signed APK/IPA in the stores may differ from `main` due
to EAS build config, native modules, or supply-chain compromise between build
and publish.

**Regulatory coverage:** PIPEDA (data minimisation in runtime), PCI-DSS
(no key leakage in binary), Apple/Google store policies, ACA/AODA (accessibility
in shipped binary).

---

## Checklist

### Signing & Provenance
- [ ] Production APK signed with the upload key registered to Spinr (not a dev key)
- [ ] iOS build signed with Spinr team provisioning profile — verify bundle ID
  `com.spinr.user` / `com.spinr.driver`
- [ ] EAS build logs retained for every store submission (≥ 12 months)
- [ ] Build reproducibility: `main` at the tagged commit rebuilds to the same
  SHA-256 hash (if non-reproducible, document why: timestamp embedding, native lib)

### Binary Static Analysis (MobSF or equivalent)
- [ ] No Stripe secret key (`sk_live_*`, `sk_test_*`) in binary strings
- [ ] No Supabase service-role key in binary
- [ ] No Firebase service-account JSON
- [ ] No admin-only URLs or endpoints (`/admin/*`) reachable from mobile
- [ ] Only expected `EXPO_PUBLIC_*` values present
- [ ] No debug logging statements with PII at info level in release build
- [ ] `applicationId` / `bundleIdentifier` matches production (`com.spinr.user` / `com.spinr.driver`)
- [ ] Version code / build number monotonically increasing per release

### Permissions & Privacy Manifest
- [ ] `AndroidManifest.xml` permissions match a documented minimum set:
  - `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION`, `ACCESS_BACKGROUND_LOCATION` (driver only)
  - `POST_NOTIFICATIONS`, `CAMERA` (document upload), `INTERNET`
  - **No** `READ_CONTACTS`, `READ_SMS`, `READ_CALL_LOG`, `WRITE_EXTERNAL_STORAGE` on API 30+
- [ ] `PrivacyInfo.xcprivacy` (iOS) declares all tracked APIs (file-timestamp,
  disk-space, user-defaults, system-boot — Apple required API reasons)
- [ ] iOS `Info.plist` usage-description strings are accurate and in English + French
  (Official Languages Act applicability to the store listing)
- [ ] Android network-security config restricts cleartext traffic (or documents exceptions)

### TLS & Cert Pinning
- [ ] Production builds pin the Spinr backend TLS certificate (or use public-key
  pinning with backup pin)
- [ ] Pin rotation runbook exists (`docs/runbooks/tls-pin-rotation.md`)
- [ ] Pin failure does not leak request bodies to fallback endpoints

### Anti-Tampering / Runtime Integrity
- [ ] Firebase App Check (Play Integrity + DeviceCheck/AppAttest) enforced in
  production backend — unattested requests rejected
- [ ] Root / jailbreak detection for high-value actions (payout, wallet topup)
  with user-facing messaging (not silent lockout)
- [ ] Debug build feature flags disabled in release (no "Admin Mode" toggle)

### Supply Chain
- [ ] `yarn.lock` / `package-lock.json` committed and frozen at release tag
- [ ] SBOM generated per release (CycloneDX JSON) and stored in `reports/sbom/`
- [ ] No dependencies with known HIGH/CRITICAL CVEs older than 30 days
- [ ] Expo SDK version within supported window (not EOL)
- [ ] EAS build secret scope limited to production builds; no dev-secrets leaked

### Store Submission Gates
- [ ] Privacy policy URL reachable and contains all sub-processors (closes DV-16)
- [ ] ToS URL reachable
- [ ] Support email responsive within 24 h
- [ ] App Store screenshots match current UI (not outdated)
- [ ] Age rating accurate (17+ if any ride/driver-contact features warrant it)
- [ ] Data-safety disclosure (Play Store) matches `docs/data-classification.md`
  exactly
- [ ] Crash-free user rate > 99.5% (Firebase Crashlytics) in the 14 days before
  store push

### Telemetry Parity
- [ ] Crash events from the signed binary reach Crashlytics (not just debug builds)
- [ ] Release-build logs land in the backend with the correct `app_version` tag
- [ ] Heartbeat from active users matches DAU metric within 5%

---

## Evidence Collection

For each release, capture:

| Artifact | Path | Retention |
|---|---|---|
| Signed APK | `reports/releases/driver-vX.Y.Z.apk` (LFS or external bucket) | 2 y |
| Signed IPA | `reports/releases/driver-vX.Y.Z.ipa` | 2 y |
| MobSF report | `reports/releases/driver-vX.Y.Z-mobsf.pdf` | 2 y |
| SBOM | `reports/sbom/driver-vX.Y.Z.cdx.json` | 7 y |
| EAS build log URL | `reports/releases/driver-vX.Y.Z.txt` | 12 mo |
| Store-submission screenshots | `reports/releases/driver-vX.Y.Z-store/` | 2 y |

---

## Cadence

- **Per release**: run full checklist before submitting to stores.
- **Per audit cycle** (every 90 days): re-verify against the production binary
  (not just `main`).
- **On any CVE disclosure affecting Expo / React Native / native libs**: targeted
  re-check.

---

## Tools

| Tool | Use |
|---|---|
| **MobSF** (Mobile Security Framework) | Static binary analysis, permissions map, API key detection |
| **apkanalyzer** (Android SDK) | Manifest dump, resource inspection |
| **otool** / **codesign** (macOS) | IPA binary inspection, signature verification |
| **cyclonedx-node** / **cyclonedx-python** | SBOM generation |
| **Firebase App Check** | Runtime attestation |
| **Play Integrity API** | Runtime attestation (Android) |
| **Apple DeviceCheck / AppAttest** | Runtime attestation (iOS) |

---

## Output Format

Findings emit to the same YAML schema as other dimensions. Use `dimension: "23"`
in the YAML block and treat `blast_radius` as:

- `self` — affects individual installation (e.g. missing cert pin on a single device)
- `org` — affects every user of the release (e.g. secret leaked in binary)
- `regulator` — affects store compliance or privacy disclosure (e.g. undeclared permission)

---

## Known Gaps at Adoption

- D23 is newly added (2026-04-24). No prior rider or driver audit covered it.
- Schedule a D23 audit per app **before the next store submission**.
