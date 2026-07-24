# Spinr — Complete Repository Code Review

Full audit of all five surfaces (backend, rider-app, driver-app, admin-dashboard, shared) plus repo-wide sweeps for money, security, migrations/RLS, PIPEDA/legal compliance, and observability. Findings were produced by 25 domain reviewers reading the actual code; the criticals below were re-verified by reading the cited source.

**301 findings after de-duplication** — 16 critical, 103 major, 182 minor.

| Category | Count |
|---|---|
| Correctness / bug | 71 |
| Financial loss | 47 |
| Security | 42 |
| User experience | 42 |
| Reporting / observability | 34 |
| Compliance (PIPEDA) | 31 |
| Legal / regulatory | 13 |
| Performance | 13 |
| Process / tooling | 8 |


---

## Critical (16)

### 1. Circuit-breaker half-open probe leaks permanently on deadline abort, wedging every DB call in 503
`backend/repositories/_base.py:228` · **CRITICAL** · Correctness / bug · _auth-identity_

**Issue.** When the circuit breaker releases its single half-open probe and that probe request aborts via the client-deadline path (wait_for timeout or the in-loop deadline pre-check), neither record_success() nor record_failure() is called, so _probe_in_flight stays True forever and should_allow() returns False for every future call — the entire API returns 503 on all DB operations until the process restarts.

**Evidence.** should_allow() sets `self._probe_in_flight = True` when transitioning open→half_open (lines 92-96) and only record_success/record_failure reset it (lines 104-125). In run_sync, the deadline-timeout path does `future.cancel(); ... raise ServiceUnavailableException("database") from None` (lines 225-228) and the in-loop deadline check raises the same (lines 211-213); both are re-raised untouched by `except ServiceUnavailableException: raise` (lines 237-238) — bypassing the breaker bookkeeping at lines 300-309 entirely. Same for the bare `if isinstance(exc, ValueError): raise` at 240-241. With the DB slow enough to have opened the breaker, the probe hanging past the client deadline (shared/api/client.ts sends X-Deadline-Ms on requests) is the *likely* outcome, not an edge case. Once leaked, no call ever reaches record_success, so the breaker can never close.

**Fix.** Wrap the probe accounting in a try/finally: on any exit from run_sync after should_allow() returned True in half-open (or open→half_open) state without a definitive success, call _breaker.record_failure() (returns state to open with a fresh OPEN_DURATION). Simplest: add a `_breaker.release_probe()` that clears _probe_in_flight, and call it from the deadline-timeout / deadline-exhausted / ValueError early-exit paths.

### 2. Admin wallet credit uses non-atomic read-modify-write on balance
`backend/routes/admin/wallet.py:144` · **CRITICAL** · Financial loss · _x-money-sweep_

**Issue.** admin_credit_wallet reads wallet.balance, computes new_balance in Python, then writes it back with only {"id": wallet["id"]} as the filter — no compare-and-swap on the balance value and no row-locking RPC — so a concurrent write to the same wallet (another admin action, a ride payment, or a cancellation-fee debit) can be silently overwritten (lost update).

**Evidence.** old_balance = _q(wallet.get("balance", 0)); credit = _q(req.amount); new_balance = old_balance + credit; await db.update_one("wallets", {"id": wallet["id"]}, {"$set": {"balance": _q(new_balance), ...}}) — the update filter does not include the balance that was read, unlike the atomic `wallet_pay_for_ride` RPC used at backend/routes/wallet.py:282 which explicitly re-fetches post-RPC because a concurrent debit under its FOR UPDATE lock can race a naive read.

**Fix.** Route all wallet balance mutations (credit, debit, cancellation fee, no-show fee) through a single Postgres function with row-level locking (mirroring corporate_wallet_apply_delta), or add an optimistic-concurrency filter {"id": wallet_id, "balance": old_balance} and retry on 0-row match.

### 3. Admin wallet debit has the same lost-update race as credit
`backend/routes/admin/wallet.py:232` · **CRITICAL** · Financial loss · _x-money-sweep_

**Issue.** admin_debit_wallet performs the identical read-then-write pattern as admin_credit_wallet: balance sufficiency is checked against a stale in-memory old_balance and the write has no compare-and-swap guard, so a concurrent debit/credit on the same wallet can result in an incorrect final balance or a debit succeeding against a balance that no longer exists.

**Evidence.** old_balance = _q(wallet.get("balance", 0)); ... new_balance = old_balance - debit; await db.update_one("wallets", {"id": wallet["id"]}, {"$set": {"balance": _q(new_balance), ...}})

**Fix.** Same as the credit endpoint — use an atomic RPC or optimistic-concurrency WHERE clause on balance.

### 4. Standard payout: TOCTOU balance race and no compensation if DB insert fails after Stripe transfer
`backend/routes/drivers/payouts.py:590` · **CRITICAL** · Financial loss · _drivers_

**Issue.** Two concurrent /payouts requests can both pass the balance check and double-withdraw the same earnings, and a payouts-row insert failure after a successful Stripe Transfer leaves the transfer unrecorded so the balance never decreases and can be withdrawn again.

**Evidence.** Balance is read at line 590 (`balance = await earnings.get_driver_balance(...)`), Stripe Transfer.create runs at line 623, and the payouts row is only inserted at line 656 with no reversal/compensation on failure (contrast request_instant_payout lines 808-826 which reverses on persist failure). Nothing serializes concurrent payout requests — @idempotent_endpoint only dedupes identical retried requests.

**Fix.** Reserve the amount atomically first (insert the payouts row status='pending' before the transfer, or use a DB-side claim like corporate_wallet_apply_delta), transfer second, and reverse the transfer if the terminal write fails, as the instant path does.

### 5. Driver-cancel endpoint has no ownership check — any driver can cancel any ride
`backend/routes/drivers/ride_cancel.py:57` · **CRITICAL** · Security · _x-security-sweep_

**Issue.** POST /rides/{ride_id}/cancel (driver-side) never verifies that the calling driver is the ride's assigned driver, so any authenticated driver can cancel any other driver's or an unassigned ride.

**Evidence.** `ride = await db_supabase.get_ride(ride_id)` (line 57) has no driver_id filter, and `_cancel_filter = {"id": ride_id, "status": {"$in": [...]}}` (lines 79-91) also omits `driver_id: driver["id"]`. Compare with the sibling `mark_rider_noshow` in the same file (line 190): `if ride.get("driver_id") != driver["id"]: raise HTTPException(status_code=403, detail="Not your assigned ride")` — that check is present there but entirely missing from `cancel_ride`.

**Fix.** Add `if ride.get("driver_id") != driver["id"]: raise HTTPException(status_code=403, detail="Not your assigned ride")` before the cancel branch, and add `driver_id: driver["id"]` to `_cancel_filter` so the atomic update is also ownership-scoped.

### 6. Any driver can cancel any other user's pre-trip ride (no ownership check)
`backend/routes/drivers/ride_cancel.py:79` · **CRITICAL** · Security · _drivers_

**Issue.** cancel_ride never verifies the ride belongs to the calling driver, so any authenticated driver can cancel any ride in a pre-trip state by ride_id.

**Evidence.** The handler loads the driver and the ride but the atomic update filter is only `_cancel_filter = {"id": ride_id, "status": {"$in": [...pre-trip states]}}` — no `driver_id` condition and no `ride.get("driver_id") != driver["id"]` guard, unlike mark_rider_noshow (line 190) and rate_rider (line 390). The caller then sets THEMSELVES available and records a period-1 transition for themselves.

**Fix.** Reject with 403/404 when ride['driver_id'] != driver['id'] (allow only the assigned driver), and include driver_id in the atomic cancel filter.

### 7. Wrong-driver cancel corrupts SGI insurance period audit trail for an uninvolved driver
`backend/routes/drivers/ride_cancel.py:132` · **CRITICAL** · Legal / regulatory · _x-security-sweep_

**Issue.** Because cancel_ride is exploitable by any driver (see IDOR finding above), `record_period_transition(driver["id"], 1)` records an insurance-period transition for the attacking driver — who was never in period 2 for this ride — while the real assigned driver's period-2 row is left open forever (append-only ledger never closed), corrupting the regulatory audit trail required by CLAUDE.md's insurance-period rules.

**Evidence.** `await _deps.record_period_transition(driver["id"], 1)` at line 132 uses the calling driver's id, not the ride's real `driver_id`, and this function has no verification that `driver["id"] == ride.get("driver_id")`.

**Fix.** Fix the ownership check in cancel_ride (see prior finding); once fixed, `driver["id"]` will always equal `ride["driver_id"]`, closing this gap as a side effect.

### 8. Rider wallet no-show fee debit has the same lost-update race
`backend/routes/drivers/ride_cancel.py:265` · **CRITICAL** · Financial loss · _x-money-sweep_

**Issue.** The no-show fee charge to the rider's wallet reads balance, computes new_balance, and writes back with only an id filter — same TOCTOU gap as the cancellation-fee and admin wallet paths, risking silently dropped or double-applied charges under concurrency.

**Evidence.** old_balance = Decimal(str(rider_wallet.get("balance", 0))); new_balance = max(old_balance - total_fee, Decimal("0")); ... await db_supabase.update_one("wallets", {"id": rider_wallet["id"]}, {"balance": float(new_balance.quantize(Decimal("0.01"))), ...})

**Fix.** Same as other wallet-balance sites — atomic RPC or optimistic-concurrency filter.

### 9. Active-ride rider object leaks password_hash, fcm_token and other users-row secrets to the driver app
`backend/routes/drivers/ride_reads.py:149` · **CRITICAL** · Security · _drivers_

**Issue.** get_active_ride returns the full rider users row minus only phone/email/stripe_customer_id, exposing password_hash, FCM push tokens, session/auth state and any other column to any driver on a ride.

**Evidence.** `safe_rider = {k: raw[k] for k in raw if k not in {"phone", "email", "stripe_customer_id"}}` is a blocklist over `get_user_by_id`, which does `supabase.table("users").select("*")` (backend/repositories/auth_repo.py:51) — so every other users column ships to the driver client.

**Fix.** Switch to an allowlist projection (e.g. first_name, photo_url, rating, id) instead of a three-field blocklist.

### 10. Rider wallet cancellation-fee debit is a non-atomic read-modify-write
`backend/routes/rides/cancellation.py:136` · **CRITICAL** · Financial loss · _x-money-sweep_

**Issue.** cancel_ride_rider debits the rider's wallet for the cancellation fee by reading balance, computing new_balance in Python, then writing with only {"id": rider_wallet["id"]} as the filter — a concurrent wallet mutation (e.g. a wallet top-up webhook or another ride's fee) landing between the read and write is lost.

**Evidence.** old_balance = _round(_d(rider_wallet.get("balance", 0))); new_balance = max(_round(old_balance - total_cancel_fee), Decimal("0")); ... await _deps.db_supabase.update_one("wallets", {"id": rider_wallet["id"]}, {"balance": _f(new_balance), ...})

**Fix.** Move this debit through the same locked RPC used by wallet_pay_for_ride, or add optimistic concurrency on the balance column.

### 11. Booking-time card pre-auth hold is never released on cancellation; fee charge overwrites the hold's PI reference
`backend/routes/rides/cancellation.py:268` · **CRITICAL** · Financial loss · _rides-booking_

**Issue.** When a card ride is cancelled (rider cancel or 5-min no-driver auto-cancel), the manual-capture hold of grand_total + RIDE_AUTH_BUFFER_CAD placed at booking is never cancelled, and if a cancellation fee was card-charged, the ride row's payment_intent_id is overwritten with the fee PI, destroying the only stored reference to the hold.

**Evidence.** booking.py places the hold before insert (`_preauthorize_ride_card` → `authorize_ride`, fields `payment_intent_id`/`auth_status='authorized'`). `cancel_ride_rider` never calls `cancel_authorization` (grep shows its only caller is payment_service.py:840, the Change-Card override). cancellation.py lines 264-268: `_base_update["payment_status"] = cancel_fee_payment_status; _base_update["payment_intent_id"] = cancel_fee_payment_intent_id` — the booking hold's PI is replaced while auth_status stays 'authorized'. No sweeper covers it: utils/preauth_capture.py:121 scans only `{"status": "completed", "payment_status": "pending", ...}` and utils/payment_retry.py:222 explicitly excludes `status != CANCELLED`. matching.py ride_search_timeout (line 1296) likewise cancels with no hold release.

**Fix.** In cancel_ride_rider and ride_search_timeout, before overwriting any payment fields, read the ride's payment_intent_id/auth_status and call cancel_authorization(ride_id, payment_intent_id) when auth_status is 'authorized'/'fare_only', then set auth_status='released'. Store the cancellation-fee PI in a separate column (e.g. cancel_fee_payment_intent_id) instead of overwriting payment_intent_id. Add a reconciliation sweep for cancelled rides with auth_status still open.

### 12. /rate credits unbounded, uncharged tips into driver_earnings on every call
`backend/routes/rides/rating.py:64` · **CRITICAL** · Financial loss · _rides-secondary_

**Issue.** POST /rides/{id}/rate accumulates tip_amount into ride.tip_amount and driver_earnings on every call, with no server-side dedupe, no idempotency decorator, no payment-status check, and no upper bound on the tip — the tip is never charged to the rider by this endpoint.

**Evidence.** rating.py lines 64-87: `if rating_data.tip_amount > 0: ... new_tip = _round(_d(ride.get("tip_amount") or 0) + tip_delta); new_driver_earnings = _round(_d(ride.get("driver_earnings") or 0) + tip_delta); await _deps.db_supabase.update_ride(...)` — no settle_* call, no check of ride.payment_status, no check that the ride was already rated. Unlike /tip (payments.py:54 `@idempotent_endpoint`, line 79 ERR_TIP_DUPLICATE) and TipRequest (`le=500`), schemas.py:421 `tip_amount: DecimalStr = Field(default=Decimal("0.0"), ge=0, ...)` has NO maximum. The rider app itself documents the hole (ride-completed.tsx:238 "/rate ... ACCUMULATES tip into driver_earnings on every call ... driver is over-credited while only one tip is charged (Codex P1)") but the fix is client-side only; the offline queue in rideStore.ts:590 can still replay a rate_ride request after the screen already posted it. A colluding rider+driver can call /rate repeatedly with tip_amount=999999: driver_earnings (and the T4A-feeding driver_earnings_snapshot rebuilt via build_earnings_snapshot at lines 75-86) inflate without any charge; on an already-paid ride the tip is simply never collected.

**Fix.** In rate_driver: (1) reject the request (or ignore the tip) if ride.rider_rating is already set or ride.tip_amount > 0 (mirror ERR_TIP_DUPLICATE); (2) add le=500 to RideRatingRequest.tip_amount in schemas.py; (3) only record the tip when payment_status is still pending/failed (i.e. it will be collected by process-payment), otherwise return a structured error directing the client to the charged tip flow; (4) add @idempotent_endpoint(scope="ride_rate").

### 13. Mid-trip stop add/remove never recomputes grand_total, taxes, or the fare snapshot — the amount actually billed never changes
`backend/routes/rides/stops.py:70` · **CRITICAL** · Financial loss · _rides-booking_

**Issue.** Adding or removing a stop updates total_fare/estimated_fare/distance_fare/time_fare on the ride but leaves grand_total, tax_amount, area fees, driver_earnings, admin_earnings, and fare_breakdown_snapshot untouched, and settlement charges grand_total — so the rider is billed the pre-edit amount while the driver is shown (and earns via the earnings snapshot) the post-edit fare.

**Evidence.** stops.py add_stop_mid_trip writes only `{**fare_update, "stops": stops, "updated_at": ...}` where fare_update from `_reestimate_fare_for_stops` contains distance_km, duration_minutes, distance_fare, time_fare, estimated_fare, total_fare — no grand_total/tax. Settlement uses grand_total first: services/payment_service.py:203 `total = _round(_d(ride.get("grand_total") or ride.get("total_fare") or 0))` (also lines 301, 555). Driver earnings snapshot at completion is rebuilt from base+distance+time fares (lifecycle.py:246), which WERE updated — so driver earns the new higher fare while the rider pays the old grand_total; with 0% commission the platform eats the difference. GST/PST also stay computed on the old subtotal.

**Fix.** In _reestimate_fare_for_stops (or the stops endpoints), recompute area fees and taxes via calculate_all_fees on the new total_fare, write updated tax_amount/tax_breakdown/grand_total and driver_earnings/admin_earnings, and refresh fare_breakdown_snapshot (or mark it invalidated) in the same update.

### 14. Safety-team notification dead in production: notify_safety_team missing from fallback import branch
`backend/routes/safety.py:24` · **CRITICAL** · Correctness / bug · _safety-privacy_

**Issue.** The except-ImportError branch (the one that actually runs in production) never imports notify_safety_team, so every POST /safety/report raises NameError at the notify call and the safety team is never alerted via WS/email/CRITICAL log.

**Evidence.** Lines 19-27: the try branch imports `from ..features import notify_safety_team` but the except branch imports only db_supabase, get_current_user, and create_ticket_for_safety. server.py:143 imports `from routes.safety import api_router` (top-level), so the relative `from .. import db_supabase` raises ImportError and the fallback branch is what runs. Line 107 `await notify_safety_team(incident)` then raises NameError, swallowed by the broad `except Exception` at line 109 which only logs — the endpoint still returns success. The WS broadcast, email fan-out, and CRITICAL on-call paging log in features.py notify_safety_team (line 1742) never execute for any user-submitted safety report.

**Fix.** Add `from features import notify_safety_team` to the except branch (and add a regression test that imports routes.safety in top-level mode and asserts notify_safety_team is bound).

### 15. Unverified email accepted at profile creation is trusted as a corporate-billing identity
`backend/routes/users.py:67` · **CRITICAL** · Financial loss · _core-infra_

**Issue.** POST /users/profile stores any email the caller types with only a uniqueness check (no ownership verification), and that email is later trusted by /corporate/join-domain as 'proof of employment', letting an attacker join a company's corporate account and bill rides to it.

**Evidence.** users.py:67-104 sets `email_lower = request.email.strip().lower()` and writes it after only checking no other user has it — no OTP/link verification. routes/corporate_rider.py:143-159 then does `user_email = (... current_user.get("email") ...)`, matches its domain against `corporate_allowed_domains`, and calls `join_via_domain(...)` whose docstring says 'the domain match is itself proof of employment'. The comment at corporate_rider.py:142 claims the email is 'JWT-sourced identity' but it was never verified.

**Fix.** Require email ownership verification (send a code/link, e.g. reuse the existing hash_otp email-OTP machinery) before persisting users.email, or at minimum store an email_verified flag and make /corporate/join-domain and /auth/verify-email-otp only trust verified emails.

### 16. Allowance-covered ride settlement CREDITS the corporate master wallet instead of debiting it
`backend/services/payment_service.py:419` · **CRITICAL** · Financial loss · _wallet-corporate_

**Issue.** settle_corporate uses corporate_allowance_service.apply_rollback for the allowance-covered fare portion, and the RPC maps 'allowance_rollback' to a POSITIVE master delta, so every allowance-covered corporate ride adds the fare to the company's wallet balance instead of charging it.

**Evidence.** payment_service.py:419 `await corporate_allowance_service.apply_rollback(... amount=_f(allowance_debit), notes=f"ride:{ride_id}:allowance")`; migrations/29_corporate_allowance_rpc.sql lines 68-77: `-- rollback: master +p_amount, used +p_amount (undo a prior grant)` / `v_master_delta := p_amount;`. For a member with base allowance amount=500, used=0 and a $50 ride, master balance goes UP $50 and used goes to 50 — the company is never charged; net company funds even increase for base-allowance rides. Only the master_fallback portion (apply_adjustment, delta negative, line 430-432) actually debits. Tests (test_corporate_ride_payment.py) mock apply_rollback so the sign is never exercised.

**Fix.** Introduce a proper 'ride_debit' path: call corporate_wallet_apply_delta with type='ride_debit', delta=-allowance_debit, ride_id set, plus a member-scoped used+=allowance_debit update in one RPC (extend migration 29 with a 'ride_debit' branch: master -p_amount, used +p_amount). Reserve 'allowance_rollback' for genuine grant undo. Backfill/audit existing corporate wallet balances.


---

## Major (103)

### 1. Installed pre-commit hook only warns (never blocks) on float money arithmetic, contradicting documented guarantee
`.git/hooks/pre-commit:93` · **MAJOR** · Process / tooling · _x-money-sweep_

**Issue.** CLAUDE.md and domain-payments.md both state a pre-commit hook 'blocks float arithmetic in fare_service.py and routes/payments.py', but the actually-installed hook only prints a YELLOW warning and always exits 0 for the float-money check, so float money math can be committed and merged undetected.

**Evidence.** FLOAT=$(git diff --cached -U0 -- '*.py' '*.ts' | grep "^+" | grep -iE "(fare|price|amount|earning|payout)\s*[+\-\*\/]\s*[0-9]*\.[0-9]+"); if [ -n "$FLOAT" ]; then echo -e "${YELLOW}  WARNING — possible float money arithmetic...${NC}"; else ... fi — this branch never sets BLOCKED=1, unlike the secrets/forbidden-file/PII checks in the same script.

**Fix.** Make the float-money check set BLOCKED=1 (or add a dedicated blocking check scoped to fare_service.py/routes/payments.py as documented), or update the docs to accurately describe it as a warning-only heuristic.

### 2. Watchdog registration and heartbeat coverage gaps leave many loops unmonitored
`backend/core/lifespan.py:434` · **MAJOR** · Reporting / observability · _background-loops_

**Issue.** Nine spawned loops are absent from _WATCHDOG_LOOP_NAMES (safety_checkin, reconciliation, preauth_capture, referral_payout, driver_claim_reaper, route_finalizer, route_gap_monitor, suspension_reactivation, zoho_desk_sync) and three registered names never record a heartbeat (subscription_expiry, t4a_annual_job, push_retry), so the stale-loop alerter can never fire for payment-, dispatch-, and safety-critical loops — several of which dutifully record heartbeats that nothing ever checks.

**Evidence.** lifespan.py lines 434-453 list 17 names; check_and_alert (loop_alert.py line 51) iterates only registered names. Grep shows referral_payout.py:120 and route_gap_monitor.py:198 record heartbeats not in the list; routes/drivers/subscriptions.py check_expiring_subscriptions, utils/t4a_annual_job.py and utils/push_retry.py contain no record_heartbeat call at all despite being registered, so they sit in 'never_ticked' forever; safety_checkin_loop.py:70 records under the unregistered name "safety_checkin_loop".

**Fix.** Make registration self-derive from _spawn (register every spawned name), add record_heartbeat calls to subscription_expiry, t4a, push_retry, fix safety_checkin's name to match, and add a test asserting the watchdog list equals the spawned-loop set.

### 3. App Check enforcement (ENV=production) breaks the public trip-share tracking page
`backend/core/middleware.py:85` · **MAJOR** · User experience · _auth-identity_

**Issue.** The browser tracking page at track.spinr.ca fetches `/api/v1/rides/track/{share_token}` directly from the backend, but that path is not in _APP_CHECK_EXEMPT_PREFIXES, so with enforcement_enabled=is_production every fetch returns 401 'App Check token required' and the shareable live-trip link — a safety feature — is dead in production.

**Evidence.** admin-dashboard/src/app/track/[rideId]/page.tsx line 88: `await fetch(\`${apiUrl}/api/v1/rides/track/${shareToken}\`)` (polled every 5 s, line 99). routes/rides/sharing.py line 171: `@router.get("/track/{share_token}")` mounted under prefix `/rides` → `/api/v1/rides/track/...`. middleware.py _APP_CHECK_EXEMPT_PREFIXES (lines 85-132) contains no `/api/v1/rides` entry; FirebaseAppCheckMiddleware.dispatch returns 401 on missing token when enforcing (lines 172-184). A browser cannot mint an X-Firebase-AppCheck token.

**Fix.** Add `/api/v1/rides/track/` to _APP_CHECK_EXEMPT_PREFIXES (the endpoint is already authorised by the unguessable share token), and add a regression test asserting a token-less GET to /api/v1/rides/track/x passes App Check in enforcement mode.

### 4. RelativeRedirectMiddleware rewrites legitimate external redirects (Supabase signed Storage URLs) into broken same-host paths
`backend/core/middleware.py:694` · **MAJOR** · Correctness / bug · _auth-identity_

**Issue.** The middleware relativizes ANY absolute http(s) Location on 301/302/307/308, including redirects that intentionally point off-host; the legacy driver-document path returns RedirectResponse(doc['document_url']) where document_url is an absolute Supabase Storage signed URL, so the browser is redirected to /storage/v1/object/... on the API host and gets a 404 — admins cannot open those driver documents, blocking onboarding review.

**Evidence.** middleware.py lines 692-699: `if location.startswith("http"): ... response.headers["location"] = relative` — no check that the host is our own backend. documents.py line 1001: `return RedirectResponse(doc["document_url"])`; those URLs come from Supabase create_signed_url (documents.py lines 248-269, _extract_signed_url), which are absolute `https://<project>.supabase.co/storage/...` URLs.

**Fix.** Only strip scheme+host when the Location host matches the request host (or a configured internal host list): `parsed = urlparse(location); if parsed.netloc in {request.url.netloc, <internal hosts>}: relativize`, otherwise leave the absolute URL untouched.

### 5. DeadlineMiddleware trusts the client clock — a device with a skewed clock gets 503 on every request
`backend/core/middleware.py:731` · **MAJOR** · Correctness / bug · _auth-identity_

**Issue.** X-Deadline-Ms is computed client-side as Date.now()+timeout (shared/api/client.ts:28) and compared to the server clock with no skew guard or minimum floor; a phone whose clock is behind the server by more than the axios timeout produces an already-expired deadline on every request, so run_sync rejects every DB call with ServiceUnavailableException (503) and the app is completely unusable for that user, with no error indicating why.

**Evidence.** middleware.py lines 722-733: `remaining_ms = deadline_epoch_ms - now_epoch_ms; monotonic_deadline = _t.monotonic() + (remaining_ms / 1000.0)` — negative remaining is stored unconditionally. _base.py lines 193-196 then raise ServiceUnavailableException before any work. shared/api/client.ts line 28: `'X-Deadline-Ms': String(Date.now() + timeoutMs)` — pure device wall-clock. Devices with manual time / dead RTC batteries / disabled NTP are a real population; retries never recover because each retry recomputes from the same skewed clock.

**Fix.** Clamp server-side: if remaining_ms <= 0 (or absurdly small/large, e.g. outside 0..120000), treat the header as skewed and either ignore it or floor remaining to a sane minimum (e.g. min 2s, max the known client timeout). The request's arrival itself proves the client had not given up at send time.

### 6. corporate_accounts RLS policy left out of the P0 FOR ALL remediation
`backend/migrations/17_corporate_accounts_fk.sql:122` · **MAJOR** · Security · _x-migrations_

**Issue.** corporate_accounts (billing entity: credit_limit, contact_email/phone) still carries the exact 'FOR ALL TO authenticated, role = admin only (no super_admin, no WITH CHECK)' policy pattern that migration 142 explicitly identified and fixed as a P0 on nine other corporate tables plus disputes — corporate_accounts was never included in that sweep.

**Evidence.** 17_corporate_accounts_fk.sql:122-131: `CREATE POLICY "Admin full access corporate_accounts" ON corporate_accounts FOR ALL TO authenticated USING (... users.role = 'admin')`. Compare 142_fix_rls_financial_tables.sql:66-113 which drops this exact anti-pattern on nine other tables ('Migration 27 dynamically created "Admin full access <table>" FOR ALL TO authenticated on nine tables ... corporate_wallets and corporate_wallet_transactions are direct money-mutation vectors'), and 224/225 which both note 'RLS posture unchanged (corporate_accounts RLS from migration 17)' without revisiting it.

**Fix.** Add a new migration that drops 'Admin full access corporate_accounts' and replaces it with enumerated SELECT-only (admin+super_admin) plus REVOKE INSERT/UPDATE/DELETE from authenticated, matching the pattern already applied to the other nine corporate tables in migration 142.

### 7. New table reintroduces the FOR ALL RLS anti-pattern retired by migration 142
`backend/migrations/206_corporate_sections.sql:73` · **MAJOR** · Security · _x-migrations_

**Issue.** corporate_sections, created 64 migrations after the P0 fix in 142, recreates the same banned pattern: FOR ALL TO authenticated with only role = 'admin' (excluding super_admin), violating the explicit CLAUDE.md house rule ('Policies enumerate SELECT, INSERT, UPDATE, DELETE — never FOR ALL on user-writable tables') that migration 142 was written specifically to enforce.

**Evidence.** 206_corporate_sections.sql:73-75: `EXECUTE 'CREATE POLICY "Admin full access corporate_sections" ON corporate_sections FOR ALL TO authenticated USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text AND users.role = ''admin''))'`. This is the identical shape 142_fix_rls_financial_tables.sql:66-90 tore out of nine other corporate tables and called a P0.

**Fix.** Replace with enumerated SELECT (admin+super_admin) policy and rely on service_role for writes, consistent with 142's pattern and 195_ride_live_activities.sql's later comment 'Explicit per-operation policies (house rule bans FOR ALL on user-data tables)'; also fix the missing super_admin role check.

