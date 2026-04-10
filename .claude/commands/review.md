# /review — Pre-Commit Code Review for spinr

Review all staged and unstaged changes. Domain-aware for ride share.

## Security (BLOCK if found)
- No hardcoded secrets, Supabase keys, Stripe live keys
- No .env files staged
- No PII in logs (GPS, phone numbers, names, emails)
- No string-concatenated SQL (must use parameterised queries or ORM)
- All API inputs validated (Pydantic for Python, Zod for TypeScript)
- Supabase service role key not in frontend or mobile code

## Code Quality
- Python: type hints present, no bare except clauses
- TypeScript: no any types, errors handled on all async functions
- No dead code or unused imports
- New functions have docstrings (Python) or JSDoc (TypeScript)

## spinr Domain Checks

### Money / Payments
- No float arithmetic for fares or payouts (use Decimal or integer cents)
- Stripe test mode used in non-production paths
- Platform commission and driver split match CLAUDE.md values

### Trip State Machine
- No skipped states
- CANCELLED only before TRIP_STARTED
- State changes emit events

### Location / Privacy
- No raw GPS in logs
- Driver location not over-shared to riders
- Supabase data in Canada region

### Safety
- SOS does not auto-dial 911
- SOS notifies emergency contact AND safety team
- Safety features not behind feature flags

### Auth
- JWT has expiry
- Supabase RLS policies not bypassed
- FLAG: auth changes need human security review

### Insurance Periods
- Period 0/1/2/3 correctly classified
- Period changes logged for regulatory audit

## Output Format
SPINR PRE-COMMIT REVIEW
=======================
Files: X changed | +Y -Z lines
Domains: [list]

SECURITY:         PASSED / BLOCKED — details
MONEY:            N/A / PASSED / ISSUES
STATE MACHINE:    N/A / PASSED / ISSUES
LOCATION/PRIVACY: N/A / PASSED / REVIEW
SAFETY:           N/A / PASSED / ISSUES
AUTH:             N/A / PASSED / HUMAN REVIEW REQUIRED
CODE QUALITY:     GOOD / SUGGESTIONS
TESTS:            COVERED / MISSING — what needs tests

VERDICT: Safe to commit / Fix first / Human review required
