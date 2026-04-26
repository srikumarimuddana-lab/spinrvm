# P3 — Backend Hardening: Post-Launch Stability and Resilience

These 3 items are MED/LOW severity hardening fixes that should land in the first hardening sprint after public launch. They protect against load-spike failure modes (broadcast event-loop stalls, synchronized loop-wake load spikes) and close a vendor-monitoring gap that risks PIPEDA disclosure drift.

Source audit: `reports/audits/2026-04-23-backend-api-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~7 hours.

---

## B-P3-1 · WebSocket `broadcast()` Is Sequential — Blocks Event Loop at Scale

**What's wrong:** `backend/socket_manager.py:97,99` iterates connections and `await`s `connection.send_json(msg)` one at a time. At 500+ connections per replica, broadcast hangs the event loop multiple seconds. There's also no per-message timeout, so a single slow consumer (back-pressured client, half-open socket) blocks every other connection.

**File to fix:** `backend/socket_manager.py:97,99`

**How to fix:**
```python
async def broadcast(self, msg: dict) -> None:
    coros = [
        asyncio.wait_for(conn.send_json(msg), timeout=2.0)
        for conn in list(self.active_connections.values())
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)
    for conn_id, result in zip(self.active_connections.keys(), results):
        if isinstance(result, Exception):
            logger.warning("ws broadcast failed", extra={"conn_id": conn_id})
```

**Regression test:** Spawn 1000 mock sockets; broadcast an event; assert total wall-clock under 200 ms; verify a deliberately slow consumer doesn't block others.

**Why it matters:** Driver dispatch fan-out, scheduled-ride alerts and surge-update broadcasts all flow through this path. Blocking during a broadcast affects in-flight HTTP requests on the same replica.

**Effort:** 2 h · **Severity:** MEDIUM · **Risk score:** 12 · **Audit ref:** 14-6

---

## B-P3-2 · Background Loops Have No Jitter — Synchronized DB Wake Spikes

**What's wrong:** `backend/core/lifespan.py:96,115` uses hardcoded `asyncio.sleep(interval)` for the 7 startup loops. Across replicas they wake on common-second boundaries (every 60s, every 120s, etc.), producing periodic synchronized DB load spikes. Dispatch quality degrades exactly at the spike moments because the DB pool is saturated.

**File to fix:** `backend/core/lifespan.py:96,115` + each loop module under `backend/utils/`

**How to fix:**
1. Apply a ±10% jitter in each loop's sleep:
   ```python
   await asyncio.sleep(interval * (0.9 + random.random() * 0.2))
   ```
2. Add per-loop duration + error metrics: `spinr.bgloop.<name>.duration_ms`, `spinr.bgloop.<name>.errors.count`. This also closes 17-5 / 17-10 from rider Phase E.

**Regression test:** Parse logs over 1 hour; assert no two loops wake within the same 1-second window across replicas.

**Why it matters:** Visibility plus desynchronization. Jitter alone smooths the load curve; metrics surface the desync proof and let us see if any loop is silently stuck.

**Effort:** 2 h · **Severity:** LOW · **Risk score:** 8 · **Audit ref:** 14-7

---

## B-P3-3 · No Scheduled Sub-Processor Monitoring Cadence (PIPEDA disclosure drift)

**What's wrong:** `docs/vendor-inventory.md:129` notes "monthly sub-processor list check" as a TODO. There is no scheduled task. New sub-processors (e.g. Stripe adds a reseller, Twilio rotates an SMS partner) drift outside our published privacy-policy disclosure for weeks before anyone notices.

**File to fix:** new `backend/utils/subprocessor_audit.py` + scheduled GitHub Action (or Railway cron)

**How to fix:** Quarterly job that fetches each vendor's published sub-processor list (Stripe, Twilio, Firebase, Supabase, Sentry, FCM) and diffs against `docs/vendor-inventory.md`. Alerts SRE + compliance on any diff. Output: a PR auto-opened against `docs/vendor-inventory.md` with the diff so the change is reviewed and re-disclosed before it lands silently.

**Regression test:** Schedule the task in dry-run mode; simulate Stripe adding a sub-processor; verify the diff alert fires.

**Why it matters:** Every undisclosed sub-processor is a quiet PIPEDA principle-3 (consent) violation. Catching them within a 90-day window is the practical bound.

**Effort:** 3 h · **Severity:** LOW · **Risk score:** 12 · **Regulations:** PIPEDA, SOC2 · **Audit ref:** 22-3B

---

## Checklist

- [ ] B-P3-1 Concurrent `asyncio.gather` broadcast with per-message timeout (14-6)
- [ ] B-P3-2 ±10% jitter on every background loop; per-loop metrics (14-7)
- [ ] B-P3-3 Quarterly sub-processor diff job; auto-PR on changes (22-3B)

## After this file

- See `backend-P4-future-features.md` (no current backlog items — the backend audit produced no P4-bucketed findings).
