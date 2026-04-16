---
description: Structured workflow for investigating and fixing bugs in the Spinr platform
---

# Bug Fix Workflow

## Step 1: Reproduce the Bug
1. Understand the reported behavior vs expected behavior
2. Identify the affected component (backend, rider-app, driver-app, admin)
3. Try to reproduce locally
4. Check logs for errors:
   - Backend: `backend/logs/app.log` or `railway logs --service backend`
   - Sentry: Check for error reports
   - Frontend: Check console output

## Step 2: Root Cause Analysis
1. Trace the code path from the error
2. Check recent changes: `git log --oneline -20`
3. Identify the exact line(s) causing the issue
4. Check if the bug exists in related code (similar endpoints, similar screens)
5. Document the root cause

## Step 3: Implement the Fix
Follow the relevant role's coding rules:
- Backend fix → `.agents/roles/backend-developer.md`
- Frontend fix → `.agents/roles/frontend-developer.md`

Rules for fixes:
- Fix the root cause, not just the symptom
- Don't introduce new bugs — check surrounding code
- Keep the fix minimal and focused

## Step 4: Write Regression Test
**REQUIRED — every bug fix must have a test that would have caught the bug.**

```python
# Backend test example
def test_bug_fix_ride_status_not_updated():
    """Regression test: ride status was not updating after driver acceptance."""
    # Setup
    ride = create_test_ride()
    # This was the failing scenario
    result = accept_ride(ride.id, driver_id)
    # Verify the fix
    assert result.status == "accepted"
```

// turbo
Run tests: `cd backend && python -m pytest -v`

## Step 5: Verify Fix
1. Reproduce the original bug scenario — confirm it no longer occurs
2. Test related functionality — confirm nothing else broke
3. Check edge cases around the fix

## Step 6: Update Documentation
- [ ] If the bug revealed a pattern issue, update `.agents/standards/` files
- [ ] If the fix changed API behavior, update `.agents/docs/api-reference.md`
- [ ] If it was a security bug, update `.agents/roles/security-engineer.md` with the pattern to check

## Step 7: Final Checklist
- [ ] Root cause identified and documented
- [ ] Fix addresses root cause (not just symptom)
- [ ] Regression test added
- [ ] All existing tests still pass
- [ ] No new security issues introduced
- [ ] Related code checked for similar bugs
