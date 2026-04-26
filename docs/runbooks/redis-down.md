# Runbook — Redis Outage Response

**Owner:** `devops` + `backend` · **Severity:** HIGH (degraded-mode acceptable short-term)
**D18 dimension** · **Target availability:** 99.9%

---

## What Redis Holds

| Data | Criticality | Impact if lost |
|---|---|---|
| OTP codes | HIGH | New logins delayed (5 min) |
| OTP lockout counters | HIGH | Brute-force protection degraded (DV-6) |
| Rate-limit counters | HIGH | Rate limits fall back to per-process (DV-6) |
| WebSocket pub/sub channels | CRITICAL | Multi-replica WS fan-out breaks |
| Active ride-offer state (30s TTL) | HIGH | Dispatch retries; offers may be stale |
| Session/refresh token cache | MEDIUM | Forces Supabase reads (slower) |

See `docs/data-classification.md` § "Redis Keys" for the authoritative list.

---

## Detection

- `/healthz` endpoint's Redis check fails
- `utils/redis_client.py` warnings in logs ("Redis unavailable — falling back")
- Rate-limit-degraded alert (per DV-6 remediation)
- Elevated dispatch failure rate

---

## Triage (first 5 minutes)

1. [ ] Confirm scope: is it rate-limit Redis, session Redis, or WS-pubsub Redis?
   (The 3 URLs are independently configured.)
2. [ ] Confirm whether Redis is down or just unreachable from backend network
3. [ ] Check Upstash / Redis Cloud status page
4. [ ] Open incident: `reports/incidents/YYYY-MM-DD-redis-<slug>.md`

---

## Mitigation

### If `RATE_LIMIT_REDIS_URL` is down

- Backend falls back to in-process dict (current behaviour per DV-6)
- **Action:** alert SRE that effective rate limit is `N × limit` across replicas
- **Mitigation:** manually reduce `UVICORN_WORKERS` to 1 temporarily if fleet size permits
- **Risk:** OTP brute-force protection degraded — consider emergency rate reduction
  via `SlowAPI` config to 1 attempt/5s globally

### If `WS_REDIS_URL` (pub/sub) is down

- **Critical** for multi-replica WS fan-out
- Symptom: rider on replica A not receiving events from driver on replica B
- **Mitigation:** scale backend to 1 replica temporarily (loses HA but restores correctness)
- Long-term: add Redis Sentinel / Cluster failover

### If `REDIS_URL` (OTP + session cache) is down

- OTP: fallback to in-process dict (per replica); users may need to retry on a
  different replica
- **Mitigation:** force session layer to re-read from Supabase (slower but correct)

---

## Recovery

Once Redis is back:
- [ ] Verify `/healthz` returns 200 with Redis check passing
- [ ] Flush stale in-process counter state (restart backend pods gracefully)
- [ ] Re-initialize WS pub/sub subscribers on each replica
- [ ] Monitor OTP failure rate for 30 min; if elevated, extend lockout windows

---

## Communication

**If user-facing impact** (rare for Redis):
- In-app banner: "Some features may be slow. Active rides unaffected."

**If internal-only:**
- No user communication needed; document for post-mortem.

---

## Post-Incident

- [ ] Post-mortem: `reports/postmortems/YYYY-MM-DD-redis.md`
- [ ] File action items in OPEN-ITEMS-TRACKER
- [ ] If outage exceeded 30 min, re-evaluate whether Redis Sentinel / replica is needed
- [ ] If OTP brute-force was attempted during fallback, preserve logs for forensics

---

## Known Gaps (feed back into framework)

- **DV-6 open**: no SRE alert fires when rate-limit Redis falls back — fix blocks launch
- No Redis replica configured (single-AZ risk) — file as P2 when Redis-cluster plan is made
- WS pub/sub has no persistent fallback (in-memory channel switch is fine for short outages)
