# Clean-Sheet Audit — Five Research Sessions

These follow the 60-minute rapid baseline (`one-hour-run.md`). Each session spends one
hour researching **new approaches, techniques and standards** for one area of Spinr.
It compares them with what the code does today and recommends what to adopt, with a
first step that can ship behind a feature flag.

**The five sessions can run at the same time.** Each one writes only to its own folder
(`docs/audit/clean-sheet/research/<area>/`) and pushes its own branch and PR, so they
can't conflict.

| # | Session | Folder |
|---|---|---|
| 1 | Dispatch & marketplace | `research/dispatch-marketplace/` |
| 2 | Pricing, payments & CRA | `research/pricing-payments/` |
| 3 | Trust, safety & fraud | `research/trust-safety-fraud/` |
| 4 | Mobile & realtime | `research/mobile-realtime/` |
| 5 | Security, data & infrastructure | `research/security-data-infra/` |

---

## 1. How every session runs (shared)

**Model plan:** pick **Fable** as the session model; it acts as orchestrator and writes
the synthesis. Launch these lanes in one message, in the background:

| Lane | Model | subagent_type | Job |
|---|---|---|---|
| **C: current state** | `sonnet` | the area's `spinr-*` reviewer | How does Spinr do it today? Cite `path:line`, known limits and open `ACTION_ITEMS.md` items. Returns text |
| **R: external research** | `fable` | general-purpose | Up to 12 web searches. Primary sources first: engineering blogs of Uber, Lyft, Grab, Bolt and DoorDash; papers; standards bodies; vendor docs. Returns cited technique notes |
| **A: adversary** | `sonnet` | `spinr-edge-case-reviewer` | How do today's approach and each candidate technique fail, get gamed, or cost too much at Spinr's scale? Returns text |

**Timeline:**

| Minutes | Step |
|---|---|
| 0–5 | Read inputs |
| 5–35 | Lanes run |
| 35–50 | Orchestrator synthesis |
| 50–60 | Commit, draft PR, report |

At minute 35, use whatever has returned and list anything missing in `DEFERRED.md`.

