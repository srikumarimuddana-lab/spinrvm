---
description: Checks to run before every commit to the Spinr repository
---

# Pre-Commit Checks Workflow

Run ALL of these checks before any commit. Every item is mandatory.

## 1. No Secrets Exposed
// turbo
```bash
git diff --cached --name-only | findstr ".env"
```
- [ ] No `.env` files staged for commit
- [ ] No API keys, tokens, or passwords in code
- [ ] Check: `grep -r "sk_live\|sk_test\|password\s*=" --include="*.py" --include="*.ts" --include="*.tsx" backend/ rider-app/app/ driver-app/app/`

## 2. Tests Pass
// turbo
```bash
cd backend && python -m pytest -v --tb=short
```
- [ ] All backend tests pass
- [ ] No test files removed without replacement

## 3. Type Safety
// turbo
```bash
cd rider-app && npx tsc --noEmit 2>&1 | head -20
```
- [ ] No new TypeScript errors introduced
- [ ] Python type hints on all new functions

## 4. Code Quality
- [ ] No `print()` statements in backend (use `logger`)
- [ ] No `console.log()` in production frontend code
- [ ] No commented-out code blocks
- [ ] No `TODO` without a plan to address

## 5. Lint Check
// turbo
```bash
cd backend && python -m flake8 --max-line-length=120 --count server.py routes/ 2>&1 | tail -5
```
- [ ] No lint errors

## 6. Documentation Updated
Check if any of these changed:
- API endpoint added/modified → `.agents/docs/api-reference.md` updated?
- Database schema changed → `.agents/docs/database-schema.md` updated?
- Deployment config changed → `.agents/docs/deployment-guide.md` updated?
- Architecture changed → `.agents/docs/architecture.md` updated?

## 7. Commit Message Format
Use conventional commits:
```
feat: add ride tracking WebSocket endpoint
fix: resolve OTP expiry not being checked
docs: update API reference for driver endpoints
test: add payment flow integration tests
refactor: extract fare calculation into utility
chore: update dependencies
```

## Quick Summary
If ALL checks pass → Safe to commit ✅
If ANY check fails → Fix before committing ❌
