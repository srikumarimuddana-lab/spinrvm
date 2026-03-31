---
name: API Standards
description: API design rules for the Spinr backend
---

# API Standards

## URL Structure
```
/api/v1/{resource}          # Collection
/api/v1/{resource}/{id}     # Single item
/api/v1/{resource}/{id}/{sub-resource}  # Nested resource
```

### Examples (Current Spinr Routes)
```
GET    /api/v1/rides              # List rides
POST   /api/v1/rides              # Create ride
GET    /api/v1/rides/{ride_id}    # Get ride details
PATCH  /api/v1/rides/{ride_id}    # Update ride
DELETE /api/v1/rides/{ride_id}    # Cancel ride

GET    /api/v1/drivers            # List drivers
POST   /api/v1/drivers/register   # Register driver
PATCH  /api/v1/drivers/status     # Update driver status

POST   /api/auth/login            # Login
POST   /api/auth/verify-otp       # Verify OTP
```

## HTTP Methods
| Method | Use For | Success Code |
|--------|---------|-------------|
| GET | Retrieve resource(s) | 200 |
| POST | Create resource | 201 |
| PATCH | Partial update | 200 |
| PUT | Full replacement | 200 |
| DELETE | Remove resource | 204 |

## Request Format
- Content-Type: `application/json`
- Auth: `Authorization: Bearer <token>`
- Pagination: `?page=1&limit=20`

## Response Format

### Success
```json
{
  "id": "abc-123",
  "status": "active",
  "created_at": "2026-03-26T10:00:00Z"
}
```

### Collection (with pagination)
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "limit": 20
}
```

### Error
```json
{
  "detail": "Ride not found"
}
```

## Status Codes
| Code | Meaning | When to Use |
|------|---------|-------------|
| 200 | OK | Successful GET, PATCH, PUT |
| 201 | Created | Successful POST |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Invalid input, validation error |
| 401 | Unauthorized | Missing or invalid auth token |
| 403 | Forbidden | Valid auth but insufficient permissions |
| 404 | Not Found | Resource doesn't exist |
| 422 | Unprocessable | Valid input but can't process (business logic error) |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Error | Unexpected server error |

## Auth Requirements
| Endpoint Type | Auth Required | Dependency |
|--------------|--------------|------------|
| Public (health, status) | No | None |
| User actions | Yes | `Depends(get_current_user)` |
| Admin actions | Yes (admin role) | `Depends(get_admin_user)` |
| Webhooks | Signature verification | Custom verification |

## Rate Limiting
- Auth endpoints: 5 requests/minute
- Public endpoints: 30 requests/minute
- Authenticated endpoints: 60 requests/minute
- Admin endpoints: No limit (trusted users)

## Validation Rules
- All request bodies validated via Pydantic models
- Path parameters validated for format (UUID format for IDs)
- Query parameters have defaults and type validation
- File uploads limited by size and type
