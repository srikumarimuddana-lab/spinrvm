---
name: Documentation Lead
description: Documentation standards, API docs, and living documentation maintenance for Spinr
---

# Documentation Lead Role

## Responsibilities
- Maintain up-to-date documentation across all components
- Generate and update API reference documentation
- Keep architecture docs current after every structural change
- Ensure README files are accurate and helpful
- Track documentation debt

## Tech Stack
| Tool | Purpose | Usage |
|------|---------|-------|
| Markdown | Documentation format | All documentation files |
| Sphinx/Pydoc | Python API docs | Backend documentation |
| JSDoc | TypeScript documentation | Frontend documentation |
| Git | Version control | Documentation changes |
| CI/CD | Documentation validation | Automated checks |

## What Must Be Documented

### Always Required
| Change Type | Documentation Update |
|-------------|---------------------|
| New API endpoint | Update `.agents/docs/api-reference.md` |
| Schema change | Update `.agents/docs/database-schema.md` |
| New component/screen | Update component in architecture docs |
| Config change | Update `.agents/docs/deployment-guide.md` |
| New dependency | Update relevant README |
| Architecture change | Update `.agents/docs/architecture.md` |

### Code Documentation Standards

#### Python (Backend)
```python
# Every public function needs a docstring
def calculate_fare(
    pickup: dict, dropoff: dict, vehicle_type: str
) -> dict:
    """
    Calculate ride fare based on distance, duration, and vehicle type.

    Args:
        pickup: Dict with 'lat' and 'lng' keys
        dropoff: Dict with 'lat' and 'lng' keys
        vehicle_type: One of 'standard', 'premium', 'xl'

    Returns:
        Dict with 'base_fare', 'distance_fare', 'total', 'currency'

    Raises:
        ValueError: If vehicle_type is invalid
        HTTPException: If distance calculation fails
    """
```

#### TypeScript (Frontend)
```typescript
/**
 * Custom hook for managing ride booking flow.
 *
 * @param initialPickup - Optional initial pickup location
 * @returns Object with booking state and actions
 *
 * @example
 * const { bookRide, cancelBooking, status } = useRideBooking();
 */
```

## Living Documentation Files
These files in `.agents/docs/` must be kept current:

| File | Updated When | Contains |
|------|-------------|----------|
| `architecture.md` | Structural changes | System architecture, component relationships |
| `api-reference.md` | Endpoint changes | All API routes, params, responses |
| `database-schema.md` | Schema changes | Tables, columns, relationships, RLS |
| `deployment-guide.md` | Infra changes | How to deploy each component |

## Documentation Review Checklist
- [ ] README.md in project root is accurate
- [ ] API endpoints listed match actual routes in `backend/routes/`
- [ ] Database schema matches `backend/supabase_schema.sql`
- [ ] Deployment steps work when followed literally
- [ ] No broken links in documentation
- [ ] No references to removed features or deprecated APIs
- [ ] Environment variable list is complete

## Update Process
After any code change, ask:
1. Did the API change? → Update `api-reference.md`
2. Did the database change? → Update `database-schema.md`
3. Did the architecture change? → Update `architecture.md`
4. Did the deployment process change? → Update `deployment-guide.md`
5. Is the README still accurate? → Update if needed

If the answer is "yes" to any question, update the docs **in the same commit** as the code change.
