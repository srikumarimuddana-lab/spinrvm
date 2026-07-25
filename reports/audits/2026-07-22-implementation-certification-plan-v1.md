# Spinr Implementation & Certification Plan (Round 1)

**Date:** 2026-07-22
**Scope:** Item #3 of the broader multi-surface review requested — "a complete Implementation Plan for database, backend, frontend, mobile apps (Android + iOS) from current state to certified for the Play Store / App Store, and everything else."
**Method:** Three code-grounded research passes (infra/DB/deployment, mobile store-certification readiness, testing/CI/QA readiness), cross-referenced against `CLAUDE.md`, `docs/PROJECT_BLUEPRINT.md`, `docs/PRODUCTION_READINESS.md`, `ACTION_ITEMS.md`, all 9 ADRs, all 33 runbooks, and the compliance docs. No new code or config was written in this round — this is a planning document.
**Companion document:** `reports/audits/2026-07-22-scheduled-rides-driver-tracking-audit-v1.md` (round 1 of this review, PR #2222) covers scheduled-ride and driver-tracking findings and is not repeated here.

---

## 0. Correcting the premise, honestly

You asked for an implementation plan "from scratch to certified." **That's not the situation on the ground, and pretending otherwise would produce a plan that's disconnected from reality.** This is a mature, actively-engineered platform:

- 4 live surfaces (backend, rider-app, driver-app, admin-dashboard) plus a shared TS package, all wired together.
- `docs/PRODUCTION_READINESS.md` (2026-06-09 audit) already put the platform at **~90% production-ready**, with most security hardening (HttpOnly tokens, OTP lockout, refresh-token reuse detection, Stripe idempotency, CSRF, MFA) closed.
- 303 database migrations, 9 ADRs, 33 operational runbooks, 21 CI/CD workflows, a full security-gates pipeline (SAST, dependency audit, secret scanning, container scanning, license checking), and a fully-drafted App Store/Play Store metadata package (descriptions, keywords, review notes, working reviewer test accounts) already exist.
- The mobile apps already build correctly-formatted store artifacts (.aab / signed .ipa) with real Apple/Google account IDs wired into EAS.

**What this actually is, then, is a gap-closure and sequencing plan** — identifying what stands between "90% ready, extensively documented" and "launched and certified," and putting it in the right order. That is a more useful and more honest deliverable than a generic zero-to-launch checklist, and it's what follows. Where a genuine from-scratch step is still needed (e.g., a staging environment doesn't exist), it's called out plainly.

---

## 1. Current-state summary by layer

### 1.1 Database

- **303 migration files**, highest sequence number **244** (gap is pre-existing duplicate-prefix debt — documented, CI-gated against new collisions, not a new problem).
- **Three separate, uncoordinated DB-apply mechanisms coexist**, none wired into automated CI:
  1. `backend/scripts/migrate.py` — Supabase-credential based, `schema_migrations(version)` tracking, handles `CONCURRENTLY` via autocommit. This is the one migration comments reference as authoritative.
  2. `backend/scripts/run_migrations.py` — `DATABASE_URL`-based, `schema_migrations(filename, checksum)` tracking, refuses to re-apply a modified file (runtime append-only enforcement).
  3. `.github/workflows/apply-supabase-schema.yml` — manual (`workflow_dispatch`), applies a **full schema dump** (`backend/supabase_schema.sql`) via raw `psql`, a fundamentally different mechanism from the two incremental runners.
- **No staging database** — every engineer points at their own personal Supabase project (per `docs/dev-setup.md`); no shared or throwaway staging Supabase project exists despite several docs (`docs/MOBILE_SMOKE.md`, `docs/ci-security-gates.md`, `docs/external-testing.md`) referencing a staging URL/DB as if operational. Treat `ACTION_ITEMS.md` E1 ("no staging exists") as ground truth — those other docs are aspirational, not current state.
- Recent migrations (242–244) are solid examples of the convention working as intended: append-only audit tables, RLS in the same file as the table, documented rollback plans.

### 1.2 Backend

- **Deploy topology is decided (ADR-007: Fly.io Toronto primary, Railway warm standby, single Cloudflare CNAME cutover, shared Redis) but the CI implementation doesn't match the decision:**
  - `deploy-fly.yml` — the Fly leg, correct per ADR-007.
  - `deploy-backend.yml` — a standalone Railway leg, correct as the "standby" per ADR-007.
  - **`ci.yml` also contains its own embedded `deploy-backend` job that deploys to Railway a second time on every push to `main`**, with a comment claiming *"Railway is the primary backend host"* and a Render fallback — this directly contradicts ADR-007 (dated a month later) and means Railway gets deployed to twice per push while the comment actively misleads anyone reading `ci.yml` in isolation.
  - `render.yaml`'s region is `oregon` — not Canadian. It's only reachable via the stale fallback path above, and the backend-compute-region guard in `core/config.py` only checks `SUPABASE_REGION`, not compute region, so this wouldn't fail startup — but it's a live policy inconsistency given how much weight this codebase puts on Canadian data residency elsewhere.
