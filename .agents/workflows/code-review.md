---
description: Comprehensive code review following industry standards for the Spinr platform
---

# Code Review Workflow

Follow every step in order. Reference the role files in `.agents/roles/` for domain-specific standards.

## Step 1: Identify Changed Files
1. Check which files changed: `git diff --name-only HEAD~1` (or the relevant commit range)
2. Categorize changes:
   - Backend (`backend/`) → Apply rules from `.agents/roles/backend-developer.md`
   - Frontend (`rider-app/`, `driver-app/`, `admin-dashboard/`) → Apply rules from `.agents/roles/frontend-developer.md`
   - Config/Deploy changes → Apply rules from `.agents/roles/devops-engineer.md`
   - Security-sensitive changes → Apply rules from `.agents/roles/security-engineer.md`
   - Test changes → Apply rules from `.agents/roles/qa-engineer.md`

## Step 2: Security Review
Apply ALL checks from `.agents/roles/security-engineer.md`:

### Critical Security Checks
- [ ] No secrets or API keys in code (grep for `sk_live`, `sk_test`, `password=`, hardcoded URLs)
- [ ] Auth dependencies are used on all protected endpoints
- [ ] Input validation exists (Pydantic models for backend, TypeScript types for frontend)
- [ ] No sensitive data in logs (check `logger.info`, `logger.error`, `console.log`)
- [ ] Environment variables used for all config values
- [ ] `.env` files are in `.gitignore`

### Authentication & Authorization
- [ ] JWT secret is strong and from environment (not hardcoded)
- [ ] Token expiry is reasonable (currently 30 days — consider shorter)
- [ ] Firebase tokens are verified server-side
- [ ] Admin role cannot be self-assigned
- [ ] Session invalidation works on logout
- [ ] OTP expiry is enforced (currently 5 minutes)

### Payment Security
- [ ] Stripe SDK used with secure keys (not publishable key on server)
- [ ] Payment intents created server-side only
- [ ] Webhook signature verification implemented
- [ ] No payment amounts set from client input without server validation

## Step 3: Code Quality Review
### Structure & Organization
- [ ] Functions are < 50 lines (split if longer)
- [ ] Files are < 300 lines (split if longer)
- [ ] No commented-out code blocks
- [ ] No `TODO` or `FIXME` without a linked issue
- [ ] Naming is clear and consistent (snake_case for Python, camelCase for TS)
- [ ] No duplicate code — extract shared logic into utilities

### Type Safety
- [ ] Type annotations on all functions (Python type hints, TypeScript interfaces)
- [ ] All props have TypeScript interfaces defined (frontend)
- [ ] Database queries use proper type definitions

### Code Style
- [ ] Follows project-specific coding standards from `.agents/standards/`
- [ ] Consistent formatting (use auto-formatters)
- [ ] Proper error handling patterns

## Step 4: Error Handling Review
### Backend
- [ ] All endpoints have try/catch blocks
- [ ] HTTP exceptions use correct status codes (400, 401, 403, 404, 500)
- [ ] Errors are logged with context (not just `except: pass`)
- [ ] Database queries handle `None` results
- [ ] Rate limiting implemented for public endpoints

### Frontend
- [ ] API calls have loading/error/success states
- [ ] Network errors show user-friendly messages
- [ ] Crash-prone operations are wrapped in try/catch
- [ ] State management handles error states properly

## Step 5: Performance Review
### Backend Performance
- [ ] No N+1 database queries (batch where possible)
- [ ] Large lists use pagination
- [ ] Database queries are optimized (use indexes)
- [ ] Caching implemented for expensive operations
- [ ] API responses are not excessively large

### Frontend Performance
- [ ] Images are optimized and lazy-loaded
- [ ] No unnecessary re-renders in React components
- [ ] State updates are efficient
- [ ] Large lists use virtualization

### Mobile App Performance
- [ ] Efficient state management (Zustand stores)
- [ ] Proper image handling and caching
- [ ] Background task optimization

## Step 6: Test Coverage Check
Apply rules from `.agents/roles/qa-engineer.md`:

### Backend Test Requirements
- [ ] Unit tests exist for new/changed functions
- [ ] Auth endpoints have tests
- [ ] Payment-related code has tests
- [ ] Edge cases are covered (null inputs, empty lists, invalid IDs)
- [ ] Database operations have tests
- [ ] Rate limiting is tested

### Frontend Test Requirements
- [ ] Zustand stores have tests
- [ ] Critical user flows are tested
- [ ] Component rendering and interactions tested
- [ ] Error states are tested

