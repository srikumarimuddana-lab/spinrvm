# Pending Execution Backlog — Architecture & Dispatch → A-grade

> **Created:** 2026-07-14 · **Resume:** Thursday 2026-07-16 (after quota renewal)
> **Branch:** `hardening/insurance-batch-timeout-period1-guard` (16 commits ahead of `main`, nothing pushed)
> **How to resume:** read this file top-to-bottom, then execute **one** subtask under the
> Execution Protocol below and STOP. Start with Tier 1, item 1.

This is the single source of truth for what is left in the Architecture (B-→A) and
Dispatch (B→A) hardening mandate. It supersedes nothing — the three underlying plans
still hold the deep detail:
- `.claude/plans/lets-create-a-plan-refactored-kurzweil.md` (first-increment: T1/S1/S2/R1/F1/C1 + later phases A-*/B-*)
- `.claude/plans/durable-offer-timeout-plan.md` (#9 — DONE)
- `.claude/plans/ride-event-version-plan.md` (#11 — DONE)

---

## Standing constraints (unchanged — re-read before touching anything)

- Work **only** in the local repo. Do **not** touch production DB, customer data, Stripe,
  Twilio, Supabase prod, Railway, Vercel, Firebase, EAS, or secrets.
- Do **not** deploy, push, open a PR, or modify external state until explicitly asked.
- Do **not** use `git reset`, `git stash`, force-push, direct `main` commits, or `git add -A`.
- Never edit an applied migration — create a new forward migration (next free number).
- Preserve unrelated concurrent edits. The file
  `reports/compliance/2026-04-26-supabase-service-role-key-breach-assessment.md` is being
  edited by another session — **never stage it**; stage only the named files per subtask.
- Every implementation subtask ≤ 3 files. One independently testable logical change per commit.
- TDD: failing test → minimal impl → passing test.
- Do **not** silence DB / dispatch / insurance / payment / auth / safety errors.
- Do **not** change surge cap, driver-commission model, insurance policy, or
  contractor-control behaviour without business/legal approval.
- Never expose raw errors / PII to users.
- Commit trailer (required):
  ```
  Co-developed with Claude Code (claude-sonnet-4-6)

  Co-Authored-By: Claude <noreply@anthropic.com>
  ```

## Execution Protocol (per subtask)

1. Write the pre-edit report: subtask name, invariant, ≤3 files, current→desired behaviour,
   the failing test, migration/compat/rollback/telemetry impact.
2. Write the failing test; run it; show it RED.
3. Implement the minimal change; run the test; show it GREEN.
4. Run the domain suite. Any failure → prove pre-existing (throwaway `git worktree` at HEAD,
   never stash/reset) before proceeding.
5. Lint/typecheck the changed files.
6. `git status`/`git diff` — confirm only the named files changed (compliance report untouched).
7. Run the relevant reviewer(s): migration reviewer for SQL; a dispatch/architecture,
   insurance, security, or money reviewer as the change dictates.
8. Rebuild graphify: `python -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"`
   (gitignored output — nothing to stage; it can be slow, run in background if needed).
9. Stage **only** that subtask's named files.
10. Commit one logical change with the trailer.
11. Report the result.
12. **STOP.** Do not begin another subtask. Do not push or deploy. Wait for "CONTINUE".

---

## DONE — 16 commits on this branch (for traceability)

| Area | Commits |
|---|---|
| Insurance batch-timeout Period-1 guard (S1) | `249b39df` |
| Surge single-writer leader lock (R1) | `e0f6cd37` |
| Concurrent admin WS fan-out (F1) | `079310e6` |
| Ride-status contract parity (T1/C1, rider + driver + issue #5) | `cedebdda` `d00f2e27` `66e60bda` |
| **Durable offer-timeout plan #9** (M1/M2/W1–W4) | `d2d0fc05` `dd70d3c9` `c00c99df` `a3a59f38` `cd5e77f3` `bf8addd5` |
| **Event-version plan #11** (V1 mig-225 / V2 stamp / V4 driver / V3 rider) | `a2b6b6f1` `789584d5` `c872fa91` `883e72c3` |

---

## PENDING — 16 items in 4 tiers

### TIER 1 — ready now, local, no gate (do these first)

These finish the #11 event-version work: the driver's V4 guard and the rider's V3 guard are
in place but only the `driver_accepted` broadcast currently carries a `version`. Each
remaining lifecycle broadcast must stamp `version` from its **post-update row** so clients
can order it. Pattern at every site: the handler already does
`guard = await _deps.db.update_one("rides", {...}, {"$set": {...status...}})`; `guard` is the
updated row (from `_single_row_from_res`) with the trigger-bumped `version`. After the
existing `if guard is None` check, pass `version=guard.get("version")`. (At sites that re-read
the ride post-update — e.g. accept — use that re-read row, as V2 did.)

> **Key nuance for the DRIVER (V4):** the driver's guard only helps for events the driver did
> **not** initiate. `driver_arrived` / `in_progress` are driver-initiated (redundant to the
> driver) — stamping `version` there benefits the **rider** (V3). The driver benefits from
> version + `driver_user_id=` on the **cancellation** broadcasts (admin/rider/auto cancels),
> which is item 4 and the highest-value one for V4.

**1. Stamp `version` on the `driver_arrived` broadcast** *(rider benefit)*
- Invariant: the `driver_arrived` `ride_status_changed` payload carries the ride's version.
- File: `backend/routes/drivers/ride_flow.py` (broadcast at ~line 597; `guard` from the arrive
  UPDATE ~line 561–575) + test.
- `ride` there is the PRE-update row — must use `guard.get("version")`, not `ride["version"]`.
- Failing test: broadcast for `driver_arrived` emits `version=<guard version>`.
- Commit: `feat(ws): stamp version on driver_arrived broadcast`

**2. Stamp `version` on the `in_progress` broadcasts** *(rider benefit)*
- File: `backend/routes/drivers/ride_flow.py` — verify-otp (~line 657) AND start (~line 724);
  both have `guard` from their IN_PROGRESS UPDATE. + test.
- Also check `routes/rides/lifecycle.py:120` / `:276` (an in_progress path) — if it holds a
  post-update row, stamp there too; otherwise note as its own follow-up to stay ≤3 files.
- Commit: `feat(ws): stamp version on in_progress broadcasts`

**3. Stamp `version` on the `completed` broadcast** *(rider benefit)*
- File: `backend/routes/drivers/ride_complete.py` (~line 767; the completion UPDATE's row) + test.
- Commit: `feat(ws): stamp version on ride-completed broadcast`

**4. Stamp `version` + pass `driver_user_id` on the `cancelled` broadcasts** *(rider AND driver — highest value for V4)*
- Sites (multiple — may need to split into ≤3-file subtasks):
  - `backend/routes/rides/cancellation.py:390` and `:500`
  - `backend/routes/drivers/ride_cancel.py:150` and `:347`
  - `backend/routes/admin/rides.py:496`, `:599`, `:961` (admin cancel/complete)
- Two changes per site: (a) `version=<post-update row version>`; (b) where the driver did NOT
  initiate the cancel, add `driver_user_id=<assigned driver's user_id>` so the driver's
  connection receives it and V4 can drop stale ones. Look up the driver's `user_id` from the
  ride's `driver_id` (there's already a `get_driver_by_id` helper).
- **Split guidance:** do rider/driver cancels (cancellation.py + ride_cancel.py) as one subtask,
  admin cancels (admin/rides.py) as another — keeps each ≤3 files.
- Commit(s): `feat(ws): stamp version + notify driver on ride-cancelled broadcast`

### TIER 2 — ready, but gated on explicit user go

**5. Backend test-debt cleanup.** Pre-existing failing/erroring suites proven unrelated to the
hardening work during this branch:
- `test_ride_accept_flow.py::...test_accept_updates_status_and_sends_ws` (`update_one` await_count 2≠1 — the ride_metrics second UPDATE)
- `test_ride_accept_flow.py::...test_searching_path_claim_filter_requires_unclaimed_ride`
- `test_dispatch_metrics.py::test_accept_ride_counts_accept_and_observes_latency` (`'coroutine' object has no attribute 'data'` — run_sync mock)
- `test_ws_fanout_metrics.py::test_pubsub_fanout_observed` (fanout counter 0)
- rider-app `store/__tests__/aiChatStore.test.ts` (jest "import outside scope" config error)
- **Gate:** per the "ask before softening/touching tests" rule — do NOT start without an
  explicit user go. When cleared, treat each as its own root-cause + fix subtask.

### TIER 3 — blocked on external sign-off (not codeable locally)

**6. S2 — preserve driver `is_online` intent on missed offers** *(fixes issue #3)*
- Invariant: system-driven missed-offer handling sets only `is_available=False` (+ optional
  system-paused marker); it never writes `is_online`.
- Files: `backend/routes/rides/matching.py` (~the single `:948-951` and batch `:1130-1134`
  auto-offline sites — re-grep, lines drift) + test.
- **BLOCKING gate: business + legal approval** (changes contractor-control behaviour — a driver
  stays "online" after N misses). Prepare test + impl; hold the commit for sign-off.

- **Migration 225 apply** — DBA sign-off before applying the `rides.version` trigger to the
  hottest table in production. Apply-time gate, not a code subtask. (Already committed as
  `a2b6b6f1`; safe/reviewed, but confirm trigger overhead under load with a DBA.)

### TIER 4 — larger phases (each becomes its own TDD plan on approval)

Sequenced after Tier 1. Each is multi-week with its own human gates; do NOT start without
turning it into a dedicated plan first.

**Plan A — Architecture**
- **A-a.** Extract `RideStatus`/`OfferStatus`/geo/event-version/idempotency value objects;
  generate TS client contracts from FastAPI OpenAPI; delete manual client enums (permanent
  fix for #6). *Contract-review-board gate.*
- **A-b.** One **ride transition service** — atomic check(state+version) → validate transition
  → write state+timestamps → append versioned event → append outbox row → return version.
  Routes become thin adapters; ban direct `ride.status` writes via import-boundary test.
  *Makes #4/#5/#11 correct by construction.*
- **A-c.** Transactional **outbox/inbox + separate worker** process; move durable work off API
  replicas. (Absorbs the remaining worker-driven search-timeout piece of dispatch B-c.)
- **A-d.** Architecture-enforcement tests: import boundaries, no sync Supabase in async routes,
  no money floats, no unknown ride statuses; ADRs + per-domain CODEOWNERS. *Eng-director gate.*

**Plan B — Dispatch**
- **B-a.** **Unify dispatch** — fold `matching.py` inline logic + `DispatchService` into one
  path; delete the dead copy (fixes #1/#2). Shadow-compare before cutover; kill switch.
- **B-b.** Explicit **offer state machine** (`pending/accepted/declined/expired/revoked`) with
  DB invariants + partial unique indexes (one accepted offer/ride, one active assignment/driver,
  one pending offer/ride-driver). *New migration.*
- **B-d.** **Insurance-period-from-committed-state** service — single authoritative transition
  point; generalises S1, fixes #4 structurally. *Safety-ops + SGI/compliance gate.*
- **B-e.** **Geospatial consolidation** (= issue #8) — populate/backfill PostGIS `location`,
  make it the authoritative candidate source, Redis for presence only, remove the dead RPC
  path. *New migration; dual-read + reconcile before cutover.*
- **B-f.** Deterministic **explainable ranking** — versioned weights + reason breakdown;
  routing-provider fallback.
- **B-g.** **Dispatch replay simulator** — requests/locations/ETAs/accept-decline-timeout/
  scheduled/WAV/service-area — prove KPI impact without production.

> **B-c (durable timeout)** is substantially delivered by the #9 plan already committed
> (durable offer-expiry reaper + stuck-ride-sweeper backstop). The only remainder — moving
> search-expiry onto the dedicated worker — folds into A-c. Not counted as a separate item.

---

## Count summary

| Tier | Items | Executable now? |
|---|---|---|
| 1 — event-version broadcast follow-ups | 4 | ✅ yes, no gate |
| 2 — backend test-debt cleanup | 1 | ⏸ needs user go |
| 3 — S2 + migration-225 apply | 1 (+1 apply gate) | 🔒 legal / DBA |
| 4 — Plan A (4) + Plan B (6) | 10 | 📋 each needs its own plan |
| **Total pending** | **16** | **4 immediately actionable** |

**Recommended Thursday start:** Tier 1, item 1 (`driver_arrived` version stamp) — smallest,
zero-gate, and unblocks the rest of the event-version end-to-end story.