**Inputs to read first:**
- `CLAUDE.md`
- master prompt §4 and §7
- `standards-and-scale.md`
- this file (your session's section only)
- if they exist: the latest `docs/audit/clean-sheet/rapid-baseline-*/` findings for your area

**Rules:**
- Research and recommend only. No code, config or data changes.
- No PII in any output.
- Evidence labels: VERIFIED / INFERRED / ASSUMED / PROPOSED / UNKNOWN.
- Every external claim is cited (URL plus the date it was read).
- Screen every idea against "What Spinr Is NOT" (CLAUDE.md) and against Spinr's scale:
  Saskatchewan cities, not a global network. If a technique only pays off at Uber's
  scale, say so and put it on **hold**.

**Radar entry format** (`techniques-radar.md`, one per technique):

```markdown
### <Technique name> — ADOPT | TRIAL | ASSESS | HOLD
- What it is (plain language): …
- Who uses it / source: … (URL, date read)
- Spinr today: … (path:line, VERIFIED/INFERRED)
- Benefit for Spinr at our scale: …   Cost/effort: S/M/L   Risk: …
- How it fails or gets gamed: …
- First step behind a flag (if ADOPT/TRIAL): …   Measure success by: <metric>
```

**Outputs** (all in the session's folder):

| File | Contents |
|---|---|
| `current-state.md` | Lane C's findings |
| `techniques-radar.md` | Every technique evaluated |
| `recommendations.md` | Top 5, plain language, each with the reason it beats the alternative, a flagged first step, a rollback, and a verification step |
| `DEFERRED.md` | What this session didn't cover |

**Kickoff prompt:** paste into a new session, replacing `<N>` with 1–5.

```text
Run Spinr research session <N> from docs/audit/clean-sheet-prompt/research-sessions.md.
You are the Orchestrator. Follow §1 of that file exactly and the section for session
<N>. Research and recommend only — no code, config, or data changes. Launch lanes C, R,
and A in ONE message as background Agent calls with the listed models. Lanes return
text; only you write files, into your session's folder. Stop waiting at minute 35.
Finish by committing, pushing this session's branch, opening a draft PR with
.github/pull_request_template.md, and reporting to me in plain language: the top 5
recommendations, why each beats its alternative, what needs my decision, and what was
NOT verified.
```

---

## 2. Session 1: Dispatch & marketplace

- **Lane C agents:** `spinr-dispatch-reviewer` (then `spinr-realtime-reliability-reviewer` rules).
- **Scope:** `backend/services/{dispatch_service,dispatch_candidates,driver_offer_service,demand_tiles,h3_heatmap}.py`, `backend/routes/rides/matching.py`, `backend/utils/{offer_expiry_reaper,driver_presence,maps_eta,surge_engine}.py`.
- **Research questions:**
  - Matching: nearest-driver vs batched/global assignment (bipartite matching over short windows). When does batching beat greedy at small scale?
  - ETA quality: road-network ETA vs learned corrections. How big is the error today?
  - Supply positioning: heatmaps and demand forecasts shown to drivers without control-of-work pressure.
  - Offer design: timeout length, sequential vs broadcast offers, destination/fare transparency, acceptance fairness.
  - Airport, event and winter-storm modes.
  - Fairness audit of dispatch outcomes by neighbourhood.
- **Guardrails:** drivers are contractors, so no penalties for being offline. Any algorithm change needs a replay test on past trips before rollout.

## 3. Session 2: Pricing, payments & CRA

- **Lane C agents:** `spinr-money-auditor` (then `spinr-surge-auditor` and `spinr-corporate-billing-reviewer` rules).
- **Scope:** `backend/services/{fare_service,payment_service,ledger_service,stripe_connect_ledger_service,incentive_service}.py`, `backend/utils/{surge_engine,payment_retry,stripe_reconcile,t4a_income,t4a_annual_job}.py`, `backend/routes/{payments,wallet,webhooks,fares}.py`, `docs/finance/`.
- **Research questions:**
  - Upfront guaranteed pricing vs metered pricing: dispute rates, handling route changes.
  - Double-entry ledger patterns (immutable journal, balances derived, not stored); daily reconciliation against Stripe.
  - Payment reliability: retries, card updater, 3DS after the trip, pre-authorisation hold sizing.
  - Driver payouts: instant vs scheduled, negative balances, handling tips that arrive after a payout.
  - Incentive design that resists gaming.
  - CRA, as questions answered only with primary sources:
    - GST/HST obligations for ride-sharing
    - digital-platform operator reporting
    - T4A rules and thresholds
    - record retention
- **Guardrails:** 0% commission; surge cap 2.5×; every charge is a disclosed line item. Tax conclusions without a primary source go to the escalation list for an accountant.

## 4. Session 3: Trust, safety & fraud

- **Lane C agents:** `spinr-safety-sos-reviewer` (then `spinr-fraud-auditor` and `spinr-insurance-period-auditor` rules).
- **Scope:** `backend/routes/{safety,promotions,quests,loyalty}.py`, `backend/routes/rides/safety.py`, `backend/routes/drivers/location.py`, `backend/utils/{gps_filtering,route_deviation_alerter}.py`, `backend/services/insurance_period_gps_correction.py`, `.claude/context/domain-safety.md`.
- **Research questions:**
  - GPS spoofing detection: speed plausibility, mock-location flags, app attestation (Play Integrity, App Attest).
  - Collusion and fake-trip detection: shared device, payment or location patterns; graph signals.
  - Account takeover: SIM-swap signals, risk-based step-up, passkeys.
  - Promotion and referral abuse: velocity limits, device fingerprinting within PIPEDA limits.
  - Safety: trip PIN, route-deviation alerts, crash detection, trusted contacts, how dispatchers triage SOS.
  - Driver fatigue limits without control-of-work.
  - Handling law-enforcement data requests.
- **Guardrails:** no ad or behavioural-profiling SDKs; every fraud action has an appeal path; measure false-positive cost.

## 5. Session 4: Mobile & realtime

- **Lane C agents:** `spinr-realtime-reliability-reviewer` (then `spinr-performance-sla-reviewer` and `spinr-accessibility-reviewer` rules).
- **Scope:** `rider-app/`, `driver-app/`, `shared/utils/{gpsSmoothing,markerPlayback}.ts`, `backend/routes/websocket.py`, `backend/socket_manager.py`, `backend/utils/ws_pubsub.py`, `.github/workflows/eas-rollout-control.yml`, `docs/known-forks.md`.
- **Research questions:**
  - Offline-first trip state: finish a trip with no signal, then reconcile the fare on reconnect.
  - Adaptive GPS sampling by speed and trip state for battery life; smoothing and map-matching on the device.
  - WebSocket efficiency: permessage-deflate, delta/quantised coordinates, reconnect with state resync, and whether a push fallback is needed.
  - App start time and bundle size; images; low-end Android devices.
  - OTA update rollout with automatic rollback on crash rate; forced upgrades for breaking API changes.
  - Shared mobile core to end rider/driver forks.
  - Accessibility (screen readers, dynamic type), cold-weather and one-hand use.
- **Guardrails:** no visual-regression tooling exists for the rider or driver apps, so UI claims are reasoned about, not screenshotted. Say so.

## 6. Session 5: Security, data & infrastructure

- **Lane C agents:** `spinr-security-auditor` (then `spinr-migration-reviewer`, `spinr-cicd-infra-reviewer` and `spinr-observability-reviewer` rules).
- **Scope:** `backend/routes/auth.py`, `backend/utils/{crypto,refresh_tokens}.py`, `backend/core/{config,middleware}.py`, `backend/migrations/` (RLS, pgsodium), `.github/workflows/security-gates.yml`, `docs/threat-model/`, `docs/adr/`, `standards-and-scale.md` §1–§6.
- **Research questions:**
  - Tokens: HS256 → asymmetric JWT (ES256/EdDSA) with a published key set and rotation; binding refresh tokens to a device.
  - Admin login with passkeys/WebAuthn.
  - Encryption: pgsodium's support status and the migration path; envelope encryption with a cloud key-management service; tokenising SIN and licence numbers.
  - Standards mapping: OWASP ASVS L2, MASVS and API Top 10 — what we meet, what we miss.
  - Supply chain: SBOM per build, SLSA level.
  - Observability: OpenTelemetry naming now, and the trigger for turning on tracing (ADR-014).
  - Data: partitioning location/time-series tables, tiered retention, data contracts.
  - Resilience: shedding load by priority (SOS and payments first), isolating each vendor so one outage can't take down the rest, error budgets.
- **Guardrails:** read-only connectors only. Never touch production secrets. Canadian data residency.
