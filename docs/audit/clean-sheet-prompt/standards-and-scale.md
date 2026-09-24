# Clean-Sheet Audit — Standards, Techniques, Tagging & Scale Readiness

Companion to `docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md`. It answers four
questions:
1. What techniques and standards are already in place?
2. Which have we never formally researched?
3. How do we tag everything so it explains itself?
4. How do we know each SaaS product will hold at 10× load **before** we get there?

**Evidence note:** the §1 inventory comes from grep counts and file reads taken on
2026-09-24. It is **INFERRED** until a lane opens each call site and confirms it.
Counts show that a technique is present, not that it's used correctly everywhere.

---

## 1. What is in place today (INFERRED — lanes must verify)

| Area | Technique / standard found | Evidence (start here) |
|---|---|---|
| Password / secret hashing | bcrypt (51 refs); one argon2 ref (check whether it is dead code) | `grep -rn bcrypt backend` |
| OTP & token hashing | Keyed HMAC-SHA256 with a pepper; SHA-256 refresh-token hashes; constant-time `hmac.compare_digest` | `backend/utils/crypto.py:11-40` |
| PII at rest | pgsodium deterministic AEAD keys for driver PII, plus a key-rotation runbook | `docs/runbooks/pii-key-rotation.md`, `services/driver_import_service.py:361` |
| JWT signing | HS256 (24 refs, shared secret); ES256 (6 refs — check where; likely Apple/Firebase) | `grep -rn "HS256\|ES256" backend` |
| Transport | HTTPS/TLS only (per the export-compliance note); no certificate pinning in the mobile apps | `rider-app/app.config.ts:77`, `driver-app/app.config.ts:82` |
| HTTP security headers | CSP, HSTS, X-Frame-Options middleware | `backend/core/middleware.py` |
| Rate limiting | SlowAPI + Redis (114 refs); OTP lockout | `backend/core/middleware.py`, `docs/runbooks/rate-limits.md` |
| Response compression | **No gzip/brotli middleware found** in `server.py`/`core/`. Only profile-image compression exists | `routes/users.py:631` |
| WS payloads | JSON; **no permessage-deflate or binary encoding found**; 64 KB cap, 10 s heartbeat | `routes/websocket.py` |
| Geo algorithms | Haversine (372), OSRM routing (241), polyline encoding (613), geohash (106), H3 hex grid (99) | `services/h3_heatmap.py`, `utils/route_distance.py`, `deploy/` (OSRM) |
| Resilience | Transactional outbox (387), idempotency keys (274+), Redis `SET NX` leader locks (47+), backoff (56), `asyncio.wait_for` timeouts (52), circuit breaker (25) | `services/outbox.py`, `docs/runbooks/transactional-outbox.md` |
| Caching | Minimal: 3 `lru_cache`, 7 `Cache-Control`, 2 ETag | grep |
| Money | Decimal-only rule plus a pre-commit float block; double-entry-style ledger service; Stripe reconciliation | `services/ledger_service.py`, `utils/stripe_reconcile.py` |
| AppSec pipeline | Semgrep (custom rules), Bandit, Gitleaks, pip-audit, yarn/npm audit, Trivy, license checks, OWASP ZAP baseline DAST, Dependabot, Schemathesis pilot | `.github/workflows/security-gates.yml`, `dast-zap-baseline.yml`, `.semgrep/` |
| Supply chain | Signed Fly image deploy, pinned Docker images, pip-compile lockfiles | `deploy-fly-signed-image.yml`, `docs/runbooks/docker-image-pinning.md` |
| Capacity monitors | Supabase capacity, billing usage, certificate/domain expiry, secret rotation, subprocessor monitors | `.github/workflows/*-monitor.yml` |
| Load testing | Locust with a target guard (can't be aimed at prod by accident) | `loadtest/` |
| Observability | Sentry tags, Prometheus-style metrics, synthetic checks; **distributed tracing deliberately deferred** | ADR-010, ADR-014, `monitoring/synthetic-checks.yaml` |
| Architecture decisions | 16 ADRs | `docs/adr/` |
| Threat models | Per surface: backend, admin, rider, driver | `docs/threat-model/` |
| Standards cited | OWASP mentioned in PRD/blueprint/audit playbook; WCAG 2.1 AA; PIPEDA; SK TNC rules | `docs/PRD.md`, `audit-framework/` |

## 2. Gaps and observations (candidate findings — verify, don't assume)

1. **No formal control mapping.** OWASP ASVS, MASVS and the API Security Top 10 are
   mentioned but have no row-by-row mapping, so nobody can say "we meet ASVS L2" or
   point to the controls we miss.
2. **HS256 shared-secret JWTs.** Every service that verifies a token can also mint one.
   Evaluate asymmetric signing (ES256/EdDSA) with key IDs (`kid`) and rotation before
   more services or a partner API arrive.
3. **pgsodium dependency.** Check Supabase's current support status for pgsodium
   (ASSUMED risk: Supabase has signalled deprecation). Plan the path to app-layer
   envelope encryption (AES-GCM with keys in a KMS) if needed.
4. **No response compression.** JSON list endpoints and admin exports go out
   uncompressed. Evaluate gzip/brotli at the edge (Cloudflare) vs the app, measuring
   payload size and CPU cost.
5. **WS payload efficiency.** Location updates are JSON over WS with no compression or
   delta encoding. At higher driver counts this becomes bandwidth and battery cost.
   Evaluate permessage-deflate, delta/quantized coordinates, and adaptive update rates.
6. **Little caching.** Fare-config, service-area, and settings reads likely hit
   Supabase on every request. Evaluate Redis read-through caches with explicit
   invalidation.
7. **No certificate pinning.** This is a deliberate trade-off (pinning breaks rotation).
   Record the decision in an ADR rather than leaving it implicit.
8. **Tracing deferred (ADR-014).** Name the trigger that ends the deferral, e.g.
   "when a p95 regression can't be localized in less than 1 hour".
9. **No tagging taxonomy** shared across code, logs, metrics, Sentry, DB, vendors, and
   cost. See §4.
10. **No per-vendor capacity budget with tripwires.** Monitors exist but aren't tied to
    a 10× plan. See §5.

---

## 3. Standards and frameworks to measure against (pick a level; don't claim certification)

| Area | Standard | Target level to evaluate |
|---|---|---|
| Web / API security | OWASP ASVS (current version), OWASP API Security Top 10 | ASVS L2 for backend + admin |
| Mobile security | OWASP MASVS + MASTG test cases | MASVS-L1 + resilience items for the driver app (GPS spoofing) |
| Program | NIST CSF 2.0 functions; CIS Controls v8 IG1 | Gap map only |
| Trust for corporate buyers | SOC 2 Trust Services Criteria (readiness, not audit) | Readiness gap list |
| Payments | PCI DSS scope via Stripe-hosted fields (SAQ A eligibility) | Confirm no card data touches our servers |
| Privacy | PIPEDA's 10 fair-information principles | Principle → control map |
| Supply chain | SLSA levels; SBOM (CycloneDX/SPDX) | SLSA L2 + an SBOM per build |
| Reliability | SRE golden signals; RED (services) / USE (resources); error budgets; DORA metrics | Per critical path |
| Observability | OpenTelemetry semantic conventions (naming, even before tracing) | Attribute names |
| Architecture docs | C4 model (context, container, component) + ADRs | C1–C3 diagrams |
| Accessibility | WCAG 2.1 AA (2.2 delta noted) | Rider/driver/admin |
| App design | Twelve-Factor App | Config, statelessness, disposability |

---

## 4. Tagging taxonomy: make every component explain itself

One vocabulary, applied everywhere. The same key means the same thing in code, logs,
metrics, Sentry, the database, and every vendor console.

| Key | Values (examples) | Why |
|---|---|---|
| `domain` | dispatch, payments, auth, corporate, safety, drivers, rides, admin, ai (already used by Sentry) | Routing, ownership |
| `surface` | backend, rider-app, driver-app, admin, website | Blast radius |
| `component` | e.g. `fare_service`, `offer_expiry_reaper` | Precise location |
| `owner` | role or team (not a person's name) | Who is paged |
| `tier` | T0 (money/safety/dispatch), T1 (core UX), T2 (supporting), T3 (internal) | Review depth, SLA, on-call |
| `data_class` | public, internal, confidential, PII, PII-sensitive (SIN, licence), financial | Encryption, logging, and retention rules |
| `sla` | a row in the CLAUDE.md Performance SLA table | Alert thresholds |
| `flag` | the `app_settings` key gating it, or `none` | Rollback path |
| `vendor` | supabase, stripe, twilio, fcm, gmaps, zoho, … | Outage impact map |
| `env` / `region` | production/staging/dev · ca-central | Residency |
| `cost_center` | infra, maps, sms, payments, ai, observability | Cost per ride |
| `scale_limit` | the known ceiling plus the metric that warns first (§5) | Know the limit in advance |

**Where each key goes:**
- a module docstring header or `CODEOWNERS`
- structured log fields and metric labels (low-cardinality keys only)
- Sentry tags
- `COMMENT ON TABLE/COLUMN` for `data_class` and owner
- vendor resource metadata/labels (Fly, Railway, Vercel, Supabase, Stripe metadata)
- `app_settings` flag descriptions
- runbook front matter
- PR labels

**Rollout:** add tags to new code first (enforce with a lint/CI check), and backfill
T0 components next. Never do a big-bang retag.

---

## 5. Per-SaaS load and capacity budget (fill in during the audit; tripwire = alert before the limit)

| Vendor | What to measure | Known limit (cite plan/docs) | Tripwire | What breaks at 10× | Lever |
|---|---|---|---|---|---|
| Supabase Postgres | connections/pool, CPU, IOPS, row counts of hot tables, slow queries, egress | plan limits | 70% | pool exhaustion, lock contention on ride/wallet rows | pgbouncer mode, indexes, read replica, partitioning (location pings) |
| Supabase Storage | document/image bytes, signed-URL rate | plan | 70% | cost, upload latency | client-side compression, lifecycle rules |
| Redis | memory, ops/s, pub/sub fan-out, eviction | plan | 70% | lost locks, WS fan-out lag | key TTLs, sharding by channel |
| Fly.io / Railway | CPU, memory, event-loop lag, WS connections per machine | machine size | 70% | WS drops, loop lag | horizontal scale, connection caps |
| Vercel (admin/website) | function invocations, bandwidth, build minutes | plan | 70% | cost | caching, ISR, edge |
| Stripe | API rate limit, webhook backlog | Stripe docs | 50% | settlement delays | idempotent retries, queueing |
| Twilio | SMS throughput/cost, OTP conversion | account | cost alert | OTP delays, SMS-pumping fraud | geo permissions, fraud guard, verify service |
| FCM / APNs | send rate, failure rate | vendor | failure rate > 2% | missed offers | retry (push_retry), collapse keys |
| Google Maps | calls/ride by API, spend | `utils/maps_budget.py` | budget % | spend | caching, fewer calls, OSRM for distance |
| Sentry | event quota, spike protection | plan | 80% | lost errors | sampling, rate limits per issue |
| Expo EAS | build minutes, OTA bandwidth | plan | 80% | release delays | `[build]` gating (exists) |

**Stress validation plan:** use Locust against staging only (target guard). For each
test, record p95 per SLA path and the first component to degrade:
- baseline at 1× current peak
- spike at 10×
- 2-hour soak at 3×
- chaos: vendor down, Redis down, slow Supabase

---

## 6. New techniques to research (evaluate with evidence; don't adopt by default)

Each lane using web research must cite sources and write
`adopt / trial / assess / hold`, with a reason, for every item.

- **Security & crypto:** passkeys/WebAuthn for admin login; asymmetric JWT with JWKS
  rotation; DPoP or refresh-token binding; app attestation (Play Integrity / App
  Attest) against GPS spoofing and emulators; envelope encryption with a cloud KMS;
  field-level tokenization of SIN/licence numbers.
- **Transmission & compression:** HTTP/3/QUIC at the edge; brotli for JSON; WS
  permessage-deflate; quantized/delta location encoding; adaptive GPS sampling by
  speed and state (battery).
- **Architecture:** a single ride state-machine module with an event log; outbox →
  change data capture; a feature-flag service with audit and targeting; contract-first
  OpenAPI with generated mobile clients.
- **Data:** partitioning of time-series location data; tiered retention/archival;
  data contracts for analytics tables.
- **Reliability:** load shedding and priority queues (SOS and payments first);
  bulkheads per vendor; error budgets tied to release freezes.
- **Mobile:** offline-first trip state with conflict resolution; OTA rollout with
  automatic rollback on crash rate.
