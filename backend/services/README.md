# Backend Service Layer

This directory holds the **service layer** for the Spinr backend. Services are
where the business logic lives — separate from the HTTP-handling routes and
the database access layer.

## Why a service layer?

Right now most route files (`routes/drivers.py`, `routes/rides.py`,
`routes/fares.py`) mix three concerns inside a single function:

1. HTTP parsing / response shaping
2. Database access
3. Business rules (matching, pricing, state transitions)

That makes the rules **untestable without FastAPI and Supabase**, and it makes
the routes balloon (`drivers.py` is currently 2,230 LOC). The service layer
fixes both problems.

## The contract

```
┌───────────────┐    ┌──────────────────┐    ┌─────────────┐
│   Route       │ →  │   Service        │ →  │ Repository  │
│ (HTTP I/O)    │    │ (business rules) │    │  (db.py)    │
└───────────────┘    └──────────────────┘    └─────────────┘
```

A route should look like this:

```python
@api_router.get("/fares")
async def get_fares_for_location(lat: float = Query(..., ge=-90, le=90),
                                 lng: float = Query(..., ge=-180, le=180)):
    return await FareService(db).fares_for_location(lat, lng)
```

Everything above the `await` is HTTP. Everything below is business logic.

## Conventions

1. **One service per domain concept.** `FareService`, `RideService`,
   `DispatchService`, `PaymentService`, `DriverService`. Don't create
   `MiscService`.

2. **Constructor takes its dependencies.** Services accept `db` (and any
   external clients like `stripe`, `twilio`) in `__init__`. This makes
   them testable with mocks.

3. **Public methods are async and return plain dicts/lists** (or domain
   objects). Don't return `JSONResponse` or `HTTPException`. Routes do
   that.

4. **Raise domain exceptions, not HTTP exceptions.** Use the existing
   `SpinrException` hierarchy in `utils/error_handling.py`. The global
   handler maps them to HTTP responses.

5. **No FastAPI imports inside services.** If you find yourself importing
   `Request`, `Depends`, or `HTTPException`, the logic belongs in the
   route, not the service.

6. **Pure helpers stay pure.** Functions that don't touch I/O (e.g.
   `_fd`, `build_default_fares` in `FareService`) live as static
   methods or module-level functions. They're trivial to test.

7. **Tests live in `backend/tests/services/`** and run without a real
   database (use `unittest.mock` or fakes).

## Naming

| Suffix | Meaning | Example |
|--------|---------|---------|
| `Service` | Stateful, holds dependencies | `FareService(db)` |
| `*_helpers.py` | Pure functions, no class | `geo_utils.calculate_distance` |
| `*_repository.py` | Data access wrappers (future) | `RideRepository.find_active(rider_id)` |

## Migration plan

This layer is being introduced incrementally. The order matters:

1. ✅ **`FareService`** — pricing math (this is the reference implementation)
2. ⏳ **`DispatchService`** — `match_driver_to_ride` from `routes/rides.py:78-170`
3. ⏳ **`RideService`** — state transitions (create, accept, arrive, start, complete, cancel)
4. ⏳ **`PaymentService`** — Stripe integration, payment confirmation, refunds
5. ⏳ **`DriverService`** — driver availability, earnings, location updates
6. ⏳ **`NotificationService`** — push notifications, SMS, emergency alerts

For each migration:

1. Create the service with its tests
2. Refactor the route(s) to use the service
3. Verify route behavior is unchanged (integration test)
4. Move on to the next service

Don't try to extract everything at once. Each service should land in its own
PR with passing tests.

## What does NOT belong here

- HTTP handling (request parsing, responses, status codes)
- Authentication (use `Depends(get_current_user)` in routes)
- Database schema definitions (those live in migrations)
- Pydantic request/response models (those live in `schemas.py`)
- Cross-cutting utilities (logging, error formatting — those are in `utils/`)
