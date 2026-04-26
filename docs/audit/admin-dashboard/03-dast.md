# Admin Dashboard Audit — Phase 3: DAST / Functional Walkthrough

**Date:** 2026-04-26  
**Method:** Static DAST — full read of all 21 files under `backend/routes/admin/`. Stack could not be spun up in this environment; live HTTP tests replaced by code-path analysis of every route handler.

---

## 1. Methodology

For every endpoint the following was checked:
1. **Auth gate** — explicit `Depends(get_admin_user)` in handler vs router-level only.
2. **Module RBAC** — correct module required; no bypass path.
3. **Input validation** — Pydantic model with field constraints vs raw `Dict[str, Any]`.
4. **Audit log** — `audit_logs` insert present on every state-mutating call.
5. **Business-logic invariants** — surge cap, money Decimal rule, data retention bounds.
6. **Error path** — does the handler surface errors correctly?

Files reviewed: `__init__.py`, `auth.py`, `analytics.py`, `documents.py`, `drivers.py`, `faqs.py`, `legal_documents.py`, `maintenance.py`, `messaging.py`, `monitoring.py`, `promotions.py`, `rides.py`, `service_areas.py`, `settings.py`, `staff.py`, `subscriptions.py`, `support.py`, `users.py`, `vehicle_fleet.py`, `wallet.py`.

---

## 2. Auth Gate Coverage

All sub-routers are mounted on `admin_router` with `dependencies=[Depends(get_admin_user)]` in `__init__.py`. Individual handlers therefore inherit auth without repeating `Depends`. This is correct architecture per the inline docs.

**Inconsistency noted (not a vulnerability):** `rides.py` repeats `Depends(get_admin_user)` on two handlers (`admin_cancel_ride`, `admin_get_ride_route_map`) while all other handlers in that file rely on the router-level dep. This creates confusion about which level provides the gate and risks the pattern drifting — if a handler is ever extracted or remounted, it silently loses auth.

**Confirmed gap:** `GET /users` (`users.py:23`) and `GET /users/{user_id}` (`users.py:65`) — no handler-level `Depends(get_admin_user)`. Relying solely on router-level.

---

## 3. Critical Finding: Plaintext Credential Exposure in Settings

### F-24 — `GET /settings` returns production secrets in API response

**Severity: CRITICAL**  
**File:** `backend/routes/admin/settings.py:47`, `backend/settings_loader.py:22`

`GET /settings` calls `get_app_settings()` which returns the full row from the `settings` table merged with `AppSettings` schema defaults. The schema (`backend/schemas.py:111–116`) includes:

```python
google_maps_api_key: str = ""
stripe_secret_key: str = ""
stripe_webhook_secret: str = ""
twilio_auth_token: str = ""
```

Any admin with the `settings` module (e.g. a `finance` role admin granted this module) can call `GET /api/v1/admin/settings` and receive Stripe's live secret key, Twilio auth token, and Google Maps API key in plaintext JSON. These keys have full API access — Stripe secret key allows arbitrary charges, payouts, and subscription management.

**Reproducer:** `GET /api/v1/admin/settings` with a valid settings-module JWT.

**Suggested fix:** Mask all credential fields in the GET response: return the first 8 characters followed by `*****`, or return `null` and require a separate "reveal" endpoint with elevated logging. Never return credential material verbatim to browser clients.

---

## 4. High Severity Findings

### F-25 — Privilege escalation via `PUT /staff/{staff_id}`

**Severity: HIGH**  
**File:** `backend/routes/admin/staff.py:181`

`create_staff` (line 96) correctly gates creation behind `if admin.get("role") != "super_admin"`. However `update_staff` (line 181) and `delete_staff` (line 227) have **no equivalent role check**. Any admin with the `staff` module can:

1. Call `PUT /api/v1/admin/staff/{id}` with body `{"role": "super_admin"}` to promote any account.
2. Call `DELETE /api/v1/admin/staff/{id}` to remove any admin including super admins.

Combined impact: a custom-role admin granted the `staff` module can delete the real super admin and promote themselves. This is a one-step privilege escalation from any module-granted role to `super_admin`.

**Reproducer:**
```http
PUT /api/v1/admin/staff/<target_id>
Authorization: Bearer <token with staff module>
{"role": "super_admin"}
```

**Suggested fix:** Add `if admin.get("role") != "super_admin": raise HTTPException(status_code=403, ...)` at the top of both `update_staff` and `delete_staff`. Prevent downgrading the last active super admin to avoid lockout.

---

### F-26 — Surge cap bypass via service area update

**Severity: HIGH**  
**File:** `backend/routes/admin/service_areas.py:91`, `backend/services/fare_service.py:148`

