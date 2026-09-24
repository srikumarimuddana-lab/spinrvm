# R10 — Security & Data-Protection Lead (W2) — findings

**Lane:** R10 · **Written:** 2026-09-24 · **Status:** IN PROGRESS (skeleton; sections filled incrementally, each written to disk as completed)
**Repo:** `/home/user/spinrvm` at `main` (see §0 for HEAD). **Report-and-recommend only.** No PII in this document.
**Builds on:** `rapid-baseline-2026-09-24/A2-security.md` (SEC-A2-001..008 — not repeated, only deepened/re-verified where cited), `00-history.md` (HIST-017, §4 row 16, §9 Q2), `W0-SUMMARY.md`.
**Companion matrices:** `../matrices/threat-model.md`, `../matrices/data-classification.md`.
**Evidence labels:** VERIFIED / INFERRED / ASSUMED / PROPOSED / UNKNOWN per master §4. `VERIFIED-LIVE <date>` = observed on the production Supabase project via read-only MCP.

## 0. Scope, method, and what was actually run
_pending_

## 1. Steelman — what the current design gets right
_pending_

## 2. Attack pass — finding cards (§7.1)
_pending_

## 3. BOLA/IDOR sweep — every `{…id}` path parameter and body-supplied id on non-admin routes, and how ownership is enforced
**Method (VERIFIED, 2026-09-24):** static sweep script over `backend/routes/**` (admin router excluded — every `/api/admin/*` router is mounted behind `get_admin_user`/`require_module`, see `backend/server.py:581` and `routes/admin/__init__.py`). For each route decorator whose path contains an `{…id}` segment, the first ownership-style check within 70 lines of the decorator is recorded. 102 routes swept. **Result: no route trusts a path/body id without scoping it to the caller.** Pattern families found:
1. **Filter-at-query** — `{"id": <id>, "user_id"|"rider_id"|"driver_id": caller}` (favorites, notifications, addresses, emergency-contacts, safety-checkin, scheduled-ride delete, driver arrive/start/verify-otp). A foreign id yields 0 rows → 404. Strongest form: cannot be forgotten on a later write.
2. **Fetch-then-compare** — `ride.get("rider_id") == current_user["id"]` and/or `ride.get("driver_id") == driver["id"]`, admin bypass only via the private `_admin_verified` marker (queries, tracking, chat, receipts, sharing, stops, tips/process-payment, rating, lost-found, disputes, wallet pay, promo apply). Weaker: a future branch that skips the compare is a silent IDOR.
3. **Dependency guard** — `require_company_admin` / `require_company_member` (`backend/dependencies/company_guard.py:27,47`) and `_ensure_member` (corporate_rider) for every corporate `{company_id}` route.
4. **Capability token instead of identity** — `GET /offer-cards/{ride_id}.png` (`routes/offer_card.py:72`, short-TTL HMAC bound to ride+driver) and `GET /rides/track/{share_token}` (`routes/rides/sharing.py:229`, 32-byte random, 24 h expiry). Correct design for OS/browser fetches that cannot carry a JWT.
5. **Env-gated test hooks** — `POST /rides/{ride_id}/simulate-arrival` refuses in production (`routes/rides/lifecycle.py:50-51`) and still checks `rider_id`.

**Body-supplied ids (Pydantic fields named `user_id`/`ride_id`/`driver_id` on non-admin routes, VERIFIED):** `wallet.py:117` `WalletPayRequest.ride_id` → `:240` rider check; `disputes.py:43` → `:64-72` rider-or-assigned-driver check; `payments.py:97/120/127` → `_authoritative_ride_charge(ride_id, rider_id=current_user)` `:64` and `:632`; `promotions.py:49/54` → `:205` query-scoped and `:418` rider check; `support.py:68` `ChatRequest.driver_id` is accepted but **unused** by the handler (legacy field — harmless, but a reviewer might assume it scopes something; recommend deleting). `users.py:771` is a response model, not an input.

