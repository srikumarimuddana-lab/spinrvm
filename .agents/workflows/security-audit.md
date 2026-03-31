---
description: Full security audit workflow for the Spinr platform
---

# Security Audit Workflow

Follow ALL steps. Reference `.agents/roles/security-engineer.md` for detailed checks.

## Step 1: Secrets Scan
// turbo
```bash
cd backend && grep -rn "sk_live\|sk_test\|password\s*=\|secret\s*=\|Bearer\s" --include="*.py" . | grep -v ".env" | grep -v "__pycache__" | grep -v ".pyc"
```

Check results:
- [ ] No hardcoded API keys or tokens
- [ ] No hardcoded passwords or secrets
- [ ] No bearer tokens in source code

// turbo
```bash
cd rider-app && grep -rn "sk_live\|sk_test\|supabase.*key\|api.*key" --include="*.ts" --include="*.tsx" app/ store/ 2>/dev/null | head -20
```

- [ ] Frontend doesn't contain secret keys (only public/anon keys are acceptable)

## Step 2: Environment File Audit
- [ ] `backend/.env` exists and is in `.gitignore`
- [ ] `backend/.env.example` exists with placeholder values
- [ ] `rider-app/.env` exists and is in `.gitignore`
- [ ] No `.env` files committed to git: `git log --all --oneline --diff-filter=A -- "*.env"`

## Step 3: Authentication Audit
Review `backend/dependencies.py`:
- [ ] JWT_SECRET loaded from environment (not hardcoded)
- [ ] Token expiry is reasonable (currently 30 days)
- [ ] Firebase token verification is server-side
- [ ] Admin role check exists and cannot be bypassed
- [ ] Session invalidation works properly
- [ ] OTP expiry enforced (5 minutes)

## Step 4: API Endpoint Security
For each route file in `backend/routes/`:
- [ ] Every non-public endpoint uses `Depends(get_current_user)`
- [ ] Admin endpoints use `Depends(get_admin_user)`
- [ ] Rate limiting applied to auth endpoints
- [ ] Input validation on all parameters (Pydantic models)
- [ ] No debug endpoints exposed

## Step 5: Database Security
- [ ] Supabase RLS policies active (check `backend/supabase_rls.sql`)
- [ ] Users can only read/write their own records
- [ ] No public tables without row-level security
- [ ] Parameterized queries used (no string concatenation for SQL)

## Step 6: Dependency Vulnerability Scan
// turbo
```bash
cd backend && pip audit 2>&1 | tail -20
```
// turbo
```bash
cd rider-app && npm audit --production 2>&1 | tail -20
```
// turbo
```bash
cd admin-dashboard && npm audit --production 2>&1 | tail -20
```

## Step 7: Logging Security
- [ ] No secrets in log output (search for `logger.info` and `logger.error` with token/key info)
- [ ] No PII (Personally Identifiable Information) in logs
- [ ] Production log level is INFO (not DEBUG)
- [ ] Log files not publicly accessible

## Step 8: Payment Security (Stripe)
- [ ] Stripe secret key only on server side
- [ ] Payment amounts validated server-side
- [ ] Webhook signatures verified in `backend/routes/webhooks.py`
- [ ] No card numbers stored anywhere

## Step 9: Report
Generate security report with:
```
### Security Audit Report
- **Date**: [date]
- **Critical Issues**: [list]
- **High Issues**: [list]
- **Medium Issues**: [list]
- **Low Issues**: [list]
- **Dependencies with Vulnerabilities**: [list]
- **Overall Security Score**: [score]/100
```