`PUT /service-areas/{area_id}` accepts `surge_multiplier` in its allowlist (line 91) with no upper bound. `fare_service.py:148` reads `service_areas.surge_multiplier` directly without a cap:

```python
surge = float(matching_area.get("surge_multiplier", 1.0))
```

`SURGE_CAP = 2.5` is defined and enforced only in `surge_engine.py`'s auto-mode tier calculation — it is **not applied at fare calculation time**. An admin with the `service_areas` module can set `surge_multiplier: 10.0` on any area, causing riders to be charged 10× without any system-level cap.

The CLAUDE.md states: _"Any value > 2.5 requires documented justification (regulatory + reputational risk)"_ — there is currently no enforcement of this policy in code.

**Reproducer:**
```http
PUT /api/v1/admin/service-areas/<area_id>
{"surge_multiplier": 10.0}
```
Then request a fare estimate for that area — it returns 10× the base fare.

**Suggested fix:**
- In `fare_service.py`: `surge = min(float(matching_area.get("surge_multiplier", 1.0)), SURGE_CAP)` — import `SURGE_CAP` from `surge_engine`.
- In `service_areas.py` update handler: reject `surge_multiplier > 2.5` unless caller is `super_admin` AND a `justification` field is supplied.
- Add equivalent cap in `PUT /service-areas/{area_id}/surge`.

---

## 5. Medium Severity Findings

### F-27 — `PUT /settings` writes production secrets with no audit log

**Severity: MEDIUM**  
**File:** `backend/routes/admin/settings.py:52`

Rotation of Stripe secret key, Twilio auth token, or Google Maps API key via `PUT /settings` leaves no entry in `audit_logs`. If credentials are rotated (or exfiltrated and then rotated to cover tracks), there is no forensic record of who made the change or when.

**Suggested fix:** Add an `audit_logs` insert on every settings PUT, recording `actor_id`, `action: "settings_updated"`, and the list of keys changed (not their values).

---

### F-28 — Mass push notification endpoint has no audit trail

**Severity: MEDIUM**  
**File:** `backend/routes/admin/messaging.py:35`

`POST /cloud-messaging/send` with `audience: "all"` dispatches push notifications to every rider and driver. The `cloud_messages` row is inserted (line 106) but records no `actor_id` — only the message content and recipient count. There is no way to determine which admin triggered a mass blast after the fact.

Additionally `audience: "all"` is present in the `Literal` type, enabling a single call to notify all users with no additional confirmation mechanism.

