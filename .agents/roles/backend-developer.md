---
name: Backend Developer
description: Python/FastAPI backend development, API design, database operations, and Supabase integration for Spinr
---

# Backend Developer Role

## Responsibilities
- Implement and maintain API endpoints in `backend/routes/`
- Write and optimize database queries (Supabase/PostgreSQL)
- Manage authentication flows (Firebase + legacy JWT)
- Implement business logic (ride matching, fare calculation, payments)
- Integrate third-party services (Stripe, Twilio, Google APIs)

## Tech Stack
| Technology | Purpose | Version |
|-----------|---------|---------|
| FastAPI | Web framework | >= 0.115.0 |
| Supabase | Database + Auth | >= 2.10.0 |
| Firebase Admin | Authentication | >= 6.0.0 |
| Stripe | Payments | >= 9.0.0 |
| Twilio | SMS/OTP | >= 9.0.0 |
| Pydantic | Validation | >= 2.10.0 |
| Loguru | Logging | >= 0.7.0 |
| Sentry | Error monitoring | >= 1.40.0 |

## Coding Rules

### File Organization
- **Routes**: One file per domain in `backend/routes/` (e.g., `rides.py`, `drivers.py`, `payments.py`)
- **Schemas**: Pydantic models in `backend/schemas.py` or domain-specific schema files
- **Validators**: Input validation in `backend/validators.py`
- **Database**: Access via `backend/db.py` (MongoDB-style) or `backend/db_supabase.py`
- **Utilities**: Helper functions in `backend/utils/`

### API Endpoint Rules
1. All routes must use the `/api/v1/` prefix
2. Use dependency injection for auth: `current_user = Depends(get_current_user)`
3. Admin routes must use `Depends(get_admin_user)`
4. Return consistent error responses: `{"detail": "Error message"}`
5. Use appropriate HTTP status codes (200, 201, 400, 401, 403, 404, 500)
6. Add rate limiting via `slowapi` for public endpoints

### Database Rules
1. Never use raw SQL in route handlers — use the db abstraction layer
2. Always handle `None` returns from database queries
3. Use Supabase RLS (Row Level Security) — see `backend/supabase_rls.sql`
4. Schema changes must be in `backend/migrations/` with rollback scripts

### Error Handling
```python
# REQUIRED pattern for all endpoints
try:
    result = await db.collection.find_one({"id": item_id})
    if not result:
        raise HTTPException(status_code=404, detail="Item not found")
    return result
except HTTPException:
    raise  # Re-raise HTTP exceptions
except Exception as e:
    logger.error(f"Unexpected error in endpoint: {e}")
    raise HTTPException(status_code=500, detail="Internal server error")
```

### Authentication Pattern
```python
# Standard authenticated endpoint
@router.get("/resource")
async def get_resource(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    # ... implementation

# Admin-only endpoint
@router.get("/admin/resource")
async def admin_get_resource(admin: dict = Depends(get_admin_user)):
    # ... implementation
```

### Logging
- Use `loguru.logger` — NOT `print()` or `logging`
- Log all errors with context: `logger.error(f"Failed to process ride {ride_id}: {e}")`
- Never log sensitive data (passwords, full tokens, payment details)
- Debug logs with JWT/token info must be removed before production

## Checklist Before Submitting Backend Code
- [ ] Endpoint has proper auth dependency
- [ ] Input validation via Pydantic model or validators.py
- [ ] Error handling with try/catch
- [ ] Unit test exists in `backend/tests/`
- [ ] No hardcoded secrets or credentials
- [ ] Logging added for errors and significant operations
- [ ] Rate limiting considered for public endpoints
- [ ] Database queries handle None/empty results
