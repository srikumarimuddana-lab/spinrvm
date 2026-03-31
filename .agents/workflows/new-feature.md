---
description: End-to-end workflow for developing a new feature in the Spinr platform
---

# New Feature Development Workflow

Follow every step. Do NOT skip testing or documentation.

## Step 1: Understand Requirements
1. Clarify the feature scope with the user
2. Identify which components are affected:
   - Backend API (`backend/`)
   - Rider App (`rider-app/`)
   - Driver App (`driver-app/`)
   - Admin Dashboard (`admin-dashboard/`)
   - Database (Supabase schema)
3. Check `.agents/docs/architecture.md` for how the feature fits into the system

## Step 2: Plan Implementation
1. List all files that need to change
2. Identify new files to create
3. Check for existing patterns in `.agents/roles/backend-developer.md` or `.agents/roles/frontend-developer.md`
4. Document the plan before writing code

## Step 3: Database Changes (if needed)
1. Write migration SQL in `backend/migrations/`
2. Update `backend/supabase_schema.sql` with the new schema
3. Add RLS policies in `backend/supabase_rls.sql`
4. Update `.agents/docs/database-schema.md`

## Step 4: Backend Implementation
Follow `.agents/roles/backend-developer.md`:
1. Create/update route in `backend/routes/`
2. Add Pydantic models in `backend/schemas.py`
3. Add validation in `backend/validators.py` if needed
4. Use proper auth dependencies (`get_current_user` or `get_admin_user`)
5. Add error handling with proper status codes
6. Add logging with `loguru.logger`

## Step 5: Frontend Implementation
Follow `.agents/roles/frontend-developer.md`:
1. Create/update screens in `app/` directory
2. Add/update Zustand store in `store/`
3. Create reusable components if needed
4. Add TypeScript types for all props and state
5. Handle loading, error, and empty states
6. Use `StyleSheet.create()` for all styles

## Step 6: Write Tests
Follow `.agents/roles/qa-engineer.md`:
1. **Backend tests** in `backend/tests/`:
   - Test success cases
   - Test error cases (400, 401, 404)
   - Test edge cases (empty input, invalid IDs)
2. **Frontend tests** (if applicable):
   - Test Zustand store mutations
   - Test critical user flows

// turbo
3. Run tests: `cd backend && python -m pytest -v`

## Step 7: Security Review
Follow `.agents/roles/security-engineer.md`:
- [ ] No secrets hardcoded
- [ ] Auth properly applied
- [ ] Input validated
- [ ] No sensitive data in logs

## Step 8: Update Documentation
Follow `.agents/roles/documentation-lead.md`:
1. Update `.agents/docs/api-reference.md` if API changed
2. Update `.agents/docs/database-schema.md` if schema changed
3. Update `.agents/docs/architecture.md` if structure changed
4. Add docstrings to all new functions

## Step 9: Final Checklist
- [ ] All tests pass
- [ ] No TypeScript/lint errors
- [ ] Security review passed
- [ ] Documentation updated
- [ ] No debug logs or `print()` statements
- [ ] Code follows standards in `.agents/standards/coding-standards.md`