- **Post-deploy verification is a bare `/health` poll**, not a real smoke test (`ACTION_ITEMS.md` A2, still open) — a bad deploy that returns 200 on `/health` but is otherwise broken (e.g., auth misconfigured) would still go green.
- **`docs/ENVIRONMENT_VARIABLES.md` under-documents `backend/core/config.py` by ~18 fields**, including `SUPABASE_REGION` (the actual PIPEDA data-residency enforcement variable — hard-fails production boot if unset/non-Canadian, but isn't in the reference doc at all) and the Firebase audience-check IDs (also production-required). One factual error: doc says `ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 30; code sets it to 15.
- **`render.yaml` uses `SUPABASE_KEY`**, not the real field name `SUPABASE_SERVICE_ROLE_KEY` — would misconfigure the Render fallback if ever actually exercised.

### 1.3 Admin dashboard (frontend)

- Deployed to Vercel (Montreal, `yul1` — correct for residency) via a job embedded in `ci.yml`, including a gitleaks scan of the built `.next` output to catch accidentally-bundled client-side secrets. This path is solid.
- **`frontend/` (an older Expo-web surface) is dead** — its Vercel deploy job is explicitly disabled (`if: false`, "deprecated 2026-04-14"). It's not part of the live 4-surface architecture and should be archived, consistent with how this repo already handles other stale directories (`memory/`, `discovery/` per `CLAUDE.md`'s "Claude-Adjacent Directories" table).
- **`DEPLOYMENT.md` (repo root) is significantly stale**: it never mentions Fly.io, and describes `driver-app/` as a Vercel-deployed static site — which hasn't been true since the apps moved to native EAS builds. This needs a rewrite, not a patch.

### 1.4 Mobile apps — general readiness

- Both apps use Expo's managed/continuous-native-generation workflow — **no checked-in `ios/`/`android/` native folders** — which is the simpler, lower-maintenance certification path, and it's already what's in place.
- **The stated premise of "Expo SDK 54" (both in the original ask and in `docs/adr/002-expo-react-native.md`) is out of date.** Both apps are actually on **Expo SDK 55 / RN 0.85.2** today. The ADR needs a status update or a superseding entry before any certification section cites it.
- Bundle IDs (`com.spinr.user` / `com.spinr.driver`), Apple Team ID, App Store Connect app IDs, and Play service-account path are all real, non-placeholder values already wired into `eas.json`.
- Full store metadata — descriptions, keywords, category, age rating, privacy policy URL, support URL, and (importantly) **working App Review test accounts with notes** — is already drafted in both apps' `store-assets/metadata.json`.

### 1.5 iOS certification specifics

- **Privacy Manifest is fully populated**, in two places that must be kept in sync by hand: the `ios.privacyManifests` block in `app.config.ts` (what EAS actually reads) and a standalone `PrivacyInfo.xcprivacy` file in each app (for the prebuild target). Both correctly declare `NSPrivacyTracking: false` and a full data-collection nutrition label.
- `ITSAppUsesNonExemptEncryption: false` is set in both — avoids the App Store Connect encryption-questionnaire gate.
- Purpose strings (camera, photo library, always/when-in-use location) are present and scoped to actual usage; App Review notes in `driver-app/store-assets/metadata.json` already address why background location is used continuously — this is exactly the kind of thing Apple review scrutinizes, and it's handled.
- App Tracking Transparency is correctly *absent* — no tracking SDK exists, so no ATT prompt is needed; this is consistent, not a gap.
- **Known, self-flagged gap:** the driver-app's rich ride-offer push-notification extension (`withOfferCardNotificationService`) is disabled in code with an explicit comment that it needs its own Apple provisioning profile that hasn't been granted yet, and must be re-enabled before production cutover.

### 1.6 Android certification specifics

