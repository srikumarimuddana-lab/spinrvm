# Architecture Decision Records

ADRs capture the context, decision, and consequences of significant technical choices made in the Spinr platform. They are immutable once accepted — if a decision is reversed, a new ADR supersedes the old one rather than editing it.

**Format:** Each ADR follows the [MADR template](https://adr.github.io/madr/) — Status · Context · Decision · Consequences.

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-001](001-supabase-postgres.md) | Supabase as the managed Postgres provider | Accepted |
| [ADR-002](002-expo-react-native.md) | Expo SDK for both mobile apps | Accepted |
| [ADR-003](003-fastapi-backend.md) | FastAPI as the backend framework | Accepted |
| [ADR-004](004-redis-in-process-fallback.md) | Transparent in-process Redis fallback for dev/test | Accepted |
| [ADR-005](005-jwt-firebase-dual-auth.md) | Dual auth: Firebase ID token + short-lived HS256 JWT | Accepted |
| [ADR-006](006-railway-deployment.md) | Railway as the primary hosting platform | Accepted (amended by ADR-007) |
| [ADR-007](007-fly-primary-railway-standby.md) | Fly.io primary with Railway as warm standby (DNS cutover) | Accepted |
| [ADR-008](008-report-branding-fixed-vs-branded.md) | Two report modes — Spinr-branded vs. fixed regulator format | Accepted |
| [ADR-009](009-data-transfer-background-export-and-unredacted-scope.md) | Data Transfer export runs as a background job, and is deliberately unredacted | Accepted |
| [ADR-010](010-metrics-aggregation-and-alerting.md) | Cross-replica metrics aggregation and production alerting | Accepted |
