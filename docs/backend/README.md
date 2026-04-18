# Backend Documentation

Comprehensive, cross-linked reference for the Spinr FastAPI backend.

**Start here:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) — stack, topology, request lifecycle, domain map.

## Domain docs

- [`AUTH_AND_USERS.md`](./AUTH_AND_USERS.md) — OTP, JWT, refresh rotation, admin login, user + staff management.
- [`RIDES_AND_DISPATCH.md`](./RIDES_AND_DISPATCH.md) — ride state machine, dispatch, fares, surge, scheduled rides, WebSocket events.
- [`WALLET_AND_PAYMENTS.md`](./WALLET_AND_PAYMENTS.md) — rider wallet, Stripe, loyalty, quests, promotions, disputes, notifications.
- [`INFRASTRUCTURE.md`](./INFRASTRUCTURE.md) — startup, middleware, config, DB client, schemas, validators, Redis, rate limiter, error handling, migrations.
- [`ADMIN_AND_OPS.md`](./ADMIN_AND_OPS.md) — admin console endpoints, monitoring, maintenance, runbooks.

## Lookup

- [`REFERENCE.md`](./REFERENCE.md) — function & class index with file:line pointers.

## Subsystems with their own docs

- Corporate B2B — [`../CORPORATE_B2B.md`](../CORPORATE_B2B.md) (master wallet, KYB, off-session auto-topup, spend policies).

---

**Last full pass:** 2026-04-17.