### 8. Admin idle-session timeout silently disabled on DB write failure, not surfaced
`backend/migrations/233_admin_staff_last_activity_at.sql:8` · **MAJOR** · Security · _x-migrations_

**Issue.** The migration's own incident note describes the admin idle-timeout security control being silently skipped ('the write is soft-handled (warning + continue)... silently disables the idle-timeout security control'), and the current backend code (backend/dependencies/__init__.py) still has this exact soft-fail behavior even after the column was added — both the malformed-timestamp path and the update-failure path swallow the error with logger.warning and let the admin request through, violating CLAUDE.md's explicit rule to never warn-and-continue on auth errors.

**Evidence.** backend/migrations/233_admin_staff_last_activity_at.sql:8-11 documents the incident. backend/dependencies/__init__.py:325-332: `except Exception as _ts_err: logger.warning(f"Malformed last_activity_at for staff {user_id} — letting through: {_ts_err}")` and `except Exception as _upd_err: logger.warning(f"Could not update last_activity_at for staff {user_id}: {_upd_err}")` — both paths let the admin request proceed instead of surfacing the failure, so a persistent DB write failure means last_activity_at is never advanced or is silently ignored, defeating the 30-minute idle-timeout control indefinitely without any error-level alert.

**Fix.** Use logger.error with full exception detail and consider fail-closed (or a bounded fail-open with a Sentry alert tagged domain=admin) instead of a silent warning; add a metric/alert so repeated failures to persist last_activity_at get noticed rather than silently degrading the security control.

### 9. allowance_reset destroys granted-but-unspent funds without crediting master
`backend/migrations/29_corporate_allowance_rpc.sql:72` · **MAJOR** · Financial loss · _wallet-corporate_

**Issue.** A grant moves money out of the master wallet (master -amount, used -amount), but the period reset zeroes `used` with master delta 0, so any granted-but-unspent top-up (negative `used`) vanishes from the ledger at period end instead of being returned to the master wallet.

**Evidence.** Lines 69-74: grant → `v_master_delta := -p_amount; v_used_delta := -p_amount`; reset → `v_master_delta := 0; v_used_delta := -v_used`. If a $100 grant (master -100, used=-100) is unspent at period end, apply_reset sets used=0 with no master credit — the $100 is gone from both master balance and the member earmark.

**Fix.** In the allowance_reset branch, when v_used < 0 credit the master wallet with -v_used (return the unspent earmark): `v_master_delta := GREATEST(-v_used, 0)`, keeping v_used_delta := -v_used, and write both ledger rows.

### 10. Driver DB read error silently swallowed (fail-open, no log)
`backend/onboarding_status.py:129` · **MAJOR** · Compliance (PIPEDA) · _x-observability_

**Issue.** A failure of get_rows('drivers') is caught and treated as driver=None with no logging, which both hides the DB error and can make an existing driver's onboarding status silently resolve to None (skipped) instead of surfacing loudly.

**Evidence.** Lines 125-134: `except Exception: driver = None` then `if not user.get('is_driver') and not driver: return None, None, None`. Per CLAUDE.md 'Do not silently swallow errors' a DB failure must be logged at error level with the underlying exception and surfaced (503), never turned into a generic 'no driver' fallback path.

**Fix.** Let DatabaseError propagate (or catch, `logger.error(..., exc_info=True)` including `e.details['original']`, and raise HTTPException 503) instead of masking it as driver=None.

### 11. Driver 'reject' action accepted by the request model but has no handler branch — always 400
`backend/routes/admin/drivers.py:1392` · **MAJOR** · Correctness / bug · _admin-routes_

**Issue.** POST /admin/drivers/{id}/action with action='reject' passes Pydantic validation but falls into the else branch and returns 400 'Unknown action: reject', so admins cannot reject applications via the documented lifecycle endpoint.

**Evidence.** Line 116: `action: Literal["approve", "reject", "suspend", "ban", "unban", "reactivate"]`; the handler (lines 1392-1437) has elif branches only for approve/suspend/ban/unban/reactivate, then `raise HTTPException(400, f"Unknown action: {req.action}")`. The docstring (line 1380), action_titles (line 1450) and push map (line 1488) all advertise reject.

**Fix.** Add an `elif req.action == "reject":` branch that sets status='rejected' (or 'needs_review'), stores rejection_reason, and requires a reason like suspend/ban.

### 12. Broadcast notification awaits up to 10,000 sequential pushes inside the request handler
`backend/routes/admin/faqs.py:166` · **MAJOR** · Performance · _admin-routes_

**Issue.** POST /admin/notifications/send with audience all/riders/drivers loops `await send_push_notification` per user in-request with no background task or concurrency, so the request takes minutes and times out mid-broadcast, leaving delivery partially complete with no resume.

**Evidence.** Lines 166-180: `all_users = await db.get_rows("users", {}, columns="id", limit=10000)` then `for u in all_users or []: await send_push_notification(u["id"], title, body)`. Contrast messaging.py which uses BackgroundTasks + asyncio.gather + Semaphore(50) and returns 202. This is also the CLAUDE.md anti-pattern 'awaiting provider calls inline in a request handler'.

**Fix.** Fan out via BackgroundTasks with bounded concurrency (mirror messaging._fan_out), or delegate this endpoint to the cloud-messaging path.

### 13. ToS/Privacy edits overwrite content in place with no version history and no audit log
`backend/routes/admin/legal_documents.py:58` · **MAJOR** · Compliance (PIPEDA) · _admin-routes_

**Issue.** admin_upsert_legal_document bumps `version` but replaces `content` on the same row, destroying the text of every prior version users consented to, and records no audit trail of who changed the legal documents.

**Evidence.** Lines 58-65: `next_version = int(existing.get("version") or 1) + 1; update_one("legal_documents", {"id": existing["id"]}, {"content": content, "version": next_version, ...})`. No log_admin_action anywhere in the file, and no handler receives the admin identity. CLAUDE.md: consent language version is stored on signup and material changes require re-consent — a PIPEDA/consent audit cannot reproduce what version N said, and the find-then-insert upsert (lines 52-76) can also race into duplicate (audience, doc_type) rows.

**Fix.** Append a new versioned row per edit (keep old rows immutable), add a unique constraint on (audience, doc_type, version), and log_admin_action with the admin actor.

### 14. Driver daily rollup discovers online drivers from only the first 10k GPS points of the day
`backend/routes/admin/maintenance.py:176` · **MAJOR** · Reporting / observability · _admin-routes_

**Issue.** The rollup identifies 'drivers online that day' by fetching at most 10,000 driver_location_history rows ordered by timestamp ascending — roughly the first hour of a busy day — so drivers who were online later without completing rides get no driver_daily_stats row, corrupting online_minutes and the driver-utilization KPI.

**Evidence.** Lines 176-187: `presence_rows = await db_supabase.get_rows("driver_location_history", {"timestamp": {...}}, order="timestamp", columns="driver_id", limit=10000)`. A single driver emits well over 10k points/day at typical 3-5 s intervals, so the cap cannot cover 'all active drivers' as the comment claims.

**Fix.** Get distinct driver_ids via SQL (SELECT DISTINCT driver_id ... or an RPC) instead of fetching raw rows, mirroring the existing compute_driver_phase_distances server-side approach.

### 15. Marketing suppression deletion claims to be audited but writes no audit row
`backend/routes/admin/messaging.py:477` · **MAJOR** · Compliance (PIPEDA) · _admin-routes_

**Issue.** DELETE /admin/marketing/suppressions/{id} re-enables marketing to a bounced/complained/unsubscribed address with no audit_logs record of who lifted it, despite the docstring asserting 'The deletion is audited via the standard admin audit trail' — a CASL exposure with no accountability trail.

**Evidence.** Lines 477-489: the handler does find_one + delete_many + `return {"success": True}`; there is no log_admin_action or audit_logs insert anywhere in messaging.py, and the router mount grants this to any admin with the notifications module.

**Fix.** Log an audit row (actor, channel, masked target, original suppression reason) before deleting, and consider restricting bounce/complaint-sourced deletions to super_admin.

### 16. valid_from, valid_days, valid_hours, applicable_areas, user_segments accepted at creation but never enforced at redemption
`backend/routes/admin/promotions.py:44` · **MAJOR** · Correctness / bug · _fares-promos_

**Issue.** Admin-created promos can carry a start date, day-of-week/hour windows, area lists and user segments, but _validate_promo_for_user and list_available_promos check none of them, so riders can redeem a promo before its valid_from date, outside its scheduled days/hours, and outside its intended areas.

**Evidence.** PromotionCreateRequest persists valid_from/valid_days/valid_hours_start/valid_hours_end/applicable_areas/user_segments (lines 194, 205-216); routes/promotions.py _validate_promo_for_user rules 1-10 (lines 125-238) and list_available_promos (lines 481-587) only ever read expiry_date, service_area_id, uses, assigned_user_ids, first_ride_only, new_user_days, inactive_days, min/max_total_rides, total_budget — applicable_areas is also distinct from the service_area_id column the area filter uses (line 454), so area-restricted promos created via this admin surface behave as global.

**Fix.** Enforce valid_from, valid_days/valid_hours (in area local time), and applicable_areas in _validate_promo_for_user and list_available_promos, or reject those fields at creation until they are enforced.

### 17. Insert-failure fallback silently strips private-promo targeting fields
`backend/routes/admin/promotions.py:233` · **MAJOR** · Financial loss · _fares-promos_

**Issue.** If the promotions insert fails for any reason, the retry re-inserts without assigned_user_ids/inactive_days/min_total_rides/max_total_rides, so a promo intended for specific users or segments is silently created with no targeting and becomes redeemable by everyone.

**Evidence.** Lines 233-243: 'except Exception: ... retrying without them; row = await db_supabase.insert_one("promotions", doc)' — the bare except catches all errors (constraint violations, transient failures), not just missing-column errors, and the response gives the admin no indication the restrictions were dropped.

**Fix.** Only fall back on a missing-column error (inspect the PostgREST error code), otherwise re-raise; if the fallback is taken, fail loudly or return a warning that the targeting fields were not stored.

### 18. Admin force-cancel allows cancelling an in_progress ride, violating the ride state machine
`backend/routes/admin/rides.py:404` · **MAJOR** · Legal / regulatory · _admin-routes_

**Issue.** admin_cancel_ride only rejects completed/cancelled, so a ride with a passenger aboard (in_progress) can be flipped to cancelled — CLAUDE.md forbids any transition from in_progress except completed, and a cancelled in-progress trip corrupts insurance-period and fare records.

**Evidence.** Lines 404-405: `if ride.get("status") in ("completed", "cancelled"): raise HTTPException(400, ...)` — in_progress passes. The update at line 425 is also unconditioned on current status, so a ride completing between the read and the write is overwritten to cancelled (no `{"status": ...}` filter like the acceptance race guard).

**Fix.** Reject cancellation when status == 'in_progress' (offer force-complete instead), and make the update conditional on the previously-read status so a concurrent completion wins.

### 19. Admin cancel frees the driver without recording an insurance period transition
`backend/routes/admin/rides.py:452` · **MAJOR** · Compliance (PIPEDA) · _admin-routes_

**Issue.** admin_cancel_ride calls set_driver_available but never record_period_transition, leaving the driver logged in Period 2/3 in driver_insurance_periods while actually back in Period 1 — an SGI insurance-period misclassification.

**Evidence.** Lines 452-461 only call `await db_supabase.set_driver_available(driver_id, True)`; contrast admin_complete_ride lines 576-577 which call both `set_driver_available(driver_id, True)` and `record_period_transition(driver_id, 1)`. CLAUDE.md: every period transition must be logged; misclassification is a regulatory and insurance liability.

**Fix.** Add `await record_period_transition(driver_id, 1)` to the cancel path alongside freeing the driver.

### 20. Deactivating the last active super_admin is not blocked — full admin lockout possible
`backend/routes/admin/staff.py:257` · **MAJOR** · Correctness / bug · _admin-routes_

**Issue.** The 'cannot demote the last active super admin' guard only fires on role change; setting is_active=false on the last super_admin (including yourself) succeeds, bumps token_version, revokes all refresh tokens, and locks the org out of staff management permanently.

**Evidence.** Lines 247-250 guard only `if req.role is not None and req.role != "super_admin" and s.get("role") == "super_admin"`. Lines 257-263 process `req.is_active is False` with no super_admin count check and no self-target check, and immediately `revoke_all_for_user(staff_id)`.

**Fix.** Apply the same active-super_admin count check when is_active=False targets a super_admin row, and reject self-deactivation.

### 21. DSAR status update calls nonexistent db_supabase.get_one — endpoint always 500s
`backend/routes/admin/users.py:494` · **MAJOR** · Correctness / bug · _admin-routes_

**Issue.** PATCH /admin/dsars/{id}/status crashes with AttributeError on every call because db_supabase has no get_one function.

**Evidence.** Line 494: `req = await db_supabase.get_one("data_export_requests", {"id": request_id})`. Grep across backend shows no `def get_one` anywhere (repositories/_base.py exports find_one/get_rows only, and db_supabase.py has no __getattr__ fallback). Admins can never mark a PIPEDA data-export request in_progress/completed/rejected, jeopardizing the 30-day s.9 response SLA the docstring cites.

**Fix.** Replace with `await db_supabase.find_one("data_export_requests", {"id": request_id})` and add a regression test that patches backend.db_supabase and exercises the endpoint.

### 22. Email-OTP login matches accounts by unverified users.email — account pre-claim takeover
`backend/routes/auth.py:492` · **MAJOR** · Security · _core-infra_

**Issue.** /auth/verify-email-otp resolves the session target via _find_user_by_email over users.email, which any user can pre-claim unverified via POST /users/profile, so the real inbox owner who later logs in with 'Spinr for Business' is silently logged into the attacker's account.

**Evidence.** auth.py:492 `existing_user = await _find_user_by_email(email)` then mints tokens for that row; users.py:67-104 lets any authenticated user set that email without proving ownership. The attacker retains phone-OTP access to the same account, seeing the victim's subsequent rides, saved addresses, and activated corporate invites (`_activate_pending_company_invites`, auth.py:557).

**Fix.** Only allow email-OTP login to match accounts whose email was set through a verified flow (email_verified flag), and require email verification when users.email is changed via /users/profile.

### 23. Corporate email OTP is 4 digits with no brute-force lockout
`backend/routes/auth.py:658` · **MAJOR** · Security · _core-infra_

