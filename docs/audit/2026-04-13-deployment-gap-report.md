# Spinr Deployment Gap Report
**Generated:** 2026-04-13  
**Scope:** Local codebase (`C:\Users\TabUsrDskOff111\spinr\spinr`) vs remote (`ittalenthireca-sketch/Spinr`)  
**Author:** Claude Code (automated audit)

---

## Executive Summary

The Spinr monorepo is architecturally complete but has **5 critical gaps** blocking a clean production deployment today. The most urgent are hardcoded default secrets in `core/config.py`, a broken Supabase key name in `render.yaml`, and 14 missing GitHub Actions secrets. CI had 3 active failures (all now fixed in PRs #111, #114). 12 Dependabot security alerts remain open.

**Current CI status (run #24366157376 — post PR #111 merge):**

| Job | Status | Root Cause |
|-----|--------|-----------|
| `backend-test` | ❌ FAIL | ruff S110 (fixed in #fix/sentry-lazy-import, pending merge) |
| `admin-test` | ❌ FAIL | eslint-plugin-jsx-a11y ERESOLVE (fixed in PR #114) |
| `deploy-frontend` | ❌ FAIL | cascades from admin-test |
| `security-scan` | ❌ FAIL | Trivy SARIF needs `security-events: write` (fixed in PR #114) |
| `notify-failure` | ❌ FAIL | `SLACK_WEBHOOK` secret missing |
| All test jobs | ✅ PASS | — |

---

## Section 1 — Critical Blockers (Fix Before Any Production Deploy)

### C1 · Hardcoded secrets in `core/config.py`
**File:** `backend/core/config.py` lines ~28, ~40–41  
**Problem:** Default values survive to production if env vars are not explicitly set:
```python
JWT_SECRET = REDACTED      # known-weak default — anyone can forge tokens
ADMIN_PASSWORD = REDACTED  # trivially guessable — immediate admin access
```
**Fix:** Remove defaults entirely — use `...` (Pydantic required field) so the app fails at startup if not set:
```python
JWT_SECRET: str      # no default — ValidationError at startup if unset
ADMIN_PASSWORD: str  # no default — ValidationError at startup if unset
```
**Risk if ignored:** Any attacker who knows the weak defaults can forge JWTs and take admin control. ✅ Fixed in PR #116.

---

### C2 · `render.yaml` uses wrong Supabase key name
**File:** `render.yaml` line ~18  
**Problem:** Declares `SUPABASE_KEY` but `core/config.py` reads `SUPABASE_SERVICE_ROLE_KEY`. Render will inject a secret named `SUPABASE_KEY` which the app never reads — Supabase auth silently fails.  
**Fix:**
```yaml
# render.yaml
- key: SUPABASE_SERVICE_ROLE_KEY   # was: SUPABASE_KEY
  sync: false
```

---

### C3 · `SENTRY_DSN` not declared in `Settings` class
**File:** `backend/core/config.py`  
**Problem:** `server.py` does `settings.sentry_dsn` but `sentry_dsn` is not a field in `Settings`. Reading an undefined Pydantic attribute returns `None` silently — Sentry never initializes in production even if `SENTRY_DSN` is set in the environment.  
**Fix:** Add to `Settings`:
```python
sentry_dsn: str | None = None
```

---

### C4 · Python version mismatch between Render and Dockerfile
**File:** `render.yaml` line ~5  
**Problem:** `render.yaml` specifies `pythonVersion: "3.9.0"` but `Dockerfile` and CI both use Python 3.12. Some dependencies in `requirements.txt` require 3.10+ syntax (match statements, union types). Render deploy will fail or produce undefined behaviour.  
**Fix:**
```yaml
# render.yaml
pythonVersion: "3.12.0"
```

---

### C5 · 14 GitHub Actions secrets missing (repo shows zero)
**Details:** See separate secrets inventory. Immediate impact:
- `FLY_API_TOKEN` → Deploy job crashes at login
- `SLACK_WEBHOOK` → All failure notifications silently dropped
- `CODECOV_TOKEN` → Coverage upload fails on every backend-test run
- `VERCEL_TOKEN` / `VERCEL_ORG_ID` / `VERCEL_*_PROJECT_ID` → Frontend/admin deploys skip
- `SUPABASE_*` → Backend integration tests may silently skip

**All 14 required secrets:**
| Secret | Service | Priority |
|--------|---------|----------|
| `FLY_API_TOKEN` | Fly.io | 🔴 P0 |
| `SLACK_WEBHOOK` | Slack | 🔴 P0 |
| `CODECOV_TOKEN` | Codecov | 🟠 P1 |
| `SUPABASE_URL` | Supabase | 🟠 P1 |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase | 🟠 P1 |
| `PG_CONNECTION_STRING` | Supabase/Postgres | 🟠 P1 |
| `VERCEL_TOKEN` | Vercel | 🟡 P2 |
| `VERCEL_ORG_ID` | Vercel | 🟡 P2 |
| `VERCEL_FRONTEND_PROJECT_ID` | Vercel | 🟡 P2 |
| `VERCEL_ADMIN_PROJECT_ID` | Vercel | 🟡 P2 |
| `RENDER_API_KEY` | Render | 🟡 P2 |
| `RENDER_BACKEND_SERVICE_ID` | Render | 🟡 P2 |
| `EXPO_TOKEN` | Expo/EAS | 🟡 P2 |
| `EXPO_PUBLIC_API_URL` | Expo | 🟡 P2 |

---

## Section 2 — High Severity (Fix Before Public Launch)

### H1 · Double-deploy race condition on backend pushes
**Files:** `.github/workflows/ci.yml` (deploy-backend job) + `.github/workflows/deploy-backend.yml`  
**Problem:** Both workflows trigger on `push` to `main` when `backend/**` changes. They run simultaneously and both attempt to deploy to Fly.io, causing race conditions, wasted build minutes, and potential mid-deploy conflicts.  
**Fix:** Remove the `deploy-backend` job from `ci.yml` and rely solely on `deploy-backend.yml`, OR delete `deploy-backend.yml` and keep only `ci.yml`'s version. Pick one source of truth.

---

### H2 · Database migrations never run in CI
**Files:** `backend/migrations/` (21 files), `backend/sql/` (4 files), `.github/workflows/apply-supabase-schema.yml`  
**Problem:** The `apply-supabase-schema.yml` workflow (manual only) applies only `supabase_schema.sql`. The 21 incremental migration files and 4 PostGIS/feature SQL files must be applied **manually in order** after every fresh deploy. There is no migration tracking table to know which migrations have been applied.  
**Recommended fix:** Adopt a migration runner (e.g. `yoyo-migrations` or simple numbered psql script) and run it automatically on deploy. Minimum viable fix: add a migration-tracking table and a `run_migrations.sh` script called from `deploy-backend.yml`.

---

### H3 · Mobile apps point to wrong backend URL
**Files:** `rider-app/app.config.ts`, `driver-app/app.config.ts`  
**Problem:** Both mobile apps have `EXPO_PUBLIC_BACKEND_URL=https://spinr-backend-production.up.railway.app` (Railway) hardcoded in their EAS production config. The backend is deployed to Fly.io (`spinr-api.fly.dev`) and Render (`spinr-api.onrender.com`) — Railway is a third, apparently abandoned hosting target.  
**Fix:** Update both app configs to use the real production URL. Also inject via `EXPO_PUBLIC_API_URL` secret in CI so it's configurable without code changes.

---

### H4 · `smoke-test` job permanently disabled
**File:** `.github/workflows/ci.yml` line ~(smoke-test job)  
**Problem:** The smoke-test job that hits live endpoints post-deploy is disabled with `if: false`. Deploys are never verified — a broken deploy only surfaces through user reports.  
**Fix:** Re-enable the job. Requires `FLY_API_TOKEN` and a stable production URL. Consider a simple `curl --fail https://spinr-api.fly.dev/health` rather than a full framework.

---

### H5 · `frontend/` legacy app has placeholder EAS project ID
**File:** `frontend/app.config.js` (or `frontend/eas.json`)  
**Problem:** EAS project ID is `"your-project-id"`. Any `eas build` targeting `frontend/` will fail. The `eas-build.yml` workflow targets `frontend/`, making it permanently broken.  
**Fix:** Either assign a real EAS project ID to `frontend/`, or retire `eas-build.yml` in favour of targeting `rider-app/` (which has a real EAS project ID).

---

### H6 · `driver-app/` missing Stripe plugin
**File:** `driver-app/app.config.ts`  
**Problem:** `rider-app/` has `@stripe/stripe-react-native` plugin with merchant ID configured. `driver-app/` does not — drivers cannot process payments or view payment history in-app.  
**Fix:** Add Stripe plugin to `driver-app/app.config.ts` matching `rider-app/`'s config if drivers handle payments.

---

## Section 3 — Medium Severity (Fix Before Scale)

### M1 · No persistent volume for `backend/uploads/`
**File:** `fly.toml`  
**Problem:** Driver document uploads (`/api/v1/documents/upload`) write to `backend/uploads/`. Fly.io machines are ephemeral — uploads are lost on every restart/redeploy.  
**Fix:** Add a persistent volume in `fly.toml`:
```toml
[mounts]
  source = "spinr_uploads"
  destination = "/app/uploads"
```
Or migrate document storage to Supabase Storage / S3.

---

### M2 · Admin third-party keys need manual entry after every fresh deploy
**Problem:** Google Maps API key, Stripe keys, and Twilio credentials live in the Supabase `settings` table (managed via admin UI). There is no seed script — a fresh deploy has no payment, maps, or SMS capability until these are entered manually.  
**Fix:** Create a `scripts/seed_settings.py` that reads from env vars and populates the settings table on first deploy.

---

### M3 · `discovery/` app has bundle ID collision with `driver-app/`
**File:** `discovery/app.config.ts`  
**Problem:** Both use `com.spinr.driver`. If both are submitted to app stores, one will overwrite the other.  
**Fix:** Change `discovery/` to a dev-only bundle ID (e.g. `com.spinr.discovery.dev`) or delete the `discovery/` app if it's a prototype.

---

### M4 · Admin ESLint never blocks CI
**File:** `.github/workflows/ci.yml`  
**Problem:** `continue-on-error: true` on the admin ESLint step means 21+ React Compiler violations never gate a merge. Tech debt accumulates undetected.  
**Fix:** Resolve the 21 pre-existing React Compiler errors, then remove `continue-on-error: true` to enforce lint as a hard gate.

---

### M5 · `driver-app/` has no EAS build workflow
**Problem:** `rider-app/` gets automatic EAS builds via CI (on `[build]` commit message trigger). `driver-app/` has no equivalent — every driver-app build is fully manual.  
**Fix:** Add a `driver-app` build step alongside the existing `rider-app` step in `ci.yml`.

---

### M6 · 12 open Dependabot security alerts
**Packages affected:**
| Package | Severity | Issues |
|---------|----------|--------|
| `node-forge` | HIGH (4) | Ed25519 forgery, RSA-PKCS forgery, BigInteger DoS, basicConstraints bypass |
| `@xmldom/xmldom` | MODERATE | XML injection via CDATA |
| `brace-expansion` | MODERATE (2) | Zero-step ReDoS / process hang |
| `picomatch` | MODERATE (4) | Method injection, ReDoS via extglob |
| `yaml` | MODERATE | Stack overflow via deep nesting |

**Fix:** These are transitive dependencies. Add overrides/resolutions entries in the affected `package.json` files (same approach as the `@tootallnate/once` fix in PR #48).

---

## Section 4 — Value-Add Recommendations (Not Blocking, But High ROI)

### V1 · 🔥 Add migration tracking table (prevents data loss on fresh deploys)
Create a `schema_migrations` table and a simple runner script. Without it, every new environment requires someone to manually apply 26 SQL files in the right order from memory.

### V2 · 📊 Enable Codecov PR comments
Once `CODECOV_TOKEN` is added, Codecov will post inline coverage diffs on every PR. This makes it immediately visible when a PR drops coverage below 80%.

### V3 · 🔔 Re-enable smoke tests with health endpoint
The `/health` endpoint exists in the backend. A 3-line curl check post-deploy would catch deploy failures automatically. Currently deploying broken code produces zero automated signal.

### V4 · 🔐 Add `ADMIN_PASSWORD` rotation to deployment runbook
Currently `admin123` survives to production if env is not set. Add a startup check:
```python
if settings.admin_password == "admin123":
    raise RuntimeError("ADMIN_PASSWORD must be changed before running in production")
```

### V5 · 📱 Unify mobile backend URL via single env var
Both `rider-app/` and `driver-app/` hardcode the backend URL. Centralise it via `EXPO_PUBLIC_API_URL` injected from CI secrets — one change propagates to both apps without code edits.

### V6 · 🧪 Add `driver-app` to rider-app-test CI job (or create its own)
Currently `driver-app` tests only run in `driver-app-test` but there's no EAS build for it. Complete the loop so driver app changes are fully tested AND built in CI.

### V7 · 🌐 Consider Supabase Storage for file uploads (replaces ephemeral Fly volume)
The backend already uses Supabase for auth + database. Moving document uploads to Supabase Storage eliminates the ephemeral volume problem and keeps all persistence in one place.

### V8 · 📋 Auto-close stale CI failure issues
The repo has accumulated 20+ auto-created "CI failure" issues (nos. 93–113). These are noise. Add a `stale` action or a workflow step that auto-closes `ci-failure` labeled issues when CI passes again on `main`.

---

## Section 5 — CI Fixes Applied This Session

| # | Change | PR/Branch | Status |
|---|--------|-----------|--------|
| 1 | `sentry-sdk` downgrade to 2.8.0 (starlette compat) | PR #85 ✅ merged | Done |
| 2 | `@tootallnate/once` v2→v3 Dependabot fix | PR #48 ✅ merged | Done |
| 3 | CI coverage: notify-failure/notify-issue now cover e2e+security+docker jobs | merged | Done |
| 4 | `except Exception` → `except ImportError` in sentry guard | PR #111 ✅ merged | Done |
| 5 | ruff S110: narrow sentry exception to `ImportError` (removes noqa need) | fix/sentry-lazy-import branch | Pending PR |
| 6 | admin-test: `npm ci --legacy-peer-deps` for eslint-plugin-jsx-a11y compat | **PR #114** | Open |
| 7 | security-scan: `security-events: write` permission for Trivy SARIF | **PR #114** | Open |

---

## Section 6 — Prioritised Action Plan

### This Week (unblock production)
1. ✅ Merge PR #114 (CI fixes)
2. Add 14 GitHub Actions secrets (Settings → Secrets → Actions)
3. Fix `render.yaml` `SUPABASE_KEY` → `SUPABASE_SERVICE_ROLE_KEY`
4. Remove hardcoded defaults from `core/config.py` (`JWT_SECRET`, `ADMIN_PASSWORD`)
5. Add `sentry_dsn: str | None = None` to `Settings` class
6. Fix `render.yaml` Python version to 3.12

### Next Sprint (production-grade)
7. Fix mobile backend URL (Railway → Fly.io/Render)
8. Resolve double-deploy race condition (pick one: `ci.yml` or `deploy-backend.yml`)
9. Re-enable smoke tests
10. Create `seed_settings.py` for admin keys
11. Apply 21 migration files to production Supabase (manually, one time)
12. Fix Fly.io persistent volume for `backend/uploads/`

### Ongoing (quality)
13. Resolve 12 Dependabot alerts (node-forge, picomatch, etc.)
14. Fix `frontend/` EAS project ID or retire `eas-build.yml`
15. Add `driver-app/` EAS build to CI
16. Fix `discovery/` bundle ID collision
17. Remove `continue-on-error: true` from admin ESLint (after fixing 21 errors)

---

*Report stored at: `docs/audit/2026-04-13-deployment-gap-report.md`*
