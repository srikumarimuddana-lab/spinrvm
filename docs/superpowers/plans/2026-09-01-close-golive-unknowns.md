# Close go-live operational unknowns

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three go-live gaps called out after the 5xx/P1 remediations: leftover `/metrics?token=` scrapers, unverified admin wallet/export UI, and no production Next.js build.

**Architecture:** We cannot log into production Grafana from this repo. Fix everything that *is* in-repo (Alloy already uses Bearer; log when a query token is still sent, never log the secret; Playwright the wallet and export flows; run `npm run build`). Production Grafana remains an ops check documented in the runbook.

**Tech Stack:** FastAPI `/metrics`, Grafana Alloy (`metrics-agent/`), admin-dashboard Next.js 16 + Playwright, pytest.

**Spec:** The three unknowns in the 2026-09-01 5xx/P1 change-impact log.

## Global Constraints

- Never log `METRICS_AUTH_TOKEN` or the `?token=` value (PIPEDA / credential).
- Do not re-enable query-string scrape auth.
- Playwright e2e must mock `/api/**` via `setupAdminMocks` (no live backend).
- Do not apply migration 396 as part of this plan.
- Do not commit unless the user asks.

---

### Task 1: Query-token scrape — detect without leaking

**Files:**
- Modify: `backend/server.py` (`metrics()`)
- Modify: `backend/tests/test_metrics_auth.py`
- Modify: `docs/runbooks/capacity-scaling.md`

**Interfaces:**
- Consumes: existing Bearer-only `/metrics` gate
- Produces: `spinr.metrics` warning when `query_params["token"]` is present; 401 unchanged if no Bearer

- [x] **Step 1: Write the failing test** — assert a warning is logged and the secret string is not in the log message.
- [x] **Step 2: Log then 401** — if `request.query_params.get("token") is not None`, warn `" /metrics query-string token rejected; use Authorization: Bearer"`.
- [x] **Step 3: Runbook** — document in-repo Alloy uses `bearer_token`; `?token=` is 401; grep `spinr.metrics` after deploy.
- [x] **Step 4: pytest** — `tests/test_metrics_auth.py` with `-p no:xonsh --no-cov`

---

### Task 2: Playwright — wallet credit sends a reused idempotency key

**Files:**
- Modify: `admin-dashboard/e2e/misc-admin-2.spec.ts`

**Interfaces:**
- Consumes: `creditUserWallet(userId, amount, reason, idempotencyKey)`
- Produces: POST `/api/admin/wallet/credit` body includes `idempotency_key` matching UUID shape

- [x] **Step 1: Mock** GET wallet, GET user details, POST credit (capture body).
- [x] **Step 2: Drive UI** — open Jane Rider → fill amount/reason → Credit → Confirm credit.
- [x] **Step 3: Assert** captured JSON has `idempotency_key` length ≥ 8 and charset `^[A-Za-z0-9._:-]+$`.

---

### Task 3: Playwright — export approval toast + settings toggle

**Files:**
- Modify: `admin-dashboard/e2e/data-transfer.spec.ts`
- Modify: `admin-dashboard/e2e/settings.spec.ts`

- [x] **Step 1: Data Transfer** — search Jane, select row, Export tab, reason ≥ 10 chars, Export; mock 202 `{ approval_required: true }`; expect "Approval required".
- [x] **Step 2: Settings Security** — expect the dual-approval switch (`#dual_approval_exports_enabled`).

---

### Task 4: Production build + run the new e2e specs

**Files:** none (commands)

- [x] **Step 1:** `cd admin-dashboard && npm run build`
- [x] **Step 2:** `npx next start` then `npx playwright test` the new specs
- [x] **Step 3:** Update the change-impact log “What was NOT verified”