**Issue.** /auth/verify-email-otp has no failure counter or lockout (unlike the phone path's SEC-008 5-fail/24h lockout), so the 4-digit code (10,000 combinations) can be brute-forced within its 5-minute lifetime by rotating IPs, yielding full account takeover of any email-addressable account.

**Evidence.** send_company_email_otp uses generate_otp() which is OTP_LENGTH=4 digits (dependencies/__init__.py:51,65). verify_company_email_otp (auth.py:637-702) checks only the per-IP `@limiter.limit("5/minute")` — there is no _record_otp_failure/_check_otp_lockout equivalent and no attempt counter on the corporate_email_otp_records row. The code's own comment (auth.py:132) admits the IP limiter is spoof/rotation-vulnerable; the phone path added a phone-keyed lockout for exactly this reason.

**Fix.** Add a per-email failure counter mirroring _record_otp_failure/_check_otp_lockout (or an attempts column on the OTP row, invalidating it after ~5 wrong guesses), and consider 6-digit codes for the email flow.

### 24. Recycled phone number can reactivate a stranger's deleted account for up to 7 years
`backend/routes/auth.py:885` · **MAJOR** · Security · _core-infra_

**Issue.** A pending_deletion tombstone keeps the phone attached for the 7-year retention window, and verify_otp hands a reactivation token to whoever currently controls that phone number — carriers recycle numbers in ~90 days, so a new owner can resurrect and fully access the previous owner's account (ride history, saved addresses, wallet).

**Evidence.** auth.py:872-887: for status pending_deletion, verify_otp returns `"reactivation_token": create_reactivation_token(existing_user["id"], phone)` based solely on OTP possession of the phone; users.py:170 sets deletion_scheduled_at 2557 days out, so the window is years long. POST /auth/reactivate (auth.py:1078) then restores full access with no additional identity check.

**Fix.** Gate reactivation after a dormancy threshold (e.g. >90 days since deletion request) behind an additional factor — the account's verified email, last payment card digits, or support review — rather than phone OTP alone.

### 25. Hard delete of a corporate account cascades away the entire financial ledger
`backend/routes/corporate_accounts.py:657` · **MAJOR** · Compliance (PIPEDA) · _wallet-corporate_

**Issue.** DELETE /admin/corporate-accounts/{id} performs a hard row delete; corporate_wallets has ON DELETE CASCADE from corporate_accounts and corporate_wallet_transactions cascades from corporate_wallets, so one admin click permanently destroys the company's wallet balance and full transaction ledger — violating the 7-year financial-record retention CLAUDE.md/Saskatchewan requirement.

**Evidence.** corporate_repo.py:155 `supabase.table("corporate_accounts").delete().eq("id", account_id)`; migration 27 line 68 `company_id UUID NOT NULL UNIQUE REFERENCES corporate_accounts(id) ON DELETE CASCADE` (corporate_wallets) and line 89 `wallet_id UUID NOT NULL REFERENCES corporate_wallets(id) ON DELETE CASCADE` (corporate_wallet_transactions). Members, allowances, and allowance requests cascade too (lines 122, 213, 261).

**Fix.** Replace hard delete with the existing status transition to 'closed' (soft delete); restrict physical deletion to accounts with zero wallet transactions, or archive ledger rows to an audit table before delete.

### 26. Allowance-request approval is not atomic — concurrent approvals double-grant
`backend/routes/corporate_company.py:358` · **MAJOR** · Financial loss · _wallet-corporate_

**Issue.** decide_allowance_request does a read-then-act status check and calls apply_grant BEFORE update_allowance_request, and update_allowance_request has no status='pending' filter, so two concurrent approvals (or an approval retried after the status update fails) each apply the grant, double-crediting the member from the master wallet.

**Evidence.** corporate_company.py:358 `if request.get("status") != "pending": raise 409` (plain read), then :370 `await apply_grant(...)`, then :379 `return await update_allowance_request(...)`; corporate_repo.py:817-835 update_allowance_request updates `.eq("id", request_id)` with no `.eq("status", "pending")` claim.

**Fix.** Claim the request atomically first: UPDATE corporate_allowance_requests SET status='approved', reviewed_by=?, reviewed_at=now() WHERE id=? AND status='pending' RETURNING *; only apply_grant when the claim returned a row (and on grant failure flip the row back to pending/failed).

### 27. auto_approve_monthly_count cap never enforced — auto_approved_this_period is never incremented
`backend/routes/corporate_rider.py:240` · **MAJOR** · Financial loss · _wallet-corporate_

**Issue.** The auto-approval branch checks `used_auto < int(auto_monthly)` but no code path ever increments corporate_member_allowances.auto_approved_this_period (grep shows only DEFAULT 0, the reset to 0, and this read), so a member can chain unlimited auto-approved allowance top-ups, each granting company master-wallet funds without admin review.

**Evidence.** corporate_rider.py:240 `used_auto = allowance.get("auto_approved_this_period") or 0` and the `used_auto < int(auto_monthly)` guard; repo-wide grep for auto_approved_this_period matches only corporate_repo.py:733 (init 0), reset_allowance_period (reset to 0), schema default, and this read — corporate_allowance_apply_delta (migration 29) does not touch it.

**Fix.** Atomically increment auto_approved_this_period as part of the auto-approve claim (UPDATE corporate_member_allowances SET auto_approved_this_period = auto_approved_this_period + 1 WHERE id=? AND auto_approved_this_period < ? RETURNING *) before inserting the auto_approved request and applying the grant; fall back to a pending request when the claim returns zero rows.

### 28. Auto-approved request row is written before (and survives without) the grant
`backend/routes/corporate_rider.py:247` · **MAJOR** · Correctness / bug · _wallet-corporate_

**Issue.** submit_request inserts the allowance request with status='auto_approved' first, then applies the grant only `if wallet and allowance.get("id")` — a missing wallet silently yields an approved request with no funds, and an apply_grant failure (e.g. wallet_below_floor) raises after the row is already approved, leaving the member seeing 'approved' with no money and no retry path.

**Evidence.** Lines 247-264: `row = await insert_allowance_request(..., status="auto_approved")` precedes `wallet = await get_corporate_wallet_by_company(company_id)`; `if wallet and allowance.get("id"): await apply_grant(...)` — no else, no rollback, and apply_grant exceptions propagate uncaught after the insert.

**Fix.** Insert as pending, apply the grant, then flip to auto_approved on success (or approve atomically with the grant); on grant failure mark the request denied/failed with the floor error so the member and admin see the true state.

### 29. Resolving with resolution='approved' but no refund_amount silently issues no refund
`backend/routes/disputes.py:216` · **MAJOR** · Financial loss · _support-misc_

**Issue.** If an admin approves a dispute without explicitly entering refund_amount, the Stripe refund block is skipped entirely (condition requires truthy refund_amount), yet the dispute is marked resolved with refund_amount 0 — the rider is told their dispute was handled but no money is ever returned, and the resolved-guard at line 203 prevents retrying.

**Evidence.** Line 216: `if req.resolution in ("approved", "partial_refund") and req.refund_amount:` — with refund_amount None/0 the code falls through to `update_data = {"status": "resolved", ..., "refund_amount": req.refund_amount or 0}` and line 203 blocks re-resolution of resolved disputes.

**Fix.** For resolution='approved' default refund_amount to the dispute's requested_amount (or reject the request with 400 'refund_amount required'), so an approval can never be a silent zero-dollar refund.

### 30. _ride_income treats tips inconsistently: canonical path excludes tip, legacy fallback includes it
`backend/routes/drivers/_shared.py:108` · **MAJOR** · Reporting / observability · _drivers_

**Issue.** For rows with driver_earnings set (fare-only per ride_complete.py's comment) tips are excluded, while legacy rows fall back to base+distance+time+tip, so T4A and /earnings totals mix tip-inclusive and tip-exclusive incomes across rides.

**Evidence.** `if r.get("driver_earnings") is not None: return _d(r.get("driver_earnings"))` (fare-only per ride_complete.py:626-630 'driver_earnings ... holds the fare-only amount') vs fallback `_d(base_fare)+_d(distance_fare)+_d(time_fare)+_d(tip_amount)`.

**Fix.** Make the fallback match the canonical definition (drop tip_amount from it, report tips separately) or consistently add tips in both branches, and pick one for the T4A.

### 31. payable_balance formula diverges from earnings screen and canonical driver_earnings
`backend/routes/drivers/earnings.py:55` · **MAJOR** · Financial loss · _drivers_

**Issue.** get_driver_balance computes earnings as base+distance+time+tip only — ignoring driver_earnings (which includes the minimum-fare uplift), incentive claims, and no-show/cancellation fees — so the withdrawable balance that bounds Stripe payouts disagrees with what /earnings and ride history tell the driver they earned.

**Evidence.** Lines 55-64 sum fare components + tip; /earnings (lines 251-286) uses `_ride_income` plus `_incentive_total`, `_cancel_fees_total`, `_total_tax` and driver_bonuses. _shared.py:686-697 notes the driver keeps the min-fare uplift via total_fare-derived fare_share, which the balance formula drops.

**Fix.** Compute payable_balance from the same income definition as /earnings (_ride_income + incentives + cancel fees + bonuses [+ tax pass-through]) so drivers can withdraw exactly what they are shown.

### 32. v2 location-batch path skips GPS-spoofing checks — mocked fixes update the live driver marker
`backend/routes/drivers/location.py:151` · **MAJOR** · Security · _drivers_

**Issue.** The v2 durable-outbox path updates drivers.lat/lng from the latest non-rejected point without calling check_location_integrity, and the breadcrumb persister stores mocked=true points rather than rejecting them — so a spoofed/mocked fix moves the live marker used by the 200 m arrive geofence and dispatch, while the legacy path rejects the same point.

**Evidence.** _persist_v2_location_batch (lines 151-163) picks `latest` from points minus `result.ack.rejected` and writes lat/lng directly; utils/breadcrumbs.py rejection reasons are only coordinate/time-window checks and it stores `"mocked": bool(point.get("mocked", False))` (line 334) without rejecting; legacy path (lines 361-370) rejects via check_location_integrity when mocked/teleport/impossible-speed.

**Fix.** Run check_location_integrity (or at minimum reject mocked=true and teleport-implausible points) before using a v2 point for the live drivers-row marker update.

### 33. Vehicle-field profile edit mid-trip forces the driver offline and records insurance period 0 with a passenger aboard
`backend/routes/drivers/profile.py:195` · **MAJOR** · Legal / regulatory · _drivers_

**Issue.** update_my_driver flips an active driver to needs_review with is_online=False/is_available=False without checking for an active ride, and then records insurance period 0 — so a driver editing e.g. vehicle_color during an in_progress trip is logged as Period 0 (personal auto only) while carrying a passenger (Period 3), and the ride continues with an offline/unverified driver.

**Evidence.** Lines 195-198 set `updates["status"]="needs_review"; updates["is_online"]=False` with no active-ride query; lines 216-217 `if changed_vehicle and ... driver.get("is_online"): await _deps.record_period_transition(driver["id"], 0)`.

**Fix.** Reject (409) vehicle/document edits while the driver has an active ride or pending offer; only then apply the needs_review + offline transition.

### 34. decline_ride accepts any searching/driver_assigned ride and force-sets the caller available, breaking offer-claim parking
`backend/routes/drivers/ride_flow.py:448` · **MAJOR** · Correctness / bug · _drivers_

**Issue.** decline_ride never verifies the caller was assigned/offered the ride, and unconditionally runs set_driver_available(True) + period-1 — a driver holding a pending offer (or even a direct assignment) for ride X can call decline on any other searching ride Y and re-enter the dispatch pool while still claim-parked, enabling a double-offer; and declining a direct-assigned ride never reverts it to searching, leaving the ride stuck until the offer-timeout sweeper.

**Evidence.** Lines 449-462 check only ride status ∈ (searching, driver_assigned) with no driver_id/offer ownership check; line 488 `await db_supabase.set_driver_available(driver["id"], True)` (driver_repo clamps only on is_online, not on pending offers/active rides); the early-resolution block (line 530) only re-dispatches when status == 'searching'.

**Fix.** Require a pending ride_offers row or ride.driver_id == driver.id before processing the decline; only release availability when no other claim exists; for a direct-assigned ride, atomically revert status to searching and clear driver_id.

### 35. Pickup OTP: crashes on NULL stored OTP, matches empty-vs-empty, and has no brute-force limiter
`backend/routes/drivers/ride_flow.py:652` · **MAJOR** · Security · _drivers_

**Issue.** verify_pickup_otp raises TypeError→500 when rides.pickup_otp is NULL, starts the trip if both stored and submitted OTP are empty strings, and has no attempt limit — a 4-digit OTP can be brute-forced (~10k tries) to start the meter without the rider aboard.

**Evidence.** `stored_otp = ride.get("pickup_otp", "")` returns None for a NULL column value (dict default only covers a missing key), then `hmac.compare_digest(stored_otp, request.otp)` with None raises; RideOTPRequest (_shared.py:221) is `otp: str` with no min_length; no counter/lockout exists on this endpoint (unlike the auth OTP's 5-failures/hour rule).

**Fix.** Use `ride.get("pickup_otp") or ""` and reject when the stored OTP is empty; add min_length=4 to RideOTPRequest; add a Redis attempt counter with lockout per ride.

### 36. Non-active driver with is_verified=True can go online (rejected/pending statuses pass the gate)
`backend/routes/drivers/status.py:171` · **MAJOR** · Security · _drivers_

**Issue.** The final status gate only raises when the driver is BOTH not verified AND not active, so a driver whose status is 'rejected' or 'pending' but whose legacy is_verified flag is True passes all checks and goes online.

**Evidence.** Lines 171-180: `if is_online and driver.get("status") not in ("active",): if not driver.get("is_verified", False) and driver.get("status") != "active": raise ...` — verified+rejected falls through; yet the comment at line 339 asserts 'Only status='active' drivers reach this point'.

**Fix.** Unconditionally reject go-online for any status other than 'active' (drop the is_verified escape hatch).

### 37. Driver can go offline while in driver_assigned state — insurance period 0 recorded while obligated (Period 2)
`backend/routes/drivers/status.py:190` · **MAJOR** · Legal / regulatory · _drivers_

**Issue.** The go-offline block only checks driver_accepted/driver_arrived/in_progress, so a driver with a driver_assigned ride can toggle offline and line 634 records insurance period 0 even though CLAUDE.md defines driver_assigned as Period 2 (TNC primary commercial), misclassifying the SGI audit trail and leaving an assigned ride with an 'offline' driver.

**Evidence.** Lines 190-198 filter `"$in": [RideStatus.DRIVER_ACCEPTED, RideStatus.DRIVER_ARRIVED, RideStatus.IN_PROGRESS]` — DRIVER_ASSIGNED missing; line 634 `record_period_transition(driver_id, 1 if is_online else 0)` runs unconditionally on a flip.

**Fix.** Include DRIVER_ASSIGNED in the offline-block query (or auto-decline/release the assignment first) so period 0 is never opened while a ride obligation exists.

### 38. driver_documents fetch failure silently disables document-expiry gating on go-online
`backend/routes/drivers/status.py:226` · **MAJOR** · Compliance (PIPEDA) · _drivers_

**Issue.** If the approved-documents query raises, the code silently sets approved_docs=[] and relies solely on legacy top-level expiry columns; for drivers whose expiries live only in driver_documents (legacy columns null), an expired licence/insurance no longer blocks go-online — an SGI/insurance-eligibility gate lost to a swallowed DB error.

**Evidence.** Lines 217-227: `try: approved_docs = await db_supabase.get_rows("driver_documents", ...) except Exception: approved_docs = []` — no log, no 503, violating the project's 'never silently swallow DB errors' rule on a regulatory gate.

**Fix.** On lookup failure raise 503 (fail closed) or at minimum logger.error and refuse go-online when legacy expiry columns are also empty.

### 39. Recurring Spinr Pass checkout charges the pre-tax plan price — GST/PST never collected on renewals
`backend/routes/drivers/subscriptions.py:545` · **MAJOR** · Legal / regulatory · _drivers_

**Issue.** The one-off checkout path computes provincial tax and charges subtotal+tax, but the recurring (mode=subscription) path charges the Stripe Price which the mismatch guard requires to equal the pre-tax plan price — so recurring subscribers are never charged GST/PST, under-collecting tax the platform must remit and diverging from the one-off price for the same plan.

**Evidence.** Recurring: lines 496-556 enforce `_price_cents == dollars_to_cents(plan_price)` and create the session from `_price_id` with no tax computation. One-off: lines 565-571 `_tax = await _compute_subscription_tax(driver["id"], plan_price); _amount_cents = dollars_to_cents(_tax["total"])`.

**Fix.** Attach Stripe Tax (or a tax-inclusive Price validated against plan price + tax) to subscription-mode sessions, or require recurring Prices to equal the tax-inclusive total and record the tax split in subscription_payments.

### 40. T4A summary omits incentives/bonuses/cancel fees and attributes income by booking date, not completion date
`backend/routes/drivers/tax_exports.py:45` · **MAJOR** · Legal / regulatory · _drivers_

**Issue.** The T4A figure sums only per-ride driver_earnings and excludes incentive bonuses, quest/referral bonuses and no-show/cancellation fees the platform paid the driver, and get_rides_for_driver filters the tax year on created_at (UTC booking time) rather than ride_completed_at, so CRA-facing income is understated and can land in the wrong tax year.

**Evidence.** get_t4a_summary sums `_ride_income(r)` over completed rides only; no driver_bonuses / ride_incentive_claims / cancellation_fee_driver query. backend/repositories/ride_repo.py:294-297 applies `gte/lt("created_at", ...)` for the year window.

**Fix.** Include all platform-paid amounts (bonuses, incentives, cancel fees) in the T4A total and window the year on ride_completed_at (ideally in the driver's local timezone).

### 41. T4A on-demand summary buckets by created_at while annual job uses ride_completed_at
`backend/routes/drivers/tax_exports.py:45` · **MAJOR** · Reporting / observability · _reporting-tax_

**Issue.** The on-demand T4A summary counts a driver's rides for a year by created_at, but the T4A annual issuance/eligibility job sums earnings by ride_completed_at, so the notification and the downloadable slip can report different income and a ride straddling year-end lands in different tax years.

**Evidence.** get_t4a_summary calls get_rides_for_driver(from_date=f'{year}-01-01', to_date=f'{year+1}-01-01') and get_rides_for_driver filters on created_at (repositories/ride_repo.py:294-297 gte/lt on 'created_at'). The annual job's _driver_annual_earnings filters rides on ride_completed_at $gte/$lt (utils/t4a_annual_job.py:167-170). A ride created Dec 31 but completed Jan 2 is counted in different years by the two paths; CRA income should be recognized by completion, not creation.

**Fix.** Bucket the on-demand T4A summary by ride_completed_at (add a completed-date range filter) so it matches the annual job and reflects when income was actually earned.

### 42. PIPEDA data export reads nonexistent 'driver_payouts' table — payout history missing or whole export fails
`backend/routes/drivers/tax_exports.py:623` · **MAJOR** · Compliance (PIPEDA) · _drivers_

**Issue.** _build_and_email_data_export queries table 'driver_payouts', but payouts are written to 'payouts' (payouts.py insert_one('payouts', ...)) and no migration creates driver_payouts, so the DSAR export either omits all payout data or the read raises and the whole export silently never arrives (the driver was already told 'check your email').

**Evidence.** Line 623 `db_supabase.get_rows("driver_payouts", ...)` vs payouts.py lines 656/809 `insert_one("payouts", payout)` and get_payout_history reading "payouts"; grep of backend/migrations shows no driver_payouts table (only referral_payouts).

**Fix.** Query the 'payouts' table in the export, and make the background task notify the user (or retry) on failure instead of only logging.

### 43. Manual surge override above 2.5 is silently clamped in fare paths while dashboards show the raw value
`backend/routes/fares.py:235` · **MAJOR** · Reporting / observability · _fares-promos_

**Issue.** Admins may set a manual surge up to 10.0 with written justification, but both fare builders unconditionally clamp to SURGE_CAP=2.5 regardless of surge_source, so the justified override is never actually charged — while get_surge_status reports the raw stored multiplier, meaning the admin dashboard advertises a multiplier (e.g. 3.0x) that estimates and bookings price at 2.5x.

**Evidence.** routes/fares.py:234-238 'min(matched_area.get("surge_multiplier", 1.0), SURGE_CAP)' and services/fare_service.py:438-441 apply the cap with no surge_source check; routes/admin/service_areas.py:203 accepts ge=1.0 le=10.0 and lines 517-553 require justification above 2.5; utils/surge_engine.py get_surge_status line 337 returns area.get('surge_multiplier') uncapped when active.

**Fix.** Either honour manual surge_source up to the validated 10.0 in the fare paths (cap only auto), or reject admin values above 2.5 outright — and make get_surge_status report the effective (charged) multiplier in either case.

### 44. GET /favorites always returns empty list — get_rows called with unsupported 'skip' kwarg
`backend/routes/favorites.py:44` · **MAJOR** · Correctness / bug · _support-misc_

**Issue.** db.get_rows is called with skip=skip but the underlying repositories/_base.py get_rows signature is (table, filters, order, desc, limit, offset, columns) with no 'skip' parameter, so every call raises TypeError which the except block swallows, returning [] — saved favorites are never shown to any rider.

**Evidence.** favorites.py: `favorites = await db.get_rows("favorite_routes", {...}, limit=limit, skip=skip, order="use_count")` inside try/except that does `logger.error(...); favorites = []`. _base.py:595 `async def get_rows(table, filters=None, order=None, desc=False, limit=None, offset=None, columns="*")` — no skip and no **kwargs, so TypeError on every request.

**Fix.** Pass offset=skip instead of skip=skip (and add desc=True for use_count ordering); remove the exception swallow so a DB failure returns 503 per CLAUDE.md instead of a silent empty list.

### 45. confirm_payment strands ride in payment_status='processing' with no release on failure
`backend/routes/payments.py:391` · **MAJOR** · Correctness / bug · _payments-core_

**Issue.** After claim_ride_payment_processing flips the ride pending->processing, any failure (Stripe retrieve error, timeout) raises 500 without releasing the claim, and because the ride's payment_intent_id is only written at the end, the payment_retry loop skips it forever.

**Evidence.** Line 391 `claimed = await db_supabase.claim_ride_payment_processing(ride_id)` then the try block at 413-482 has no compensating pending-reset in its `except Exception` (lines 480-482 just raise 500). A retry of /payments/confirm returns early at line 367 (`payment_status in ("paid", "processing")` -> `already_processed`). payment_retry.py line 298 `if not payment_intent_id or not stripe_secret: continue` skips processing rides with no PI, and stripe_reconcile's heal (stripe_reconcile.py:526 `if not pi_id: return False`) also gives up — permanent wedge; rider can never pay and thinks payment 'already processed'.

**Fix.** On any exception after the claim, atomically reset payment_status processing->pending (mirroring auto_settle_guest_corporate's release), and/or persist payment_intent_id onto the ride before the Stripe retrieve so retry/heal paths can recover it.

### 46. confirm_payment writes raw Stripe intent.status ('succeeded') into ride.payment_status instead of 'paid'
`backend/routes/payments.py:471` · **MAJOR** · Financial loss · _payments-core_

**Issue.** A successful client-confirmed payment leaves the ride with payment_status='succeeded', a value no other code recognizes as settled, so the ride escapes reconciliation and can be double-collected or permanently blocked from re-confirmation.

**Evidence.** Lines 468-474: `await db_supabase.update_ride(ride_id, {"payment_status": intent.status, ...})`. Settled-state checks elsewhere use 'paid': webhooks.py:197 `if _pstatus in ("paid", "waived_admin", "refunded")`, webhooks.py:624 `in ("paid", "waived_admin", "processing")`; stripe_reconcile.py:166 only reconciles `payment_status: "paid"` rides; confirm's own idempotency check (payments.py:367) only handles ('paid','processing') so a re-confirm falls through to the claim (requires 'pending') and 409s forever. An admin-sent invoice.paid for such a ride would re-settle it (double collect) because 'succeeded' passes the terminal-status guard. The behaviour is even locked in by test (backend/tests/routes/test_payments.py:269 asserts "succeeded").

**Fix.** Map intent.status=='succeeded' to payment_status='paid' (plus paid_at), map non-terminal statuses back to 'pending'/'failed' rather than writing raw Stripe vocabulary, and update the test.

### 47. total_budget cap is dead — budget_used is never incremented
`backend/routes/promotions.py:237` · **MAJOR** · Financial loss · _fares-promos_

**Issue.** Both promo validation paths gate on promo.budget_used >= total_budget, but no code anywhere in the backend ever increments budget_used, so a budget-capped promo never stops discounting.

**Evidence.** Rule 10 (line 237) and list_available_promos (line 550) read budget_used; repo-wide grep for budget_used matches only these checks, tests, and migrations (82, 96) — there is no writer. _record_promo_application updates only uses.

**Fix.** Increment budget_used atomically (RPC like increment_promo_uses, e.g. UPDATE promotions SET budget_used = budget_used + :discount WHERE id = :id AND (total_budget = 0 OR budget_used + :discount <= total_budget) RETURNING) inside _record_promo_application, and 409 when the budget is exhausted.

### 48. POST /promo/apply consumes the promo but never discounts the ride
`backend/routes/promotions.py:341` · **MAJOR** · Financial loss · _fares-promos_

**Issue.** The standalone /promo/apply endpoint records a promo_applications row and increments promotions.uses but never writes discount_amount/promo_code/promo_application_id/grand_total to the ride row, so the rider's per-user allowance and the promo's global uses are burned while settlement (which reads ride.discount_amount) charges full fare.

**Evidence.** apply_promo (lines 341-384) calls _validate_promo_for_user then _record_promo_application and returns {'success': True, 'discount_applied': ...} with no rides update; grep shows the only writers of ride.discount_amount are rides/booking.py:940-950 and admin/rides.py — nothing links a /promo/apply application to the ride. It also uses ride.total_fare (line 364) as grand_total for free_ride promos (taxes/area fees excluded), doesn't check the ride isn't already discounted (promo stacking of recorded uses), and doesn't check ride status (promo can be 'applied' to a completed ride).

**Fix.** Have apply_promo persist subtotal_fare/discount_amount/promo_code/promo_application_id/grand_total on the ride exactly like rides/booking.py:940-950, reject rides that already have promo_application_id set or are past in_progress, and pass ride.grand_total (not total_fare) for free_ride promos — or remove the endpoint if booking-time application is the only supported flow.

### 49. _reestimate_fare_for_stops drops the airport surcharge and the minimum-fare clamp from the recomputed total
`backend/routes/rides/_shared.py:216` · **MAJOR** · Financial loss · _rides-booking_

**Issue.** The recomputed total after a stop mutation is base + distance + time + booking_fee only, omitting airport_fee (which calculate_fare includes in total_fare) and skipping the minimum-fare clamp, so an airport ride loses its surcharge from total_fare/estimated_fare and a stop removal can price the ride below the configured minimum fare.

**Evidence.** _shared.py lines 216-218: `new_total = _round(_d(ride.get("base_fare", 0)) + new_distance_fare + new_time_fare + _d(ride.get("booking_fee", 0)))`. Compare services/fare_service.py:209-210: `subtotal = _round(base_fare + distance_fare + time_fare + booking + ap_fee); total_fare = _round(max(subtotal, minimum))`. The ride row stores airport_fee separately (booking.py:770) but it is never added back, and no `max(..., minimum_fare)` is applied.

**Fix.** Include `_d(ride.get("airport_fee", 0) or 0)` in new_total and apply the minimum-fare clamp (fetch minimum_fare from the fare config or persist it on the ride row at booking) before writing estimated_fare/total_fare.

### 50. Geofence and estimate rejection logs write raw GPS coordinates (PIPEDA violation)
`backend/routes/rides/booking.py:450` · **MAJOR** · Compliance (PIPEDA) · _rides-booking_

**Issue.** Booking and estimate geofence paths log rider pickup/dropoff/stop coordinates at 5-decimal (~1m) precision, which CLAUDE.md's PIPEDA rules explicitly forbid in logs ('Raw GPS coordinates (lat/lng) — log geohashed area at most'); these lines fire for every out-of-area booking attempt and every unmatched estimate.

**Evidence.** booking.py:450 `logger.info("[geofence] reject pickup=(%.5f,%.5f) ...", body.pickup_lat, body.pickup_lng, ...)`, plus dropoff at line 476 and stops at line 500. estimates.py:168 `"[estimate] no service area matched pickup (%.5f, %.5f)"`, line 196 dropoff, line 217 stops. Contrast matching.py:219-221 which was already fixed with the comment 'PIPEDA: do not log raw pickup lat/lng — coordinates are forbidden in logs'.

**Fix.** Log a geohash/rounded area (e.g. 2-decimal ≈ 1km) or just the service-area count and ride/rider correlation id, mirroring the matching.py fix.

### 51. Surge can be bypassed on an immediate ride by sending is_scheduled=true with no scheduled_time
`backend/routes/rides/booking.py:590` · **MAJOR** · Financial loss · _rides-booking_

**Issue.** A client that sends is_scheduled=true but omits scheduled_time gets surge forced to 1.0 while the ride still dispatches immediately, letting any direct API caller (or modified app) evade surge pricing entirely during surge windows; the driver loses the surge portion of their earnings.

**Evidence.** booking.py:590 `if (body.is_scheduled or body.scheduled_time) and surge > Decimal("1.0"): surge = Decimal("1.0")` resets surge on the flag alone, but line 657 `_is_deferred_schedule = body.scheduled_time is not None` and line 711 `_initial_status = RideStatus.SCHEDULED if _is_deferred_schedule else RideStatus.SEARCHING` mean the ride dispatches now. schemas.py CreateRideRequest has no validator requiring scheduled_time when is_scheduled=True (validate_scheduled_time only runs `if value is not None`). Side effect: the ride persists is_scheduled=True (line 736) so it also pollutes GET /rides/scheduled.

**Fix.** Key the surge exclusion on `_is_deferred_schedule` (scheduled_time present) instead of `body.is_scheduled or body.scheduled_time`, or add a model validator rejecting is_scheduled=True without scheduled_time.

### 52. Pre-auth hold is orphaned when the ride insert fails after authorization (including the concurrent-duplicate idempotency path)
`backend/routes/rides/booking.py:883` · **MAJOR** · Financial loss · _rides-booking_

**Issue.** The card hold is placed before _insert_ride_with_code; if the insert then fails (rides_one_active_per_rider 409, ride_code exhaustion 503, or losing the idempotency race where the existing ride is returned), the just-created PaymentIntent hold is never cancelled — the rider's card keeps a fare+buffer hold for up to ~7 days with no ride row referencing it, and in the duplicate-request race the rider ends up with two simultaneous holds.

**Evidence.** booking.py lines 846-883: `_preauthorize_ride_card`/`_attach_preauthorized_hold` populate ride_data, then `fresh_ride, _idempotent_reuse = await _insert_ride_with_code(...)`. _insert_ride_with_code raises HTTPException(409) on `rides_one_active_per_rider` (line 1154), returns the OTHER request's ride on `idx_rides_rider_idempotency_key` (lines 1157-1163), and raises 503 after 3 code collisions (line 1170). There is no try/except/finally that calls cancel_authorization on any of these paths; the reuse-guard lookup (line 126) only finds PIs attached to existing ride rows.

**Fix.** Wrap the insert in try/except and on any failure (or idempotent-reuse return) call cancel_authorization(ride_id, pi) for a hold created in this request before re-raising/returning.

### 53. Driver cancellation-fee payout is made even when the rider fee was not collected, and an uncollected fee is never retried or blocked
`backend/routes/rides/cancellation.py:238` · **MAJOR** · Financial loss · _rides-booking_

**Issue.** pay_driver_cancellation_fee runs unconditionally after the rider charge attempt, so a declined card, an empty wallet (charge clamped to balance, down to $0), or a missing wallet row still pays the driver the full fee; the failed fee is then never recovered because payment_retry excludes cancelled rides and create_ride's unpaid-ride block only matches status=completed.

**Evidence.** cancellation.py lines 134-162: wallet branch clamps `new_balance = max(old_balance - total_cancel_fee, 0)` and skips entirely if `rider_wallet` is None; card branch sets `cancel_fee_payment_status = "failed"` on decline but does not raise. Line 238: `if driver_id and charged_driver > 0: await pay_driver_cancellation_fee(...)` — no check of collection outcome. utils/payment_retry.py:222 filters `"status": {"$ne": RideStatus.CANCELLED}`; booking.py:403-411 unpaid block filters `"status": RideStatus.COMPLETED`. The response (line 417) also reports the full fee as charged regardless of the wallet clamp.

**Fix.** Record uncollected cancellation fees as a receivable (e.g. extend the unpaid-ride block to cancelled rides with payment_status='failed', or negative wallet balance), or gate/flag the driver payout for reconciliation when the rider-side charge did not succeed; return the actually-collected amount in the response.

### 54. _offer_timeout_handler reverts the ride to searching with an unconditional id-only update, clobbering a concurrent accept
`backend/routes/rides/matching.py:1027` · **MAJOR** · Correctness / bug · _rides-booking_

**Issue.** Between the status check and the revert there are multiple awaits (miss-streak Redis call, app-settings read, driver release, insurance write); a driver accept landing in that window (valid: accept_filter matches status=driver_assigned + driver_id) is silently overwritten back to searching with driver_id=None, so the driver's app shows an accepted ride while the ride re-dispatches to someone else — and the handler has already flipped the accepting driver back to is_available=True (or fully offline in the auto-offline branch).

**Evidence.** matching.py line 977 checks `ride.get("status") != RideStatus.DRIVER_ASSIGNED or ride.get("driver_id") != driver_id` on a stale read, then lines 1027-1038 run `db.update_one("rides", {"id": ride_id}, {"$set": {"status": RideStatus.SEARCHING, "driver_id": None, ...}})` with no status/driver_id condition. This handler is live: routes/admin/rides.py:952 spawns it for admin manual assignment. Contrast drivers/ride_flow.py accept which uses a conditional filter, and the batch path (process_expired_offer) which claims atomically.

**Fix.** Make the revert conditional — `update_one("rides", {"id": ride_id, "status": RideStatus.DRIVER_ASSIGNED, "driver_id": driver_id}, ...)` — and skip the driver-release/miss-streak/notify side effects when zero rows match.

### 55. ride_search_timeout cancels with a non-atomic id-only update, racing a last-second driver accept
`backend/routes/rides/matching.py:1296` · **MAJOR** · Correctness / bug · _rides-booking_

**Issue.** Offers can still be pending right up to the 300s deadline (re-dispatch every 10s with 15s offer windows), so a driver accepting at ~300s can flip the ride to driver_accepted after the handler's get_ride check but before its unconditional update_ride — the accepted ride is then overwritten to cancelled with no driver release (driver stays is_available=False and drives to a pickup for a cancelled ride, and no driver-facing WS/push is sent because this path assumes no driver exists).

**Evidence.** matching.py lines 1281-1306: `current_ride = await get_ride(r_id); if ... status == SEARCHING:` then `await _deps.db_supabase.update_ride(r_id, {**base_update, ...})` — repositories/ride_repo.py:229-237 shows update_ride filters `.eq("id", ride_id)` only. The docstring itself notes the durable stuck_ride_sweeper uses "an atomic replay-safe DB claim"; this in-process twin does not. accept_ride's guard (status=searching) does not protect against a cancel that starts after the accept commits.

**Fix.** Use a conditional claim: `update_one("rides", {"id": r_id, "status": RideStatus.SEARCHING}, base_update)` and only emit the cancellation events when a row was claimed.

### 56. POST /rides/{id}/tip credits the driver a tip that is never collected from the rider
`backend/routes/rides/payments.py:113` · **MAJOR** · Financial loss · _rides-secondary_

**Issue.** add_tip writes tip_amount and driver_earnings (and rebuilds the T4A-feeding driver_earnings_snapshot) but never charges the rider's card, wallet, or corporate account, so every tip through this endpoint is pure platform loss at payout time.

**Evidence.** payments.py lines 82-113: `new_driver_earnings = _round(existing_earnings + tip_amount); update_payload = {"tip_amount": ..., "driver_earnings": ...}; await _deps.db_supabase.update_ride(ride_id, update_payload)` — there is no settle_card/settle_wallet/settle_corporate call anywhere in the function. Tips are only actually collected inside process_payment (total_charge = _q(_grand) + tip_rounded, line 333) or via Stripe webhooks/_tip_ride_update for charges that already included the tip. A ride that is already paid (the only realistic time a standalone tip is added — the endpoint requires status COMPLETED) gets driver_earnings incremented with no corresponding charge; nothing in webhooks.py, payment_retry, or stripe_reconcile.py collects it. The rider app exposes this via walletStore.ts:126-129 `addTip: ... api.post(\`/rides/${rideId}/tip\`, { amount })`.

**Fix.** Make add_tip actually settle the tip: charge the saved card via an off-session PaymentIntent (or debit the wallet via the idempotent RPC) and only write tip_amount/driver_earnings after the charge succeeds; alternatively disable the endpoint until a charge path exists, since process-payment already handles tips on the normal flow.

### 57. Rate-limit decorators on all rides read endpoints are applied above @router.get and are therefore dead
`backend/routes/rides/queries.py:41` · **MAJOR** · Security · _rides-booking_

**Issue.** In queries.py the @ride_read_limit decorator is placed ABOVE @router.get, so FastAPI registers the unwrapped function and the limiter wrapper never executes — GET /rides/active, /rides/history, /rides/scheduled and /rides/{ride_id} have no per-route rate limiting at all, and GET /rides/stats (which scans up to 10,000 rows per call) has no limiter whatsoever.

**Evidence.** queries.py lines 41-42, 149-150, 274-275, 292-293 all read `@ride_read_limit` / `@router.get(...)` in that order; utils/async_limiter.py:74-75 shows limit() returns a new `wrapper` function (so it must run before route registration), and FastAPI's route decorator returns the original function after registering it. Every other rides file (booking.py:289-291, cancellation.py:42-43, stops.py:40-41) uses the correct order with @router first.

**Fix.** Move @ride_read_limit below @router.get on all four endpoints and add a limiter to get_rider_stats; consider a regression test asserting the registered endpoint function is the rate-limit wrapper.

### 58. Re-rating the same ride re-enters the rolling average, letting one rider skew a driver's rating
`backend/routes/rides/rating.py:94` · **MAJOR** · Correctness / bug · _rides-secondary_

**Issue.** rate_driver never checks whether the ride was already rated, so every repeat call increments drivers.total_ratings and folds another rating into the rolling average — a single rider can repeatedly 1-star one completed ride to tank a driver's public rating (or repeatedly 5-star to inflate it).

**Evidence.** rating.py lines 51-58 overwrite rider_rating unconditionally, then lines 92-105: `old_count = int(driver.get("total_ratings") or 0); new_count = old_count + 1; new_avg = ((old_avg * old_count + Decimal(str(rating_data.rating))) / new_count)...` with no `if ride.get("rider_rating"):` guard anywhere. The endpoint is only rate-limited (ride_rating_limit), not deduplicated; the rider app's hasRatedRef latch (ride-completed.tsx:245) is client-side only and the offline queue (rideStore.ts:590) can replay. The read-modify-write is also unguarded against concurrent ratings from different rides (lost update).

**Fix.** Reject (or make idempotent) a second rating: `if ride.get("rider_rating") is not None: raise HTTPException(400, "ERR_ALREADY_RATED")` before updating; for the aggregate, prefer an atomic SQL update (rating = (rating*total_ratings + :new)/(total_ratings+1), total_ratings = total_ratings+1) to also close the concurrent lost-update.

### 59. Cancelled-ride receipt shows the full booked fare, taxes, and grand_total instead of the cancellation fee actually charged
`backend/routes/rides/receipts.py:84` · **MAJOR** · Reporting / observability · _rides-secondary_

**Issue.** For cancelled rides the receipt's fare_breakdown and grand_total are built from the booking-time fare fields (base/distance/time fares, GST/PST tax_breakdown, grand_total), none of which were charged — the only amount actually collected (cancellation_fee_admin + cancellation_fee_driver) appears in a separate field and is excluded from the line items and total, so the receipt misstates both the amount charged and the GST/PST collected.

**Evidence.** receipts.py allows cancelled rides (line 39 terminal_statuses). Lines 83-89: `fare_lines = _build_fare_breakdown(ride); receipt_grand_total = ride.get("grand_total") or ((ride.get("total_fare",0) or 0) + ...)` — cancellation.py:256-263 sets only status/cancelled_at/cancellation_fee_* on cancel, leaving booking-time total_fare/grand_total/tax_breakdown intact (booking.py:778 writes grand_total at creation). _build_fare_breakdown (_shared.py:368-436) has no cancellation-fee line type, so a $4.50 cancel charge renders as e.g. 'Ride fare (8.2 km) $18.00, GST (5%) $0.98, PST (6%) $1.17, grand_total $23.50'. Saskatchewan rule (CLAUDE.md): receipts must show GST/PST actually charged as separate line items — here taxes never collected are displayed as if charged. The float fallback sum at lines 85-88 is also raw float money addition, violating the Decimal-only convention.

**Fix.** For status==cancelled, build the breakdown from the cancellation fee only (one 'Cancellation fee' line plus its actual tax treatment, if any) and set grand_total to cancellation_fee_admin + cancellation_fee_driver (0 when no fee was charged); compute any fallback sum with _d()/_round() instead of float addition.

### 60. SOS SMS falsely tells emergency contacts 'Location shared with emergency services'
`backend/routes/rides/safety.py:118` · **MAJOR** · Legal / regulatory · _rides-secondary_

**Issue.** The SOS SMS to emergency contacts states the user's location was shared with emergency services, but Spinr never contacts 911 or any emergency service — the incident only goes to Spinr's own safety team, so the copy is false and can cause a contact to not call 911 believing responders are already informed.

**Evidence.** safety.py line 118: `location_text = " Location shared with emergency services." if body.latitude and body.longitude else ""` and lines 119-122 build the SMS: "URGENT: {user_name} triggered an emergency alert during a Spinr ride.{location_text} Call them or emergency services immediately." The only recipients of the location are the safety_incidents row (line 83), the admin WS broadcast (line 90), and notify_safety_team (line 101) — no 911/PSAP integration exists. CLAUDE.md 'What Spinr Is NOT': SOS "never claims to replace calling emergency services".

**Fix.** Change the copy to be truthful, e.g. " Their live location has been shared with Spinr's safety team." — never reference 'emergency services' as already having the location; keep the existing 'Call them or emergency services immediately' call to action.

### 61. Share tokens created via GET /share never get a creation timestamp, so they never expire
`backend/routes/rides/sharing.py:57` · **MAJOR** · Compliance (PIPEDA) · _rides-secondary_

**Issue.** GET /rides/{id}/share creates shared_trip_token without shared_trip_token_created_at, and track_shared_ride only enforces the 24-hour expiry when that timestamp exists — since the rider app exclusively uses the GET endpoint, effectively all production share links are permanently valid anonymous URLs exposing the rider's exact pickup/dropoff addresses.

**Evidence.** sharing.py line 57: `await _deps.db_supabase.update_ride(ride_id, {"shared_trip_token": share_token})` — no created_at, unlike POST /share (line 103) which sets shared_trip_token_created_at. In track_shared_ride, lines 181-187: `token_created = ride.get("shared_trip_token_created_at"); if token_created:` — expiry is skipped entirely when the field is NULL, and the terminal-status branch (lines 193-199) still returns pickup_address/dropoff_address forever. The rider app calls only GET: ride-in-progress.tsx:323/369, ride-tracking-webview.tsx:77, ai-assistant.tsx:91 all do `api.get(\`/rides/${id}/share\`)`. A share link forwarded/leaked once (SMS, chat history) discloses the rider's home/work addresses to any anonymous holder indefinitely — contrary to the intended 24h window and PIPEDA data-minimization.

**Fix.** Set shared_trip_token_created_at in the GET handler exactly as the POST handler does, and in track_shared_ride treat a missing/malformed timestamp on an ended ride as expired (fail closed) instead of allowing access; backfill created_at for existing tokens or null out tokens on ride completion.

### 62. Synchronous Gemini call blocks the event loop inside an async handler
`backend/routes/support.py:122` · **MAJOR** · Performance · _support-misc_

**Issue.** model.generate_content is a blocking HTTP call executed directly in the async support_chat handler, freezing the entire event loop of the replica for the full LLM latency (often 1-5 s), which stalls concurrent dispatch/fare/WebSocket traffic and breaches the CLAUDE.md SLA table (dispatch offer <2 s, WS fan-out <100 ms).

**Evidence.** Lines 117-123: `genai.configure(...); model = genai.GenerativeModel(...); response = model.generate_content(scrubbed_message)` — no await, no asyncio.to_thread, inside `async def support_chat`.

**Fix.** Wrap the call in `await asyncio.to_thread(model.generate_content, scrubbed_message)` or use the async client (generate_content_async).

### 63. Account deletion deletes driver_documents by the wrong key — documents survive deletion
`backend/routes/users.py:236` · **MAJOR** · Compliance (PIPEDA) · _core-infra_

**Issue.** DELETE /users/profile deletes driver_documents filtered on {'driver_id': user_id}, but driver_documents.driver_id stores drivers.id (a separate uuid), so zero rows match and the driver's license/insurance/registration images are retained despite the endpoint returning 'Account permanently deleted'.

**Evidence.** users.py:236 `await db_supabase.delete_many("driver_documents", {"driver_id": user_id})`. documents.py:599 sets `driver_id = drv_profile["id"]` (the drivers-row id), and drivers rows are created with `"id": str(uuid.uuid4()), "user_id": current_user["id"]` (routes/drivers/profile.py:168-171), so drivers.id != users.id. The same route correctly uses {'user_id': user_id} for the drivers table one line earlier, confirming the two keys differ.

**Fix.** Look up the driver row first (`driver = await get_driver_by_user_id(user_id)`) and delete driver_documents where driver_id == driver['id']; add a regression test asserting documents are gone after account deletion.

### 64. Phone number change requires no OTP verification and no E.164 normalization
`backend/routes/users.py:268` · **MAJOR** · Security · _core-infra_

**Issue.** PATCH /users/profile/phone accepts any string >= 10 chars with no OTP proof of ownership and no validate_phone normalization, enabling phone pre-claim takeover and self-lockout with duplicate accounts.

**Evidence.** users.py:267-278: `phone = request.phone.strip(); if len(phone) < 10: raise ...` then writes directly — validate_phone (used by /auth/send-otp, validators.py:31) is never called. Attack: set your phone to a victim's number before they register; when the victim OTP-signs-up, verify_otp's get_user_by_phone (auth.py:841) finds the attacker's row and logs the victim into the attacker-controlled account. Also, a user entering '306-555-1234' stores an un-normalized string, so OTP login (which normalizes to +1306...) never matches — next login silently creates a duplicate account, orphaning wallet and ride history.

**Fix.** Require an OTP sent to the NEW number before committing the change (standard Uber/Lyft flow), and run validate_phone to store the normalized E.164 value.

### 65. charge.refunded marks ride fully 'refunded' on any partial refund
`backend/routes/webhooks.py:851` · **MAJOR** · Financial loss · _payments-core_

**Issue.** charge.refunded fires for partial refunds too (amount_refunded is the cumulative refunded total, not necessarily the full charge), but the handler unconditionally sets payment_status='refunded', so a $2 partial refund marks the whole ride refunded — a terminal state that blocks later invoice settlement and misstates revenue/tax reporting.

**Evidence.** Lines 848-859: `refunded_cents = int(charge.get("amount_refunded", 0))` then `update_one("rides", ..., {"payment_status": "refunded", "refund_amount": str(refunded_amount), ...})` with no comparison of amount_refunded against the charge's amount_captured. _handle_ride_invoice_paid line 197 treats 'refunded' as terminal ('refunded' in the skip set), and record_refund_event's tax reversal is proportional but the ride-level status is binary.

**Fix.** Compare amount_refunded to the charge's captured amount; set payment_status='refunded' only when fully refunded, otherwise a 'partially_refunded' state (or keep 'paid') while still recording refund_amount and the ledger reversal.

### 66. invoice.paid/payment_failed read top-level invoice.subscription, removed in the pinned Basil API version
`backend/routes/webhooks.py:1131` · **MAJOR** · Correctness / bug · _payments-core_

**Issue.** Under the pinned Stripe API version 2025-04-30.basil the top-level `invoice.subscription` field no longer exists, so `invoice.get("subscription")` is always None and Spinr Pass renewals are never applied (rows never extended, renewal ledger rows never recorded, past_due never flagged).

**Evidence.** Line 1131 `stripe_sub_id = invoice.get("subscription")` and line 1332 same in invoice.payment_failed. The codebase itself documents the Basil payload change for the sibling field: `_extract_invoice_payment_intent` (lines 113-125) says 'Under the pinned API version (2025-04-30.basil) the top-level ``invoice.payment_intent`` field is removed' — Basil similarly moves the subscription reference to `invoice.parent.subscription_details.subscription`. With stripe_sub_id None, the handler logs 'invoice.paid without subscription id — skipping' (line 1147) and renewal processing (1152-1324) never runs; drivers whose pass renews are either locked out at expires_at or the renewal charge is never ledgered.

**Fix.** Add a Basil-aware extractor mirroring _extract_invoice_payment_intent: try invoice['subscription'], then invoice['parent']['subscription_details']['subscription'], then an expanded Invoice.retrieve fallback; use it in both invoice.paid and invoice.payment_failed.

### 67. Twilio inbound-SMS signature verification silently disabled if setting is empty, including in production
`backend/routes/webhooks.py:1800` · **MAJOR** · Security · _x-security-sweep_

**Issue.** twilio_inbound_sms only validates the X-Twilio-Signature header when `settings.get("twilio_auth_token")` is non-empty; if that app_settings row is ever blank in production (misconfiguration, migration gap), the endpoint accepts unsigned, unauthenticated POSTs and processes them as legitimate Twilio callbacks with no environment-based fail-closed guard.

**Evidence.** `if auth_token: ... validate(...) ... else: logger.warning("[TWILIO] inbound SMS not signature-verified (twilio_auth_token unset)")` — the else branch falls straight through to processing STOP/START keywords with no `if settings.ENV == "production": raise` guard, unlike the CORS/JWT-secret production fail-fast pattern used elsewhere in this codebase (core/middleware.py `_validate_production_config`).

**Fix.** In production (`app_config.ENV == "production"`), return 503/403 instead of processing when `auth_token` is empty, mirroring the CORS wildcard-in-production fail-fast pattern.

### 68. Driver wallet cancellation-fee credit is a non-atomic read-modify-write
`backend/services/cancellation_service.py:127` · **MAJOR** · Financial loss · _x-money-sweep_

**Issue.** pay_driver_cancellation_fee credits the driver's wallet using the same unguarded read-then-write pattern; if two cancellation fees land for the same driver in quick succession (plausible — driver cancels back-to-back offers), one credit can be lost because both reads see the same stale balance.

**Evidence.** new_balance = _round(_d(str(wallet.get("balance", 0))) + fee_dec); await db_supabase.update_one("wallets", {"id": wallet["id"]}, {"balance": _f(new_balance), ...})

**Fix.** Route through a locked RPC (mirroring corporate_wallet_apply_delta) instead of an application-level read-modify-write.

### 69. Policy evaluation is fail-open on DB errors — spend caps silently bypassed
`backend/services/corporate_policy_service.py:113` · **MAJOR** · Financial loss · _wallet-corporate_

**Issue.** evaluate_policy_for_ride catches all exceptions from get_corporate_policy and the allowance fetch, logs a warning, and proceeds with policy={} which evaluate_policy treats as unconditional pass — during any DB degradation, max_fare_per_ride, time windows, and allowance_only rules are all silently bypassed and rides book on company money.

**Evidence.** Lines 113-120 `except Exception as exc: logger.warning("[policy] could not fetch policy ... treating as no policy")` and evaluate_policy line 176-177 `if not policy: return {"pass": True, ...}`. This directly violates the CLAUDE.md rule 'Never logger.warning(...) and continue on a DB/auth/payment error' — the fail-open trade-off was never surfaced for decision.

**Fix.** Distinguish 'no policy configured' (pass) from 'policy fetch failed' (raise or return a blocking PolicyResult with a policy_unavailable failed_rule); at minimum use logger.error and a metric, and let callers decide whether to block or allow with an explicit flag.

### 70. Tax not recalculated when fare is recalculated at completion — GST/PST receipt lines wrong
`backend/services/fare_service.py:386` · **MAJOR** · Legal / regulatory · _fares-promos_

**Issue.** When actual distance differs from the estimate (and fare_lock is off), recalculate_fare_for_distance builds the new grand_total using the booking-time tax_amount, and the completion flow never recomputes tax, so the receipt's GST (5%) / PST (6%) line items no longer equal the required percentages of the recalculated fare — over-collecting tax on shorter trips and under-collecting on longer ones.

**Evidence.** fare_service.py:386-388 'tax_amount = _d(ride.get("tax_amount", 0)) ... new_grand_total = _round(new_total_fare + area_fees_total + tax_amount - discount)'; routes/drivers/ride_complete.py:463 applies fare_adj and the only tax reference in the file (line 702) passes the stale ride.tax_amount through to the receipt.

**Fix.** Recompute area fees and GST/PST from the recalculated taxable base inside recalculate_fare_for_distance (using the booking-time rates stored on the ride) and return the new tax_amount/area totals with the adjustment.

### 71. record_payment_event has no idempotency key — financial_events can be double-written
`backend/services/payment_service.py:127` · **MAJOR** · Financial loss · _payments-core_

**Issue.** The 7-year tax/audit ledger insert has no dedup on (ride_id, payment_intent_id, event_type), and it is reachable from multiple concurrent paths (settle_card, payment_intent.succeeded webhook, _handle_ride_invoice_paid, payment_retry requires_capture branch, manual event replay), so races or replays append duplicate charge rows that overstate collected revenue and GST remittance.

**Evidence.** record_payment_event (lines 127-180) does a bare `insert_one("financial_events", ...)`. The webhook guard is read-then-write: webhooks.py:624 reads `_already_settled = ride.get("payment_status") in (...)` from a get_ride at line 566, then updates and writes the ledger at 664 — a settle_card completing between the read and the write produces two stripe_charge rows for one charge. _handle_ride_invoice_paid also writes the ledger BEFORE update_ride (line 250) and raises on update failure; the documented recovery path is manual replay of the stuck event, which re-runs record_payment_event and duplicates the row.

**Fix.** Add a unique index on financial_events (event_type, ride_id, ref) (ref = payment_intent_id) and make record_payment_event insert-ignore-conflict, or dedupe inside the function before insert.

### 72. spinr_payment_settlement_total emitted with inconsistent label keys
`backend/services/payment_service.py:569` · **MAJOR** · Reporting / observability · _x-observability_

**Issue.** The same counter spinr_payment_settlement_total is emitted with different label dimensions in two sites, so corporate guest auto-settlements never appear in any dashboard/alert that aggregates by the `method` label.

**Evidence.** payment_service.py:569 emits `{"outcome":..., "path":"corporate_guest_auto"}` (no `method` label), while routes/rides/payments.py:179 emits `{"method":..., "outcome":...}` (no `path` label). In Prometheus these are distinct series; a payment-success-rate panel using `sum by(method)(spinr_payment_settlement_total)` (the KPI ≥99% dashboard) silently drops all corporate-guest settlement outcomes.

**Fix.** Standardize the label set across both call sites — emit `method` (e.g. "corporate_guest") and `outcome` on every settlement, and put the settlement path in a separate consistently-present label if needed, so all settlements roll up under one query.

### 73. Zoho reverse-sync closes disputes without any refund, permanently blocking the refund endpoint
`backend/services/zoho_desk_integration.py:154` · **MAJOR** · Financial loss · _support-misc_

**Issue.** close_linked_records sets disputes.status='resolved' whenever the linked Zoho ticket is closed, with no resolution/refund recorded; admin_resolve_dispute then rejects the dispute with 400 'Dispute already resolved' (disputes.py:203), so a support agent closing the ticket in Zoho forecloses the rider's refund path entirely.

**Evidence.** _LINKED_TABLES line 154 includes `{"table": "disputes", "status_col": "status", "closed": "resolved"}` and close_linked_records writes `{col: "resolved"}`; disputes.py line 203: `if dispute.get("status") in ("resolved", "rejected"): raise HTTPException(400, "Dispute already resolved")`.

**Fix.** Exclude disputes from the auto-close list (or only auto-close disputes that already have a resolution recorded), keeping the money decision exclusively in admin_resolve_dispute.

### 74. Expired-document suspension has no active-ride guard and writes no insurance-period transition
`backend/utils/document_expiry.py:147` · **MAJOR** · Legal / regulatory · _background-loops_

**Issue.** When a document crosses its expiry the loop suspends the driver, clears Redis presence, and force-disconnects their WebSocket with no check for an active ride — a driver mid-trip (in_progress, Period 3) loses their live connection and is flipped is_online=False, and no driver_insurance_periods row is written for the offline transition, leaving the SGI-auditable period log showing an open Period 1+ window that never closes.

**Evidence.** Lines 147-166: the claim filter is only `{"id": driver["id"], "status": {"$ne": "suspended"}}` — no active-ride exclusion (contrast stale_intent_reconciler.py lines 156-177 which explicitly skips drivers_on_ride and then calls `record_period_transition(driver_id, 0)` at line 219; document_expiry never calls record_period_transition). Line 166 `manager.disconnect(f"driver_{user_id}")` executes unconditionally after the claim.

**Fix.** Skip (and log) drivers linked to an active ride, deferring suspension to trip end; on a successful offline flip, call record_period_transition(driver_id, 0) as the stale-intent path does.

### 75. HTML email receipt omits promo/discount line — rows exceed printed Total
`backend/utils/email_receipt.py:132` · **MAJOR** · Financial loss · _reporting-tax_

**Issue.** The emailed HTML receipt renders base/fees/subtotal/tax/tip but no discount line, while the grand total prefers the persisted grand_total (net of discount), so a discounted ride's visible rows sum higher than the shown Total.

**Evidence.** Between the subtotal divider (line 130) and the tip (line 153) only tax_breakdown rows are added; grep for 'discount|promo' in email_receipt.py returns nothing. grand_total_d = _q(_d(grand_total) + tip) uses the persisted grand_total (fare_service.py:388 subtracts discount). Rows therefore don't add up to Total, and the promo the rider earned is undisclosed.

**Fix.** Emit a discount line (label 'Promo (code)' when promo_code present, else 'Discount') from ride['discount_amount'] before the Total, as the JSON receipt path (_build_fare_breakdown) already does.

### 76. Idempotency decorator caches non-dict returns (402 JSONResponse) as HTTP 200 with a repr-string body
`backend/utils/idempotency.py:217` · **MAJOR** · Correctness / bug · _payments-core_

**Issue.** When a handler returns a JSONResponse (e.g. create_payment_intent's 402 'action_required' 3DS response), the decorator caches it as status 200 with body str(JSONResponse) via json.dumps(default=str); a client retry with the same Idempotency-Key then receives 200 with a garbage string body, breaking the 3DS flow and looking like success.

**Evidence.** Line 217 `await write_cached_response(cache_key, 200, result, ttl=ttl_seconds)` hard-codes 200 and passes the raw result; write_cached_response (line 96) does `_json.dumps({"status": status_code, "body": body}, default=str)` which stringifies a JSONResponse to '<starlette.responses.JSONResponse object at ...>'. create_payment_intent (payments.py:243-260) returns exactly such a JSONResponse(status_code=402) and is decorated with @idempotent_endpoint.

**Fix.** Detect Response/JSONResponse results: cache their real status_code and decoded body (or skip caching non-2xx), never hard-code 200.

### 77. Reaper re-dispatches rides it lost the offer claim on, racing the in-process batch handler into double-offering
`backend/utils/offer_expiry_reaper.py:117` · **MAJOR** · Correctness / bug · _dispatch_

**Issue.** _reap_tick re-dispatches every ride in its candidate set even when process_expired_offer returned False for all of that ride's offers (claim lost to the in-process handler); when the reaper tick lands during _batch_offer_timeout_handler's expiry gather, both actors call match_driver_to_ride concurrently, and since _match_driver_to_ride_attempt has no pending-offer check or per-ride dispatch mutex, both pass the status==searching guard, claim disjoint driver sets, and insert two offer batches — up to 2x max_offers drivers claimed and pinged for one seat, plus two competing _batch_offer_timeout_handler timers.

**Evidence.** offer_expiry_reaper.py:117-121: `for ride_id in {o["ride_id"] for o in expired}: ... if ride[0].get("status") == "searching": await match_driver_to_ride(ride_id)` — the loop iterates all fetched offers regardless of `results`/`won`. matching.py:1243-1255: the batch handler gathers process_expired_offer then unconditionally `await match_driver_to_ride(ride_id)`. _match_driver_to_ride_attempt (matching.py:151-930) only guards `ride.get("status") != RideStatus.SEARCHING` — it never checks for offers just inserted by a concurrent invocation.

**Fix.** In the reaper, only re-dispatch rides where at least one claim was won (zip results with offers and build the ride set from wins); additionally guard match_driver_to_ride with a short-TTL Redis SET NX per-ride dispatch lock (e.g. spinr:dispatch_lock:{ride_id}, ~5s) so concurrent re-dispatchers collapse to one attempt.

### 78. Synchronous Stripe SDK calls inside async background loops block the event loop
`backend/utils/payment_retry.py:331` · **MAJOR** · Performance · _background-loops_

**Issue.** payment_retry, corporate_autotopup, reconciliation, and stripe_reconcile all call the synchronous stripe-python SDK directly from async coroutines with no thread offload, so each Stripe HTTP round trip (hundreds of ms; seconds on timeout; a full paginated day-list for reconcile) freezes the replica's entire event loop — stalling WebSocket fan-out (<100ms SLA), dispatch offers, and every in-flight request on that pod.

**Evidence.** payment_retry.py lines 331, 356, 423 (`stripe.PaymentIntent.retrieve/confirm/capture(...)` awaited nowhere); corporate_autotopup.py line 128 (`stripe.PaymentIntent.create(...)`); reconciliation.py line 133 (`stripe.PaymentIntent.list(**kwargs)` in a pagination while-loop); stripe_reconcile.py lines 150-153 (`.auto_paging_iter()` iterated synchronously) and 529. Contrast push_retry.py line 253 which correctly uses `await asyncio.to_thread(messaging.send, message)`.

**Fix.** Wrap every Stripe SDK call in these loops in `await asyncio.to_thread(...)` (or a dedicated executor), especially the paginated list iterations in the two reconcile jobs.

### 79. Stranded-hold capture collects the tip from the rider but never credits it to driver_earnings
`backend/utils/payment_retry.py:445` · **MAJOR** · Financial loss · _payments-core_

**Issue.** The requires_capture branch captures grand_total + tip_amount from the rider's card, then marks the ride paid without applying _tip_ride_update, so the driver is underpaid by the tip amount that was actually collected.

**Evidence.** Lines 398-427 compute `owed_d = Decimal(...grand...) + tip_d` and capture that many cents; the paid-write at lines 445-455 sets only `{"payment_status": "paid", "auth_status": "captured"}` — no tip_amount/driver_earnings delta, unlike every settlement path in payment_service.py which merges `**_tip_ride_update(ride, tip)`. This also trips the nightly FARE_ATTRIBUTION_MISMATCH check (stripe_reconcile.py:63 subtracts tip from driver_earnings) for every ride settled this way.

**Fix.** Merge `**_tip_ride_update(ride, tip_d)` into the paid-write in the requires_capture branch.

### 80. Quest expiry uses raw string comparison — crashes on NULL end_date and expires offset-timezone quests early
`backend/utils/quest_tracker.py:90` · **MAJOR** · Correctness / bug · _fares-promos_

**Issue.** update_quest_progress_on_ride_complete compares quest.end_date to now as raw strings: a NULL end_date raises TypeError (None < str) so that quest's progress is never advanced, and an end_date stored with a non-UTC offset (e.g. '...T20:00:00-06:00') compares lexicographically as already past up to 6 hours early, flipping progress to 'expired' and discarding rides completed inside the real window — while quests.py uses proper _parse_dt so the quest still shows active to the driver.

**Evidence.** Lines 89-96: 'if quest.get("end_date", "") < now:' where now = datetime.now(timezone.utc).isoformat(); quest.get returns None (not '') when the column is NULL, and ISO strings with differing offsets are not lexicographically comparable. quests.py lines 39-60 exists precisely because 'the old code compared these as raw strings'.

**Fix.** Reuse quests.py's _parse_dt: parse end_date to a tz-aware datetime, treat None as open-ended, and compare datetimes.

### 81. PDF receipt omits promo/discount line — rows don't reconcile to total
`backend/utils/receipt_pdf.py:45` · **MAJOR** · Financial loss · _reporting-tax_

**Issue.** generate_receipt_pdf's _fare_lines never emits a discount/promo row, yet the grand total is taken from persisted grand_total which is net of discount, so the listed rows sum to MORE than the printed Total whenever a promo was applied.

**Evidence.** _fare_lines builds Base/Distance/Time/Booking/Airport/min-fare/Subtotal/GST/PST/Tip rows but has no handling of ride['discount_amount']/promo_code (grep finds no discount/promo in the file). grand = _q(_d(persisted_grand) + tip) where fare_service builds new_grand_total = new_total_fare + area_fees_total + tax_amount - discount (services/fare_service.py:388). So Subtotal+GST+PST+Tip > Total by the discount amount, and the discount the rider received is never disclosed.

**Fix.** Add a negative 'Promo (code)' / 'Discount' line from ride.get('discount_amount') before the tax rows (mirroring routes/rides/_shared.py:422) so the displayed rows reconcile to the persisted grand_total.

### 82. Reconciliation compares ALL succeeded Stripe PIs against only 'stripe_charge' financial_events — guaranteed false discrepancy alerts
`backend/utils/reconciliation.py:133` · **MAJOR** · Reporting / observability · _background-loops_

**Issue.** _sum_stripe_intents totals every succeeded PaymentIntent for the day (including corporate top-ups, wallet top-ups, and driver-subscription charges) while the DB side sums only event_type='stripe_charge' rows, so any day with a non-ride charge fires the finance discrepancy alert and writes a reconciliation_discrepancies row — chronic false positives that will train finance to ignore the alert that exists to catch real drift.

**Evidence.** Lines 133-136 sum `pi.amount` for every `pi.status == "succeeded"` with no metadata/scope filter; line 85 queries `_sum_financial_events(date, "stripe_charge")`. That non-ride PI scopes exist and occur is proven by stripe_reconcile.py lines 282-284, which must explicitly skip `("driver_subscription", "corporate_topup", "wallet_topup")` scopes to avoid orphan false-flags.

**Fix.** Filter the Stripe sum to ride-charge PIs (exclude the known non-ride metadata scopes, as stripe_reconcile already does) or widen the financial_events sum to include the corresponding top-up/subscription event types.

### 83. Daily Stripe reconciliation crashes on every month-end date
`backend/utils/reconciliation.py:155` · **MAJOR** · Correctness / bug · _background-loops_

**Issue.** _sum_financial_events builds the window end with datetime(date.year, date.month, date.day + 1), which raises ValueError whenever the reconciled day is the last day of a month, so month-end reconciliation is silently skipped ~12 times a year.

**Evidence.** Line 155: `day_end = datetime(date.year, date.month, date.day + 1, tzinfo=timezone.utc).isoformat()`. Reconciliation runs after 02:00 for `yesterday` (line 64), so on the 1st of any month yesterday is a month-end (e.g. Jan 31 -> datetime(2026,1,32) raises). The exception is caught at lines 86-88 and the function returns — and because the date-suffixed lock at line 60 was already claimed via SET NX before running, no replica can retry that day. Finance loses exactly the month-boundary day, the day most likely to matter for CRA/SOC2 reporting.

**Fix.** Use `day_end = (datetime(date.year, date.month, date.day, tzinfo=timezone.utc) + timedelta(days=1)).isoformat()`, and claim the lock only after a successful run (or delete it on failure) so a failed day can retry.

### 84. redis_client only reads REDIS_URL from the environment — a deploy configured with RATE_LIMIT_REDIS_URL/WS_REDIS_URL alone silently runs OTP lockout, SMS caps, caches, and leader locks in-process
`backend/utils/redis_client.py:80` · **MAJOR** · Security · _auth-identity_

**Issue.** _get_redis() reads only os.environ['REDIS_URL'] with no fallback to RATE_LIMIT_REDIS_URL/WS_REDIS_URL, yet production validation (middleware._validate_production_config) requires only RATE_LIMIT_REDIS_URL and lifespan's own error message blesses 'RATE_LIMIT_REDIS_URL + WS_REDIS_URL' as sufficient — such a deploy boots clean while OTP brute-force lockout, the per-phone SMS send cap, row caches, idempotency keys and every redis_set_nx leader lock silently use the per-replica in-process dict, multiplying auth-attack limits by N replicas and resetting them on every restart.

**Evidence.** redis_client.py lines 78-83: `url = os.environ.get("REDIS_URL", ""); if not url: return None` — no other URL consulted. config.py documents RATE_LIMIT_REDIS_URL/WS_REDIS_URL as 'Falls back to REDIS_URL' (lines 108-112) but no reverse fallback exists. middleware.py lines 526-537 only require RATE_LIMIT_REDIS_URL in production. lifespan.py lines 116-123: "Set REDIS_URL (or RATE_LIMIT_REDIS_URL + WS_REDIS_URL) before launch" — the parenthesised alternative leaves redis_client on the dict. routes/auth.py _check_otp_lockout/_enforce_otp_send_cap (lines 142-206) call redis_get/redis_incr from this module; the dict fallback succeeds, so the intended fail-closed 503 never fires.

**Fix.** In _get_redis(), fall back through REDIS_URL → RATE_LIMIT_REDIS_URL (matching the documented chain), or make _validate_production_config require REDIS_URL itself in production. Also log at ERROR (not silently) when production ENV runs on the in-process fallback.

### 85. Referral kind inferred from 'RIDE' code prefix — custom rider referral codes are never paid
`backend/utils/referral_payout.py:201` · **MAJOR** · Financial loss · _fares-promos_

**Issue.** The payout loop classifies a referral as rider vs driver solely by whether the stored code starts with 'RIDE', but rider referral codes may be a stored custom referral_code with any prefix (users.py honours user.referral_code first); such referrals are classified as driver referrals, the referee has no drivers row, _process_one returns silently, and the $5/$5 rewards owed to both sides are never paid and never expired.

**Evidence.** referral_payout.py line 201-202: 'return str(code).upper().startswith("RIDE")'; routes/users.py line 530: 'return user.get("referral_code") or f"RIDE{user[\'id\'][:8].upper()}"' and lines 655-668 accept non-RIDE custom codes at application time (looked up via users.referral_code). In _process_one the driver path (lines 351-359) returns when no driver row exists for the referee.

**Fix.** Store an explicit referral kind (or the referrer's user id + kind) on the referee row at application time and dispatch on that, instead of inferring from the code prefix; backfill and alert on referees whose kind resolution currently dead-ends.

### 86. DSAR deletion keeps full PII for 7 years, contradicting the documented 30-day scrub/anonymization policy
`backend/utils/retention_purge.py:16` · **MAJOR** · Compliance (PIPEDA) · _safety-privacy_

**Issue.** Migration 216 (which this loop invokes) removed the 30-day anonymization step for deletion requests — deleted accounts retain full name/phone/email fully attributable for ~7 years — directly contradicting the CLAUDE.md/PIPEDA commitment that 'all other PII is scrubbed within 30 days' and ride records become anonymized.

**Evidence.** retention_purge.py docstring lines 14-16: "DSAR-deleted accounts HARD-DELETED at 7 years... NO earlier anonymization; records stay attributable until then... Was: anonymize at 30 days." Migration 216_deletion_hard_delete_no_anonymize.sql: "Old Step H: at the 30-day grace it ANONYMIZED the account... New Step H: NO anonymization — records stay fully attributable". CLAUDE.md compliance section still promises 30-day scrub, and PIPEDA limits retention after deletion to what the Transportation Act requires (trip record, insurance periods) — not the whole identity row.

**Fix.** Either restore 30-day anonymization of non-regulated PII (keeping only regulated trip/insurance linkage attributable), or obtain documented legal sign-off and update CLAUDE.md, the privacy policy, and in-app deletion copy so user-facing promises match the implemented retention.

### 87. Retention-purge loop metrics are no-op stubs
`backend/utils/retention_purge.py:260` · **MAJOR** · Reporting / observability · _x-observability_

**Issue.** The retention_purge loop defines local _metric_gauge and _metric_inc as empty `pass` stubs, so its duration and error metrics are never emitted even though every other background loop imports the real metrics functions.

**Evidence.** Lines 260-265: `def _metric_gauge(...): pass  # stub` and `def _metric_inc(...): pass  # stub`. These shadow the real emitters, so the `spinr_bgloop_errors_total{loop="retention_purge"}` call at line 304 and `spinr_bgloop_duration_ms{loop="retention_purge"}` at line 297 do nothing. Compare presence_sweeper.py:49-50 which imports `from .metrics import inc as _metric_inc` / `set_gauge as _metric_gauge`.

**Fix.** Import the real functions the same way as presence_sweeper.py (`from .metrics import inc as _metric_inc, set_gauge as _metric_gauge` with the try/except dual-import fallback) and delete the local stubs, so the PIPEDA retention loop's failures and runtime are observable/alertable.

### 88. Top-level import fallback omits notify_safety_team — safety team never paged on escalation
`backend/utils/safety_checkin_loop.py:40` · **MAJOR** · Correctness / bug · _background-loops_

**Issue.** In the top-level import mode (one of the two entrypoint styles CLAUDE.md mandates supporting), notify_safety_team is never imported, so every escalation's call to it raises NameError which is swallowed by the surrounding except — the safety incident row is created but the safety team WS/email/paging notification silently never happens.

**Evidence.** Lines 35-39 (package mode) import `notify_safety_team, send_push_notification` and `manager as _ws_manager`; the ImportError fallback at lines 40-43 imports only `db`, `send_push_notification`, and `_log_audit`. At line 191 `await notify_safety_team(incident)` then raises NameError, caught by `except Exception as _notify_exc` at line 192 and merely logged, on every single escalation.

**Fix.** Add `from features import notify_safety_team` (and the socket manager import) to the fallback branch; add a regression test that imports the module in top-level mode and asserts notify_safety_team resolves.

### 89. Safety check-in timer keys off the wrong column and resets on every ride update
`backend/utils/safety_checkin_loop.py:93` · **MAJOR** · Correctness / bug · _background-loops_

**Issue.** The loop reads ride.get("ride_started_at") but the query only selects `started_at`, so the value is always None and the 20-minute safety check-in timer falls back to `updated_at`, which is bumped by ordinary ride writes — the check-in fires late or never on exactly the long trips it exists to protect.

**Evidence.** Line 84 selects `columns="id,rider_id,started_at,updated_at"`; line 93 is `started_at_raw = ride.get("ride_started_at") or ride.get("updated_at")` — `ride_started_at` was never selected (migration 137 confirms `started_at` is a generated alias of `ride_started_at`, so the correct key exists in the row). Any write to the rides row during the trip refreshes updated_at and restarts the 20-minute clock at line 100.

**Fix.** Change line 93 to `ride.get("started_at")` (or select `ride_started_at` and read that), and never fall back to updated_at for the trip-start time.

### 90. Sentry scrubber misses request bodies, stack-frame locals, extra/contexts, and breadcrumb data
`backend/utils/sentry_scrub.py:31` · **MAJOR** · Compliance (PIPEDA) · _safety-privacy_

**Issue.** scrub_event only redacts event['message'], logentry.message, and exception value strings; request data, stacktrace frame local variables, event extra/contexts, and breadcrumb['data'] egress to Sentry unscrubbed, so raw GPS coordinates, phone numbers, and addresses in a failing booking request or in function locals reach a third party.

**Evidence.** scrub_event (lines 31-51) touches only message/logentry/exception[].value; scrub_breadcrumb (lines 54-61) touches only crumb['message'], not crumb['data']. server.py:454-464 initializes with FastApiIntegration and send_default_pii=False — which strips headers/cookies/IP but NOT request bodies (max_request_body_size default) or frame locals (include_local_variables defaults to True). A 500 in POST /rides carries pickup_lat/pickup_lng and full addresses in the captured request body and in local vars like `ride` — CLAUDE.md forbids raw GPS and addresses in Sentry events.

**Fix.** In sentry_sdk.init set include_local_variables=False and max_request_body_size='never' (or extend scrub_event to walk exception.values[].stacktrace.frames[].vars, event['request']['data'], event['extra']/'contexts', and scrub_breadcrumb to walk crumb['data']).

### 91. stripe_reconcile fetches an unordered first-2000 slice of all paid rides ever, breaking reconciliation at scale
`backend/utils/stripe_reconcile.py:162` · **MAJOR** · Reporting / observability · _background-loops_

**Issue.** The DB side of the daily reconciliation queries ALL rides with payment_status='paid' (no date filter, no order, limit 2000) and filters to yesterday in Python, so once the platform has >2000 lifetime paid rides yesterday's rides are largely or entirely absent from the arbitrary slice — every legitimate Stripe PI is then flagged STRIPE_ORPHAN and the paid-vs-Stripe checks silently check nothing.

**Evidence.** Lines 162-180: `db_supabase.get_rows("rides", {"payment_status": "paid"}, columns=..., limit=2000)` followed by `db_rides = [r for r in db_rides if ... _in_window(r["ride_completed_at"], ...)]`. repositories/_base.py get_rows applies no default order, so the 2000 rows returned are an unspecified subset. With db_rides empty/incomplete, section 3b (lines 277-297) marks every succeeded ride PI as STRIPE_ORPHAN (logger.error storm + useless audit summary) while real mismatches go undetected.

**Fix.** Push the date window into the query (`ride_completed_at gte/lte yesterday`) with an `order="ride_completed_at"` and paginate; alert if the day's row count hits the limit.

### 92. T4A annual batch consumes its once-per-year Redis claim before doing the work — one failure loses the whole year
`backend/utils/t4a_annual_job.py:92` · **MAJOR** · Legal / regulatory · _background-loops_

**Issue.** The 400-day SET NX key is claimed before _run_issuance runs; if the batch then fails (e.g. the drivers fetch at line 105 errors and returns), the lock stays held and the CRA-required T4A notification batch for that tax year never runs and never retries — and the sole idempotency store is volatile Redis (a flush/failover on issuance day means a duplicate batch; downtime spanning the last day of February means the run is skipped for a year with no catch-up).

**Evidence.** Lines 92-97: `acquired = await redis_set_nx(lock_key, "1", _LOCK_TTL_SECONDS); if not acquired: return; await _run_issuance(prior_year)`. _run_issuance's failure path (lines 104-108) is `logger.error(...); return` with no lock release and no durable claim flag; _maybe_run_tick (lines 86-87) only fires when `now.month == 2 and now.day == days_in_feb`, so there is no later window to recover.

**Fix.** Replace the Redis-only claim with a durable DB claim flag (e.g. a t4a_issuance_runs row keyed on tax year, claimed atomically), release/skip-set it on failure so the next tick retries, and widen the trigger window (e.g. 'any day from Feb 28 while the year's flag is unclaimed').

### 93. Document-expiry notification tap navigates to invalid route
`driver-app/app/driver/notifications.tsx:93` · **MAJOR** · User experience · _driver-app-ux_

**Issue.** Tapping a document_expiry notification pushes '/driver/documents', which is not a registered route (the screen is at '/documents'), so the tap does nothing and the driver cannot reach the upload screen from the notification.

**Evidence.** if (item.type === 'document_expiry') router.push('/driver/documents' as any); — no app/driver/documents.tsx exists; the real route is '/documents'.

**Fix.** Replace '/driver/documents' with '/documents'.

### 94. Raw GPS coordinates written to logs on invalid ride offer
`driver-app/hooks/useDriverDashboard.ts:771` · **MAJOR** · Compliance (PIPEDA) · _driver-app-ux_

**Issue.** The WS ride-offer coord-validation failure path logs the raw pickup/dropoff lat/lng, violating the PIPEDA rule that raw coordinates must never appear in logs, Sentry, or analytics.

**Evidence.** console.error('[WS] dropping ride offer with invalid coords', { ride_id: data.ride_id, pickup_lat: data.pickup_lat, pickup_lng: data.pickup_lng, dropoff_lat: data.dropoff_lat, dropoff_lng: data.dropoff_lng }); — these lat/lng values are emitted verbatim. React Native console.* output is captured by device logs and can be forwarded to Crashlytics/Sentry breadcrumbs.

**Fix.** Log only data.ride_id plus a boolean/reason for which field was invalid (e.g. `{ ride_id, pickup_valid: pLat!==null, ... }`); never emit the coordinate values themselves. Same treatment for the FCM path (line ~1546 already logs only ride_id — good).

### 95. Document-expiry alert deep-links to a non-existent route
`driver-app/hooks/useDriverDashboard.ts:1617` · **MAJOR** · User experience · _driver-app-ux_

**Issue.** The 'document_expiry_warning' push handler navigates to '/driver/documents', but the documents screen actually lives at '/documents' (app/documents.tsx); there is no app/driver/documents.tsx, so the 'Update Now' button leads nowhere — blocking the driver from fixing the expired document that is keeping them from going online.

**Evidence.** router.push('/driver/documents' as any) in the document_expiry_warning branch. Directory listing shows app/documents.tsx (route '/documents') and NO app/driver/documents.tsx. The correct route used by go-online gating elsewhere in this same file is router.push('/documents') (lines ~1311-1327). notifications.tsx line 93 has the identical wrong path.

**Fix.** Change both '/driver/documents' occurrences (useDriverDashboard.ts line 1617 and notifications.tsx line 93) to '/documents' to match the actual route.

### 96. Home screen sends raw GPS coordinates to third-party open-meteo weather API
`rider-app/app/(tabs)/index.tsx:177` · **MAJOR** · Compliance (PIPEDA) · _rider-booking-ux_

**Issue.** The home screen sends the rider's exact latitude/longitude directly to an external third-party service (api.open-meteo.com) purely to show a temperature, leaking precise GPS to an out-of-country third party with no stated purpose or consent.

**Evidence.** index.tsx:177 `fetch(`https://api.open-meteo.com/v1/forecast?latitude=${loc.coords.latitude}&longitude=${loc.coords.longitude}&current_weather=true`)`. CLAUDE.md PIPEDA rules forbid raw lat/lng leaving to third parties and require data minimization/residency; a decorative temperature does not justify shipping full-precision coordinates to a non-Canadian weather host.

**Fix.** Proxy the weather call through the Spinr backend, or round the coordinates to ~1 decimal (city-level) before calling open-meteo, and document the purpose. Remove the direct client-to-third-party GPS call.

### 97. LogRocket session replay enabled with no PII redaction and US data residency
`rider-app/app/_layout.tsx:265` · **MAJOR** · Compliance (PIPEDA) · _x-compliance-legal_

**Issue.** LogRocket session replay is initialized with only a project slug and no field/text redaction config, so full-screen captures of exact pickup/dropoff addresses, rider names, phone numbers and email are streamed to a US-hosted third party — violating PIPEDA data-residency and the 'never log raw addresses/phone/name' rules.

**Evidence.** rider-app/app/_layout.tsx line 265 `LogRocket.init('gfuign/spinr')` (and driver-app/app/_layout.tsx line 306) with no `textSanitizer`/`inputSanitizer`/DOM-privacy options; `LogRocket.identify(uid)` at line 383. Session replay of the ride flow renders exact addresses and PII on screen. CLAUDE.md forbids raw GPS/addresses/full names/phones/emails leaving the app and requires Canadian data residency with legal sign-off for exceptions.

**Fix.** Either disable session replay or configure LogRocket's privacy redaction (sanitize all text inputs and PII-bearing views, redact address/phone/name fields), and confirm/justify a Canadian-hosted replay endpoint or obtain documented residency sign-off.

### 98. Emergency contacts load failure silently shows empty state
`rider-app/app/emergency-contacts.tsx:60` · **MAJOR** · User experience · _rider-account-ux_

**Issue.** If GET /users/emergency-contacts fails, the error is swallowed and the screen renders the 'No emergency contacts yet' empty state, telling the rider they have no safety contacts when they may actually have several.

**Evidence.** fetchContacts() catch block: `} catch { console.error('Failed to fetch emergency contacts'); } finally { setLoading(false); }` — no error surfaced to the user; contacts stays `[]`, so the empty-state block (`contacts.length === 0`) renders. On a safety screen this misleads the rider into thinking their emergency contacts are gone (and may prompt re-adding duplicates or leave them unprotected).

**Fix.** On fetch failure, set an error flag and render a distinct 'Couldn't load your contacts — tap to retry' state (with a retry button) instead of the empty state, and use getApiErrorMessage in a toast; per CLAUDE.md, do not silently swallow errors on a safety surface.

### 99. ToS/Privacy consent version never persisted at signup
`rider-app/app/profile-setup.tsx:105` · **MAJOR** · Compliance (PIPEDA) · _x-compliance-legal_

**Issue.** The signup gate collects a Terms/Privacy acceptance checkbox but the accepted consent-language version is never sent to or stored by the backend, so Spinr cannot prove consent or trigger re-consent on material changes as CLAUDE.md requires.

**Evidence.** profile-setup.tsx gates the submit button on `tosAccepted` (line 50/187/313) but `createProfile({first_name,last_name,email,gender})` (lines 105-110) sends no consent/terms version or timestamp. Backend `create_user` in backend/routes/auth.py (new_user dict, lines 981-991) writes no consent_version/terms_accepted_at field, and no users-table migration adds one (only corporate `terms_accepted_version` in migration 224 and marketing `consent_version` in migration 190 exist). CLAUDE.md: 'Consent: consent language version is stored on signup. Material changes require re-consent.'

**Fix.** Add `consent_version`/`terms_accepted_at` columns to users (new migration, with RLS), have profile-setup send the active policy version, and persist it in create_user; block or re-prompt when the stored version is older than the current policy version.

### 100. Google Pay path re-rates driver, double-crediting the tip into driver_earnings
`rider-app/app/ride-completed.tsx:361` · **MAJOR** · Financial loss · _rider-booking-ux_

**Issue.** handleGooglePay calls rateRide unconditionally without checking hasRatedRef, so if the rider first taps 'Pay & Done' (which rates and posts the tip via /rate) and that payment fails, then pays via Google Pay, /rate is posted a second time and the driver's earnings accumulate the tip twice while only one tip is charged.

**Evidence.** ride-completed.tsx:375-377 `try { await rateRide(rideId, rating, comment, tipAmount>0?tipAmount:undefined); } catch {}` — no `hasRatedRef` guard, unlike handleSubmit (lines 289-294) which explicitly latches hasRatedRef because '/rate accumulates tip into driver_earnings on every call'. Both the Pay&Done and Google Pay buttons are shown simultaneously for Android no-hold card rides (line 733), and handleGooglePay's only guards are isSubmitting/sheetLoading/alreadyPaid — none of which block a retry after a failed handleSubmit.

**Fix.** Guard the rateRide call in handleGooglePay with `if (!hasRatedRef.current) { ...; hasRatedRef.current = true; }`, mirroring handleSubmit.

### 101. Fare-load error UI is dead code — store swallows the error so a failed estimate fetch leaves a blank, stuck screen
`rider-app/app/ride-options.tsx:232` · **MAJOR** · User experience · _rider-booking-ux_

**Issue.** handleFetchEstimates wraps `await fetchEstimates()` in try/catch to set fetchError, but the store's fetchEstimates catches its own errors and never rethrows, so the catch never runs and the 'Could not load fares. Tap to retry' retry UI can never appear.

**Evidence.** ride-options.tsx:232-234 `try { await fetchEstimates(); } catch { setFetchError('Could not load fares. Tap to retry.'); }`. rideStore.ts fetchEstimates (lines 476-479) does `catch (error) { console.error(...); set({ isLoading: false, error: ... }); }` and does NOT rethrow. On a network/backend failure estimates stays [] and isLoading=false, so the screen renders neither the skeleton, the fetchError retry block, nor the allUnavailable busy banner (which requires estimates.length>0) — the rider sees an empty 'Choose a ride' sheet with no price, no error, no retry, and the footer/Confirm button hidden.

**Fix.** Either rethrow from the store's fetchEstimates so the screen catch fires, or drive the ride-options error state off the store's `error` field (useRideStore(s=>s.error)) instead of the local fetchError that is never set.

### 102. Persisted TanStack Query cache not cleared on logout — cross-user PII leak
`shared/store/authStore.ts:606` · **MAJOR** · Compliance (PIPEDA) · _shared-package_

**Issue.** logout() clears appCache and per-session stores but never clears the persisted TanStack Query cache ('spinr-query-cache'), so after user A logs out and user B logs in on the same device, B briefly sees A's driver row, earnings, and notifications hydrated from disk before the network refetch resolves.

**Evidence.** logout() (authStore.ts:606-634) calls setInMemoryToken(null), storage.deleteItem(...), appCache.clearUserCache(), and _runLogoutCallbacks(), but nothing calls queryClient.clear()/removeQueries or purges the 'spinr-query-cache' AsyncStorage key. queryClient.ts:57-66 persists driver.me, driver.earnings, and notifications.list to AsyncStorage under key 'spinr-query-cache' with 30-min gcTime, and a grep for queryClient.clear/removeQueries across the repo returns nothing.

**Fix.** In logout() (and the interceptor's G2 hard-logout path), call queryClient.clear() and asyncStoragePersister.removeClient() (or AsyncStorage.removeItem('spinr-query-cache')) so no previous user's cached rows survive into the next session.

### 103. Safety-report GPS always null on native — locationStore.initialize relies on web-only navigator APIs
`shared/store/locationStore.ts:57` · **MAJOR** · Correctness / bug · _shared-package_

**Issue.** locationStore.initialize() acquires position via navigator.permissions/navigator.geolocation (web-only globals absent on React Native), so on iOS/Android it throws and currentLocation stays null forever; the only writer setCurrentLocation is never called anywhere else, so driver safety reports submit with location: null.

**Evidence.** initialize() (locationStore.ts:57-92) uses navigator.permissions.query and navigator.geolocation.getCurrentPosition; on native these are undefined so the call throws into the catch (line 107) which sets error and leaves currentLocation null. A repo grep shows setCurrentLocation/setLastKnownLocation are only defined here, never invoked. driver-app/app/report-safety.tsx:53 reads currentLocation and builds reportData.location = (location?.latitude != null ...) ? {...} : null (report-safety.tsx:97-101), so the location is always null on device.

**Fix.** Populate the store from the native watcher (expo-location) — either have useDriverDashboard's watcher call setCurrentLocation, or make report-safety fetch a one-shot expo-location fix on submit instead of reading this store.


---

## Minor (182)


### Correctness / bug

- **$regex terms inside $or clauses do not escape SQL LIKE wildcards, unlike the top-level $regex path** — `backend/repositories/_base.py:503` _(auth-identity)_. _build_or_clause_term builds `col.ilike.*<value>*` using _postgrest_pattern, which escapes PostgREST syntax characters (*, comma, parens) but not `%` or `_`, whereas the top-level $regex path uses _escape_like; a user-typed `%` or `_` in any $or-based search (admin user/driver search) acts as a wildcard, over-matching results and permitting cheap full-scan patterns (e.g. '%%%') that the C6 fix at line 578 was specifically added to block on the other path. _Fix:_ Apply _escape_like to the $regex value before _postgrest_pattern in _build_or_clause_term so both filter paths neutralize `%` and `_` identically.
- **set_driver_available's is_online clamp is read-then-write, allowing is_available=True with is_online=False** — `backend/repositories/driver_repo.py:163` _(dispatch)_. When releasing a driver back to the pool (offer expiry, decline, loser preempt), set_driver_available reads is_online and then writes is_available=True filtered only on id; a driver tapping Go Offline between the read and the write ends up persisted with is_available=True and is_online=False, breaking the CLAUDE.md invariant is_available => is_online (impact is bounded because dispatch filters both flags, but the DB state is wrong and any future reader of is_available alone mis-dispatches). _Fix:_ Make the availability grant conditional in the write itself: `.update({"is_available": True, ...}).eq("id", driver_id).eq("is_online", True)` (keeping the release path unconditional), removing the TOCTOU window.
- **status-override valid set disagrees with its Pydantic Literal — 'rejected' and 'needs_review' are both unusable** — `backend/routes/admin/drivers.py:1536` _(admin-routes)_. PUT /drivers/{id}/status-override can never set needs_review (422 from the Literal) and always 400s on rejected (handler's valid set), so two of the advertised states are unreachable. _Fix:_ Make the Literal and the `valid` set identical (include both rejected and needs_review, or neither).
- **GPS cleanup swallows delete failures and always returns counts of -1 with HTTP 200** — `backend/routes/admin/maintenance.py:80` _(admin-routes)_. admin_cleanup_location_history logs and continues when either delete_many fails and returns deleted_historical/deleted_idle hardcoded to -1, so operators cannot tell whether retention cleanup actually ran — violating the 'never swallow DB errors' convention. _Fix:_ Propagate delete failures as 503, and return real deleted counts (delete with returning/count or a count-before-delete).
- **Recipient resolution silently truncates every audience at 10,000 users** — `backend/routes/admin/messaging.py:92` _(admin-routes)_. _resolve_recipients caps all audience queries at limit=10000 with no warning, so once the user base passes 10k a 'send to all customers' broadcast quietly skips everyone beyond the cap while total_recipients reports only the truncated count. _Fix:_ Page through the users table (keyset pagination on id) until exhausted, or at minimum compare count_documents against the fetched size and fail loudly when truncated.
- **vehicle_cascade_map accepted on create but silently dropped** — `backend/routes/admin/service_areas.py:420` _(admin-routes)_. ServiceAreaCreateRequest declares vehicle_cascade_map but admin_create_service_area never writes it to the insert doc, so dispatch cascade rules configured at creation are silently lost — the exact 'Pydantic silently drops them' failure the file's own comment warns about. _Fix:_ Add `"vehicle_cascade_map": area.vehicle_cascade_map` to the create doc.
- **Driver-subscription list filters status in Python after a 200-row fetch** — `backend/routes/admin/subscriptions.py:122` _(admin-routes)_. GET /admin/driver-subscriptions fetches only the first 200 rows and then filters by status client-side, so with more than 200 subscriptions the status filter silently misses matching rows. _Fix:_ Push the status into the DB filter (`{"status": status}`) and add limit/offset pagination.
- **suspended_until accepted as an arbitrary unvalidated string** — `backend/routes/admin/users.py:50` _(admin-routes)_. UserStatusRequest.suspended_until is Optional[str] with no ISO-8601/future-date validation, so a malformed value either 500s the timestamptz write or stores garbage that the booking-time auto-lift comparison mishandles, leaving a rider suspended indefinitely despite the admin setting a temporary window. _Fix:_ Type it as Optional[datetime] (Pydantic parses/validates) and reject past timestamps.
- **Fare-config update silently ignores minimum_fare and booking_fee; no bounds or audit on fare rates** — `backend/routes/admin/vehicle_fleet.py:136` _(admin-routes)_. FareConfigUpdateRequest omits minimum_fare and booking_fee, so an admin editing those fields gets 'Fare configuration updated' with no change; all fare fields are also unbounded floats (negative rates accepted) and fare-config CRUD writes no audit rows. _Fix:_ Add minimum_fare/booking_fee to the update model and handler, add ge=0 bounds on all money fields, and audit fare-config create/update/delete.
- **Refresh mints JWTs whose session_id claim is the stored user-agent string** — `backend/routes/auth.py:1528` _(core-infra)_. When users.current_session_id is empty, /auth/refresh falls back to row.get('user_agent') as the session_id, embedding a browser UA string in the token's session_id claim and corrupting session bookkeeping/audit correlation. _Fix:_ Drop the fallback: `session_id = user.get("current_session_id") or None` and pass None through to create_jwt_token.
- **is_active filter applied after pagination with unfiltered X-Total-Count** — `backend/routes/corporate_accounts.py:196` _(wallet-corporate)_. get_corporate_accounts applies the is_active filter in Python after the skip/limit DB query and always sets X-Total-Count to the whole-table count, so filtered listings return short/inconsistent pages and a wrong total header. _Fix:_ Push is_active into list_corporate_accounts_filtered as a server-side eq filter and count with the same filters (or drop the header when filters are active).
- **resolution and refund_amount are unvalidated (arbitrary strings, negative/zero amounts)** — `backend/routes/disputes.py:47` _(support-misc)_. ResolveDisputeRequest.resolution is a free string — a typo like 'aproved' marks the dispute 'resolved' with no refund and no error — and refund_amount/requested_amount accept negative values (negative refund reaches Stripe and fails as an opaque 502; a rider-supplied requested_amount of 0 is silently replaced by the full fare via the `or` at line 86). _Fix:_ Use Literal["approved","partial_refund","rejected"] for resolution, Field(gt=0) on refund_amount/requested_amount, and compare requested_amount against None rather than falsiness.
- **Duplicate-dispute guard is check-then-insert with no DB constraint** — `backend/routes/disputes.py:70` _(support-misc)_. Two concurrent create_dispute calls for the same ride both pass the 'existing open dispute' read and both insert, producing duplicate open disputes that can each be resolved with a Stripe refund (different dispute_ids yield different idempotency keys, so double refund is possible). _Fix:_ Add a partial unique index on disputes(ride_id) WHERE status IN ('open','under_review') and handle the conflict as 400, or key the refund idempotency on ride_id.
- **Fire-and-forget Zoho ticket task reference not retained** — `backend/routes/disputes.py:109` _(support-misc)_. asyncio.create_task(create_ticket_for_dispute(...)) discards the Task reference, so the task can be garbage-collected before it runs and the dispute's Zoho ticket is silently never created — support loses visibility of refund requests. _Fix:_ Keep the task in a module-level set with a discard done-callback (or use the project's spawn() helper used elsewhere for background sends).
- **Admin GET /drivers with lat/lng ignores radius and vehicle filters** — `backend/routes/drivers/location.py:302` _(drivers)_. When lat/lng are supplied, get_drivers returns the first 100 is_online drivers regardless of the requested radius/vehicle_type, so the admin nearby view shows arbitrary, unfiltered drivers. _Fix:_ Apply the same geo-bounds + distance filter used by /nearby (or drop the misleading lat/lng parameters).
- **Legacy location batch accepts unbounded point lists and out-of-range coordinates** — `backend/routes/drivers/location.py:343` _(drivers)_. The legacy batch path has no size cap (v2 caps at 500) and neither path bounds lat/lng in TripLocationPoint, so a buggy/malicious client can post huge arrays into breadcrumb persistence or write invalid coordinates (lat=999) into the drivers row, corrupting distance math and map rendering. _Fix:_ Cap legacy batches (e.g. 500 points) and add ge/le bounds to lat/lng on both models, rejecting out-of-range values.
- **apply_referral_code has a check-then-write race allowing overwrite of an applied referral** — `backend/routes/drivers/referrals.py:196` _(drivers)_. Two concurrent applies (or a retry racing the first write) both pass the 'already applied' read and both write referred_by, so the credited referrer can be silently swapped and, with different codes, two referrers can each see the referee counted. _Fix:_ Make the update conditional (`{"id": ..., "referral_code_used": None}`) and treat zero rows updated as 'already applied'.
- **Client-supplied naive captured_at timestamps can 500 the completed-ride retention check** — `backend/routes/drivers/ride_complete.py:96` _(drivers)_. _completed_batch_is_within_retention compares point.captured_at (parsed by pydantic; stays naive if the client omits a timezone) against tz-aware window datetimes, raising TypeError→500 instead of a clean 422 for delayed offline batches. _Fix:_ Normalize captured_at to UTC-aware in a validator (assume UTC when naive, or reject naive timestamps with 422).
- **Subscription-expiry lock TTL (6h+5min) exceeds the 6h sleep — enforcement cadence silently halves** — `backend/routes/drivers/subscriptions.py:1584` _(background-loops)_. The winning pod's lock (6h+300s) is still live when every pod wakes at the 6h mark, so all replicas — including the previous winner — are denied and sleep another 6h, making warning and offline-enforcement passes run roughly every 12h instead of 6h; a subscription-gated driver can keep taking rides up to ~12h past expiry, and the 24h expiry warning can arrive with half its intended lead time. _Fix:_ Use a TTL below the sleep interval (e.g. 6h − 5min) or a per-window bucket key so exactly one replica wins each 6-hour window.
- **Favorite duplicate check compares only latitudes, ignoring longitudes** — `backend/routes/favorites.py:70` _(support-misc)_. The duplicate-route check treats two routes as identical when only pickup_lat and dropoff_lat are within 0.001 degrees, so a genuinely different route with similar latitudes (common on east-west trips in grid cities) is silently not saved and the old favorite is returned instead. _Fix:_ Include pickup_lng and dropoff_lng in the proximity comparison (all four coordinates within the tolerance).
- **use_count increment is a non-atomic read-modify-write** — `backend/routes/favorites.py:107` _(support-misc)_. use_favorite_route reads use_count then writes fav.use_count+1, so concurrent uses (or replica races) lose increments, corrupting the ordering the favorites list is supposed to rely on. _Fix:_ Use a Postgres RPC or SQL `use_count = use_count + 1` (Supabase rpc) instead of client-side arithmetic.
- **Loyalty balance update is a non-atomic read-modify-write** — `backend/routes/loyalty.py:162` _(fares-promos)_. earn_points_for_ride computes new_balance/new_lifetime from the account row read earlier and writes them back unconditionally, so two concurrent earns for different rides (retry, two devices) can lose one award's points and mis-set the tier, even though the per-ride transaction insert itself is idempotent. _Fix:_ Increment atomically (RPC: UPDATE loyalty_accounts SET points = points + :n, lifetime_points = lifetime_points + :n ... RETURNING) and derive the tier from the returned lifetime value.
- **Module-level shared Response instance reused across requests lets middleware-set headers cross-leak** — `backend/routes/offer_card.py:49` _(dispatch)_. _NOT_FOUND is a single Response object returned for every 404, and per-request middleware mutates response.headers in place (X-Request-ID at middleware.py:242, per-origin Access-Control-Allow-Origin at :657), so concurrent 404 responses share one headers dict and a caller can receive another request's request-ID or CORS origin header. _Fix:_ Replace the shared constant with a fresh `Response(status_code=404)` constructed at each return site.
- **Malformed expiry_date silently makes a promo never expire** — `backend/routes/promotions.py:127` _(fares-promos)_. In _validate_promo_for_user, a ValueError from parsing expiry_date is swallowed (only HTTPException is re-raised), so a promo whose expiry string is unparseable is treated as valid forever instead of failing loudly. _Fix:_ Treat an unparseable expiry as invalid (400) or at minimum logger.error it; use parse_iso_utc like list_available_promos does.
- **Promo application inserted before uses-increment; 409 leaves orphan row that burns the rider's per-user limit** — `backend/routes/promotions.py:285` _(fares-promos)_. _record_promo_application inserts the promo_applications row first and only then calls increment_promo_uses; when the promo was concurrently exhausted the 409 path leaves the application row in place, and the per-user limit check (count of promo_applications, line 146-153) then permanently blocks the rider from a promo they never actually received. _Fix:_ Claim capacity first (increment_promo_uses), then insert the application; or delete the application row before raising the 409.
- **Duplicate promo-code check with limit=1 plus post-filter allows duplicate global codes** — `backend/routes/promotions.py:636` _(fares-promos)_. For a global promo, the uniqueness lookup fetches only one row matching the code and then filters out area-specific rows in Python, so if the single returned row happens to be area-scoped a second global promo with the same code is created; validation then resolves the code to an arbitrary one of the duplicates. _Fix:_ Query with the service_area_id-is-null condition server-side (or raise the limit and filter), add a partial unique index on (code) WHERE service_area_id IS NULL plus unique (code, service_area_id), and add the same check to admin/promotions.py.
- **max_participants enforced with check-then-insert race** — `backend/routes/quests.py:214` _(fares-promos)_. Two concurrent joins can both pass the participant-count read and both insert, exceeding a quest's max_participants cap (each participant is a potential reward liability), and a DB error during the count is swallowed and lets the join proceed. _Fix:_ Enforce the cap atomically (e.g. a participants counter on quests with UPDATE ... WHERE participants < max_participants RETURNING, or a DB constraint), and fail closed on count errors.
- **Rider typing indicator is sent to a WebSocket key built from drivers.id, not the driver's user_id** — `backend/routes/rides/chat.py:230` _(rides-secondary)_. send_typing_indicator targets `driver_{ride.driver_id}` (the drivers-table row id) while WebSocket connections register as `driver_{user_id}`, so rider-to-driver typing indicators are silently delivered to a key that never exists and the driver never sees them. _Fix:_ Resolve the driver row once and use its user_id for the target: `drv = await _deps.db_supabase.get_driver_by_id(driver_id); target = f"driver_{drv['user_id']}" if drv and drv.get("user_id") else None`.
- **Safety check-in 'I'm okay' is lost across replicas when Redis is unset, causing false escalations** — `backend/routes/rides/safety.py:207` _(rides-secondary)_. safety_checkin_response records the rider's 'I'm okay' via redis_set, which silently falls back to a per-process dict when REDIS_URL is unset — with multiple replicas the safety_checkin_loop on another replica never sees the key and escalates the ride to the trust-and-safety team even though the rider confirmed they were fine. _Fix:_ Have safety_checkin_response (or the loop) detect the in-memory fallback and fall back to a durable marker — e.g. also write a `safety_checkin_ok_at` column/flag on the ride row that the loop checks before escalating.
- **Mid-trip stops bypass all geofence and coordinate validation** — `backend/routes/rides/stops.py:62` _(rides-booking)_. AddStopMidTripRequest has no lat/lng range validators and add_stop_mid_trip performs no service-area check, so a rider can add a stop with any coordinates (including outside every service area or lat=999) to an active ride — defeating the booking-time geofence gate, mispricing the ride via multi_leg_distance on garbage coordinates, and directing the driver outside the covered operating area. _Fix:_ Add ge/le bounds on lat/lng in AddStopMidTripRequest and run the same _get_active_service_area_for_point gate against the new stop before accepting it.
- **Client-supplied reported_at stored unvalidated — malformed value 503s the safety report** — `backend/routes/safety.py:79` _(safety-privacy)_. body.reported_at is an arbitrary string written straight into a timestamptz column; a client bug sending a malformed date makes the insert raise, so the whole safety report is rejected with 503, and a well-formed but bogus timestamp corrupts the incident timeline. _Fix:_ Parse reported_at with datetime.fromisoformat (fall back to server time on failure) instead of trusting the raw string, and clamp it to a sane window around now.
- **Zoho safety ticket created via unreferenced fire-and-forget task** — `backend/routes/safety.py:99` _(safety-privacy)_. asyncio.create_task(create_ticket_for_safety(incident)) keeps no reference to the task, so it can be garbage-collected before completion and any exception is never observed — the safety ticket silently fails to reach Zoho. _Fix:_ Hold the task in a module-level set with a done-callback that discards it and logs exceptions (e.g. task.add_done_callback that logs task.exception()).
- **Emergency-contact reads swallow DB errors and return an empty list** — `backend/routes/users.py:460` _(core-infra)_. GET /users/emergency-contacts returns {'contacts': []} on any DB failure and POST treats the same failure as 'no existing contacts', silently misreporting a safety feature and bypassing the 3-contact cap — contrary to the project's explicit no-swallow rule for DB errors. _Fix:_ Raise HTTPException(503) from both handlers on DB failure so clients retry instead of rendering a false empty state.
- **ride_status_update cooldown is consumed before the authorization/ride-existence check** — `backend/routes/websocket.py:1054` _(websocket-notify)_. The 2s echo cooldown timestamp is updated before ride_id validation and participant authorization, so an invalid or unauthorized ride_status_update from a client resets the cooldown and can suppress that client's next legitimate resync echo. _Fix:_ Only update _last_status_echo_at after the ride is found and the caller is confirmed to be a ride participant, so invalid/unauthorized messages don't burn the cooldown budget.
- **Mirror search interpolates user input into PostgREST or_ filter with only commas escaped** — `backend/services/zoho_desk_db.py:124` _(support-misc)_. list_mirror builds the or_ filter by string-interpolating the admin's search term, escaping only commas; terms containing parentheses, dots, or '%' can produce malformed PostgREST syntax so the query raises and the Help Desk silently falls back to the credit-consuming live Zoho API (or returns wrong results for '%'-containing searches). _Fix:_ Sanitize the term to a safe character allowlist (or escape PostgREST reserved characters and LIKE wildcards) before interpolation.
- **Month-add clamp permanently drifts allowance period anchors** — `backend/utils/allowance_reset.py:55` _(wallet-corporate)_. _add_one_month clamps Jan 31 to Feb 28 and then advances from the clamped date, so an allowance anchored on the 29th-31st permanently drifts to the 28th (Jan 31 → Feb 28 → Mar 28 → ...), shortening/shifting members' budget periods relative to the configured anchor. _Fix:_ Store the original anchor day on the allowance row (or derive it from the initial period_start) and compute each new period_end from the anchor, clamping per-month without persisting the clamp.
- **Crash between 'retrying' claim and release wedges the ride permanently** — `backend/utils/payment_retry.py:319` _(payments-core)_. If the process dies after claiming payment_status='retrying' but before any subsequent write, the ride is never rescanned: the retry query only matches failed/requires_action/processing and the stuck-processing reconciler only queries 'processing'. _Fix:_ Include 'retrying' (with an updated_at staleness cutoff) in the retry scan or in the stuck-processing reconciler.
- **Leader-lock TTL (1.5× interval) exceeds the tick interval, degrading payment-retry cadence to ~2× the design** — `backend/utils/payment_retry.py:593` _(background-loops)_. The lock TTL of 450s outlives the 300s loop interval, so the winning pod is denied by its own unexpired lock on its next tick and (on a single replica, or when replica phases align) failed-payment retries effectively run every ~450-600s instead of every 5 minutes — and the comment claims the opposite ('a real lock expires before the next election'). _Fix:_ Use a TTL shorter than the interval (or delete the key at end of tick, or use a per-interval bucket key as surge_engine does) in both payment_retry and scheduled_rides.
- **Push retry can deliver a duplicate notification if the sent_at update fails after a successful send** — `backend/utils/push_retry.py:167` _(websocket-notify)_. When FCM/Expo delivery succeeds but the subsequent UPDATE that stamps sent_at fails, the row keeps sent_at NULL and becomes due again after the back-off, so the same push (e.g. a ride offer or SOS) is delivered a second time. _Fix:_ Retry the sent_at write with a short bounded retry, or record delivery idempotently (e.g. a provider message-id / delivered flag checked before re-send) so a failed mark-sent cannot cause a duplicate delivery.
- **Peak-rides window off by one hour on both edges vs its stated 7-9 AM / 5-8 PM definition** — `backend/utils/quest_tracker.py:111` _(fares-promos)_. The peak window is documented as 7-9 AM and 5-8 PM but the check '7 <= hour <= 9 or 17 <= hour <= 20' also counts rides completing 9:00-9:59 and 20:00-20:59, paying quest progress (and ultimately rewards) for rides outside the advertised peak window. _Fix:_ Use half-open ranges (7 <= hour < 9 or 17 <= hour < 20) or update the advertised window copy to match, keeping quest descriptions and payout logic consistent.
- **Purge cadence broken: jitter floor (21.6h) under lock TTL (23h) skips daily runs, and the 03:00 scheduler is dead code** — `backend/utils/retention_purge.py:306` _(safety-privacy)_. The loop sleeps 86400*(0.9..1.1) from process start — minimum 21.6h — while the leader lock TTL is 23h, so a single-replica deployment's next tick finds its own previous lock still live and skips, making the 'daily' purge run every ~43h in the worst case; _seconds_until_next (the documented ~03:00 UTC alignment) is never called. _Fix:_ Sleep via _seconds_until_next(3) so runs align to 03:00 UTC and are always > lock TTL apart, or drop the lock TTL below the minimum sleep (e.g. 20h).
- **in_progress query capped at 200 rows with no pagination — rides beyond the cap never get safety check-ins** — `backend/utils/safety_checkin_loop.py:80` _(safety-privacy)_. get_rows(..., limit=200) with no ordering or pagination means that once more than 200 rides are simultaneously in_progress, an arbitrary subset is silently excluded from safety check-ins and escalations every tick. _Fix:_ Page through results (offset loop or keyset on id) or at minimum order by started_at ascending so the oldest — highest-risk — rides are always included.
- **ride_search_timeout armed with bare asyncio.create_task — task can be garbage-collected mid-sleep** — `backend/utils/scheduled_rides.py:210` _(dispatch)_. The 5-minute no-drivers auto-cancel for a dispatched scheduled ride is spawned via a bare `asyncio.create_task(...)` with no retained reference; per the asyncio docs the event loop holds only a weak reference, so the timer can be GC'd during its 300s sleep and never fire, leaving the ride hanging in 'searching' until the stuck-ride sweeper's slower backstop catches it. _Fix:_ Import and use utils.background.spawn (or _deps.spawn) instead of bare asyncio.create_task, matching the live booking path.
- **Leader-lock TTL (90s) exceeds the 60s tick and is never released, halving scheduled-dispatch cadence** — `backend/utils/scheduled_rides.py:257` _(dispatch)_. check_scheduled_rides acquires spinr:scheduled_rides:lock with NX TTL=90s and never deletes it after the tick, so with a 60s(+/-6) loop every second tick is skipped (single replica: effective cadence ~120s; multi-replica: ~90-120s), meaning a scheduled ride can enter 'searching' up to ~2 minutes after its scheduled_time and 10-minute reminders drift equally — contradicting the module's own '60 seconds' contract. _Fix:_ Either set the lock TTL below the tick interval (e.g. 45s) or DELETE the lock in a finally block after the tick completes so the next 60s tick can run.
- **Surge leader lock silently degrades to all-replicas-leader on Redis errors, producing conflicting price writes** — `backend/utils/surge_engine.py:373` _(background-loops)_. redis_set_nx swallows Redis exceptions and falls back to a per-replica in-process dict (and the loop additionally forces is_leader=True on a raised error), so during a Redis outage every replica recalculates and writes surge simultaneously: N duplicate surge_pricing history rows per area every 2 minutes plus interleaved service_areas.surge_multiplier writes that can flap the rider-visible price between replicas' differing counts. _Fix:_ Make redis_set_nx distinguish 'Redis configured but erroring' (raise or return a tri-state) from 'Redis unset', and have surge skip the write-path (keep last multiplier) rather than multi-write when leadership can't be established.
- **Durable event not stored in replay outbox when the outbox pipeline fails, creating a reconnect gap** — `backend/utils/ws_pubsub.py:216` _(websocket-notify)_. If the seq INCR succeeds but the outbox rpush/publish pipeline throws, the message is bare-published live but never written to the outbox, so a client that reconnects with ?last_seq will not have this durable ride event replayed even though its seq counter advanced. _Fix:_ On pipeline failure, retry the outbox rpush independently of the publish (or fail the whole publish so the caller's local-delivery fallback is used), so any event counted in the seq sequence is also recoverable on reconnect.
- **cachedClient reads a token key that is never populated → all its requests are unauthenticated** — `shared/api/cachedClient.ts:40` _(shared-package)_. cachedClient's getStoredToken reads SecureStore/localStorage key 'auth_token', but authStore.setTokens keeps the access token in memory only and deletes 'auth_token' from storage, so getAuthHeader always resolves null and every cachedClient request goes out without an Authorization header (401 on any authenticated endpoint). _Fix:_ Route cachedClient through the same getAuthHeader() exported from client.ts (in-memory-first + refresh), or delete cachedClient if it is truly unused (no rider/driver imports found) to avoid a latent auth-broken code path.
- **Web 401-clear reads sessionStorage but deletes localStorage (key mismatch)** — `shared/api/client.ts:937` _(shared-package)_. The web token read path uses sessionStorage.getItem('auth_token') while the G2 401-clear path uses localStorage.removeItem('auth_token'), so on web the two disagree about where the token lives and a clear can miss the actual stored value. _Fix:_ Pick one web storage (sessionStorage per authStore) and use it consistently for read and clear, or drop client-side web token handling entirely if cookies are authoritative.


### User experience

- **admin_token cookie max-age (8h) shorter than admin access token TTL (12h)** — `admin-dashboard/src/app/api/auth/set-cookie/route.ts:4` _(admin-dashboard)_. The admin_token cookie is written with an 8h max-age while CLAUDE.md specifies admin access tokens live 12h, so the middleware cookie can expire before the in-memory session, causing a hard refresh mid-session to bounce to /login despite a still-valid session. _Fix:_ Align the cookie max-age with the actual admin access-token lifetime (or refresh the cookie on every silentRefresh, which already calls setAuthCookie) so cookie expiry never precedes a refreshable session.
- **"No Driver Found" ride tab silently shows all rides** — `admin-dashboard/src/app/dashboard/rides/page.tsx:184` _(admin-dashboard)_. The 'no_driver_found' status tab is explicitly excluded from setting a status filter, so selecting it fetches the unfiltered ride list (same as 'All') while appearing selected. _Fix:_ Either map the tab to the real backend status used for no-driver rides (e.g. cancelled with a no-driver reason / a dedicated filter param) or remove the tab; do not present a filter control that does nothing.
- **OTP consumed before user lookup — a DB blip burns the code and can exhaust the send cap** — `backend/routes/auth.py:816` _(core-infra)_. verify_otp deletes the OTP record before the get_user_by_phone lookup, so if that lookup 503s the correct code is already consumed; the user must request a new SMS, and repeated blips walk them into the 5-per-hour send cap. _Fix:_ Defer OTP deletion (or use the mark-verified path with a short reuse window) until after the user row is resolved and the token is minted, or delete inside the success path only.
- **Logout is gated behind a 3/min per-IP limit and a live access token** — `backend/routes/auth.py:1567` _(core-infra)_. POST /auth/logout requires an unexpired access token (get_current_user) and is rate-limited to 3/minute per client IP, so users behind carrier CGNAT (several riders share one IP) or holding an expired 15-min token get 429/401 on sign-out and their 30-day refresh token is never revoked server-side. _Fix:_ Raise the logout limit (or key it per-user), and accept a refresh-token-only revocation path (the refresh token itself proves possession) so logout works with an expired access token.
- **Bookings section filter applied after pagination** — `backend/routes/corporate_company_bookings.py:206` _(wallet-corporate)_. list_bookings fetches a skip/limit page of rides and only then drops rows whose booker isn't in the requested section, so section-filtered pages return fewer rows than limit (possibly zero) while later pages still contain matches — the portal's section filter shows incomplete/empty lists. _Fix:_ Resolve the section's member_ids first and add `corporate_member_id: {"$in": [...]}` to the DB filter before pagination.
- **Corporate top-up Stripe errors surface as raw 500** — `backend/routes/corporate_wallet.py:143` _(wallet-corporate)_. manual_topup's stripe.PaymentIntent.create (including off_session confirm=True, which raises CardError on decline/SCA-required) is not wrapped in try/except, so a declined company card returns a generic 500 instead of an actionable 402/502 message. _Fix:_ Catch stripe.error.CardError → 402 with the decline reason, other StripeError → 502, mirroring wallet.py's handler.
- **Approved-refund push notification never says 'approved' (compares to nonexistent value 'refund')** — `backend/routes/disputes.py:293` _(support-misc)_. resolution_label is set to 'approved' only when req.resolution == 'refund', but the documented resolution values are approved|partial_refund|rejected, so riders whose refund was approved are told only 'Your dispute has been reviewed.' and rejected riders get the same ambiguous wording. _Fix:_ Map labels from the real enum: approved/partial_refund → 'approved', rejected → 'reviewed and declined'.
- **rate_rider allows rating before completion and unlimited overwrites** — `backend/routes/drivers/ride_cancel.py:371` _(drivers)_. A driver can set/overwrite rider_rating and rider_comment on a ride in any state (including active or cancelled) any number of times, skewing rider ratings and letting comments be revised long after the trip. _Fix:_ Require ride.status == completed and reject (or version) re-rating once rider_rating is set.
- **arrive_at_pickup is not retry-idempotent despite the declared idempotency contract** — `backend/routes/drivers/ride_flow.py:589` _(drivers)_. A client retry of /arrive after a dropped response gets a 409 because the atomic filter only allows status=driver_accepted, contradicting _shared.ARRIVE_FROM_STATES which includes DRIVER_ARRIVED 'to make retries safe' — the driver app surfaces a spurious error at the curb. _Fix:_ On guard failure, re-read the ride and return success if it is already driver_arrived for this driver (same pattern as accept's duplicate handling).
- **Fare cache grid key can straddle a service-area boundary** — `backend/routes/fares.py:66` _(fares-promos)_. The cache key rounds coordinates to a ~1.1km grid cell without including the resolved service area, so two pickups in the same cell but on opposite sides of an area boundary share one cached fare list — a rider just inside a priced/surging area can be shown the neighbouring area's (or default) fares, which booking then contradicts. _Fix:_ Resolve the area first and include its id (or 'none') in the cache key, or shrink the grid and accept boundary cells as uncacheable.
- **Favorites listed least-used first (order='use_count' without desc)** — `backend/routes/favorites.py:49` _(support-misc)_. Once the skip-kwarg crash is fixed, get_favorite_routes orders by use_count ascending (get_rows desc defaults to False), so the rider's most-used favorites appear last instead of first. _Fix:_ Pass desc=True (and consider secondary order by last_used_at).
- **GET unsubscribe executes the opt-out, so mail-scanner link prefetch silently unsubscribes users** — `backend/routes/marketing.py:106` _(support-misc)_. The human-visible GET /marketing/unsubscribe immediately processes the opt-out and adds the contact to the suppression list; corporate mail security scanners (Outlook SafeLinks, Mimecast, etc.) fetch GET links in every delivered email, so opted-in recipients get silently unsubscribed and suppressed without ever clicking. _Fix:_ Make the GET render a confirmation page with a button that POSTs to perform the opt-out; keep the RFC 8058 POST behavior unchanged.
- **Non-ride fallback idempotency key omits the amount — two different payments in the same minute collide** — `backend/routes/payments.py:224` _(payments-core)_. The last-resort key `intent-{user}-{minute-bucket}` (and `ps-{user}-{minute}` at line 1008) contains no amount, so a user creating two different non-ride PaymentIntents within the same minute hits Stripe IdempotencyError (same key, different params) and gets a 502. _Fix:_ Include the cents amount in the fallback key: f"intent-{user}-{amount}-{minute}".
- **setup-intent returns mock client_secret in production when Stripe key is blank** — `backend/routes/payments.py:493` _(payments-core)_. Unlike get_cards, which 503s in production when stripe_secret_key is transiently empty (key rotation), create_setup_intent returns {'client_secret': 'mock_setup_secret', 'mock': True}, so a production rider trying to save a card gets an opaque Stripe SDK failure instead of a retryable error. _Fix:_ Mirror get_cards: raise 503 when ENV=production and the key is empty; keep the mock only for dev/staging.
- **Wallet balance precheck uses the pre-promo grand_total** — `backend/routes/rides/booking.py:642` _(rides-booking)_. The wallet sufficiency check runs before the promo code is validated/applied (promo is applied after insert), so a rider whose wallet covers the discounted bill but not the undiscounted one is rejected with 'Insufficient wallet balance' even though their final charge would be affordable. _Fix:_ Validate the promo (or at least compute the prospective discount) before the wallet precheck, or compare against grand_total minus the validated discount.
- **GET /messages returns the oldest 100 messages, hiding the newest once a chat exceeds 100** — `backend/routes/rides/chat.py:89` _(rides-secondary)_. get_ride_messages queries with ascending order and limit=100, so when a ride+24h-post-trip chat exceeds 100 messages the REST fallback returns only the oldest 100 and every newer message is invisible to a client relying on this endpoint. _Fix:_ Fetch the newest 100 (`desc=True, limit=100`) and reverse in memory before returning, or add offset-based pagination.
- **Receipt payment_method field surfaces the raw Stripe payment_method_id** — `backend/routes/rides/receipts.py:124` _(rides-secondary)_. The receipt's payment_method for non-corporate rides is the raw ride.payment_method_id (a Stripe 'pm_...' identifier), so riders see an opaque Stripe ID on their receipt instead of a card brand/last4, and the wallet payment method is misreported as a card. _Fix:_ Branch on ride.payment_method ('wallet' → 'Spinr Wallet', 'company_allowance' → 'Corporate Account', else card), and for cards resolve brand/last4 from the saved payment-method record rather than echoing the Stripe id.
- **Emergency contact phone accepted without digit/format validation** — `backend/routes/users.py:480` _(core-infra)_. add_emergency_contact validates only len(phone) >= 10, so a non-numeric or malformed value is stored and the SOS SMS to that contact fails at the worst possible moment — during a real emergency. _Fix:_ Run validate_phone (raise 400 on failure) and store the normalized E.164 value so Twilio delivery is guaranteed addressable.
- **Top-up accepts amounts below Stripe's $0.50 CAD minimum** — `backend/routes/wallet.py:112` _(wallet-corporate)_. TopUpRequest allows any amount > 0, so a top-up of e.g. $0.25 passes validation and fails inside Stripe's PaymentIntent.create with amount_too_small, surfaced to the user as an opaque 502 'Payment provider error'. _Fix:_ Set a minimum (e.g. ge=Decimal("5") or at least Decimal("0.50")) on the field so the client gets a 422 with a clear message.
- **Wallet top-up Stripe idempotency key omits the amount — second top-up in the same minute fails** — `backend/routes/wallet.py:192` _(wallet-corporate)_. The PaymentIntent idempotency key is `wallet-topup-{user}-{minute}` with no amount, so a user creating two top-ups with different amounts within 60 seconds hits Stripe's idempotency conflict (same key, different params) and gets a generic 502. _Fix:_ Include the amount in the key, mirroring the corporate endpoint: `f"wallet-topup-{user_id}-{amount_cents}-{int(time.time() // 60)}"`.
- **Empty location_batch consumes a rate-limit token and returns no ack** — `backend/routes/websocket.py:937` _(websocket-notify)_. A location_batch message with an empty points list passes the isinstance guard, increments the Redis rate-limit counter, then skips the delivery block and sends no location_batch_ack, so the client never receives acknowledgment for the message it sent. _Fix:_ Short-circuit empty batches before the rate-limit accounting with an immediate `{'type':'location_batch_ack','count':0}` response.
- **Admin driver_matching_algorithm setting (rating_based/round_robin) is silently ignored on the live dispatch path** — `backend/services/dispatch_service.py:202` _(dispatch)_. select_driver_by_algorithm implements rating_based and round_robin selection but is never called from the production dispatch path — matching.py always sorts by haversine distance then optionally ETA/acceptance, so an operator setting driver_matching_algorithm to rating_based or round_robin in app_settings/service_areas changes only the rating floor, not the ordering, with no warning. _Fix:_ Either wire select_driver_by_algorithm into the batch-claim ordering in _match_driver_to_ride_attempt, or remove the rating_based/round_robin options from the admin setting and document that ordering is nearest/ETA-only.
- **Low-balance email uses non-atomic read-check-notify-mark with no leader lock — duplicate billing emails across replicas** — `backend/utils/corporate_low_balance.py:54` _(background-loops)_. run_low_balance_tick reads low_balance_notified_at from the fetched row, sends the email, then marks the wallet; with no Redis lock and no compare-and-swap, two replicas whose jittered hourly ticks land close together both pass the 12h throttle and send duplicate 'wallet balance low' emails to corporate billing contacts. _Fix:_ Make mark_low_balance_notified a conditional claim (UPDATE ... WHERE low_balance_notified_at IS NULL OR < cutoff, returning the row) executed BEFORE send_email, mirroring the document_expiry F6 pattern.
- **In-flight idempotency lock never released — failed request blocks same-key retry with 409 for 150s** — `backend/utils/idempotency.py:201` _(payments-core)_. The lock is set with a 150s TTL and never deleted, and errors are not cached, so after a transient failure the client's legitimate retry with the same Idempotency-Key gets 409 'already in flight' for up to 150 seconds, contradicting the documented 'errors are not cached so the client can retry'. _Fix:_ Delete the lock key in a finally block (or at least on exception) so failed attempts are immediately retryable.
- **Retry confirm of a requires_payment_method PI omits payment_method — retries can never succeed for common declines** — `backend/utils/payment_retry.py:356` _(payments-core)_. A PI in requires_payment_method has no usable attached card; confirming it without passing a payment_method fails with invalid_request_error every attempt, so the retry loop burns all 3 retries without ever attempting the rider's (possibly updated) default card, then blocks the rider from booking. _Fix:_ Look up the rider's current default_payment_method and pass it to confirm (or fall back to charge_ride's fresh-PI path) instead of re-confirming a card-less intent.
- **In-memory Redis fallback makes rider 'I'm okay' responses invisible to the loop — false escalations** — `backend/utils/safety_checkin_loop.py:130` _(safety-privacy)_. When REDIS_URL is unset (or Redis drops), redis_get/redis_set fall back to per-process dicts, so the ok-key written by the API replica handling POST /rides/{id}/safety-checkin (routes/rides/safety.py:207) is invisible to the replica running the loop — riders who confirm they are okay still get escalated as safety incidents, and multiple replicas each send duplicate check-in pushes. _Fix:_ Detect fallback mode in redis_client (expose an is_shared() flag) and disable the check-in loop with a loud error when Redis is not actually shared, instead of generating false safety incidents.
- **10-minute reminder uses read-then-write flag, not an atomic claim — duplicate pushes when Redis degrades** — `backend/utils/scheduled_rides.py:229` _(background-loops)_. _send_reminder checks reminder_sent from the previously fetched row, sends the push, then sets the flag unconditionally; the only cross-replica guard is the Redis leader lock, and redis_set_nx silently falls back to a per-replica in-process dict on any Redis error, so during a Redis outage every replica passes the lock and riders receive N duplicate reminder pushes. _Fix:_ Claim atomically first: `update_one("rides", {"id": ride_id, "reminder_sent": False}, ...)` and only send the push when the claim returns a row (the CLAUDE.md claim-flag recipe).
- **Reminder flag written after the push with no atomic claim — duplicate reminder pushes** — `backend/utils/scheduled_rides.py:243` _(dispatch)_. _send_reminder fires the push notification first and only then unconditionally sets reminder_sent=True; when the Redis leader lock is unavailable (line 260 logs a warning and proceeds on every replica) two replicas read the same unsent rows and both push, and within one replica a failed flag-write after a successful push re-sends the reminder on the next tick — violating the Background Loop Recipe's claim-before-side-effect contract. _Fix:_ Claim first: `update_one("rides", {"id": ride_id, "reminder_sent": False}, {"$set": {"reminder_sent": True}})` and only send the push when the claim returns a row.
- **Accept button never shows a busy state, enabling duplicate accept taps** — `driver-app/app/driver/(tabs)/index.tsx:947` _(driver-app-ux)_. RideOfferPanel is rendered with isLoading hardcoded to false, so during the async acceptRide network round-trip the Accept button stays fully active with no spinner; a driver who taps twice fires two POST /accept requests. _Fix:_ Pass the store's isLoading (e.g. useDriverStore(s => s.isLoading)) into RideOfferPanel's isLoading prop so the Accept/Decline buttons disable and show progress while the accept is in flight.
- **Notification load failure silently shows 'all caught up'** — `rider-app/app/notifications.tsx:66` _(rider-account-ux)_. A failed GET /notifications is swallowed with only a console.error, leaving the list empty so the rider sees 'No notifications / You're all caught up!' even though loading actually failed. _Fix:_ Track a load-error state and render a retry/error view distinct from the genuine empty state; optionally toast getApiErrorMessage.
- **Ride-summary price (total_fare) disagrees with footer total (grand_total) on the same screen** — `rider-app/app/payment-confirm.tsx:252` _(rider-booking-ux)_. The ride summary shows selectedEstimate.total_fare (pre area-fee/tax) as the headline price while the footer 'Estimated Total' shows grand_total minus promo, so the rider sees two different dollar figures for the same ride on one screen. _Fix:_ Use the same grand_total-based totalFare for the summary headline price, or clearly label the summary figure as the base fare so the two numbers reconcile.
- **Delete-account copy says 'cannot be undone' but 30-day reactivation exists** — `rider-app/app/privacy-settings.tsx:117` _(rider-account-ux)_. The delete confirmation states the scheduled deletion 'cannot be undone', yet the app ships a reactivate-account flow that reverses it during the same 30-day grace window, so the warning is inaccurate and may deter or distress users. _Fix:_ Reword to 'You can reactivate within 30 days by signing in again; after that it is permanent,' matching the actual reactivation capability.
- **Deletion copy claims ride history removed when it is retained 7 years attributable** — `rider-app/app/reactivate-account.tsx:103` _(x-compliance-legal)_. The reactivation screen tells the user 'ride history removed when you requested deletion cannot be restored,' but under the migration-216 Uber/Lyft model the backend removes nothing at deletion request — rides stay fully attributable to the rider for 7 years — so the copy misrepresents what actually happened to their data. _Fix:_ Correct the copy to state ride records are retained for regulatory reasons and then permanently deleted (not removed now), and update the stale '30-day deletion grace window' comments in reactivate-account.tsx and otp.tsx to reflect the 7-year retention model.
- **allowFontScaling={false} on fare/price/confirm text blocks low-vision text scaling** — `rider-app/app/ride-options.tsx:963` _(rider-booking-ux)_. Fare amounts, the Confirm button price, per-card prices, ETA and surge text all set allowFontScaling={false}, so riders who increase their OS font size for readability see these critical money values locked at the base size. _Fix:_ Remove allowFontScaling={false} on money/fare/ETA text (or cap with maxFontSizeMultiplier instead) so these values respect the user's Dynamic Type / font-size setting.
- **Vehicle-selection cards have no accessibility label or role for screen readers** — `rider-app/app/ride-options.tsx:1300` _(rider-booking-ux)_. The primary ride-selection control (AnimatedVehicleCard) renders a TouchableOpacity with no accessibilityRole or accessibilityLabel, so a screen-reader user hears nothing meaningful (no vehicle name, price, ETA, or surge state) when navigating the vehicle list. _Fix:_ Add `accessibilityRole="radio"`, `accessibilityState={{ selected: isSelected, disabled: !isAvailable }}`, and an accessibilityLabel like `${name}, ${price}, ${eta} min${surge>1?', surge pricing':''}` to the card TouchableOpacity.
- **Place-details fetch failure swallowed with empty catch** — `rider-app/app/saved-places.tsx:99` _(rider-account-ux)_. selectPrediction's `catch {}` is empty, so if GET /maps/places/details fails the tapped suggestion never resolves to a selectedPlace and the Save button stays disabled with no explanation. _Fix:_ Surface getApiErrorMessage in a toast on failure and rotate the session token so the next attempt is a fresh billing session.
- **Saved-place delete failure gives the rider no feedback** — `rider-app/store/rideStore.ts:906` _(rider-account-ux)_. deleteSavedAddress swallows the error into store `error` state that the saved-places screen never reads, so a failed delete leaves the item on screen with no toast or rollback indication. _Fix:_ Re-throw from the store (as addSavedAddress does) and toast the error in the screen's onPress handler so a failed removal is visible.


### Security

- **set-cookie route accepts any unsigned token without verification** — `admin-dashboard/src/app/api/auth/set-cookie/route.ts:21` _(admin-dashboard)_. POST /api/auth/set-cookie writes any caller-supplied string into the HttpOnly admin_token cookie with no signature or shape validation, and middleware only checks the exp claim, so a crafted unsigned JWT with a future exp loads the authenticated admin shell. _Fix:_ Have the login/refresh server routes set the admin_token cookie themselves from the backend-verified token rather than trusting a client-posted value, or verify the JWT signature server-side in this route before setting the cookie.
- **Sensitive dashboard pages lack client-side module gating** — `admin-dashboard/src/app/dashboard/settings/page.tsx:1` _(admin-dashboard)_. Several sensitive pages (settings, notifications, documents, ai-console for non-super-admin, heatmap, subscriptions, etc.) do not call useRequireModule, so a staff user lacking the module who navigates directly by URL renders the full page shell before backend 403s; only the sidebar hides the link. _Fix:_ Add useRequireModule(<module>) at the top of each sensitive page consistent with rides/users/earnings, returning null until allowed.
- **Production CORS always allows http://localhost:3000 and :3001 with credentials** — `backend/core/middleware.py:578` _(auth-identity)_. always_allowed unconditionally appends http://localhost:3000 and http://localhost:3001 to the allowed origins, including in production with allow_credentials=True, so any process listening on a user's own machine (malicious npm dev server, compromised local app rendering a page) can make credentialed cross-origin requests to the production API and read the responses of the victim's cookie-based admin/portal session. _Fix:_ Only append the localhost entries when settings.ENV != 'production' (dev origins already come from the ALLOWED_ORIGINS default in config.py, so the production list should contain only real deployed origins).
- **SOS grace path accepts expired single-purpose reactivation tokens** — `backend/dependencies/__init__.py:560` _(core-infra)_. get_current_user_allow_expired does not repeat the purpose=='reactivate' rejection, so an expired reactivation token (expired <24h ago) authenticates on SOS endpoints even though the docstring for create_reactivation_token guarantees it 'never' authenticates API access. _Fix:_ Add `if payload.get("purpose") == _REACTIVATION_PURPOSE: raise original` in the grace path immediately after decoding.
- **push_tokens user policy uses FOR ALL instead of enumerated operations** — `backend/migrations/06_cloud_messaging.sql:97` _(x-migrations)_. The 'Users manage own push tokens' policy on push_tokens is FOR ALL TO authenticated rather than enumerated SELECT/INSERT/UPDATE/DELETE, violating the house convention documented and enforced elsewhere (142, 195, 171). Functionally low-risk here because Postgres reuses the USING predicate as the implicit WITH CHECK when none is given, but it sets a precedent inconsistent with every other user-writable-table migration in the repo. _Fix:_ Split into explicit SELECT/INSERT/UPDATE/DELETE policies each scoped to user_id = auth.uid()::text with matching WITH CHECK clauses on INSERT/UPDATE.
- **Promo update endpoints bypass the discount caps and negative-value checks enforced at creation** — `backend/routes/admin/promotions.py:445` _(fares-promos)_. Both PUT /admin/promotions/{id} (PromotionUpdateRequest, plain floats, no validation) and PUT /admin/promo-codes/{id} (routes/promotions.py UpdatePromoCodeRequest, no ge/le constraints) accept any discount_value — negative, >100% or >$500 — defeating the F-30/P1-4/TASK-4-1 caps enforced only on create. _Fix:_ Apply the same validation on update: reject negative values, percentage > 100, flat > 500, and negative max_discount/total_budget/min_ride_fare.
- **Credential masking leaks the first 8 characters of every secret to all settings-module admins** — `backend/routes/admin/settings.py:91` _(admin-routes)_. _mask_credentials returns `v[:8] + "*****"`, so non-super-admin staff see the first 8 chars of Twilio auth tokens, API keys and the APNs .p8 PEM on every settings GET, materially reducing secret entropy outside the audited super_admin reveal flow. _Fix:_ Mask with a fixed placeholder plus last-4 only (e.g. '•••• 4f2a'), never a prefix of the secret.
- **lms_api_base_url localhost exemption is a bypassable prefix check** — `backend/routes/admin/settings.py:241` _(admin-routes)_. The https-only validator accepts any URL starting with 'http://localhost' or 'http://127.0.0.1', including 'http://localhost.attacker.com', letting the LMS API key be exfiltrated over plaintext to an attacker-controlled host — the exact risk the field's super-admin gate exists to prevent. _Fix:_ Parse with urllib.parse and compare `parsed.hostname in ("localhost", "127.0.0.1")` exactly.
- **update_staff accepts an arbitrary role string** — `backend/routes/admin/staff.py:264` _(admin-routes)_. req.role is written verbatim to admin_staff with no validation against the known role set, so a typo like 'Operations' silently strips the account of preset modules and breaks get_admin_user's role check on next login. _Fix:_ Validate req.role against {'super_admin','operations','support','finance','custom'} and 422 otherwise.
- **No per-address send cap on /auth/send-email-otp — email bombing and provider spend** — `backend/routes/auth.py:591` _(core-infra)_. The email OTP send path has only the spoof/rotation-vulnerable per-IP 3/minute limit — there is no per-destination-email throttle analogous to _enforce_otp_send_cap, so a victim inbox can be bombed and transactional-email spend burned. _Fix:_ Add a per-normalized-email Redis counter with a 30s min-interval and hourly cap, mirroring _enforce_otp_send_cap.
- **/auth/logout-all silently no-ops for admin tokens — 12h admin sessions stay valid** — `backend/routes/auth.py:1655` _(core-infra)_. logout_all hardcodes the users table for the token_version bump, so an admin-JWT caller (accepted by get_current_user) gets {'success': true} while no admin_staff.token_version is bumped and their long-lived (12h) access tokens remain valid. _Fix:_ In logout_all, branch on the caller's role: for admin roles either proxy to the admin_staff bump or return 400 directing the client to /admin/auth/logout-all — never return success without bumping any token_version.
- **join-domain bypasses company-active check enforced by the auto-match flow** — `backend/routes/corporate_rider.py:150` _(wallet-corporate)_. do_join_domain validates only that the email domain exists in corporate_allowed_domains and never checks the company's status, so a rider can create an active membership in a suspended or closed company by calling the endpoint directly, whereas auto_match_by_email intentionally surfaces only active companies. _Fix:_ Fetch the corporate account in do_join_domain and 403/409 unless status == 'active', matching the auto-match contract.
- **Attestation nonce is issued but never stored or verified — replay protection is fictional** — `backend/routes/drivers/location.py:419` _(drivers)_. attest_nonce returns a random hex string that is never persisted, and attest_device never receives or checks a nonce, so the documented single-use/anti-replay property of Play Integrity / App Attest verification does not exist. _Fix:_ Store the nonce (Redis, short TTL, keyed to user) and require/consume it inside verify_device when validating the attestation token.
- **No CSV formula-injection escaping in DSAR export cells** — `backend/routes/drivers/tax_exports.py:323` _(drivers)_. _csv_cell writes values verbatim, so rider-controlled strings in the driver's export (e.g. a pickup_address starting with '=' or '@') become executable formulas when the driver opens rides.csv in Excel/Sheets. _Fix:_ Prefix cells starting with =, +, -, @ with a single quote (standard CSV-injection mitigation).
- **Unauthenticated /health leaks raw DB exception details and internal loop inventory** — `backend/routes/main.py:90` _(support-misc)_. The public health endpoint returns the stringified DB exception (which can include connection/host details from supabase-py errors), exception .details, and the full named list of background loops with staleness, giving reconnaissance data to anyone. _Fix:_ Return only {status, db: ok|error} publicly; gate the detailed error string and per-loop breakdown behind admin auth or an internal header.
- **Offer-card token's driver binding is never verified — documented protection is unenforced** — `backend/routes/offer_card.py:67` _(dispatch)_. sign_offer_card_token embeds a driver_id ('dr') claim and both modules' docstrings state the banner is bound to exactly one ride+driver, but the endpoint calls verify_offer_card_token with only ride_id and the verifier never inspects 'dr' — any holder of any driver's token for a ride can fetch the banner, so the claimed driver binding provides zero protection and the docs overstate the PIPEDA defence-in-depth. _Fix:_ Since the OS image fetch cannot present driver identity, either drop the 'dr' claim and correct both docstrings, or log/verify 'dr' against a pending ride_offers row for that driver as a secondary gate before rendering.
- **/promo/apply is not rate limited** — `backend/routes/promotions.py:341` _(fares-promos)_. validate and available are rate limited (promo_validate_limit/promo_available_limit) but apply_promo has no limiter, giving an unthrottled channel to enumerate promo codes (404 invalid vs 400 rule-failure vs success) and to hammer application writes. _Fix:_ Apply the same SlowAPI limit (e.g. promo_validate_limit) to /promo/apply.
- **Ride-existence leak the chat-status guard closed is still open in /messages and /typing** — `backend/routes/rides/chat.py:86` _(rides-secondary)_. get_chat_status deliberately returns 404 to non-participants to avoid confirming a ride_id exists, but get_ride_messages, send_ride_message, and send_typing_indicator return 403 for the same caller, so any authenticated user can still probe arbitrary ride_ids and distinguish existing rides (403) from non-existent ones (404). _Fix:_ Return 404 for non-participants in get_ride_messages, send_ride_message, and send_typing_indicator, matching the chat-status pattern.
- **Support escalation accepts unbounded message/transcript and has no dedicated rate limit** — `backend/routes/support.py:133` _(support-misc)_. EscalateRequest.message and transcript have no max_length and /support/escalate has no per-endpoint limiter, so any authenticated user can spam Zoho Desk with high-volume, multi-megabyte tickets (support-queue flooding and Zoho API-credit burn). _Fix:_ Add Field(max_length=...) caps (e.g. 2000 for message, 20000 for transcript) and a tight rate limit such as 3/minute on the escalation endpoint.
- **Rating comment has no length bound, allowing arbitrarily large payloads onto the ride row** — `backend/schemas.py:420` _(rides-secondary)_. RideRatingRequest.comment is an unbounded Optional[str] written verbatim to rides.rider_comment, so a client can post a multi-megabyte comment per completed ride (no body-size middleware exists), bloating the rides table and every subsequent full-row read of that ride. _Fix:_ Add `max_length=1000` (or similar) to RideRatingRequest.comment.
- **/metrics accepts the auth token as a URL query parameter, leaking it into access logs** — `backend/server.py:243` _(auth-identity)_. When METRICS_AUTH_TOKEN is set, the endpoint accepts `?token=<secret>` as an alternative to the Authorization header; query strings are recorded by Cloudflare, Fly/Railway edge logs, and any intermediate proxy, so the long-lived scrape secret leaks into logging systems and the protection quietly erodes. _Fix:_ Accept the token only via the Authorization header (Prometheus, Grafana Agent and Railway scrapers all support bearer-token config); if a query param must be kept for a legacy scraper, document the exposure and rotate the token regularly.
- **Full auth router mounted at App-Check-exempt /api/portal and triple-mounted overall — OTP quotas tripled and App Check on auth is bypassable** — `backend/server.py:351` _(auth-identity)_. auth_router is mounted at /api/v1, /api, and /api/portal; the rate limiter keys on request.url.path, so per-IP OTP send/verify quotas are effectively tripled (3/min becomes 9/min across prefixes, SMS-cost and brute-force relevant), and because the whole /api/portal/ prefix is App-Check-exempt, every auth endpoint — including /auth/firebase, /auth/refresh, /auth/send-email-otp, /auth/reactivate — is reachable without device attestation, not just the OTP/refresh/logout endpoints the portal needs. _Fix:_ Mount a restricted sub-router at /api/portal containing only the endpoints the browser portal needs (send-otp/verify-otp/send-email-otp/verify-email-otp/refresh/logout), and key OTP rate limits on a normalized scope (strip the mount prefix) or on the destination phone so multiple mounts share one bucket.
- **OAuth client_secret and refresh_token sent as URL query parameters** — `backend/services/zoho_desk_service.py:137` _(support-misc)_. _refresh_access_token posts the client_secret and long-lived refresh_token as query-string params (httpx params=), so these credentials can be persisted in outbound proxy logs, corporate egress logs, or Zoho-side access logs, unlike a form-encoded body. _Fix:_ Send the grant as a form body (`data=params`) — Zoho's token endpoint accepts standard application/x-www-form-urlencoded OAuth2 requests.
- **Rate-limit client IP trusts spoofable CF-Connecting-IP / X-Real-IP with no origin lockdown in place** — `backend/utils/rate_limiter.py:100` _(auth-identity)_. get_real_client_ip prefers the CF-Connecting-IP then X-Real-IP request headers; the deployment's origin hosts (Fly/Railway public hostnames) are directly reachable, so an attacker bypassing Cloudflare can send arbitrary values in those headers and rotate their rate-limit identity per request — defeating every IP-keyed limit including OTP send (3/min) and login (5/min), enabling SMS-cost pumping and credential brute force limited only by the per-phone caps. _Fix:_ Add an origin-authentication check: require a Cloudflare Authenticated Origin Pull cert or a shared-secret header injected by a Cloudflare Transform Rule, and fall back to the socket peer IP (ignoring CF-Connecting-IP) when that proof is absent.
- **AsyncLimiter default_limits (100/min, 1000/hr) are never applied — undecorated endpoints have no rate limit at all** — `backend/utils/rate_limiter.py:113` _(auth-identity)_. The default_limits passed to AsyncLimiter are only consulted inside the .limit() decorator when override_defaults=False (never the case in this codebase), and no SlowAPIMiddleware or equivalent global hook exists, so any route without an explicit *_limit decorator is completely unlimited while the configuration implies a global 100/minute ceiling exists. _Fix:_ Either add a lightweight global middleware that runs the default limits for any request not already checked (mirror slowapi's middleware behaviour), or delete the default_limits argument and document that every route must carry an explicit limiter decorator, enforced by a CI check that scans route definitions.
- **Web builds send no cookies — fetch omits credentials:'include' while web auth relies on HttpOnly cookies** — `shared/api/client.ts:52` _(shared-package)_. authStore documents that web relies entirely on backend HttpOnly cookies for the session (no client token storage), but the shared fetch client never sets credentials:'include', so on a cross-origin web deployment the session cookie is not attached and authenticated requests fail. _Fix:_ Add credentials: 'include' to the fetch options (at least when Platform.OS === 'web') so the HttpOnly session cookie is sent; also align the storage key/namespace (client.ts uses sessionStorage 'auth_token' on read but localStorage 'auth_token' on the G2 clear at line 938).


### Reporting / observability

- **Ride CSV export silently capped to last 24h when no date range set** — `admin-dashboard/src/app/dashboard/rides/page.tsx:180` _(admin-dashboard)_. When an admin exports rides without choosing a date range, the backend defaults date_from to 24h ago, so the downloaded CSV contains only the last day's rides while the UI gives no indication of the truncation. _Fix:_ Surface the effective date range in the export UI/filename, or require an explicit date range before enabling export, so the 24h default cannot be mistaken for a full export.
- **CSRF rejection logs use printf-style placeholders with loguru — method/path/origin are silently dropped from the log line** — `backend/core/middleware.py:392` _(auth-identity)_. loguru formats with str.format ('{}'), not %-style, so `logger.warning("CSRF token missing: %s %s", request.method, path)` and the mismatch log at lines 404-409 emit the literal text '%s %s' with the arguments discarded — every CSRF 403 in production is untraceable to an endpoint or origin, which matters because a CSRF-config gap (e.g. a missing exemption) manifests exactly here. _Fix:_ Switch to loguru brace formatting: `logger.warning("CSRF token missing: {} {}", request.method, path)` and `logger.opt(exception=True).warning("Failed to evict corrupt cache key {}", key)`.
- **Global exception handler logs unhandled 500s without a traceback and drops HTTPException headers** — `backend/core/middleware.py:650` _(auth-identity)_. cors_exception_handler logs `logger.error(f"Unhandled exception: {exc}")` with no exc_info/traceback, so a production 500 leaves only the exception's str() (often empty, e.g. KeyError('x')) with no stack to debug from; and for exceptions carrying status_code/detail it rebuilds the JSONResponse without exc.headers, discarding Retry-After / WWW-Authenticate on any HTTPException that reaches this handler. _Fix:_ Use `logger.opt(exception=exc).error(...)` (loguru) so the traceback is captured (and reaches Sentry via the ERROR sink), and pass `headers=getattr(exc, "headers", None)` through to the rebuilt JSONResponse.
- **Broadcasts are never persisted and an invalid audience silently returns success** — `backend/routes/admin/faqs.py:153` _(admin-routes)_. Broadcast sends skip the notifications-table insert (only the single-user branch inserts) so GET /admin/notifications shows no broadcast history and sent_count stays 0; an unmatched audience value (e.g. 'Drivers') matches no branch and still returns {"success": true}. _Fix:_ Insert the notification_doc for all branches with the real sent count, and constrain audience with Literal["user","all","riders","drivers"].
- **Incentive claims 'total_paid' sums only the most recent 100 claims** — `backend/routes/admin/incentives.py:92` _(admin-routes)_. GET /incentives/claims/stats caps the query at 100 rows but labels the sum total_paid/total_claims, so any incentive with more than 100 claims under-reports spend to the finance team. _Fix:_ Use a SQL aggregate (count + sum via RPC or count='exact') for the totals and keep the 100-row page only for the claims list; also sum with Decimal, not float.
- **Admin force-cancel writes no audit_logs row** — `backend/routes/admin/rides.py:386` _(admin-routes)_. admin_cancel_ride records cancelled_by_admin_id on the ride but never calls log_admin_action, unlike admin_complete_ride, so the central audit trail has no record of forced cancellations. _Fix:_ Call `await log_admin_action(admin_user, "force_cancel_ride", "rides", ride_id, {"reason": reason, "status_from": ride.get("status")})` after the successful update.
- **Payout retry endpoints claim an audit trail but write no audit_logs rows** — `backend/routes/admin/rides.py:3212` _(admin-routes)_. Single and bulk payout retry (money-moving requeues restricted to finance/super_admin) record the requester only as columns on the payout row and a logger.info line; the bulk endpoint's docstring says 'the audit log keeps a clear trail' but neither handler calls log_admin_action, so the payouts audit view (which reads audit_logs, per close-period) never shows retries. _Fix:_ Emit log_admin_action('payout_retry'/'payouts_bulk_retry', ...) with payout ids and counts.
- **Surge 'history' row is updated in place, so no surge history is actually kept** — `backend/routes/admin/service_areas.py:719` _(admin-routes)_. The docstring says a surge_pricing row is 'written for history', but the handler updates the single existing row per area (even replacing its id with a fresh uuid), so past manual surge settings are unrecoverable for regulatory review. _Fix:_ Always insert a new surge_pricing row (append-only) and mark previous rows inactive, or drop the 'history' claim and stop rewriting the primary key.
- **User export emits columns that are never selected — city/total_rides/rating/is_verified are always empty** — `backend/routes/admin/users.py:420` _(admin-routes)_. admin_export_users builds rows from _USER_LIST_COLUMNS, which deliberately excludes city/total_rides/rating/is_verified (the file's own comment says they aren't users-table columns), so every export row carries city=None, total_rides=0, rating=None, is_verified=None presented as real data. _Fix:_ Drop the four dead fields from the export or join the driver/derived data before exporting.
- **DSAR list 'total' is the page length, not the true total** — `backend/routes/admin/users.py:478` _(admin-routes)_. GET /admin/dsars returns total=len(requests) from a limit/offset page, so the dashboard's count of open PIPEDA requests is wrong whenever more than `limit` exist — exactly when SLA tracking matters most. _Fix:_ Return `await db_supabase.count_documents("data_export_requests", filters)` as total.
- **Daily/weekly/monthly/comparison earnings use float accumulation and UTC day-bucketing** — `backend/routes/drivers/earnings.py:341` _(drivers)_. The daily/weekly/monthly/comparison endpoints accumulate money in floats (violating the Decimal-only rule; drifts cents over many rows) and bucket days by the UTC prefix of ride_completed_at while /earnings uses the driver's local timezone, so evening Regina rides appear on the wrong day and the two screens disagree. _Fix:_ Accumulate with Decimal (serialize via _money_str) and convert ride_completed_at to the driver's service-area timezone before date bucketing.
- **Single-offer timeout path skips acceptance-rate update, diverging driver stats from the batch path** — `backend/routes/rides/matching.py:986` _(dispatch)_. _offer_timeout_handler increments the miss streak and can auto-offline a driver but never calls update_acceptance_rate(accepted=False), while the batch/reaper path (process_expired_offer line 1156) does — so drivers who time out on admin-assigned offers keep an inflated acceptance_rate, which feeds rank_by_eta_with_acceptance ordering and analytics. _Fix:_ Add `await update_acceptance_rate(driver_id, accepted=False)` after the miss-streak increment in _offer_timeout_handler, mirroring process_expired_offer.
- **spinr_payment_settlement_total emitted with undocumented outcome/label variants** — `backend/routes/rides/payments.py:179` _(reporting-tax)_. CLAUDE.md documents spinr_payment_settlement_total{outcome=success|failed|retry}, but the code emits an 'already_paid' outcome and inconsistent label sets ({method,outcome} here vs {outcome,path} in payment_service), so dashboards/alerts keyed on the documented enum will miss or mis-bucket series. _Fix:_ Reconcile the metric contract: either update CLAUDE.md to include the 'already_paid' outcome and standardize the label set (method,outcome[,path]) across both call sites, or map already_paid into the documented enum so exposition is consistent.
- **Wallet ledger row written outside the debit transaction — crash loses the audit entry** — `backend/routes/wallet.py:299` _(wallet-corporate)_. wallet_pay debits the balance inside the wallet_pay_for_ride RPC but inserts the wallet_transactions ledger row afterward from Python; a crash or DB error between the two leaves a balance change with no ledger entry, breaking the module's 'audit trail is immutable' guarantee and making balance_after reconstruction impossible. _Fix:_ Move the wallet_transactions insert into the wallet_pay_for_ride Postgres function so debit and ledger row commit atomically.
- **GET /wallet/transactions returns page length as 'total'** — `backend/routes/wallet.py:362` _(wallet-corporate)_. The endpoint reports "total": len(txns) where txns is the paginated page, so clients always see total <= limit and pagination UIs cannot know the real transaction count. _Fix:_ Either drop the misleading field, return has_more via the limit+1 trick, or run a separate count query for the wallet's transactions.
- **charge.dispute.closed maps every non-'won' status (incl. warning_closed) to dispute_lost** — `backend/routes/webhooks.py:1013` _(payments-core)_. Stripe closes inquiry-stage disputes with status 'warning_closed' (no money lost), but the handler marks the ride payment_status='dispute_lost' for anything that isn't 'won', misstating the ride's payment outcome in admin views and revenue reporting. _Fix:_ Map 'won' and 'warning_closed' back to 'paid'; only 'lost' to 'dispute_lost'; log-and-skip unknown statuses.
- **Wallet debit succeeds but wallet_transactions ledger insert failure is unrecoverable** — `backend/services/payment_service.py:305` _(payments-core)_. settle_wallet debits the wallet and marks the ride paid atomically via RPC, then inserts the wallet_transactions row separately; if that insert raises, the request 500s, and any retry hits the RPC's already-paid no-op which deliberately skips the ledger write — so real money moved with no wallet ledger entry, permanently. _Fix:_ Write the ledger row inside the wallet_pay_for_ride RPC transaction, or on the already-paid no-op check for and backfill a missing wallet_transactions row for the ride.
- **never_ticked loops are never flagged unhealthy — a loop that dies at startup is invisible forever** — `backend/utils/loop_monitor.py:77` _(background-loops)_. get_loop_status marks a loop that has never ticked as healthy indefinitely; the inline comment says it should be flagged once 'registered more than 2× its threshold ago' but that logic is not implemented, so an import failure or first-tick crash of any background loop (e.g. payment_retry) produces no /health degradation and no Slack alert, ever. _Fix:_ Record a registration timestamp per loop and flip never_ticked to stale/unhealthy once now - registered_at > 2× threshold, as the comment already promises.
- **Latency metric spinr_bgloop_duration_ms emitted as a gauge, not a histogram** — `backend/utils/presence_sweeper.py:83` _(x-observability)_. A metric named with the `_duration_ms` suffix (reserved by CLAUDE.md for latency histograms) is emitted via set_gauge across all replicas, so dashboards computing histogram_quantile get no buckets and replicas overwrite each other's last value. _Fix:_ Either emit these via metrics.observe()/time_ms() so they render as real histograms, or rename to a gauge-appropriate name (e.g. spinr_bgloop_last_run_ms) so dashboards/alerts don't treat it as a latency histogram.
- **Permanently failed dispatch/SOS pushes are hard-deleted from the retry queue, erasing the failure trail** — `backend/utils/push_retry.py:188` _(background-loops)_. After _MAX_ATTEMPTS failures (or a missing FCM token) the queue row — which may represent a ride offer or safety-priority push — is deleted outright, leaving only a log line: there is no durable record for support/ops to see that a driver never received a dispatch push or a rider never got a safety alert, and no metric is incremented for exhausted pushes. _Fix:_ Mark rows failed (e.g. `failed_at`/`failure_reason` columns) instead of deleting, increment a `spinr_push_retry_exhausted_total` metric, and let the retention purge clean old rows.
- **STRIPE_ORPHAN skip-list misses subscription-renewal invoice PIs and ancillary-fee PIs** — `backend/utils/stripe_reconcile.py:283` _(payments-core)_. PaymentIntents created by Stripe Billing for recurring Spinr Pass invoices carry none of our scope metadata, and cancellation-fee PIs (charge_ancillary_fee) are never stored on rides.payment_intent_id, so both are flagged STRIPE_ORPHAN every night, generating recurring false-positive error logs. _Fix:_ Also skip PIs with metadata.source ending in '_fee', PIs whose invoice field is set (Billing-generated), and admin ride-invoice PIs (match rides.stripe_invoice_id).
- **Auto-heal marks stuck-processing rides paid without ensuring a financial_events ledger row exists** — `backend/utils/stripe_reconcile.py:561` _(payments-core)_. _heal_one_processing_ride assumes the settle path already wrote the ledger row, but rides stranded by /payments/confirm (which never calls record_payment_event) get healed to 'paid' with no financial_events entry, silently understating the 7-year tax ledger. _Fix:_ Before/after healing, check financial_events for (ride_id, pi_id) and insert the stripe_charge row from Stripe truth when absent.
- **Cancelled-rides counter uses the count as a label value instead of incrementing by N** — `backend/utils/stuck_ride_sweeper.py:122` _(background-loops)_. The sweeper increments spinr_stuck_ride_sweeper_cancelled_total by 1 with a {'count': N} label, so the metric undercounts cancellations (a sweep of 7 rides adds 1, not 7) and creates unbounded label cardinality — dashboards and any match-rate alerting built on it are wrong. _Fix:_ Increment by the ride count with no dynamic label (loop `_metric_inc` N times or use an `inc(..., value=N)` form if metrics.py supports it).
- **'Online Time' stat actually shows on-trip time, not online time** — `driver-app/components/activity/ActivityView.tsx:370` _(driver-app-ux)_. The stat card labeled 'Online Time' is computed from total_duration_minutes, which the backend earnings endpoint fills with the sum of completed-ride durations (trip time), not time spent online — so drivers judging their utilization see only in-trip minutes labeled as total online hours. _Fix:_ Relabel the card to 'Trip Time' (or 'On-Trip Hours'), or source it from a real online-hours metric; do not present accumulated ride durations as online time.


### Compliance (PIPEDA)

- **Driver documents DB error swallowed to empty list without logging** — `backend/onboarding_status.py:151` _(x-observability)_. A failure reading driver_documents is caught and replaced with an empty list with no log, so a transient DB error is indistinguishable from a driver who has uploaded nothing and produces a misleading onboarding status. _Fix:_ On DB failure, log at error with exc_info and raise 503 rather than degrading silently to `[]`, which can flip the computed onboarding step.
- **Area tax update: unbounded rates, no audit log, no cache invalidation, and 500 on unknown area** — `backend/routes/admin/service_areas.py:841` _(admin-routes)_. PUT /areas/{id}/tax accepts any float (negative or >100% GST/PST/HST), writes it with no audit record or fare-cache flush, and crashes with AttributeError when area_id doesn't exist. _Fix:_ Add Field(ge=0, le=100) bounds, 404 when the area row is missing, invalidate_fare_cache(), and log_admin_action with old/new rates.
- **Demand heatmap exposes exact per-ride pickup coordinates to all drivers** — `backend/routes/drivers/profile.py:252` _(drivers)_. get_demand_heatmap returns up to 2,000 individual ride pickup lat/lng points from the last 7 days to any driver in the area — raw rider GPS (PIPEDA-sensitive per this repo's own rules) rather than aggregated/blurred demand cells, enabling identification of an individual rider's regular pickup spot. _Fix:_ Aggregate to geohash/grid cells with counts (and a minimum count per cell) before returning.
- **In-app opt-out suppression misattributed as 'unsubscribe_link' in the CASL audit trail** — `backend/routes/marketing.py:163` _(support-misc)_. update_marketing_preferences records the suppression with source='unsubscribe_link' even though the change came from the rider/driver app (body.source), so the CASL/PIPEDA consent audit trail misstates how the opt-out was obtained. _Fix:_ Pass source=body.source (rider_app/driver_app) to add_marketing_suppression.
- **Chat push falls back to the sender's full name in a cleartext notification** — `backend/routes/rides/chat.py:185` _(rides-secondary)_. When first_name is empty the chat push title uses current_user['name'] (the full legal name), violating the codebase's own PIPEDA rule that cleartext push titles carry first name only. _Fix:_ Use the existing first_name_only helper (already imported in sibling modules) for sender_name instead of falling back to the full name field.
- **Full free-text lost-item description is sent in the FCM push body without truncation** — `backend/routes/rides/lost_found.py:82` _(rides-secondary)_. The driver push notification embeds the rider's entire item_description (up to 500 chars of free text, which routinely contains PII like names, card details, or addresses) into a cleartext FCM push, contradicting the codebase's own PIPEDA push discipline and without the 100-char truncation applied to chat previews. _Fix:_ Send a generic body (e.g. "A rider reported a lost item from a recent trip — open the app for details") or truncate to a short preview, and dispatch it via _push_in_background.
- **Consent state change and CASL audit event are non-atomic — audit row can be lost after state changed** — `backend/services/marketing_consent.py:87` _(safety-privacy)_. set_consent updates marketing_preferences first and appends the marketing_consent_events audit row second; if the event insert fails the caller 500s but the preference has already flipped, leaving a consent state change with no append-only audit evidence — the record CASL/PIPEDA relies on to prove when consent was given or withdrawn. _Fix:_ Move both writes into a single Postgres function (SECURITY DEFINER, like corporate_wallet_apply_delta) or write the event first and derive/confirm the preference update, so a consent state never exists without its audit event.
- **track_ride_requested/completed/payment forward arbitrary dicts to third-party analytics without PII guard** — `backend/utils/analytics.py:330` _(reporting-tax)_. Only track_driver_online guards its payload against raw coordinates; track_ride_requested, track_ride_completed, track_ride_cancelled and track_payment_processed pass caller-supplied ride_data/payment_data verbatim to Mixpanel/Amplitude, so a caller including pickup/dropoff lat-lng or addresses would ship PII/raw GPS off-platform (PIPEDA). _Fix:_ Apply an allowlist / coordinate-rejection to the ride and payment event payloads the same way track_driver_online rejects lat/lng, so raw GPS or addresses can never reach third-party analytics even if a future caller passes them.
- **Receipt email hardcodes PST 6% and recomputes tax, contradicting the GST-only SK tax model and the ride's stored tax_breakdown** — `backend/utils/receipt_email.py:66` _(websocket-notify)_. send_ride_receipt_email always adds a PST (6%) line computed from total_fare and ignores the ride's actual tax_breakdown/tax_amount, so the emailed receipt claims PST was collected when SK rideshare charges GST only, and the line items do not reconcile to the DB grand_total. _Fix:_ Delete this unused module or rewrite _build_plain_text to render tax lines from ride['tax_breakdown']/ride['tax_amount'] like utils/email_receipt.py, so the emailed receipt matches what was actually charged.
- **PDF receipt has no surge disclosure line or note** — `backend/utils/receipt_pdf.py:88` _(reporting-tax)_. The PDF receipt never discloses that surge pricing was in effect, unlike the HTML email receipt which surfaces a surge note; CLAUDE.md requires every charge component including surge to map to a disclosed line item. _Fix:_ Add a surge disclosure note (mirroring email_receipt.py) when ride['surge_multiplier'] > 1.0 so the PDF receipt discloses surge like the email does.
- **Route-snapshot ledger marked deleted without checking per-object storage.remove results** — `backend/utils/retention_purge.py:113` _(safety-privacy)_. After a single batch storage remove call, every pending ledger row is stamped deleted_at without inspecting the remove() response, so an object that individually failed to delete (or a path mismatch) is recorded as purged and never retried — GPS route imagery can persist past its retention deadline invisibly. _Fix:_ Intersect the remove() response with the requested paths and only stamp deleted_at for confirmed deletions, leaving the rest for the next daily run.
- **Retention purge ignores its documented 03:00 UTC schedule and can skip whole days under the 23h lock** — `backend/utils/retention_purge.py:268` _(background-loops)_. _seconds_until_next is dead code — the loop ticks immediately at boot and then every 24h±10% from deploy time, so the heavy SECURITY DEFINER purge RPC runs during peak traffic after any peak-hours deploy; additionally, with the 23h static lock and 21.6h-minimum jittered intervals, all replicas' ticks can land inside the lock window and an entire day's purge cycle is skipped, stretching the PIPEDA/retention cadence to ~48h. _Fix:_ Sleep `_seconds_until_next(3)` before the first tick and re-align each iteration to 03:00 UTC (as stripe_reconcile.py does with _seconds_until), and drop the lock TTL below the minimum inter-tick gap.
- **CASL unsubscribe tokens keyed to JWT_SECRET with no key versioning — secret rotation breaks all outstanding links** — `backend/utils/unsubscribe_token.py:52` _(safety-privacy)_. Unsubscribe HMACs are signed with settings.JWT_SECRET; rotating the auth secret (a normal security operation) instantly invalidates every unsubscribe link already delivered in emails/SMS, violating CASL's requirement that the mechanism work for at least 60 days after send. _Fix:_ Use a dedicated UNSUBSCRIBE_SIGNING_KEY with support for verifying against a list of current+previous keys (key id in the payload), decoupling CASL link validity from JWT secret rotation.
- **Data-export toast promises 24-hour delivery but backend SLA is 30 days** — `rider-app/app/privacy-settings.tsx:107` _(rider-account-ux)_. The success toast tells the rider they'll receive a download link 'within 24 hours', but POST /users/data-export only queues a DSAR request tracked against PIPEDA's 30-day SLA, so the promise is routinely unmet. _Fix:_ Change the copy to reflect the real SLA (e.g. 'within 30 days, usually sooner') so the app does not make a delivery guarantee the backend does not honour.


### Financial loss

- **Area fee CRUD has no audit logging and does not invalidate the fare cache** — `backend/routes/admin/service_areas.py:765` _(admin-routes)_. Creating/updating/deleting area_fees rows changes what riders are charged but writes no audit_logs row and never calls invalidate_fare_cache, so stale cached fares are quoted for up to FARE_CACHE_TTL (300 s) and there is no record of who changed a fee. _Fix:_ Add `await invalidate_fare_cache()` and `await log_admin_action(...)` to all three handlers (add the `admin: dict = Depends(get_admin_user)` parameter to obtain the actor).
- **AdminDebitRequest.amount is float with gt=0, allowing sub-cent amounts that quantize to $0.00** — `backend/routes/admin/wallet.py:55` _(admin-routes)_. A debit of e.g. 0.004 passes validation (gt=0) and produces a zero-value ledger transaction after _q() quantization, and the float type violates the Decimal-only money convention. _Fix:_ Mirror the credit model: `amount: Decimal = Field(..., gt=Decimal("0.01"), le=Decimal("10000"))`.
- **Idempotency guard is check-then-insert — duplicate concurrent requests double-credit** — `backend/routes/admin/wallet.py:123` _(admin-routes)_. Two simultaneous credit requests with the same idempotency_key both pass the existence check and both apply the credit. _Fix:_ Add a unique index on (reference_id, type) in wallet_transactions and treat the duplicate-key error as 'return existing transaction', or claim the key atomically before mutating the balance.
- **join_quest checks end_date but not start_date; tracker also ignores start_date** — `backend/routes/quests.py:195` _(fares-promos)_. A driver can join a quest before its start window (join_quest only rejects ended quests) and quest_tracker counts every completed ride for an active progress row without checking quest.start_date, so rides completed before the advertised quest window count toward a money-bearing reward. _Fix:_ Reject joins before start_date in join_quest and skip progress increments for rides completed outside [start_date, end_date] in quest_tracker.
- **/support/chat lacks the AI-specific rate limit and daily cap applied to /ai/chat** — `backend/routes/support.py:97` _(support-misc)_. Each /support/chat message triggers paid Gemini spend, but the endpoint only has the global default 100/minute-per-IP limit — no @ai_chat_limit (10/min) and no per-user daily cap like /ai/chat — letting any authenticated driver burn LLM budget at 10x the intended rate. _Fix:_ Apply ai_chat_limit (and ideally the same per-user daily cap) to /support/chat.
- **Referral code resolved by contains-regex on user id can credit the wrong referrer** — `backend/routes/users.py:661` _(core-infra)_. apply_rider_referral resolves RIDE-prefixed codes with a case-insensitive contains match ({'$regex': suffix}) instead of an anchored prefix match, so an 8-hex-char substring appearing anywhere inside another user's UUID links the referee to the wrong referrer, who then receives the $5 payout. _Fix:_ Anchor the match to the id prefix (e.g. `{"$regex": f"^{suffix}"}` or an ilike 'suffix%' filter) and treat >1 match as ambiguous (reject).
- **Area-fee line item uses raw float() instead of the file's own Decimal helpers** — `backend/services/fare_service.py:302` _(x-money-sweep)_. build_fare_breakdown_lines gates and emits area-fee receipt line amounts with a bare float(val) instead of routing through _d()/_round()/_f() like every other line in the same function, bypassing the file's own Decimal-discipline convention and risking a receipt line that doesn't reconcile to the cent with the Decimal-computed grand_total. _Fix:_ Replace with `if _d(val) > 0: lines.append({..., "amount": _f(_round(_d(val))), ...})` to match the rest of the function and guarantee 2-dp rounding consistency.
- **Auto-topup idempotency key freezes after a declined charge — no retry for the rest of the day** — `backend/utils/corporate_autotopup.py:121` _(background-loops)_. The Stripe idempotency key is derived from (wallet, calendar date, today_sum, amount); a declined/failed PaymentIntent does not advance today_sum, so every subsequent 10-minute tick that day reuses the identical key and Stripe replays the cached decline — a transient decline (temporary card hold, issuer blip) disables auto-topup until midnight, letting the corporate wallet run dry and rides fall back or fail. _Fix:_ Include an attempt discriminator that advances on observed failure (e.g. count of today's failed topup intents, or an hourly bucket) in the seed, and use datetime.now(timezone.utc).date() instead of date.today().
- **earnings_target quest progress accumulates money as float** — `backend/utils/quest_tracker.py:104` _(fares-promos)_. earnings_target progress adds ride.driver_earnings (a DB float) to current_value with float arithmetic, violating the Decimal-only money rule; accumulated drift can leave a driver at e.g. 199.99999997 against a 200 target, blocking a completed quest's reward. _Fix:_ Accumulate via Decimal(str(...)) and quantize to 2dp before the target comparison and the DB write, or compare with a half-cent tolerance.
- **redis_set_nx fails OPEN on Redis errors — every replica acquires the 'leader lock' during a Redis outage** — `backend/utils/redis_client.py:159` _(auth-identity)_. Unlike every other operation in this module (which log ERROR and re-raise), redis_set_nx catches Redis exceptions with a warning and falls through to the per-process dict, where each replica independently 'acquires' the lock — during a Redis outage the payment-retry, preauth-capture, reconciliation, retention-purge, referral-payout and reaper loops all run concurrently on every replica, and the per-ride scheduled-delay dedupe key (scheduled_rides.py:51) stops deduplicating, so riders receive duplicate delay notifications and finance receives duplicate reconciliation alerts. _Fix:_ Make redis_set_nx consistent with the module's fail-loud policy: when a Redis URL is configured and the call errors, either raise or return False (treat as 'lock not acquired'), and log at ERROR. Returning False is the safe default for leader election — skipping one tick beats duplicating side-effects.
- **Referral candidate scan capped at 10,000 users with no truncation detection** — `backend/utils/referral_payout.py:151` _(fares-promos)_. The tick fetches at most 10,000 users with referral_code_used set — including all already-paid referees, with no ordering and no cap-hit warning — so once lifetime referred users exceed 10k, unpaid new referees can silently fall outside the window and never be paid or expired. _Fix:_ Filter server-side to referees without a referral_payouts claim (anti-join or a processed flag), or at minimum log an error and paginate when the fetch hits the cap.
- **Driver-side referral credit has no idempotency key — a failed credited_at mark write enables a double-pay on re-credit** — `backend/utils/referral_payout.py:630` _(fares-promos)_. _credit for driver-kind inserts a driver_bonuses row with no source_id (unlike quest bonuses' (kind, source_id) unique index), and in recredit_failed_claim the credited_at mark is a separate write after the money moves — if that mark write fails, the row returns to 'failed' with credited_at NULL and the next re-credit pays the same side a second time with nothing at the DB level to stop it. _Fix:_ Give referral driver_bonuses a deduplicating source_id (the referral_payouts claim id + side) under the existing unique index, so any replayed credit is rejected by the DB regardless of mark-write failures.
- **Surge demand counts in-progress/assigned rides, double-counting busy drivers and overstating surge** — `backend/utils/surge_engine.py:103` _(fares-promos)_. Demand includes driver_assigned/driver_accepted/driver_arrived/in_progress rides while supply counts only is_available drivers, so every ride already being served both adds demand and removes its driver from supply — a market with all drivers busy and zero waiting requests computes a high ratio and charges up to the 2.5x cap despite no unmet demand, on a regulated rider-facing price. _Fix:_ Count only unmet demand ('searching', optionally recent unfulfilled requests) as demand, or include on-trip drivers in the supply denominator, so steady-state full utilisation with no queue does not surge.


### Performance

- **Synchronous firebase_admin App Check verification inside async middleware blocks the event loop on every production /api request** — `backend/core/middleware.py:193` _(auth-identity)_. app_check.verify_token() is a blocking call executed directly in the async dispatch path for every enforced request; its JWKS refresh performs a synchronous network fetch with no offload to a thread, so whenever Google's JWKS endpoint is slow the entire event loop — all HTTP requests and every WebSocket (dispatch events, location updates) — stalls for the duration, directly threatening the <100 ms WS fan-out and <2 s dispatch SLAs. _Fix:_ Run verification off-loop: `await loop.run_in_executor(None, app_check.verify_token, token)`, and/or pre-warm + refresh the JWKS in a background task so the request path only does in-memory signature checks.
- **Blocking firebase_admin.verify_id_token runs on the event loop for every authenticated request** — `backend/dependencies/__init__.py:354` _(core-infra)_. get_current_user calls the synchronous firebase_auth.verify_id_token inside an async dependency on every request; when the SDK's cached Google public certs expire it performs a blocking HTTPS fetch on the event loop, stalling all in-flight requests and risking the <100ms WS fan-out / <150ms location-write SLAs. _Fix:_ Wrap the call in `await asyncio.to_thread(...)`, and short-circuit to the JWT path first when the token header alg is HS256 (Firebase tokens are RS256) to skip Firebase entirely for Spinr JWTs.
- **GET /admin/audit-logs limit parameter is uncapped** — `backend/routes/admin/maintenance.py:287` _(admin-routes)_. limit has no upper bound (Query(50) without le), so limit=1000000 fetches the entire audit_logs table in one request, and the $regex search over the jsonb `details` column runs per-request on that unbounded set. _Fix:_ Add `ge=1, le=500` bounds to limit and `ge=0` to offset.
- **GET /admin/users limit/offset are uncapped** — `backend/routes/admin/users.py:57` _(admin-routes)_. limit is a bare int query param with no upper bound, so limit=10000000 streams the entire users table (full emails/phones) in one response; POST /users/search inherits the same via UserSearchRequest.limit. _Fix:_ Bound both with Query/Field(ge=1, le=500).
- **Redundant second drivers-row fetch in the legacy location write hot path** — `backend/routes/drivers/location.py:410` _(drivers)_. update_location_batch fetches the driver row at line 359 and again at lines 410-412 for the presence refresh, adding a full DB round-trip to every background location POST against the <150 ms P95 driver-location-write SLA. _Fix:_ Reuse driver_rows[0] from the first fetch for the mark_present check.
- **Dead count_documents query in list_available_promos** — `backend/routes/promotions.py:463` _(fares-promos)_. list_available_promos issues a completed-rides-in-30-days count whose result is discarded, adding a wasted DB round-trip to every /promo/available request and every AI quote (fare-estimate SLA path). _Fix:_ Delete the call (or assign and use it if it was meant for a 30-day inactivity fast-path).
- **Public share-tracking endpoint has no rate limit** — `backend/routes/rides/sharing.py:171` _(rides-secondary)_. GET /rides/track/{share_token} is unauthenticated and undecorated, so each request performs up to three Supabase reads (ride by token, driver, driver user) with no per-IP throttle — an anonymous client polling aggressively (or a scripted flood of random tokens) drives unthrottled DB load on the ride-tracking path. _Fix:_ Add the public/api rate-limit decorator (keyed by client IP) to track_shared_ride, mirroring the other ride endpoints.
- **Profile-image base64 fallback can embed a multi-megabyte data-URI in the users row** — `backend/routes/users.py:357` _(core-infra)_. If image compression fails (original bytes kept, up to 5MB) and the storage upload also fails, the image is stored as a base64 data-URI (~6.7MB) in users.profile_image, after which every UserProfile response — including /auth/me on app launch and auth responses — carries multi-MB payloads, blowing the 200ms-class auth SLAs. _Fix:_ Cap the base64 fallback (e.g. reject or downscale to <100KB if compression failed), or store the fallback in a side table/column excluded from default UserProfile serialization.
- **location_batch rate-limiter issues one Redis INCR per point (up to ~499 serial round-trips) on the hot GPS path** — `backend/routes/websocket.py:915` _(websocket-notify)_. The per-user batch rate limiter loops calling redis_incr once per extra point, so a 500-point offline-recovery batch performs ~499 sequential awaited Redis round-trips before any work, adding large latency and Redis load to the location path. _Fix:_ Add an INCRBY wrapper to redis_client and increment by len(points) in one call, so batch accounting is a single round-trip.
- **surge_pricing history row inserted every 2-minute tick even when nothing changed** — `backend/utils/surge_engine.py:289` _(fares-promos)_. recalculate_all_surges inserts a surge_pricing row per enabled area on every tick regardless of whether the multiplier changed, growing the table by ~720 rows/day/area indefinitely. _Fix:_ Insert history only on multiplier change (plus perhaps a low-frequency heartbeat row), and/or add a retention purge for surge_pricing.


### Process / tooling

- **Duplicate migration-number prefixes beyond CLAUDE.md's documented grandfather list** — `backend/migrations` _(x-migrations)_. CLAUDE.md documents a specific grandfathered list of duplicate-prefix migrations (08, 28, 29, 48, 50, 51, 52, 54, 55, 56, 57, 58, 91, 92, 96, 138, 142, 143) as already handled by full-filename idempotency keying, but 22 additional prefixes have duplicate files not on that list (80, 81, 82, 83, 84, 100, 102, 106, 110, 114, 117, 136, 137, 140, 141, 156, 157, 185, 200, 224, 225, 229), including several in the most-recent 80 files (185_service_area_vehicle_cascade.sql / 185_service_areas_subscription_tax_config.sql, 200_data_export_objects.sql / 200_zoho_desk_ticket_service_area.sql, three files sharing prefix 224, two sharing 225, two sharing 229). The stated 'CI prefix-uniqueness check blocks them' claim in CLAUDE.md is therefore either not actually enforced or the grandfather list is stale, so a future genuine conflict could slip through unnoticed the same way. _Fix:_ Update CLAUDE.md's grandfather list to the actual full set (or regenerate it from the repo), and confirm the CI prefix-uniqueness check is actually running on PRs — the current gap between documented and real duplicates suggests it may not be catching new collisions.
- **Driver-incentive CRUD (up to $500 bonuses) writes no audit_logs rows** — `backend/routes/admin/incentives.py:119` _(admin-routes)_. create/update/toggle/delete of ride_incentives — money-affecting configuration — log only logger.info lines with no actor, so there is no audit trail of which admin changed driver bonus payouts. _Fix:_ Add `admin: dict = Depends(get_admin_user)` and log_admin_action to each mutating handler, and reuse the create pattern on the update model's incentive_type.
- **Redis flush-prefix has no audit trail** — `backend/routes/admin/monitoring.py:501` _(admin-routes)_. POST /admin/redis/flush-prefix deletes cache keys (allowlisted, but including operationally significant prefixes) and only echoes admin_id in the response — no audit_logs row records who flushed what and when. _Fix:_ Write an audit_logs row (actor, prefix, deleted_keys) after the flush.
- **Hard deletes of tickets, flags and complaints are unaudited** — `backend/routes/admin/support.py:387` _(admin-routes)_. DELETE /tickets/{id}, /flags/{id} and /complaints/{id} permanently destroy moderation/safety evidence with no audit_logs row, so any support-module admin can erase a complaint or flag (including one feeding the '3 active flags = auto-ban' logic) untraceably. _Fix:_ Prefer soft-delete (is_deleted flag) and add log_admin_action with the acting admin to all three delete handlers.
- **Admin corporate-wallet money endpoints write no audit log rows** — `backend/routes/corporate_wallet.py:84` _(wallet-corporate)_. manual_topup, manual_adjust (arbitrary signed adjustments up to ±$100,000), and update_wallet_config perform admin money/config mutations without any log_admin_action call, violating the CLAUDE.md rule that admin actions go to the audit table. _Fix:_ Add log_admin_action calls (action=corporate_wallet_topup/adjust/config_change with amount, notes, and changed fields) to all three endpoints, following the pattern in routes/corporate_accounts.py.
- **Partial vendor fetch failures return exit 0 — a permanently broken vendor URL silently drops out of the quarterly audit** — `backend/utils/subprocessor_audit.py:141` _(safety-privacy)_. run_audit returns 2 only when every vendor fetch fails; if one vendor's sub-processor page 404s forever (page moved), the audit logs a warning and exits 0, so the GitHub Action passes and that vendor is never monitored again — a silent PIPEDA re-disclosure gap. _Fix:_ Return a non-zero (or at least 1) exit code whenever errors is non-empty, or track consecutive per-vendor failures in the baseline and fail after N misses so the Action opens a PR for review.
- **Divergent unused axios client hard-logs-out on any 401** — `rider-app/utils/apiClient.ts:43` _(rider-account-ux)_. A second, cookie-based axios API client exists that force-logs-out on any refresh failure, contradicting the shared fetch client's transient-failure handling; it is unused by app code but is a latent footgun if imported. _Fix:_ Delete rider-app/utils/apiClient.ts (and its cookie-auth integration test) or align it with the shared client's transient-vs-definitive 401 logic to prevent accidental adoption.


### Legal / regulatory

- **Fare cache not invalidated on surge activation — rider can book into surge they never saw** — `backend/routes/fares.py:377` _(fares-promos)_. Cached /fares responses computed at 1.0x live for the full FARE_CACHE_TTL (default 300s); the surge engine updates service_areas without calling invalidate_fare_cache, and the 60s TTL cap only applies to entries cached while surge was already >1.0, so during a 1.0→surge transition the rider's estimate omits surge that ride creation then charges — conflicting with the 'surge must be visible before booking' rule. _Fix:_ Call invalidate_fare_cache() (or delete the affected area's grid keys) from the surge engine whenever an area's multiplier changes, or always cap the fare-cache TTL at 60s.
- **Rider cancel records insurance Period 1 unconditionally, even if the driver is offline** — `backend/routes/rides/cancellation.py:327` _(rides-booking)_. After releasing the assigned driver, cancel_ride_rider records an insurance Period 1 transition without checking whether set_driver_available actually made the driver available — if the driver went offline between assignment and the cancel, this falsely reopens an online/TNC-contingent insurance window in the append-only driver_insurance_periods audit, the exact misclassification the timeout handlers explicitly guard against. _Fix:_ Capture the set_driver_available return value and only record Period 1 when the release actually landed with is_available=True, mirroring the matching.py guards.
- **Alternate booking screen omits the surge acknowledgement enforced on ride-options** — `rider-app/app/payment-confirm.tsx:124` _(rider-booking-ux)_. payment-confirm.tsx's handleBookRide calls createRide directly with no >1.0× surge confirmation step, so if this screen is ever reached the rider can book a surged fare without the active surge acknowledgement that CLAUDE.md and ride-options require before booking. _Fix:_ Either remove the now-unused payment-confirm screen, or add the same surge-acknowledgement ConfirmSheet gate before proceeding to createRide.