### Test Quality
- [ ] Tests follow naming conventions
- [ ] Tests are isolated and don't depend on external state
- [ ] Mock data is realistic and comprehensive

## Step 7: Documentation Check
Apply rules from `.agents/roles/documentation-lead.md`:

### Code Documentation
- [ ] Public functions have docstrings/JSDoc
- [ ] Complex algorithms have inline documentation
- [ ] API endpoints have proper documentation

### Architecture Documentation
- [ ] API changes reflected in `.agents/docs/api-reference.md`
- [ ] Schema changes reflected in `.agents/docs/database-schema.md`
- [ ] Architecture changes reflected in `.agents/docs/architecture.md`
- [ ] Deployment changes reflected in `.agents/docs/deployment-guide.md`

### Project Documentation
- [ ] README is still accurate
- [ ] Environment variable documentation is up to date
- [ ] API documentation examples work

## Step 8: Integration & Compatibility Review
### API Compatibility
- [ ] New endpoints don't break existing API contracts
- [ ] Database schema changes are backward compatible
- [ ] Frontend changes don't break existing API integrations

### Cross-Platform Compatibility
- [ ] Mobile app changes work on both iOS and Android
- [ ] Admin dashboard changes work across browsers
- [ ] Responsive design is maintained

### Dependency Management
- [ ] New dependencies are necessary and secure
- [ ] Version conflicts are resolved
- [ ] License compatibility is verified

## Step 9: Report Findings
Summarize findings in this format:

```markdown
### Code Review Summary
**Date**: [YYYY-MM-DD]  
**Reviewer**: [Name]  
**Files Reviewed**: [count]  
**Lines Changed**: [count]

#### Critical Issues (Must Fix)
- [List critical security, functionality, or data integrity issues]
- [Each issue should have clear description and suggested fix]

#### High Priority Issues
- [List high priority bugs, performance issues, or missing features]

#### Medium Priority Issues
- [List code quality issues, minor bugs, or improvements]

#### Low Priority Issues
- [List style issues, minor improvements, or nice-to-haves]

#### Missing Tests
- [List untested critical paths or new functionality]

#### Documentation Gaps
- [List missing or outdated documentation]

#### Security Assessment
- **Security Score**: [1-100]
- **Vulnerabilities Found**: [count]
- **Risk Level**: [Low/Medium/High/Critical]

#### Performance Assessment
- **Performance Impact**: [None/Minor/Major]
- **Bottlenecks Identified**: [list]
- **Optimization Opportunities**: [list]

#### Overall Assessment
- **Status**: [Pass/Fail/Conditional Pass]
- **Ready for Production**: [Yes/No]
- **Recommended Actions**: [list of next steps]
```

## Quick Reference Commands

### Security Scanning
```bash
# Check for secrets in code
grep -r "sk_live\|sk_test\|password\s*=\|secret\s*=" backend/ --include="*.py"
grep -r "SUPABASE_KEY\|STRIPE\|API_KEY" rider-app/app/ --include="*.tsx" --include="*.ts"

# Python dependency vulnerabilities
pip audit

# NPM dependency vulnerabilities
cd rider-app && npm audit
cd admin-dashboard && npm audit
```

### Code Quality Checks
```bash
# Python linting and formatting
cd backend && ruff check .
cd backend && black .

# TypeScript linting
cd rider-app && npm run lint
cd admin-dashboard && npm run lint

# Test coverage
cd backend && python -m pytest --cov=backend
```

### Documentation Validation
```bash
# Check if API docs match actual endpoints
python scripts/validate_api_docs.py

# Check if database schema matches docs
python scripts/validate_schema.py
```

## Reviewer Guidelines

### Before Starting Review
1. Understand the feature/change being implemented
2. Check if this is a security-sensitive change
3. Identify which role standards apply
4. Set up the development environment if needed

### During Review
1. Follow the checklist systematically
2. Test the changes if possible
3. Look for edge cases and error conditions
4. Consider the impact on existing functionality
5. Verify security and performance implications

### After Review
1. Document all findings clearly
2. Prioritize issues by impact and severity
3. Provide actionable feedback
4. Suggest improvements, not just problems
5. Update relevant documentation

### When to Block Merge
- Critical security vulnerabilities
- Missing authentication/authorization
- Data integrity issues
- Major performance regressions
- Missing tests for critical functionality
- Breaking changes without proper migration plan

### When to Approve with Comments
- Minor code quality issues
- Missing documentation
- Performance optimizations needed
- Test coverage below target but not critical
- Style inconsistencies
