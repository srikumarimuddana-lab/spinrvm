# Runbook: EAS Build/Update Failures (rider-app / driver-app)

**What this covers:** Diagnosing a red EAS Mobile Update or EAS Native
Build job — including the specific "SDK bump breaks Metro bundling"
failure class that hit `#605`–`#612` on 2026-08-11, and the general
triage/prevention framework for the next one.

**Severity:** P1 for `main`/production channel — a broken bundle blocks
**every** fix from reaching phones (OTA or store) until resolved, so its
blast radius compounds with time, not just with the triggering diff.

**Prerequisites:**
- GitHub Actions access (Actions tab → `EAS Mobile Update` /
  `EAS Native Build` workflow runs, or `expo.dev` → project → Updates/Builds)
- Local checkout with Node 20 + Yarn
- `EXPO_TOKEN` if you need to run `eas` commands yourself (not required for
  the `expo export` diagnostic below — that's local-only, no EAS API calls)

---

## 1. Symptoms

- The EAS Updates dashboard (or the `EAS Mobile Update` GitHub Actions
  workflow) shows a run of consecutive **red ✗** entries for one or both
  apps, especially right after a dependency bump (Dependabot/Renovate
  `expo-stack` group, a manual `expo upgrade`, or a React Native version
  bump).
- Follow-up commits/PRs that touch app code keep failing too, even ones
  that have nothing to do with the original bump — because `eas-build.yml`
  re-bundles from HEAD on **every** push to `main` that touches
  `rider-app/**`, `driver-app/**`, or `shared/**`, so a broken bundle fails
  every subsequent push until the underlying resolution error is fixed.
- `tsc --noEmit` and `expo install --check` (the existing
  `mobile-dep-check.yml` checks) are both green. This is expected, not a
  contradiction — neither of those performs an actual Metro bundle. See
  §3 for why that matters.

---

## 2. Quick diagnosis — reproduce locally before touching CI

Don't guess from the EAS dashboard's truncated error output. Reproduce the
exact bundling step locally first — it's fast, free, and gives you the real
Metro stack trace instead of a summarized CI log line:

```bash
cd rider-app   # or driver-app
yarn install --frozen-lockfile
npx expo export --platform android --output-dir /tmp/export-check
```

| Result | Meaning |
|---|---|
| Exits 0, `.hbc` bundle listed in output | Bundling itself is fine — the EAS failure is something else (credentials, EAS API, build infra). Check the EAS run's raw log, not just the dashboard status dot. |
| Exits non-zero with `Unable to resolve module ...` | **Module resolution failure** — a dependency (often a native module) imports a path that doesn't exist in the currently-installed version of some other dependency. Go to §3. |
| Exits non-zero with a Babel/transform error | Different class of break (syntax the configured Babel preset can't parse) — not covered by this runbook; check `babel.config.js` against the new dependency's minimum supported RN/Expo version. |
| Exits non-zero with an out-of-memory or timeout | Infra/resource issue, not a code break — check runner resources, not app code. |

Run the same check with `--platform ios` too — resolution can differ by
platform (platform-specific `resolveRequest` branches, `.ios.ts`/`.android.ts`
file resolution).

---

## 3. Root-cause class: SDK/RN bump breaks a transitive dependency's import

This is the failure mode behind the 2026-08-11 incident
(`docs/change-log/2026-08-11-metro-rngh-renderer-shim.md`) and is common
enough after any React Native version bump to warrant its own section.

**Mechanism:** a React Native (or Expo SDK) upgrade removes, renames, or
relocates an internal file that isn't part of RN's public API — but some
third-party native module imports it directly anyway (bypassing the public
API surface, usually because the module predates the RN version that made
the internal path unstable). The import is syntactically valid TypeScript —
it typechecks fine — but at bundle time Metro can't find the file, because
the RN version actually installed no longer ships it.

**Why the existing CI checks miss it:**

| Check | What it verifies | Why it doesn't catch this |
|---|---|---|
| `tsc --noEmit` | Types line up | A string import path isn't a type; TS has no way to know the file doesn't exist on disk. |
| `expo install --check` | Installed package *versions* match Expo's SDK compatibility table | Never resolves a single module — pure version-string diffing against a manifest. |
| *(nothing, before this incident)* | Whether Metro can actually produce a bundle | — |

The only thing that ever exercised real bundling was `eas-build.yml`'s
push-triggered OTA publish — which runs **after** merge, in production,
against the `production`/`preview`/`test` channel. That is the worst place
to discover a build break: it fails silently (no PR to attach the failure
to, no reviewer looking at it) and keeps failing on every subsequent push
until someone notices the dashboard.

**Diagnostic steps once you've confirmed a resolution error locally:**

1. Read the failing module path from the Metro error
   (`Unable to resolve module <path> from <importer>`).
2. Find the importer: `grep -rn "<path>" node_modules/<importer>/` — confirm
   which dependency does the unconditional import.
3. Check whether the path still exists in the RN/Expo version actually
   installed: `npm pack react-native@<version>` (or whichever package owns
   the removed file) and inspect the tarball directly — don't trust local
   `node_modules`, which can have stray patches or stale installs.
   `npm view <package>@<version> dist.tarball` gets you the URL without a
   full pack if you just want to list contents.
4. Check whether a newer release of the importing dependency has already
   moved off the removed path (frequently the upstream fix, since the
   maintainer hits the exact same break against the new RN version).
   **Verify a newer version actually works before pinning it** — per this
   repo's standing pre-merge gate — don't bump blind.
5. If no compatible upstream release exists yet, add a targeted Metro
   `resolveRequest` redirect in `metro.config.js` (both apps already have
   an established stub/redirect pattern — follow it, don't invent a new
   mechanism). Redirect to the **semantically closest replacement** your
   app's configuration actually uses (e.g. redirecting an old-architecture
   renderer shim to the Fabric shim is only correct because both apps run
   `newArchEnabled: true` — check `app.config.ts` before assuming which
   shim is "live" for your app).
6. **Track the redirect as debt with an explicit removal condition** — add
   an `ACTION_ITEMS.md` entry naming the upstream release to watch for and
   what to delete once it ships. A silent workaround with no removal
   trigger accumulates forever.

---

## 4. Fix verification checklist

Before considering the incident closed:

- [ ] `expo export --platform android` exits 0 for **both** rider-app and
      driver-app (not just the one you noticed the failure in — if it's a
      shared dependency, assume both are affected until you've checked).
- [ ] `expo export --platform ios` exits 0 for both apps.
- [ ] Grep confirms no other importer of the same broken path exists
      elsewhere in the dependency tree (a redirect that only patches one
      call site while another remains broken looks fixed and isn't).
- [ ] The redirect/workaround is scoped as narrowly as possible (exact
      module-name match, not a broad prefix) so it can't silently swallow
      an unrelated future resolution error for the same importer.
- [ ] A real EAS run (not just local `expo export`) confirms the fix in
      the actual build environment — local success is strong evidence but
      not proof; EAS's containers can differ in subtle ways (Node version,
      registry mirror, cache state).
- [ ] `ACTION_ITEMS.md` has an entry for the durable fix (upstream bump)
      if the applied fix is a workaround, not the real thing.

---

## 5. Prevention — what changed after the 2026-08-11 incident

`.github/workflows/mobile-bundle-smoke.yml` now runs the exact
`expo export --platform android` / `--platform ios` commands from §2 as a
**required PR check** on every PR touching `rider-app/**`, `driver-app/**`,
or `shared/**` — before merge, not after. See `ACTION_ITEMS.md` C17 for the
full reasoning and its own verification record.

This closes the specific gap that let 8 EAS Mobile Update jobs fail in a
row unnoticed. It does **not** replace `eas-build.yml`'s production OTA
run — that's still the step that actually ships the bundle — it just means
the PR merging the break already failed a check, instead of the break only
surfacing after landing on `main`.

**Follow-up still needed (human, not code):** add
`mobile-bundle-smoke.yml`'s two jobs to `main`'s branch-protection required
status checks. Until that's done, the check runs and reports but does not
block merge — a repo admin needs to add it via Settings → Branches. Before
relying on it, also confirm it actually fires per PR (see the `C13` entry
in `ACTION_ITEMS.md` — an unrelated, already-open finding that some
`pull_request`-triggered workflows in this repo silently never run at all;
worth a spot-check on this new workflow's first real PR rather than
assuming a green Actions tab means every configured check ran).

---

## 6. What this runbook does NOT cover

- EAS build failures caused by **native** build config (Xcode project
  settings, Gradle, provisioning profiles, credentials) — those don't
  reproduce via `expo export` (JS-bundle-only) and need an actual
  `eas build` run or Xcode/Android Studio locally. Different failure class,
  different diagnostic path.
- Runtime crashes after a successful bundle/build — see
  `docs/runbooks/MOBILE_SMOKE.md` for the human device-smoke checklist.
- EAS service outages / credential expiry — check `expo.dev` status page
  and `eas credentials` before assuming the app code is at fault.

---

**Owner:** mobile team · add an entry to §3's diagnostic steps if a future
incident surfaces a resolution-failure pattern not covered here.