**Residual weaknesses (not IDORs today):**
- 3 driver-side handlers (`drivers/ride_flow.py:79` accept, `:653` decline, `drivers/ride_reads.py:274` offer) scope via the offer/`driver_id` on the ride rather than a query filter — correct, but they depend on `claim_driver_atomic`'s `{'status':'searching'}` CAS (R8's domain). 
- `GET /drivers/{driver_id}` (`drivers/status.py:268-300`) has a **third** branch: a rider with an *active* ride assigned to that driver receives a "safe public projection" — this is the one place a rider can read another principal's row; the projection is allow-listed, and PII decrypt (`_decrypt_driver_pii`) only runs on the admin/self branch. Reviewed, acceptable; add to the response-schema regression test.
- Family 2 (fetch-then-compare) is ~55 of the 102 routes. No lint/test enforces it. Recommendation SEC-R10-011 (below) proposes a single `load_ride_for(caller, ride_id, roles=…)` helper + a static test that fails on any `get_ride(ride_id)` call in `routes/` not followed by a membership check — the same static-test mechanism that closed the loguru class (C60/C65/C69).

<details><summary>Full sweep table (102 routes; `deps` = FastAPI dependencies on the handler; `check` = first ownership hit, line:text)</summary>

| Route | deps | First ownership check |
|---|---|---|
| `backend/routes/addresses.py:54` DELETE /{address_id} | get_current_user | 56:result = await db_supabase.delete_one("saved_addresses", {"id": address_id, "user_id": cur |
| `backend/routes/ai.py:248` GET /conversations/{conversation_id}/messages | get_current_user_active_session | 250:conversation_id: str, current_user: dict = Depends(get_current_user_active_session) ;; 259:async def delete_ai_conversation(conversation_id: str, current_user: dict = |
| `backend/routes/ai.py:258` DELETE /conversations/{conversation_id} | get_current_user_active_session | 259:async def delete_ai_conversation(conversation_id: str, current_user: dict = Depends(get_cu |
| `backend/routes/corporate_accounts.py:308` POST /{company_id}/kyb-upload-url | get_current_admin | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/corporate_accounts.py:335` POST /{company_id}/kyb-document | get_current_admin | 392:create_corporate_account's owner_bootstrap_error: each provisioning |
| `backend/routes/corporate_accounts.py:374` POST /{company_id}/kyb-review | get_current_admin | 392:create_corporate_account's owner_bootstrap_error: each provisioning |
| `backend/routes/corporate_accounts.py:508` GET /{company_id}/kyb/view | get_current_admin | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/corporate_accounts.py:553` GET /{company_id}/billing/statements/{month}/pdf | get_current_admin | 611:Create a new corporate account (rich B2B fields + optional owner bootstrap). ;; 614:account: Corporate account data (may include owner_email — the first |
| `backend/routes/corporate_accounts.py:698` GET /{account_id} | get_current_admin | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/corporate_accounts.py:728` PUT /{account_id} | get_current_admin | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/corporate_accounts.py:796` DELETE /{account_id} | get_current_admin | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/corporate_company.py:433` PATCH /members/{member_id} | require_company_admin | 438:guard=Depends(require_company_admin), ;; 495:guard=Depends(require_company_admin), |
| `backend/routes/corporate_company.py:491` DELETE /members/{member_id} | require_company_admin | 495:guard=Depends(require_company_admin), ;; 517:guard=Depends(require_company_admin), |
| `backend/routes/corporate_company.py:523` GET /members/{member_id}/allowance | require_company_admin | 527:guard=Depends(require_company_admin), ;; 540:guard=Depends(require_company_admin), |
| `backend/routes/corporate_company.py:535` PUT /members/{member_id}/allowance | require_company_admin | 540:guard=Depends(require_company_admin), ;; 562:guard=Depends(require_company_admin), |
| `backend/routes/corporate_company.py:557` PATCH /members/{member_id}/allowance | require_company_admin | 562:guard=Depends(require_company_admin), ;; 583:guard=Depends(require_company_admin), |
| `backend/routes/corporate_company.py:638` POST /allowance-requests/{request_id}/decide | require_company_admin | 643:guard=Depends(require_company_admin), ;; 707:guard=Depends(require_company_member), |
| `backend/routes/corporate_company_bookings.py:260` POST /bookings/{ride_id}/cancel | require_company_member | 264:ctx: dict = Depends(require_company_member), ;; 267:bookings; owner/admin any of the company's. Delegates to the canonical |
| `backend/routes/corporate_company_bookings.py:476` PATCH /sections/{section_id} | require_company_admin | 480:ctx: dict = Depends(require_company_admin), ;; 516:async def archive_section(section_id: str, ctx: dict = Depends(require_company_admin)): |
| `backend/routes/corporate_company_bookings.py:515` DELETE /sections/{section_id} | require_company_admin | 516:async def archive_section(section_id: str, ctx: dict = Depends(require_company_admin)): |
| `backend/routes/corporate_rider.py:191` GET /{company_id}/balance | get_current_user | 196:membership = await _ensure_member(current_user, company_id) ;; 197:allowance = await get_member_allowance(membership["id"]) or {} |
| `backend/routes/corporate_rider.py:211` GET /{company_id}/rides | get_current_user | 223:membership = await _ensure_member(current_user, company_id) ;; 224:member_id = membership["id"] |
| `backend/routes/corporate_rider.py:307` GET /{company_id}/statement/{month} | get_current_user | 315:`_ensure_member`'s lookup of the *authenticated* user's own membership; ;; 321:membership = await _ensure_member(current_user, company_id) |
| `backend/routes/corporate_rider.py:325` GET /{company_id}/statement/{month}/pdf | get_current_user | 332:expense reporting) -- rider-scoped mirror of ;; 339:membership = await _ensure_member(current_user, company_id) |
| `backend/routes/corporate_rider.py:369` POST /{company_id}/allowance-requests | get_current_user | 375:membership = await _ensure_member(current_user, company_id) ;; 376:pending = await list_pending_allowance_requests_for_member(membership["id"]) |
| `backend/routes/corporate_rider.py:425` GET /{company_id}/allowance-requests | get_current_user | 430:membership = await _ensure_member(current_user, company_id) ;; 431:return await list_company_allowance_requests(company_id, statuses=None, member_id=membersh |
| `backend/routes/corporate_subscriptions.py:93` GET /{company_id}/subscription | get_admin_user | 94:async def get_company_subscription(company_id: str, current_admin: dict = Depends(get_admi ;; 107:current_admin: dict = Depends(get_admin_user), |
| `backend/routes/corporate_subscriptions.py:103` POST /{company_id}/subscription | get_admin_user | 107:current_admin: dict = Depends(get_admin_user), ;; 118:# This endpoint requires get_admin_user — the caller is internal |
| `backend/routes/corporate_subscriptions.py:134` POST /{company_id}/subscription/cancel | get_admin_user | 138:current_admin: dict = Depends(get_admin_user), ;; 154:current_admin: dict = Depends(get_admin_user), |
| `backend/routes/corporate_subscriptions.py:150` POST /{company_id}/subscription-pilot | get_admin_user | 154:current_admin: dict = Depends(get_admin_user), |
| `backend/routes/corporate_wallet.py:126` GET /{company_id}/wallet | get_admin_user | 131:current_admin: dict = Depends(get_admin_user), ;; 163:current_admin: dict = Depends(get_admin_user), |
| `backend/routes/corporate_wallet.py:159` POST /{company_id}/wallet/topup | get_admin_user | 163:current_admin: dict = Depends(get_admin_user), ;; 193:"scope": "corporate_topup", |
| `backend/routes/corporate_wallet.py:262` POST /{company_id}/wallet/adjust | get_admin_user | 266:current_admin: dict = Depends(get_admin_user), ;; 331:current_admin: dict = Depends(get_admin_user), |
| `backend/routes/corporate_wallet.py:327` PUT /{company_id}/wallet/config | get_admin_user | 331:current_admin: dict = Depends(get_admin_user), |
| `backend/routes/disputes.py:141` GET /{dispute_id} | get_current_user | 147:if dispute.get("user_id") != current_user["id"]: |
| `backend/routes/disputes.py:206` PUT /{dispute_id}/resolve | get_current_admin | 233:raise HTTPException(status_code=409, detail="Dispute ownership requires manual review") ;; 237:raise HTTPException(status_code=409, detail="Dispute ownership requires |
| `backend/routes/drivers/offer_receipts.py:90` POST /offers/{offer_id}/receipts | get_current_user,get_token_session_id | 111:user_id=current_user["id"], |
| `backend/routes/drivers/ride_cancel.py:40` POST /rides/{ride_id}/cancel | get_current_user | 60:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 77:if ride.get("driver_id") != driver["id"]: |
| `backend/routes/drivers/ride_cancel.py:491` POST /rides/{ride_id}/noshow | get_current_user | 503:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 511:if ride.get("driver_id") != driver["id"]: |
| `backend/routes/drivers/ride_cancel.py:846` POST /rides/{ride_id}/rate-rider | get_current_user | 853:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 865:if not ride or ride.get("driver_id") != driver["id"]: |
| `backend/routes/drivers/ride_complete.py:397` POST /rides/{ride_id}/complete | get_current_user,get_token_session_id | 405:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 411:await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver["id"]}, |
| `backend/routes/drivers/ride_flow.py:79` POST /rides/{ride_id}/accept | get_current_user,get_token_session_id | 93:db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1), ;; 139:# _shared.check_driver_documents_current for the scope/cost rationale. |
| `backend/routes/drivers/ride_flow.py:653` POST /rides/{ride_id}/decline | get_current_user,get_token_session_id | 680:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 709:if ride.get("driver_id") != driver["id"]: |
| `backend/routes/drivers/ride_flow.py:1032` POST /rides/{ride_id}/arrive | get_current_user | 1035:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 1041:await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver["id"] |
| `backend/routes/drivers/ride_flow.py:1164` POST /rides/{ride_id}/verify-otp | get_current_user | 1171:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 1177:await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver["id"] |
| `backend/routes/drivers/ride_flow.py:1263` POST /rides/{ride_id}/start | get_current_user | 1282:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 1288:await db_supabase.get_rows("rides", {"id": ride_id, "driver_id": driver["id"] |
| `backend/routes/drivers/ride_reads.py:274` GET /rides/{ride_id}/offer | get_current_user | 289:Authorization mirrors decline_ride's WS-18 ownership guard (read-only ;; 301:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) |
| `backend/routes/drivers/status.py:268` GET /{driver_id} | get_current_user | 275:# ordinary rider/driver token also carries (see get_admin_user). ;; 276:is_admin = bool(current_user.get("_admin_verified")) |
| `backend/routes/drivers/status.py:335` PUT /{driver_id}/status | (router-level) | 379:if driver.get("user_id") != current_user["id"]: |
| `backend/routes/drivers/subscriptions.py:1490` POST /subscription/payments/{payment_id}/resend-invoice | get_current_user | 1502:await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 1508:if not payment or payment.get("driver_id") != driver["id"]: |
| `backend/routes/favorites.py:115` POST /{favorite_id}/use | get_current_user | 118:fav = await db.find_one("favorite_routes", {"id": favorite_id, "user_id": current_user["id ;; 135:fav = await db.find_one("favorite_routes", {"id": favorite_id, "user |
| `backend/routes/favorites.py:132` DELETE /{favorite_id} | get_current_user | 135:fav = await db.find_one("favorite_routes", {"id": favorite_id, "user_id": current_user["id ;; 153:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/favorites.py:143` POST /from-ride/{ride_id} | get_current_user | 153:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/lost_and_found.py:235` GET /{case_id} | get_current_user | 237:driver = await _driver_for_user(current_user["id"]) ;; 238:case = await _require_participant(case_id, current_user["id"], driver["id"] if driver else |
| `backend/routes/lost_and_found.py:344` PUT /{case_id}/respond | get_current_user | 351:driver = await _driver_for_user(current_user["id"]) ;; 358:if case.get("driver_id") != driver["id"]: |
| `backend/routes/lost_and_found.py:401` GET /{case_id}/messages | get_current_user | 408:driver = await _driver_for_user(current_user["id"]) ;; 409:await _require_participant(case_id, current_user["id"], driver["id"] if driver else None) |
| `backend/routes/lost_and_found.py:430` POST /{case_id}/messages | get_current_user | 437:driver = await _driver_for_user(user_id) ;; 438:case = await _require_participant(case_id, user_id, driver["id"] if driver else None) |
| `backend/routes/lost_and_found.py:468` POST /{case_id}/messages/image | get_current_user | 485:driver = await _driver_for_user(user_id) ;; 486:case = await _require_participant(case_id, user_id, driver["id"] if driver else None) |
| `backend/routes/notifications.py:477` PUT /{notification_id}/read | get_current_user | 482:{"id": notification_id, "user_id": current_user["id"]}, ;; 498:driver-only rows the other app never displayed. Unscoped — unrecognised |
| `backend/routes/notifications.py:521` DELETE /{notification_id} | get_current_user | 526:is scoped to {"id": ..., "user_id": current_user["id"]} so a caller can ;; 533:{"id": notification_id, "user_id": current_user["id"]}, |
| `backend/routes/offer_card.py:57` GET /{ride_id}.png | (router-level) | 72:verify_offer_card_token(t, ride_id=ride_id) |
| `backend/routes/payments.py:1145` POST /cards/{card_id}/default | get_current_user | 1166:# WS-18: verify the card belongs to this customer before ;; 1167:# promoting it to default. Stripe scopes the modify to the |
| `backend/routes/payments.py:1210` DELETE /cards/{card_id} | get_current_user | 1261:# WS-18: verify the card belongs to this user before detaching. |
| `backend/routes/promotions.py:737` PUT /{promo_id} | (router-level) | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/promotions.py:764` DELETE /{promo_id} | (router-level) | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/quests.py:189` POST /{quest_id}/join | get_current_user | 192:driver = await db.find_one("drivers", {"user_id": current_user["id"]}) ;; 208:# area-scoped quests from drivers outside the area (line ~133), but a money- |
| `backend/routes/quests.py:311` POST /progress/{progress_id}/claim | get_current_user | 314:driver = await db.find_one("drivers", {"user_id": current_user["id"]}) ;; 364:"driver_id": driver["id"], |
| `backend/routes/quests.py:506` PATCH /{quest_id} | get_admin_user | 507:async def admin_update_quest(quest_id: str, req: UpdateQuestRequest, admin: dict = Depends ;; 522:if req.max_participants is not None: |
| `backend/routes/quests.py:530` GET /{quest_id}/participants | get_admin_user | 530:@admin_router.get("/{quest_id}/participants") ;; 531:async def admin_get_quest_participants( |
| `backend/routes/rides/cancellation.py:42` POST /{ride_id}/cancel | get_current_user | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/rides/cancellation.py:956` DELETE /scheduled/{ride_id} | get_current_user | 977:{"id": ride_id, "rider_id": current_user["id"], "is_scheduled": True}, ;; 1004:"rider_id": current_user["id"], |
| `backend/routes/rides/chat.py:27` GET /{ride_id}/chat-status | get_current_user | 40:if ride.get("rider_id") != current_user["id"]: ;; 42:if not (driver and ride.get("driver_id") == driver["id"]): |
| `backend/routes/rides/chat.py:69` GET /{ride_id}/messages | get_current_user | 77:is_rider = ride.get("rider_id") == current_user["id"] ;; 79:is_driver = driver and ride.get("driver_id") == driver["id"] |
| `backend/routes/rides/chat.py:103` POST /{ride_id}/messages | get_current_user | 136:is_rider = ride.get("rider_id") == current_user["id"] ;; 137:driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]}) |
| `backend/routes/rides/chat.py:209` POST /{ride_id}/typing | get_current_user | 231:raise HTTPException(status_code=403, detail="Not a participant") |
| `backend/routes/rides/lifecycle.py:42` POST /{ride_id}/simulate-arrival | get_current_user | 55:if ride.get("rider_id") != current_user["id"]: ;; 92:token it would already have hit the is_driver 403 below. So no shipped |
| `backend/routes/rides/lifecycle.py:70` POST /{ride_id}/start | get_current_user | 92:token it would already have hit the is_driver 403 below. So no shipped ;; 106:if not current_user.get("is_driver"): |
| `backend/routes/rides/lifecycle.py:170` POST /{ride_id}/complete | get_current_user | 191:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/lost_found.py:32` POST /{ride_id}/lost-and-found | get_current_user | 44:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/payments.py:123` POST /{ride_id}/tip | get_current_user | 125:@idempotent_endpoint(scope="ride_tip") ;; 143:if ride.get("rider_id") != current_user.get("id"): |
| `backend/routes/rides/payments.py:374` POST /{ride_id}/process-payment | get_current_user | 388:if ride.get("rider_id") != current_user.get("id"): |
| `backend/routes/rides/queries.py:351` GET /{ride_id} | get_current_user | 369:is_rider = ride.get("rider_id") == current_user["id"] ;; 371:is_driver = driver and ride.get("driver_id") == driver["id"] |
| `backend/routes/rides/rating.py:43` POST /{ride_id}/rate | get_current_user | 45:@idempotent_endpoint(scope="ride_rate") ;; 54:if not ride or ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/receipts.py:42` GET /{ride_id}/receipt | get_current_user | 49:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/receipts.py:202` GET /{ride_id}/receipt.pdf | get_current_user | 228:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/receipts.py:274` POST /{ride_id}/email-receipt | get_current_user | 285:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/safety.py:92` POST /{ride_id}/emergency | get_current_user_allow_expired | 100:# membership is enforced below regardless. ;; 109:is_rider = ride.get("rider_id") == current_user["id"] |
| `backend/routes/rides/safety.py:483` POST /{ride_id}/emergency/false-alarm | get_current_user | 499:"reported_by_user_id": current_user["id"], |
| `backend/routes/rides/safety.py:912` POST /{ride_id}/safety-checkin | get_current_user | 931:# Verify the ride belongs to this rider and is still in_progress. ;; 932:ride = await _deps.db_supabase.get_rows("rides", {"id": ride_id, "rider_id": user_id}, lim |
| `backend/routes/rides/sharing.py:42` GET /{ride_id}/share | get_current_user | 51:# routes/rides/safety.py::trigger_emergency's membership check. ;; 52:is_rider = ride.get("rider_id") == current_user["id"] |
| `backend/routes/rides/sharing.py:127` POST /{ride_id}/share | get_current_user | 139:if ride.get("rider_id") != current_user["id"]: ;; 145:share_token = ride.get("shared_trip_token") |
| `backend/routes/rides/sharing.py:218` GET /{ride_id}/shared-contacts | get_current_user | 224:if ride.get("rider_id") != current_user["id"]: ;; 229:@router.get("/track/{share_token}") |
| `backend/routes/rides/sharing.py:331` POST /{ride_id}/live-activity/register | get_current_user | 350:if ride.get("rider_id") != current_user["id"]: ;; 375:"rider_id": current_user["id"], |
| `backend/routes/rides/stops.py:72` POST /{ride_id}/stops | get_current_user | 84:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/stops.py:136` DELETE /{ride_id}/stops/{stop_index} | get_current_user | 148:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/stops.py:196` POST /{ride_id}/stops/{stop_index}/complete | get_current_user | 206:drivers = await _deps.db.get_rows("drivers", {"user_id": current_user["id"]}, limit=1) ;; 210:ride = await _deps.db.find_one("rides", {"id": ride_id, "driver_id": dri |
| `backend/routes/rides/stops.py:258` PATCH /{ride_id}/notes | get_current_user | 277:if ride.get("rider_id") != current_user["id"]: |
| `backend/routes/rides/tracking.py:69` GET /{ride_id}/live-route | get_current_user | 90:is_rider = ride.get("rider_id") == current_user["id"] ;; 92:is_driver = bool(driver_self) and ride.get("driver_id") == driver_self["id"] |
| `backend/routes/rides/tracking.py:206` GET /{ride_id}/navigation-steps | get_current_user | 221:this cache is leg-scoped rather than position-scoped like /live-route's. ;; 222:`force_refresh` bypasses this ride-scoped cache (but not the budget |
| `backend/routes/safety.py:150` POST /report/{incident_id}/photo | get_current_user | 174:if incident.get("reported_by_user_id") != user_id: |
| `backend/routes/service_areas.py:109` GET /service-areas/{area_id}/airport-zones | (router-level) | NO-OWNERSHIP-HIT-IN-70-LINES |
| `backend/routes/users.py:944` DELETE /emergency-contacts/{contact_id} | get_current_user | 950:{"id": contact_id, "user_id": current_user["id"]}, |

</details>

## 4. Mandatory closures (brief's MUST-close list)
_pending_

## 5. Rebuild pass — §7.3 Rebuild Delta cards
_pending_

## 6. Agent control plane (greenfield §6) — product AI + dev agents
_pending_

## 7. Sweep-catalog §2.6 — every item: met / partial / missing with evidence
_pending_

## 8. Top 5
_pending_

## 9. NOT verified
_pending_

## 10. Human-only questions
_pending_

## 11. Escalations
_pending_
