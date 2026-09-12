# Dimension 24 — Industry Benchmark & Cost-Value Analysis

**Question:** For a given feature, is our approach at parity with ride-share industry
leaders (Uber, Lyft, Bolt, Ola, Grab)? If not, what specific technique closes the gap,
what would it cost us (engineering effort + ongoing third-party API spend), and what
value would it return to riders/drivers/Spinr relative to that cost?

This dimension is distinct from every other one in this framework: dimensions 01–23
check **internal correctness** ("does it work, is it safe, is it tested"). Dimension 24
checks **competitive/economic positioning** ("is this good enough to compete, and is the
money we spend on it buying us the right thing"). A feature can pass every other
dimension and still fail this one (e.g. turn-by-turn navigation: safe, tested,
well-engineered deep-link — and still not what Uber/Lyft/Bolt drivers get).

---

## Checklist

### Map Rendering & Vehicle Marker
- [ ] Marker position source documented: raw GPS vs smoothed vs snapped-to-route vs
      snapped-to-road (Roads API) — and *why* that choice was made, not just that it works
- [ ] Interpolation/dead-reckoning technique named and compared to at least one known
      industry approach (e.g. Uber/Lyft's own published engineering posts, or a
      generic GIS/telematics technique) — is ours behind, at parity, or ahead?
- [ ] Marker rendering code shared across rider-app/driver-app, or forked? If forked,
      is the fork tracked (a comment, an ACTION_ITEMS entry) or silent drift?
- [ ] Marker asset caching confirmed (ties to Dimension 14's "car marker images" line)

### Turn-by-Turn Navigation
- [ ] In-app guided navigation vs external deep-link (Google Maps/Waze/Apple Maps) —
      state which, and if external, whether a build-vs-buy decision has already been
      made (check `docs/proposals/` before treating this as an open question)
- [ ] If in-app: voice guidance, re-route-on-deviation, lane guidance — which exist
- [ ] Cost driver named: `steps=true` payload/billing impact, re-route call frequency
      and its rate-limit/debounce, before recommending a change

### Route Selection & Live Tracking
- [ ] Every Directions/Routes/Distance-Matrix call site classified: client-direct
      (mobile app holds the API key) vs backend-proxied — client-direct is a cost
      *and* security finding (bundled key, no server-side budget enforcement)
- [ ] Caching/dedup strategy named for each call site (TTL, geohash bucket, none)
- [ ] Live in-trip route/ETA refresh cadence stated, and its cost basis (metered API
      vs self-hosted OSRM/routing engine)

### Pickup/Dropoff (Start/End Point) Selection
- [ ] Selection pattern(s) in use (drag-pin, search autocomplete, saved places,
      curated venue points, map long-press) compared to what riders expect from
      category-leading apps
- [ ] Multi-stop support: present or absent, and whether that's a stated non-goal

### Fare Calculation — Cost Governance
- [ ] Every external call in the fare-estimate and booking-confirm path is checked
      against the budget/circuit-breaker mechanism already in the codebase — a call
      site that bypasses it is a finding here even if the fare math itself is correct
- [ ] Any accuracy-vs-cost tradeoff (e.g. a timeout before falling back to a cheaper
      distance method) has its business rationale re-confirmed, not re-litigated from
      scratch, if one is already documented (check CLAUDE.md/`docs/adr/` first)

### Receipts
- [ ] Line-item completeness is Dimension 08/12's job (do not re-check GST/PST here) —
      this dimension only asks: does the receipt format/delivery match what riders
      expect from category leaders (itemized PDF, in-app history, email copy)?

### Notifications
- [ ] Delivery-reliability technique named (retry/backoff, delivery receipts, silent
      push + local fallback) and compared against a known industry pattern
- [ ] Any open, unresolved "notification not received" defect is treated as a
      Dimension 10 (error handling) / 13 (notifications) finding, not re-filed here —
      this dimension only asks whether the *design*, not a specific bug, is at parity

### Cost Accounting & Governance
- [ ] A single table exists (or is produced by this audit) listing every paid
      external API call site relevant to the audited feature: call → file:line →
      volume driver (per-ride / per-request / per-loop) → metered? → cached? →
      circuit-breaker present?
- [ ] Real spend visibility gap named explicitly if the org cannot currently see
      actual dollars spent on a given API (vs. only a self-imposed budget ceiling)

### Decision De-duplication (mandatory — prevents re-litigating settled questions)
- [ ] Before filing any finding, `ACTION_ITEMS.md` and `docs/proposals/` were checked
      for an existing open or resolved item covering the same ground
- [ ] Every finding is tagged `EXTENDS-<existing-ID>` (with the ID) or `NEW` — never
      filed as unlabeled duplicate work
- [ ] If a finding would re-open something already decided (an ADR, a CLAUDE.md rule,
      a closed ACTION_ITEMS entry with stated reasoning), it must engage with that
      reasoning and explain what's changed, not silently re-propose the alternative

---

## Rating (two independent scales — do not conflate them)

**Defect scale** (reuse the framework's standard severity, from
`audit-framework/templates/run-audit.md`): CRITICAL / HIGH / MEDIUM / LOW / PASS /
RECOMMENDATION. Use this for anything that is *broken* (e.g. an unmetered API call
that can blow a budget, a silent fork that will drift further).

**Competitive-parity scale** (new to this dimension — use for anything that *works*
but may not be *good enough*):

| Maturity | Meaning |
|---|---|
| 1 — Missing | Capability doesn't exist at all |
| 2 — Workaround | A manual/external workaround stands in for it |
| 3 — Functional | Works, but a recognizably below-industry-baseline approach |
| 4 — At parity | Matches what category-leading ride-share apps ship |
| 5 — Differentiated | Better than the category baseline, a real product edge |

| Gap-vs-industry | Meaning |
|---|---|
| GREEN | At or near parity (maturity 4–5); no action needed for competitiveness |
| YELLOW | Functional gap (maturity 3); worth a scoped improvement, not urgent |
| RED | Missing/workaround (maturity 1–2) **or** an active cost/security exposure regardless of maturity |

A maturity-4/5 feature can still carry a RED *defect* finding (e.g. good marker
smoothing, but the fork between rider-app and driver-app copies is a real
maintainability risk) — report both scales, don't collapse them into one number.

## Severity Guide (defect scale, for cost/governance findings specifically)

| Finding | Severity |
|---|---|
| A high-volume paid API call site with no budget/circuit-breaker and no cache, on a path every user request touches | HIGH |
| A mobile-client-direct paid API call using a bundled key, bypassing the backend's budget guard | HIGH |
| A shared UI component forked between two apps with no tracking of the divergence | MEDIUM |
| A finding that duplicates an already-open ACTION_ITEMS entry (governance failure, not a product gap) | MEDIUM |
| A capability gap (e.g. no in-app turn-by-turn) that already has a scoped, costed proposal awaiting a go/no-go decision | LOW — the gap is known and owned; escalate the *decision*, not the finding |
| A capability gap with no existing proposal or owner | RECOMMENDATION |
