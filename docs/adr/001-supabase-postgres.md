# ADR-001: Supabase as the managed Postgres provider

**Date:** 2026-02-14
**Status:** Accepted

---

## Context

Spinr needed a production-grade relational database that could be operational quickly without dedicated DBA resources. The key requirements were:

- Postgres as the query engine (familiarity, JSON support, geospatial via PostGIS)
- Row Level Security (RLS) for multi-tenant data isolation without application-layer guards
- A hosted Auth layer for the initial prototype, with the option to replace it later
- Reasonable free tier for early-stage development
- Canadian data residency option (for PIPEDA compliance)

Alternatives considered:

| Option | Rejected because |
|--------|-----------------|
| Self-hosted Postgres on Railway | Requires backup management, failover setup, and a DBA on call |
| PlanetScale | MySQL-only; no PostGIS; Postgres compatibility is surface-level |
| Neon | Excellent Postgres but no built-in Auth or Storage |
| Firebase Firestore | NoSQL — schema-less design doesn't fit the ride/fare domain model |
| AWS RDS | High operational overhead; egress pricing; no built-in Auth |

---

## Decision

Use **Supabase** (hosted Postgres) as the primary data store, accessed exclusively via the service-role client (`supabase-py`) from the FastAPI backend.

Key implementation details:
- All DB access goes through `backend/db_supabase.py` helper functions, never raw SQL from route handlers.
- Supabase Auth is **not** used for rider/driver authentication — we issue our own short-lived JWTs. Supabase RLS policies are enforced via the service-role client, which bypasses RLS — so Spinr's application-layer access controls are the primary security boundary.
- Stripe keys, Twilio credentials, and Google Maps API keys are stored in the `app_settings` Supabase table (managed via the admin dashboard), not in `.env` files. This allows rotation without redeployment.
- The `supabase-py` client is synchronous; all calls are wrapped in `asyncio.run_in_executor` via `run_sync()` in `db_supabase.py` to avoid blocking the event loop.

---

## Consequences

**Positive:**
- Zero DBA overhead — automated backups, point-in-time recovery, connection pooling (PgBouncer) included.
- RLS is available for future hardening of the direct Supabase client if needed.
- Supabase Storage handles driver document uploads (`driver-documents` bucket).
- The admin dashboard can query Supabase directly in read-only mode for lightweight analytics without going through the FastAPI backend.

**Negative / trade-offs:**
- The synchronous `supabase-py` client means every DB call occupies a thread-pool thread. Under high load this can saturate the default thread pool. Mitigation: the backend runs with `--workers 4` and the pool has been observed as sufficient at Saskatchewan-scale load.
- HTTP/2 GOAWAY errors from Supabase's edge network require the retry logic in `run_sync()`. This is a known Supabase behaviour under bursty load.
- Vendor lock-in: migrating away from Supabase would require rewriting all ~66 `db_supabase.py` helpers and migrating the `app_settings` pattern. Estimated effort: 2–3 weeks.
