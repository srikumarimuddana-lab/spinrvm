# Admin Dashboard Audit — Phase 1: SAST Results

**Date:** 2026-04-26  
**Tools:** ruff 0.x · ESLint (Next.js config) · TypeScript strict · npm audit · pip-audit

---

## 1. ruff (Python backend — `backend/routes/admin/`)

### Run result: FIXED during this phase

| Rule | Count | Files | Status |
|---|---|---|---|
| F821 undefined name | 19 | drivers.py, messaging.py, users.py, wallet.py | **Fixed** |
| B904 raise-from | 2 | analytics.py | **Fixed** |

**Root cause:** The April 25 audit remediation commit added `Literal`, `Dict`, `Any`, `Depends`, `uuid` usages but did not add the corresponding imports. Code would have failed at startup with `NameError` the first time those routes were called.

**Fixes applied (commit `49c4594`):**

| File | Fix |
|---|---|
| `drivers.py` | Added `Literal` to `from typing import` |
| `messaging.py` | Added `Any, Dict` to `from typing import` |
| `users.py` | Added `import uuid`; `Depends` to fastapi import; `from dependencies import get_admin_user` to `except ImportError` block |
| `wallet.py` | Added `import uuid` |
| `analytics.py` | Added `from e` to two `raise ... from e` calls in except handlers (B904) |

**Post-fix ruff result:** `All checks passed!`

---

## 2. TypeScript (`tsc --noEmit`)

**Result:** No errors (exit 0). TypeScript strict mode passes cleanly.

---

## 3. ESLint (`npm run lint` — Next.js ESLint config)

**Result:** 594 warnings, **0 errors**. Build is not blocked.

All warnings are `@typescript-eslint/no-explicit-any` — the codebase uses `any` extensively in API response types and component props. This is a type-safety concern (not a security concern in isolation) but reduces the value of TypeScript's type checking.

**Most affected files (by warning count):**

| File | Issue |
|---|---|
| `src/lib/api.ts` | ~200+ `no-explicit-any` — all API response shapes untyped |
| `src/app/dashboard/rides/_components/ride-list.tsx` | ~50 `no-explicit-any` |
| `src/app/dashboard/drivers/page.tsx` | ~30 `no-explicit-any` |

**Security-relevant ESLint findings:** None. No `dangerouslySetInnerHTML`, `eval`, `new Function`, or `innerHTML` patterns detected in source.

---

## 4. npm audit

**Result before fix:** 5 moderate vulnerabilities  
**Result after `npm audit fix`:** 3 moderate vulnerabilities (hono resolved)

| Package | Advisory | CVSS | Fix | Decision |
|---|---|---|---|---|
| `hono` ≤4.12.13 | GHSA-458j-xx4x-4375 — HTML injection via JSX attr names | Moderate | `npm audit fix` — upgraded to 4.12.15 | **Fixed (commit `ed5acc8`)** |
| `postcss` (via `next`) | GHSA-qx2v-qp2m-jg93 — XSS via unescaped `</style>` | Moderate | `--force` upgrade breaks Next.js; awaiting Next.js patch | Accept/track |
| `uuid` v13 | GHSA-w5hq-g745-h8pq — buffer bounds in v3/v5/v6 | Moderate | v14 is breaking; v4 (used here) is NOT affected | Accept |
| `dompurify` (via jspdf) | GHSA-39q2-94rc-95cp + GHSA-h7mw-gpvr-xq4m — ADD_TAGS bypass | Moderate | Not called with ADD_TAGS in this codebase (jspdf dependency only) | Accept/track |

**Accepted risk notes:**
- `uuid`: Only the `v3`, `v5`, `v6` functions are affected by the buffer-bounds issue. The dashboard uses only `uuidv4()` (unaffected).
- `postcss`: Affects CSS stringify output — relevant only if dynamically generating CSS with untrusted input (dashboard does not). Track for next Next.js upgrade.
- `dompurify`: Dashboard does not call DOMPurify directly; it's a transitive dep of `jspdf` (PDF export). The bypass only applies to `ADD_TAGS` with function predicates — not used here.

---

## 5. pip-audit (Python backend)

**Result:** `No known vulnerabilities found`

---

## 6. Phase 1 New Findings

| ID | Finding | Severity | Fixed in phase? |
|---|---|---|---|
| F-11 | Missing imports (F821) — 5 files would crash at runtime on first call | CRITICAL | ✅ Fixed (`49c4594`) |
| F-12 | `raise` without `from e` in analytics exception handlers (B904) | LOW | ✅ Fixed (`49c4594`) |
| F-13 | hono ≤4.12.13 JSX HTML injection (GHSA-458j-xx4x-4375) | MEDIUM | ✅ Fixed (`ed5acc8`) |
| F-14 | 594 `no-explicit-any` ESLint warnings across dashboard (type safety degraded) | LOW | Deferred — needs API type generation sprint |
| F-15 | postcss XSS via `</style>` (transitive via next) — no direct risk here | INFO | Accepted/tracked |

> **F-11 is the critical finding:** `drivers.py`, `messaging.py`, `users.py`, and `wallet.py` would all throw `NameError` at first invocation in a fresh process — meaning any driver approval, user status change, wallet credit/debit, or message send would 500. These are now fixed.
