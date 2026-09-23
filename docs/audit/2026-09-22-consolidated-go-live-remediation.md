# Consolidated go-live remediation plan — 2026-09-22

Owner: Pandi (CTO). Executing: Ravi's SDLC team.
Status: workstreams A–C were already running when the earlier remediation plan was written. This document replaces that plan as the single list. Draft PR only, then Kiran's go, then apply migration, then deploy backend, then verify. No production writes without that go.

## Sources

| Doc | Path | What it contributes |
|---|---|---|
| Go-live test report | Vault: `Meetings/Go-Live Test Report 2026-09-22.md` | Full evening test, Supabase log counts, security advisors, Kiran's answers |
| Live ride / WebSocket incident review | Repo: `docs/audit/2026-09-22-live-websocket-incident-review.md` | Offer-by-offer evidence, disconnect reasons, confirmed Sentry stack |
| Evening offer and location check | Repo: `docs/audit/2026-09-22-evening-offer-and-location-check.md` | Fly machine state, Sentry issue counts, Stripe reconcile crash |

Times below are America/Regina (UTC-6). The test window used by the WebSocket review is 6:58:36–7:58:36 PM.

## Facts the three sources agree on

Release **v907** (`spinr-backend-yyz`, PR #5717, `d807392c`) finished at **6:54 PM**. The two `app` machines started then and stayed up. Health checks passed. The six `burst` machines were updated in that deploy and left stopped. That is a capacity drop from 8 machines to 2, not six crashes. There was no second machine restart during the test hour.

In the test hour, **19 rides** were created on two rider accounts: **3 completed and paid, 16 cancelled by the rider before acceptance**. Zero system "no drivers" cancels. Zero driver cancels.

**15 of those rides got one offer each: 3 accepted, 2 declined, 5 expired (~15s), 5 cancelled** because the rider cancelled. An offer row means the server created an offer. It does not mean the phone showed it.

**4 rides got no offer at all:** SPR-C9T9R8 (7:10:31 PM), SPR-5RUQTX (7:11:44), SPR-3XQS8D (7:13:37), SPR-2R4CM9 (7:14:13). Riders waited about 31, 72, 20, and 55 seconds.

The failing write is `update_live_driver_marker`. Deployed migration 445 declares `p_driver_id UUID` and compares it to `drivers.id`, which is TEXT in production. Postgres returns **42883 `operator does not exist: text = uuid`**. The API still returned 200 on the REST location routes, so the apps did not surface the failure. On the WebSocket, the same exception is labeled `handler_error` and the server closes the socket (`websocket.py` outer handler → `_write_ws_marker` → `update_driver_location`).

`driver_activity_log` has **134 `connection_lost` events** across the two test drivers (104 and 30). **109 are `server_close_reason=handler_error`**. The other 25 are ordinary closes (19 app-backgrounded / code 1001, 5 code 1000, 1 code 1005). Do not call all 134 crashes.

Sentry has **no fatal issues and no unhandled issues** in the three hours before ~8:40 PM. The phone events in the window are handled: one Android driver `SpinrApiError: Database operation failed` (`com.spinr.driver@2.0.0+39`), rider App Check token failures (`com.spinr.user@2.0.0+30`), and driver cold-start session-lock info logs (`com.spinr.driver@2.0.0+34`). Two driver builds were in the test. Native process crashes are not established.

Kiran replaced `update_live_driver_marker` in production at **7:57:58 PM** with a TEXT signature. By 8:38 PM only that TEXT overload was present, service_role only. Merged migration 445 in git still has the UUID parameter, so a fresh database or a replay of 445 brings the bug back.

## Correction to the evening check

The evening check reported that location errors ran **7:03–7:13 PM and then stopped**. That is when **Sentry stopped recording new events** for those grouped issues (last seen 7:13:55 PM on `/location-batch`, `/location-live`, and the WebSocket issue). Supabase and Postgres logs show **1,359 failures from 7:03:16 PM through 7:57:58 PM**, the whole test. They stopped because the function was replaced, not because the bug cleared itself. REST marker failures continued until 7:57:58 PM. The latest WebSocket `handler_error` in the incident review was 7:44:50 PM.

The evening check also left five offers unclassified. They are status `cancelled` (rider cancelled the ride). The "2 drivers online and available" count was taken around **8:26 PM**, after the test, and does not explain the four rides that received no offer during the test.

What the evening check added, and the other two docs did not already prove:

- The Stripe reconcile loop is crashing. `backend/utils/stripe_reconcile.py` calls `pi.get(...)` on a Stripe `PaymentIntent` (around lines 271, 296, 304, and 310). Current stripe-python objects are not dicts, so `.get` raises `AttributeError`. The loop was still failing ticks in the Fly log buffer around 8:00 PM. Daily money reconciliation is not running.
- Fly's CLI log buffer is about 100 lines / two minutes. It cannot reconstruct 7:00–7:58 PM. Sentry issue counts for 7:03–7:13 PM must not be added together: one failure is logged more than once (184 and 182 log lines vs 26 batch requests, 153 live requests, and 16 WebSocket events).

## What is still not proven

- Handler errors overlap only three offer windows (SPR-QBGF8B cancelled, SPR-FZGDTH expired, SPR-6R5748 declined). Four other expired offers had no `handler_error` inside their offer window. Socket closes do not explain every missed offer.
- The four rides with zero offers need a dispatch-candidate explanation. "Online" at 8:26 PM is not eligibility at 7:10 PM.
- `driver_notified_at` is NULL even on successful rides, so it cannot be used as a delivery count. No `push_retry_queue` rows were created. That does not prove push delivery worked.
- The three completed rides were paid. The 7:09 PM Stripe webhook 503 ("settlement still being finalized; retry") is a separate payment-path failure, not the offer bug.
- App Check 403s and Meta CAPI 400s are real and separate. They are not the offer outage.

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| F1 | Critical | Live marker writes failed 7:03–7:58 PM (1,359 times) on REST live, REST batch, and WebSocket. Git still has the UUID parameter. | Prod hot-fixed out of band. Source fix is workstream A. |
| F2 | Critical | Marker-write exceptions close the driver socket (`handler_error`): 109 of 134 disconnects. A driver mid-reconnect does not see an offer. | Workstream A. Does not explain every expired offer (see above). |
| F3 | Low | Period-1 insurance accumulators lost writes in the same window. | Test accounts only. No SGI correction. Workstream B quantifies it. |
| F4 | High | 16 `SECURITY DEFINER` `admin_*` RPCs are executable by `anon` and `authenticated` through `/rest/v1/rpc`. | Workstream B. |
| F5 | High | 4 rides received zero offers while drivers were in the test. | Workstream C. |
| F6 | High | After a decline or expiry, the second online driver was not re-offered. One rider waited 2m 25s. | Workstream C. |
| F7 | High | 5 offers expired unanswered. Some overlap socket drops. Not all of them do. | Re-test after A. C owns any remaining dispatch bug. |
| F8 | High | Stripe reconcile loop `AttributeError` on `PaymentIntent.get`. Reconciliation is down. | Workstream D. From the evening check. |
| F9 | Medium | Out-of-band production function replace at 7:57:58 PM. | Closed once migration 447 matches that live TEXT definition and is merged. |
| F10 | Info | XL drivers receiving Economy offers. | Intended. Kiran confirmed. Lock it with a test in C. |
| F11 | Medium | Stripe webhook 503 at 7:09 PM. The three completed rides were still marked paid. | Workstream D. Confirm the retry is idempotent. |
| F12 | Medium | Rider App Check on the production build exchanges a debug token and gets 403. | Workstream E. |
| F13 | Medium | Meta CAPI Purchase events rejected (400, invalid extended device info). | Backlog. Marketing impact. |
| F14 | Medium | Google map snapshot render failed once and was deferred to the finalizer. | Backlog. Confirm the retry ran. |
| F15 | Medium | Sentry has no source maps, so mobile titles are unreadable. Backend events lack a release tag. Grouping under-counted the 42883 burst (Sentry last-seen 7:13 PM vs logs through 7:58 PM). | Workstream F. |
| F16 | Medium | `/health` spiked to 2.9–3.8s at 8:21 PM and 8:27 PM. Typical ping in this check was ~50ms, with one earlier reading ~250ms. | Watch. Vikram correlates. |
| F17 | Low | Rider cancel reason is never sent. A health checker calls `/api/health` and gets 401. `driver_notified_at` is always NULL. | Backlog, except the health path, which is F. |
| F18 | Low | Fly log buffer cannot cover an incident hour. | Workstream F. |

## Workstreams

### A — Live marker and WebSocket (Surya + Tara) — running

Branch `fix/live-marker-text-id`.

- Migration **447**: drop the UUID overload, install the TEXT function that matches production after 7:57:58 PM, grant `service_role` only, `NOTIFY pgrst`. Leave merged migration 445 untouched.
- Real Postgres test with TEXT `drivers.id`, including a non-UUID text id. A mocked RPC test will not catch 42883.
- A marker-write database error is logged and sent to Sentry. It does not close the driver socket.
- Done: draft PR, tests passing with output.

### B — Security and insurance data (Divya) — running

Branch `fix/revoke-anon-admin-rpcs`.

- Migration **448**: revoke `anon` and `authenticated` `EXECUTE` on the 16 admin RPCs, after proving the admin dashboard does not call them with a user JWT.
- Short note on Period-1 rows lost for the test accounts. No production driver correction.

### C — No offer and no re-offer (Arjun + Surya) — running

Branch `fix/dispatch-no-offer-reoffer`. Migration **449** reserved only if a schema change is actually required.

- Per-ride reason for SPR-C9T9R8, SPR-5RUQTX, SPR-3XQS8D, SPR-2R4CM9, from candidate and presence data at request time.
- If the code never re-offers the second online driver after decline or expiry, fix that and test it.
- Cite the XL-accepts-Economy rule at file and line, and add a test that locks the intended behavior.
- If the no-offer cases only happen when the same person is both rider and driver, say so and do not block go-live on it. Kiran's gate: C blocks go-live only when the bug reproduces with separate rider and driver accounts.

### D — Payments — start now

- Replace `pi.get(...)` in `stripe_reconcile.py` with access that works on the installed stripe-python `PaymentIntent` (`pi["field"]` or `to_dict()`). Unit test with `PaymentIntent.construct_from(...)`, not a dict.
- After deploy, re-run reconciliation for the days the loop was crashing.
- Confirm the webhook 503 "still being finalized" path retries once and does not double-settle. The three completed test rides were paid, so this is a retry-safety check, not an open unpaid ride.

### E — Apps (Anika) — after A is deployed

- Same build number on iOS and Android. The test used driver `2.0.0+39` (Android) and `2.0.0+34` (iOS).
- Controlled session, separate rider and driver phones: two drivers online, driver 1 declines, driver 2 sees and hears the offer within about 15 seconds.
- Production App Check uses real attestation, not the debug token exchange.
- Send the rider cancel reason. Can follow the first go-live day if A, B, and D are green.

### F — Observability (Vikram) — start now, does not block the first retest

- Sentry source maps on EAS and Vercel builds. Release tag on backend events.
- A Fly log drain that keeps at least an hour. The CLI buffer is not an incident log.
- Alert on a 42883 or 5xx burst from the marker RPC.
- Point the health checker at `/health`, not `/api/health`.
- Look at the 8:21 PM and 8:27 PM health latency spikes.

## Release order

1. Merge A and B. Both migrations are idempotent. Apply 447 and 448 with the migration runner. Do not edit 445.
2. Verify before backend deploy: only the TEXT overload exists; `curl` with the anon key against `admin_*` RPCs returns 401 or 403; PostgREST schema has reloaded.
3. Deploy the backend with A's WebSocket change. Include C if its PR is ready and the defect is real. Include D in the same deploy if the reconcile fix is ready. D should not wait for a second release.
4. Controlled retest with the 60-minute monitor (`~/AppData/Local/hermes/scripts/spinr_golive_monitor.py`). Pass only if all of these hold:
   - 0 occurrences of 42883
   - 0 `handler_error` disconnects
   - every ride gets an offer within 5 seconds
   - after a decline, the other driver is offered in under 20 seconds
   - no `/health` sample over 1 second
   - separate rider and driver accounts, not the same person on both sides
5. Public go-live only after that retest passes.

## Go-live gate

Must be done before go-live:

- A deployed (447 in git, socket stays up when a marker write fails)
- B deployed (448, anon and authenticated cannot execute the 16 admin RPCs)
- D deployed (reconcile loop runs)
- C only if no-offer or no-re-offer reproduces with separate rider and driver accounts
- One clean monitored retest, criteria above

Can follow in the first week: E except the controlled retest itself (the retest is part of the gate), F, Meta CAPI, the map snapshot retry, and the low items in F17.

## Rules from this incident

- No out-of-band production SQL. If an emergency function replace happens again, the matching numbered migration is committed within the hour. F9 does not repeat.
- Any migration that touches an id column is tested on real Postgres using production types. Here, `drivers.id` is TEXT.
- A regulated write that fails (insurance period) is not answered as HTTP 200 without an alert.
- Sentry `lastSeen` on a grouped issue is not the end of the incident. Check Postgres or the log drain before calling a burst over.
- Do not add Sentry issue counts for the same exception. One marker failure is captured as a request error and again as a log line.