- **rider-app and driver-app target different Android API levels** — rider-app `targetSdkVersion: 35`, driver-app `targetSdkVersion: 36` — confirmed in both apps' `withForceCompileSdk.js` plugin source, not just `app.config.ts`. `docs/android-build-strategy.md` incorrectly claims both are stamped to 36; that's only true for driver-app. Two sibling apps under the same Play publisher account on different target levels means rider-app will hit Play's rolling target-API-level enforcement deadline sooner.
- **No Play Console Data Safety section content exists anywhere in the repo** — unlike the iOS side, which has a fully drafted privacy nutrition label, there's no equivalent prepared for Play's mandatory Data Safety form.
- AAB vs APK build-type selection is correctly configured (production profile defaults to `.aab`).
- A real, previously-encountered Android-16/targetSdk-36 compatibility bug is already fixed and documented: LogRocket's session-replay reflection hung the splash screen under Android's hidden-API enforcement; the fix is to gate LogRocket off by default on Android. Worth citing as evidence that future target-SDK bumps need device-level re-verification, not just a version bump.

### 1.7 Store-submission automation

- The `[build]` commit-tag path (referenced in `CLAUDE.md`) is real but **narrower than the doc implies**: it builds rider-app for both platforms, but does **not** build driver-app, and does **not** submit to either store.
- A separate manual (`workflow_dispatch`-only) workflow, `eas-native-build.yml`, can build and optionally auto-submit either app/platform — but it requires a human to trigger it each time, and nothing in CI supplies `play-service-account.json` from a secret before an auto-submit run (if that credential path is configured, it's configured directly in the Expo project, not visible from this repo).
- **`docs/deploy/04-mobile-eas.md` is referenced three times** (from both `store-assets/metadata.json` files) as the canonical submission runbook **and does not exist.** This needs to be written.

### 1.8 Accessibility (mobile-specific, distinct from the web-only ACTION_ITEMS E11)

- `docs/ACCESSIBILITY.md` explicitly marks both mobile apps "Manual — Not yet audited." Only `accessibilityLabel`/`accessibilityRole` spot-usage exists (17 rider-app / 11 driver-app files) — no comprehensive pass, no automated RN a11y linting, and `docs/MOBILE_SMOKE.md`'s manual pre-release checklist has zero accessibility items across its 8 sections. This is a real gap beyond E11 (which only covers the admin-dashboard web surface) and matters directly for the CLAUDE.md-stated WCAG 2.1 AA regulatory mandate.

### 1.9 Testing / CI / QA maturity

- **21 workflows** implement a genuinely comprehensive gate set: SAST (bandit, eslint-security, semgrep), dependency audit (pip-audit, yarn-audit, npm-audit), secret scanning (gitleaks + bundle-secrets), container scanning (Trivy), license checking, migration safety, breaking-change detection, and coverage-regression (advisory). **`docs/ci-security-gates.md` is stale** — it's still headed "Specification, to be wired in by devops," when `security-gates.yml` already implements essentially the full checklist. This is a five-minute doc fix, not an engineering gap.
- **`ACTION_ITEMS.md` A1 (per-module coverage floors) is confirmed still the single biggest open gap**: only one global floor exists (`backend/pytest.ini`, currently 60%, with a stated-but-unexecuted plan to ratchet to 65% "in the 2026-07 sprint" — that sprint has apparently passed without the bump landing), and there is **no per-module enforcement anywhere** for the CLAUDE.md-mandated 90%/80%/70% targets on payments/fare, rides/dispatch, and admin code respectively.
- E2E coverage is real (Playwright for admin-dashboard + both mobile apps' web-export paths, Maestro for native flows) but two things stand out: **the Stripe card-charge path (P0-5) is explicitly documented as a stub/xfail** — only the wallet-payment path is fully pinned by tests — and native iOS/Android E2E doesn't really exist beyond Maestro smoke flows plus a 40-step manual checklist.
- **E1 (staging) is the load-bearing blocker for four other open items**: E2 (load testing, harness already built), E4 (synthetic monitoring), a real rehearsal of E6 (DAST/pentest), and E7 (backup-restore drill) — none of these can be safely or meaningfully executed without a non-production environment to point them at.
- E8 (CODEOWNERS) is confirmed still entirely absent — no file has ever existed in this repo's git history.
- The "DAST" label in `docs/audit/admin-dashboard/03-dast.md` is misleading: it's actually a manual code-path security review (which did surface real findings, e.g. a plaintext-secrets-in-response and a privilege-escalation gap — worth someone re-checking those are closed), not evidence of actual dynamic scanning tooling. No OWASP ZAP or equivalent exists in any workflow.
- Dev-onboarding has real friction: no Docker Compose/one-command bootstrap, three separate hand-edited `.env` files, a manual LAN-IP edit for physical-device testing, and an easy-to-get-wrong backend start command. Not launch-blocking, but worth a cheap fix given it slows every new contributor.

---

## 2. Findings register (net-new, prioritized)

| ID | Sev | Area | Finding | Recommendation |
|---|---|---|---|---|
| IMP-1 | **P1** | Backend deploy | `ci.yml`'s embedded `deploy-backend` job double-deploys Railway and its comment contradicts ADR-007's Fly-primary decision. | Delete the embedded job from `ci.yml`; let `deploy-fly.yml`/`deploy-backend.yml` own their respective legs per ADR-007. Fix or remove the Render fallback (region + `SUPABASE_KEY` naming) or explicitly re-decide it's out of scope. |
| IMP-2 | **P1** | Database | Three uncoordinated DB-apply mechanisms, none in automated CI. | Pick one incremental runner as canonical (recommend `run_migrations.py` for its checksum-based append-only enforcement), wire it into the deploy pipeline, and demote the full-schema-dump workflow to bootstrap-only / documented-explicitly-manual. |
| IMP-3 | **P1** | Environment | `SUPABASE_REGION` and Firebase audience IDs — both production-boot-critical — are undocumented in `ENVIRONMENT_VARIABLES.md`; one documented default (`ACCESS_TOKEN_EXPIRE_MINUTES`) is factually wrong. | Regenerate the doc from `core/config.py` field-by-field; add a CI check that flags new config fields without doc coverage. |
| IMP-4 | **P1** | Android cert | rider-app targets API 35, driver-app targets 36 — inconsistent, and `docs/android-build-strategy.md` incorrectly states both are 36. | Bump rider-app's `withForceCompileSdk.js` to 36 to match driver-app, re-verify with a device-level smoke test given the prior LogRocket/targetSdk-36 incident, then fix the doc. |
| IMP-5 | **P1** | Android cert | No Play Console Data Safety section content exists. | Draft it now, in parallel with everything else — it's independent of other blockers and Play submission cannot proceed without it. |
| IMP-6 | **P2** | Store submission | `docs/deploy/04-mobile-eas.md` is referenced three times but doesn't exist; `[build]`-tag path only covers rider-app and never submits; no CI wiring supplies Play submission credentials. | Write the missing runbook documenting the real submission path (today: manual `eas-native-build.yml` per app). Decide whether to extend the `[build]` tag to driver-app or explicitly document it as rider-app-only. |
| IMP-7 | **P2** | Testing | A1 (per-module coverage floors) still fully open; the "2026-07 bump to 65%" noted in `pytest.ini`'s own history comment appears to have been missed. | Treat as the top engineering-effort item in the testing phase; ratchet per CLAUDE.md's module targets, starting with the already-flagged P0-5 Stripe card-charge test gap. |
| IMP-8 | **P2** | Compliance register | LogRocket (an active data-collecting SDK, already disclosed in the iOS privacy manifest) is missing from `docs/vendor-inventory.md`/`vendor-register.md`. | Add it — this is a quick, self-contained fix and closes a real PIPEDA/vendor-disclosure gap before it's found externally. |
| IMP-9 | **P2** | Accessibility | Mobile apps have no automated a11y linting and zero a11y items in the manual smoke checklist — a distinct gap from the web-only ACTION_ITEMS E11. | Add `eslint-plugin-react-native-a11y` (or equivalent) to CI, and add a VoiceOver/TalkBack pass to `docs/MOBILE_SMOKE.md`'s checklist, prioritizing the OTP and booking-flow screens already flagged as TODO in `docs/ACCESSIBILITY.md`. |
| IMP-10 | **P2** | iOS cert | Driver-app rich-notification extension is disabled pending an Apple provisioning profile the team doesn't yet have EAS-side access to mint. | Get Apple Developer account access sorted for this profile early — it's an account-administration lead-time item, not an engineering task, so it should start now rather than at the end. |
| IMP-11 | **P3** | Android Auto | Unproven on real hardware; Google Play car-app review (separate approval gate, has lead time) hasn't started. | Don't gate the main launch on this — treat as a fast-follow. Schedule the DHU/real-head-unit validation explicitly so it doesn't silently slip. |
| IMP-12 | **P3** | Docs hygiene | `DEPLOYMENT.md` (root), `docs/ci-security-gates.md` (marked "spec" when implemented), `docs/adr/002-expo-react-native.md` (says SDK 54), `PRODUCTION_READINESS.md`/`ACTION_ITEMS.md` (both ~6 weeks stale, e.g. citing 178 migrations vs. the actual 303) all need a refresh pass. | Batch these into one docs-only PR — none require a decision, just an update to match reality. |
| IMP-13 | **P3** | Dev experience | No one-command local bootstrap; three hand-edited `.env` files; manual LAN-IP edits for device testing. | Not launch-blocking. A `docker-compose.yml` or a bootstrap script would meaningfully cut new-contributor ramp time — worth doing once the P1/P2 items above are clear. |

---

## 3. Phased roadmap

This sequences the findings above plus the still-open `ACTION_ITEMS.md` P0–P4 items into an order that respects real dependencies (staging has to exist before anything that needs it can run; cheap doc fixes can happen anytime and shouldn't block engineering).

### Phase A — Foundational, unblocks everything else
1. **Stand up a staging environment** (`ACTION_ITEMS.md` E1) — a throwaway Fly app + Supabase project with synthetic data. This is the one genuine "build it from scratch" item in this whole plan, and it's the prerequisite for E2, E4, a real E6 rehearsal, and E7.
2. **IMP-1** — reconcile the backend-deploy redundancy (delete `ci.yml`'s embedded Railway job; fix or retire the Render fallback).
3. **IMP-2** — consolidate DB-migration tooling to one canonical, CI-wired runner.

### Phase B — Cheap, parallelizable, no dependencies
- IMP-3 (env var doc), IMP-8 (vendor register), IMP-12 (doc staleness batch) — all doable immediately and in parallel with Phase A, by anyone, without blocking on anything.
- IMP-5 (Play Data Safety content) and IMP-6 (write the missing EAS submission runbook) — also independent, can start now.
- IMP-10 (Apple provisioning profile access) — start the account-administration request now since it has external lead time.

### Phase C — Testing & coverage hardening
- IMP-7 / `ACTION_ITEMS.md` A1 (per-module coverage floors), starting with the P0-5 Stripe card-charge E2E gap since it's both a coverage hole and a real payment-path risk.
- `ACTION_ITEMS.md` A2 (real post-deploy smoke test, not just `/health`).
- IMP-9 (mobile accessibility linting + smoke-checklist coverage).

### Phase D — Mobile certification execution
- IMP-4 (rider-app targetSdk parity with driver-app, then device-level re-verification).
- Finalize and internally review both apps' store listings against the drafted `store-assets/metadata.json`.
- Submit to Play internal track / TestFlight via the (now-documented, per IMP-6) manual `eas-native-build.yml` path for both apps.

### Phase E — Pre-launch operational drills (needs Phase A's staging environment)
- `ACTION_ITEMS.md` E2 (execute the already-built load-test harness).
- `ACTION_ITEMS.md` E7 (backup-restore drill — a backup is only real after a restore).
- `ACTION_ITEMS.md` C1 (Railway↔Fly failover drill, never yet exercised).
- `ACTION_ITEMS.md` E6 (real DAST tooling against staging, plus booking the external pentest — budget item, start the procurement conversation now given typical lead time).
- `ACTION_ITEMS.md` E4 (synthetic monitoring/SLO alerting).

### Phase F — Governance, then launch
- `ACTION_ITEMS.md` E8 (CODEOWNERS), E9 (postmortem template), E12 (on-call/escalation doc) — cheap, do alongside Phase E.
- Promote from internal/TestFlight track to public App Store / Play Store once Phases A–E are clear.
- IMP-11 (Android Auto) and IMP-13 (dev-experience bootstrap) as fast-follows after launch — neither should hold the launch date.

---

## 4. What this plan deliberately does not cover

- **Privacy policy / Terms of Conditions content** — item #4 of your original request, a separate workstream. Two relevant gaps surfaced in this research that the privacy/legal pass should pick up directly: `docs/data-classification.md`'s own open-items list flags **DV-16** (Gemini not yet disclosed as a sub-processor in the privacy policy) and **DV-8** (no scheduled hard-delete automation at PIPEDA retention horizons); `docs/dpa-register.md` is ~3 months stale with several vendor DPAs marked "⚠ VERIFY" or "TBD."
- **SGI (Saskatchewan) quarterly regulatory reporting** — `docs/compliance/sgi-quarterly.md` is explicitly self-labeled a "gap write-up, not an implemented pipeline": the underlying data (insurance periods, per-phase trip distances) is captured, but no export/submission tooling exists, and six open questions need SGI/legal answers before it can be built. Worth its own short workstream once legal is looped in — flagging here so it doesn't get lost between "implementation plan" and "regulatory."
- Detailed engineering task breakdowns for each roadmap item (e.g., the exact code diff to reconcile the backend-deploy workflows) — this plan sequences and scopes the work; execution should follow this repo's normal `≤3-files-per-subtask` convention per `CLAUDE.md`.
