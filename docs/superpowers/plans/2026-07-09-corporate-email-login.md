# Corporate Email Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Uber-for-Business-style email OTP login for company portal users.

**Architecture:** Backend adds company email OTP issue/verify endpoints under the existing portal auth namespace and stores short-lived hashed codes in a service-role-only Supabase table. The Next.js dashboard keeps refresh tokens in HttpOnly cookies through local `/api/company-auth/*` route handlers and updates `/company-login` to collect email instead of phone.

**Tech Stack:** FastAPI, Supabase/Postgres, Redis lockout helpers where already used, existing transactional email provider, Next.js App Router, Zustand.

## Global Constraints

- Keep company portal auth separate from staff admin auth.
- Do not expose refresh tokens to browser JavaScript.
- Do not log raw email addresses.
- Enable RLS on every new user-data table.
- Use explicit service-role grants for the new OTP table.
- Keep each commit to one logical change and no more than three implementation files when possible.
- Use TDD: write failing tests before production code.

---

### Task 1: Backend Email OTP Storage And Routes

**Files:**
- Create: `backend/migrations/219_corporate_email_otp_records.sql`
- Modify: `backend/routes/auth.py`
- Test: `backend/tests/test_company_email_login.py`

**Interfaces:**
- Produces: `POST /api/portal/auth/send-email-otp` with body `{ "email": "name@company.com" }`.
- Produces: `POST /api/portal/auth/verify-email-otp` with body `{ "email": "name@company.com", "code": "123456" }`.
- Produces: standard `AuthResponse` with `token`, `refresh_token`, `user`, `csrf_token`, and expiry fields.

- [ ] **Step 1: Write failing backend tests**

Create tests that patch `db_supabase`, `send_transactional_email`, and token issuance. Verify hashed OTP persistence, valid-code token issuance, invite acceptance by email, and invalid-code rejection.

- [ ] **Step 2: Run backend tests and confirm failure**

Run: `pytest backend/tests/test_company_email_login.py -q`

Expected: failing imports or missing route/function assertions.

- [ ] **Step 3: Add migration**

Create `corporate_email_otp_records` with RLS, indexes on email and expiry, and service-role grants only.

- [ ] **Step 4: Implement backend endpoints**

Add request models, email normalization, code generation, DB persistence, email send, verification, user lookup/create, pending invite acceptance, token issuance, and CSRF generation in `backend/routes/auth.py`.

- [ ] **Step 5: Run backend tests**

Run: `pytest backend/tests/test_company_email_login.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

Run: `git add backend/migrations/219_corporate_email_otp_records.sql backend/routes/auth.py backend/tests/test_company_email_login.py && git commit -m "feat: add corporate email otp backend"`

### Task 2: Company Auth Proxy And API Helpers

**Files:**
- Create: `admin-dashboard/src/app/api/company-auth/verify-email-otp/route.ts`
- Modify: `admin-dashboard/src/lib/companyApi.ts`
- Test: `admin-dashboard/src/lib/__tests__/companyApi.test.ts`

**Interfaces:**
- Consumes: backend `POST /api/portal/auth/send-email-otp`.
- Consumes: backend `POST /api/portal/auth/verify-email-otp`.
- Produces: `sendCompanyEmailOtp(email: string): Promise<void>`.
- Produces: `verifyCompanyEmailOtp(email: string, code: string): Promise<CompanyOtpVerifyResult>`.

- [ ] **Step 1: Write failing frontend API tests**

Mock `fetch` and assert the new helpers call the expected routes and strip refresh-token handling through the local Next route.

- [ ] **Step 2: Run targeted tests and confirm failure**

Run: `cd admin-dashboard && npm test -- src/lib/__tests__/companyApi.test.ts`

Expected: missing exported helper failures.

- [ ] **Step 3: Implement route handler and helpers**

Mirror the existing `verify-otp` route handler, changing only the upstream backend path. Add the two helper exports to `companyApi.ts`.

- [ ] **Step 4: Run targeted tests**

Run: `cd admin-dashboard && npm test -- src/lib/__tests__/companyApi.test.ts`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add admin-dashboard/src/app/api/company-auth/verify-email-otp/route.ts admin-dashboard/src/lib/companyApi.ts admin-dashboard/src/lib/__tests__/companyApi.test.ts && git commit -m "feat: add company email auth proxy"`

### Task 3: Company Login UI

**Files:**
- Modify: `admin-dashboard/src/app/company-login/page.tsx`
- Test: `admin-dashboard/src/__tests__/company-login.test.tsx`

**Interfaces:**
- Consumes: `sendCompanyEmailOtp`.
- Consumes: `verifyCompanyEmailOtp`.
- Keeps existing `completeLogin`, invite-token acceptance, membership loading, and role-based redirect behavior.

- [ ] **Step 1: Write failing UI tests**

Assert the login page asks for work email, sends an email code, verifies the code, accepts invite tokens, loads memberships, and redirects.

- [ ] **Step 2: Run targeted UI tests and confirm failure**

Run: `cd admin-dashboard && npm test -- src/__tests__/company-login.test.tsx`

Expected: old phone-login UI fails the email assertions.

- [ ] **Step 3: Update login UI**

Replace phone state and normalization with email state and normalization. Update copy, input type, placeholders, and handler calls to use the email helpers.

- [ ] **Step 4: Run UI tests**

Run: `cd admin-dashboard && npm test -- src/__tests__/company-login.test.tsx`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run: `git add admin-dashboard/src/app/company-login/page.tsx admin-dashboard/src/__tests__/company-login.test.tsx && git commit -m "feat: switch company login to email"`

### Task 4: Verification And Graph Refresh

**Files:**
- Modify: `graphify-out/graph.json`
- Modify: `graphify-out/GRAPH_REPORT.md`
- Modify: `graphify-out/manifest.json`

**Interfaces:**
- Consumes: all prior committed tasks.
- Produces: refreshed graphify outputs and verification evidence.

- [ ] **Step 1: Run focused backend tests**

Run: `pytest backend/tests/test_company_email_login.py -q`

Expected: pass.

- [ ] **Step 2: Run focused frontend tests**

Run: `cd admin-dashboard && npm test -- src/lib/__tests__/companyApi.test.ts src/__tests__/company-login.test.tsx`

Expected: pass.

- [ ] **Step 3: Rebuild graphify**

Run: `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"`

Expected: command exits 0. If `python3` is unavailable on Windows, use `python` with the same code.

- [ ] **Step 4: Commit graph outputs**

Run: `git add graphify-out/graph.json graphify-out/GRAPH_REPORT.md graphify-out/manifest.json && git commit -m "chore: refresh graphify after corporate email login"`
