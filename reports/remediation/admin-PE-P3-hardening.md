# Phase E · P3 — Admin Hardening (Phase E Findings)

LOW severity and hardening items from the Phase E audit. Schedule after P1/P2 are cleared and the admin panel is operationally stable.

Source audit: `reports/audits/2026-04-26-admin-panel-v2-phase-e.txt`
Branch: `claude/plan-deferred-tasks-qtT8I`

**Estimated total effort:** ~8–14 hours.

---

## Already Implemented in This Session

| Finding | Title | Status |
|---------|-------|--------|
| [18-6] | .env.example | ✅ Created at `admin-dashboard/.env.example` |
| [20-4] | Registry pin in .npmrc | ✅ Fixed: `registry=https://registry.npmjs.org/` |
| [23-8] | robots.txt + noindex metadata | ✅ Fixed: `public/robots.txt` + layout.tsx metadata.robots |

---

## A-PE-P3-1 · Build Output Not Scanned for Secrets

**What's wrong:** Gitleaks scans git history but not the compiled `.next/` output. A `NEXT_PUBLIC_*` variable pointing to a server-only secret would slip through — the built bundle leaks but the source doesn't match Gitleaks patterns.

**Finding:** `[17-5]` · Severity: LOW · Risk score: 6

**File to fix:** `.github/workflows/security-gates.yml` or `.github/workflows/ci.yml`

**How to fix:** After `npm run build`, add a step that runs `gitleaks detect --source .next/ --no-git` (or truffleHog over `.next/`) before the Vercel upload step.

**Effort:** 1 h · **Audit ref:** 17-5

---

## A-PE-P3-2 · NEXT_PUBLIC_API_URL: Add Localhost Fallback Warning

**What's wrong:** `admin-dashboard/next.config.ts:3-6` — if `NEXT_PUBLIC_API_URL` is unset in production, the config silently calls localhost. A production misconfiguration produces confusing 502s.

**Finding:** `[18-4]` · Severity: LOW · Risk score: 4

**Status:** The fail-fast `throw` was added in next.config.ts during this session (Phase E implementation). This finding is effectively resolved.

**Effort:** 0 h (done) · **Audit ref:** 18-4 ✅

---

## A-PE-P3-3 · lucide-react SVG Rendering — Upgrade to Latest

**What's wrong:** `lucide-react` 1.8.0 is behind; two older SVG-rendering bugs existed in dynamic-icon-name code paths.

**Finding:** `[19-6]` · Severity: LOW (general hygiene)

**File to fix:** `admin-dashboard/package.json`

**How to fix:** `npm install lucide-react@latest`; run visual regression check on dashboard icons.

**Effort:** 0.5 h · **Audit ref:** 19-6

---

## A-PE-P3-4 · Sentry Release Tag Already Added (Verify Integration)

**Status:** Release tag was added in this session: `release: process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA`. Verify in Sentry UI post-deploy that "Releases" are auto-created.

**Finding:** `[21-6]` · Done · **Audit ref:** 21-6 ✅

---

## A-PE-P3-5 · Vendor Register (PIPEDA DPA Inventory)

**What's wrong:** No `docs/vendor-register.md` listing each third-party processor (Vercel, Sentry, Supabase, Vercel Analytics) with DPA URL, data class, region, effective date, and renewal date.

**Finding:** `[22-4]` · Severity: LOW · Risk score: 4 · Regulations: PIPEDA, SOC2

**How to fix:** Create `docs/vendor-register.md` with the template:

```markdown
| Vendor | Data class | DPA URL | Region | Effective | Renewal |
|--------|-----------|---------|--------|-----------|---------|
| Vercel (hosting) | Admin session, page paths | ... | yyz1 (pinned) | ... | Annual |
| Sentry | Error telemetry (PII-scrubbed) | ... | US (pending EU migration) | ... | Annual |
| Supabase | All app data | ... | ca-central-1 | ... | Annual |
| Vercel Analytics | Page views (IDs scrubbed) | ... | US edge | ... | Annual |
```

**Effort:** 1–2 h (legal input needed for DPA URLs) · **Audit ref:** 22-4

---

## A-PE-P3-6 · noopener on target="_blank" Links

**What's wrong:** `<a target="_blank">` without `rel="noopener noreferrer"` allows opened tabs to access `window.opener`. Modern browsers default to noopener, but defence-in-depth.

**Finding:** `[23-10]` · Severity: LOW

**How to fix:** Enable ESLint rule `react/jsx-no-target-blank` (part of `eslint-plugin-react` recommended set). Audit any existing instances.

**Effort:** 1 h · **Audit ref:** 23-10

---

## A-PE-P3-7 · Service Worker Usage Policy (Documentation)

**Finding:** `[23-9]` — Admin has no service worker. If a future PR adds one, it must be scoped to `/dashboard/*`, cache only static assets, never cache HTML or API responses (staleness on payouts/dispatch is a correctness hazard).

**How to fix:** Add a one-paragraph note to `docs/runbooks/admin-pwa.md` explaining the constraint.

**Effort:** 0.25 h · **Audit ref:** 23-9

---

## Checklist

- [x] A-PE-P3-1 Bundle secret scan in CI (17-5) — G5b job added to security-gates.yml
- [x] A-PE-P3-2 NEXT_PUBLIC_API_URL fail-fast (18-4) — done in next.config.ts
- [x] A-PE-P3-3 lucide-react upgrade (19-6) — upgraded to ^1.11.0 (latest)
- [x] A-PE-P3-4 Sentry release tag (21-6) — done; verify in Sentry UI post-deploy
- [ ] A-PE-P3-5 Vendor register PIPEDA DPA inventory (22-4)
- [x] A-PE-P3-6 noopener lint rule (23-10) — react/jsx-no-target-blank: "error" in eslint.config.mjs
- [x] A-PE-P3-7 Service worker policy doc (23-9) — created docs/runbooks/admin-pwa.md
