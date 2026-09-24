# Clean-Sheet Audit — Industry Tech-Stack Benchmark

What the large, profitable ride-share companies run, set against Spinr's stack. The
purpose is to **copy the patterns that fit Spinr's scale** — not to copy products built
for millions of trips a day.

**Evidence status (2026-09-24):** everything below is **INFERRED from secondary sources**
(search results, ByteByteGo, engineering-blog summaries). The primary sources — the
companies' own open-source licence pages and engineering blogs — were **blocked by this
environment's network policy** when this file was written (`www.lyft.com`,
`assets.grab.com`, `www.uber.com`). Lane A3 of the rapid baseline must re-check the
primary sources if the network allows, and upgrade or correct each row.

## 1. Primary sources to check

| Company | Source | What it shows |
|---|---|---|
| Lyft | https://www.lyft.com/legal/licenses | Third-party and open-source software used in Lyft products |
| Grab | https://assets.grab.com/wp-content/uploads/media/grab-oss-attributions.txt | Open-source software in Grab's mobile apps (Android list includes AndroidX, ConstraintLayout, etc.) |
| Uber | In-app only: Settings → About → Legal → Software licences. Also https://github.com/uber and https://uber.github.io | Mobile libraries; Uber's own open-source projects |
| Uber, Lyft, Grab | Engineering blogs: `uber.com/blog/uber-tech-stack-part-two`, `eng.lyft.com`, `engineering.grab.com` | Why each choice was made |

Bolt and DiDi: no primary source found. Search results for Bolt were app-agency
marketing pages, not Bolt's own material, so they are excluded as unreliable.

**Caveat on licence pages:** they list the libraries shipped in the apps. That's mostly
mobile UI, networking and serialization. They say little about backend architecture,
so blogs are the better source for backend choices.

## 2. What the leaders run (INFERRED)

| Layer | Uber | Lyft | Grab | Spinr today |
|---|---|---|---|---|
| Backend languages | Go (new high-throughput services), Java (older marketplace) | Go, Python | Go, Java, Kotlin (Grab-Kit Go framework) | Python / FastAPI monolith |
| API contracts | gRPC + Protobuf, Thrift; QUIC at the edge | Protobuf-first design | gRPC/Protobuf (Grab-Kit) | REST/JSON + OpenAPI; WebSocket JSON |
| Service mesh / edge | Uber Gateway on NGINX | **Envoy** (created at Lyft); Envoy Mobile for app networking | Kubernetes + mesh | Cloudflare → Fly/Railway; no mesh |
| Workflows | **Cadence** (Temporal came from it) | Flyte (ML and data) | Event-driven on Kafka | asyncio loops + transactional outbox + leader locks |
| Messaging | Kafka, Flink | Kafka, Spark | Kafka at the centre (booking state machines, notifications) | Redis pub/sub + Postgres outbox |
| Primary data | Docstore (MySQL/Postgres on RocksDB) | AWS RDS etc. | SQL/NoSQL mix | Supabase Postgres |
| Geospatial | **H3** hex grid (Uber created it) | — | GrabMaps (own maps) | H3, geohash, OSRM, Google Maps |
| Config / flags | Flipr / UCDP config platform | — | — | `app_settings` table flags |
| Metrics / tracing | M3 metrics, **Jaeger** tracing | Envoy stats | observability stack | Prometheus-style metrics, Sentry; tracing deferred (ADR-014) |
| Mobile | Native Swift/Kotlin, **RIBs** architecture | Native Swift/Kotlin | Native (superapp) | Expo / React Native (ADR-002) |

Sources: ByteByteGo "Uber Tech Stack"; Uber blog "Tech Stack Part II: The Edge and
Beyond"; highscalability "Brief History of Scaling Uber"; Lyft Engineering (Protobuf
design, Flyte); Packt on Envoy Mobile; Grab Tech (Grab-Kit, data ingestion);
Factor House on Kafka at Grab. Full URLs are in the PR description that added this file.

## 3. Pattern-by-pattern verdict for Spinr (starting position; lanes must confirm)

| Pattern | Verdict | Why |
|---|---|---|
| H3 hex grid for demand, surge and heatmaps | **KEEP** (already used) | Same choice as Uber; fits any scale |
| Config/flag platform (Flipr-style) | **MODIFY** | `app_settings` works. Add audit history, targeting (by city or % of users) and a kill switch |
| Durable workflow engine (Cadence/Temporal-style) for long-running flows | **ASSESS** | Payouts, scheduled rides, KYB and document expiry run on hand-built loops. A workflow engine gives retries, timers and visibility for free, but adds infrastructure. Compare against the current outbox + loops |
| Contract-first APIs (Protobuf/gRPC-style) | **TRIAL** as OpenAPI-first with generated mobile clients | Gets the "one contract, generated clients" benefit without a gRPC migration. Lower risk than switching transport |
| Distributed tracing (Jaeger / OpenTelemetry) | **ASSESS** — set the ADR-014 trigger | Leaders all trace. Spinr can start with OpenTelemetry attribute naming now and turn tracing on when a latency problem can't be pinned down |
| Kafka event backbone | **HOLD** | Built for millions of events per second. Postgres outbox + Redis fit Saskatchewan scale. Revisit only when a measured capacity limit says so |
| Service mesh (Envoy) / microservices split | **HOLD** | A monolith is right for Spinr's team size. Splitting would add failure points without a scale need |
| Native mobile + RIBs | **HOLD** | Expo is the recorded decision (ADR-002). Look instead at the specific native gaps (background location, battery life) inside research session 4 |
| Own maps (GrabMaps) | **HOLD** | Only pays off at huge scale. OSRM + Google with `maps_budget.py` is the proportionate choice |
| ML platform (Flyte / Michelangelo) | **HOLD** | No ML models in production yet. Use simple rules and forecasts first |

## 4. The lesson

The giants' stacks mostly solve problems of **scale** (millions of trips a day,
thousands of engineers). Spinr's edge is **correctness, transparency and local
compliance**. Borrow the patterns that make systems correct and observable: a single
geo index, contract-first APIs, durable workflows, tracing and a config platform. Hold
the ones that exist for scale (Kafka, mesh, microservices, own maps) until a measured
capacity limit from `standards-and-scale.md` §5 says otherwise.