**Suggested fix:** Record `actor_id` (the requesting admin's ID from the JWT) in the `cloud_messages` row and in an `audit_logs` entry. Consider requiring `super_admin` role for `audience: "all"` blasts.

---

### F-29 — Support ticket replies hardcode `sender_id: "admin-001"`

**Severity: MEDIUM**  
**File:** `backend/routes/admin/support.py:229`

```python
"sender_id": "admin-001",  # Could be dynamic based on current admin
```

Every reply sent via `POST /tickets/{ticket_id}/reply` is attributed to `admin-001` in the `support_messages` table regardless of which staff member actually replied. This breaks audit accountability — if a support agent sends an inappropriate or incorrect reply, the audit trail points to a hardcoded placeholder rather than the actual actor.

**Suggested fix:** Pass `admin` via `Depends(get_admin_user)` to this handler and use `admin["id"]` as `sender_id`.

---

### F-30 — Promotions use raw `Dict[str, Any]` — unvalidated financial fields

**Severity: MEDIUM**  
**File:** `backend/routes/admin/promotions.py:88, 271`

Both `POST /promotions` and `PUT /promotions/{promotion_id}` accept `promotion: Dict[str, Any]`. Financial fields like `discount_value`, `total_budget`, `max_uses`, and `referrer_reward` receive no type coercion or bounds validation:

- `discount_value=-50` creates a promotion that **adds** money to a rider's fare (negative discount).
- `total_budget=0` with `is_active=True` creates an active promotion with no budget ceiling.
- `max_uses=0` combined with `max_uses_per_user=99999` results in unlimited use for any rider.

**Suggested fix:** Replace `Dict[str, Any]` with a Pydantic `PromotionCreateRequest` model with `gt=0` constraints on `discount_value`, `total_budget ge=0`, and max sensible bounds.

---

### F-31 — Location history cleanup accepts unbounded `days` parameter

**Severity: MEDIUM**  
**File:** `backend/routes/admin/maintenance.py:26`

```python
async def admin_cleanup_location_history(days: int = 30):
```

No minimum or maximum bound. `POST /maintenance/cleanup-location-history?days=0` would delete all `driver_location_history` rows immediately. The Saskatchewan Transportation Act requires GPS data retention for 3 years (per CLAUDE.md). Accidental or malicious `days=0` would cause an immediate regulatory compliance violation.

**Suggested fix:** Change to `days: int = Query(30, ge=7, le=1095)` — enforce 7-day floor (dispute resolution) and 3-year ceiling.

---

## 6. Low Severity Findings

### F-32 — `resolved_by` in dispute resolution is caller-supplied

**Severity: LOW**  
**File:** `backend/routes/admin/support.py:141`

`PUT /disputes/{dispute_id}/resolve` reads `resolved_by` from `resolution.get("resolved_by", "admin")` (line 148). A caller can attribute the resolution to any arbitrary string, allowing audit trail tampering (e.g. attributing a questionable resolution to "auto_system").

**Suggested fix:** Derive `resolved_by` from `Depends(get_admin_user)` — use `admin["id"]` directly.

---

### F-33 — Audit log coverage: 9+ modules write zero entries

**Severity: LOW**  
**File:** Multiple

Modules with **zero** `audit_logs` writes on any mutating operation:

| Module | Unlogged mutations |
|---|---|
| `promotions.py` | create, update, delete promo codes |
| `service_areas.py` | create/update area, fee, surge override, fare config |
| `documents.py` | create/update/delete doc requirements, approve/reject driver docs |
| `legal_documents.py` | create/update/delete legal docs |
| `faqs.py` | create/update/delete FAQs |
| `vehicle_fleet.py` | create/update/delete vehicle types |
| `messaging.py` | send mass notification |
| `support.py` | create/update/delete disputes, tickets, flags, complaints |
| `maintenance.py` | bulk-delete GPS history, rollup stats |
| `settings.py` | update Stripe/Twilio/Google credentials |

Contrast with modules that DO log: `staff.py`, `users.py` (status changes), `wallet.py` (credit/debit), `drivers.py` (approve/verify/ban).

PIPEDA requires ability to demonstrate who changed what and when. Current coverage ~15% of write operations.

---

### F-34 — Float arithmetic in money/fee paths

**Severity: LOW**  
**File:** `backend/routes/admin/service_areas.py:200, 217`, `backend/routes/admin/maintenance.py:206`

```python
# service_areas.py:200
"amount": float(fee.get("amount", 0)),

# maintenance.py:206
total_earnings = sum(float(r.get("driver_earnings") or 0) for r in rides ...)
```

CLAUDE.md convention: _"use Python `Decimal` only (never float)"_ for money arithmetic. Float sums accumulate rounding error. Although these paths are analytics/display (not settlement), maintaining consistency with the convention avoids future bugs when values are reused in settlement paths.

---

### F-35 — Defense-in-depth gap: read handlers have no explicit auth dep

**Severity: LOW (INFO)**  
**File:** `backend/routes/admin/users.py:23, 65`

`admin_get_users` and `admin_get_user_details` have no `Depends(get_admin_user)` in their signatures. If `users_router` were ever remounted outside `admin_router` (e.g. in a migration or test file), these would become unauthenticated. Low risk today but fragile.

---

## 7. Business Logic Findings Summary

| Path | Issue |
|---|---|
| `PUT /service-areas/{id}` + `PUT /service-areas/{id}/surge` | No surge cap enforcement at API or fare-calc level |
| `POST /promotions` | Negative `discount_value` accepted |
| `POST /maintenance/cleanup-location-history` | `days=0` deletes all GPS history (retention violation) |
| `PUT /disputes/{id}/resolve` | `resolved_by` caller-supplied |
| `POST /tickets/{id}/reply` | `sender_id` hardcoded to `admin-001` |

---

## 8. Phase 3 New Findings

| ID | Finding | Severity |
|---|---|---|
| F-24 | `GET /settings` returns Stripe secret key + Twilio auth token in API response | CRITICAL |
| F-25 | `PUT/DELETE /staff/{id}` lack `super_admin` role check — privilege escalation | HIGH |
| F-26 | Surge cap bypass: `PUT /service-areas/{id}` allows uncapped `surge_multiplier` | HIGH |
| F-27 | `PUT /settings` writes production credentials with no audit log | MEDIUM |
| F-28 | Mass push notification has no audit trail recording actor identity | MEDIUM |
| F-29 | Ticket replies hardcode `sender_id: "admin-001"` — audit attribution broken | MEDIUM |
| F-30 | Promotions POST/PUT accept raw dict — unvalidated financial fields | MEDIUM |
| F-31 | Location cleanup `days` param unvalidated — `days=0` destroys all GPS history | MEDIUM |
| F-32 | `resolved_by` in dispute resolution is caller-supplied | LOW |
| F-33 | Audit log missing on 9+ modules (promotions, service areas, docs, support, etc.) | LOW |
| F-34 | Float arithmetic in fee/earnings paths violates Decimal-only convention | LOW |
| F-35 | `GET /users` handlers rely solely on router-level auth dep (defense-in-depth) | INFO |
