# Data Classification Map

**Purpose:** Every DB column, Redis key, log field, and API response field is
classified. Required for PIPEDA record-keeping (s.4.7), SOC2 CC6.1, and
incident-response impact assessment.

**Ownership:** `data` + `compliance` · Re-review cadence: quarterly or on any
schema change.

---

## Classification Levels

| Class | Definition | Example | Handling |
|---|---|---|---|
| **C1 — Public** | No confidentiality requirement | Driver first name shown in ride UI, surge multiplier, city name | Can appear in logs, API responses to any role, public dashboards |
| **C2 — Internal** | Internal-only but not personally identifying | Ride IDs, aggregate counts, surge polygons | Must not leak to unauthenticated users; OK in logs |
| **C3 — Restricted** | Personally identifying (PII) or business-sensitive | Rider/driver phone, email, home/work address, DOB, driver rating, fare amounts | Encrypted at rest (pgsodium); redacted in logs; role-gated |
| **C4 — Secret** | Payment cards, credentials, government IDs | Stripe customer IDs (partial OK), licence number, SIN (driver tax), bank account | Not stored unless legally required; if stored, encrypted + audit-logged read; never in logs |
| **C5 — Regulated Retention** | Data with mandated retention + deletion horizon | Driver T4A data (CRA 7 y), ride GPS trace (OPC guidance 2 y), audit logs (SOC2 1 y) | Must have hard-delete cron; retention horizon documented below |

---

## Per-Table Column Map (authoritative — update on schema change)

### `users`

| Column | Class | Encrypted at rest | In logs? | Cross-surface visibility | Retention |
|---|:-:|:-:|:-:|---|---|
| `id` (uuid) | C2 | — | Yes (as `user_id`) | All roles | Lifetime of account |
| `phone` | C3 | pgsodium | **Redacted** | Self, admin-support+ | 2 y post-deletion |
| `phone_hash` | C3 | N/A (hash) | Yes | Internal indexing only | 2 y post-deletion |
| `email` | C3 | pgsodium | **Redacted** | Self, admin-support+ | 2 y post-deletion |
| `first_name` | C1 | — | OK (short form) | All (rider sees driver's) | 2 y post-deletion |
| `last_name` | C3 | pgsodium | **Redacted** | Self, admin-support+, driver only during active ride | 2 y post-deletion |
| `role` | C2 | — | Yes | All | Lifetime |
| `created_at` | C2 | — | Yes | All | Lifetime |
| `deleted_at` | C2 | — | Yes | Admin+ | N/A |

### `drivers`

| Column | Class | Encrypted at rest | In logs? | Cross-surface visibility | Retention |
|---|:-:|:-:|:-:|---|---|
| `user_id` | C2 | — | Yes | All | Lifetime |
| `status` (searching/active/suspended) | C2 | — | Yes | Internal | Lifetime |
| `licence_number` | C4 | pgsodium | **Never** | Admin-compliance only | 7 y post-account-close (CRA) |
| `licence_expiry` | C3 | — | OK | Admin+, driver self | 7 y post-account-close |
| `sin_last4` | C4 | pgsodium | **Never** | Admin-finance only | 7 y (CRA) |
| `stripe_connect_id` | C3 | — | OK (partial) | Admin-finance, driver self | Until disconnection |
| `bank_account_last4` | C4 | pgsodium | **Never** | Admin-finance, driver self | Until rotated |
| `vehicle_plate` | C3 | — | OK | Admin+, rider during active ride | Lifetime |
| `rating_avg` | C3 | — | OK | All (rounded to 1 decimal) | Lifetime |
| `current_lat`, `current_lng` | C3 (live), C5 (trace) | — | **Redacted** | Rider during active ride only | Live: in-memory only; trace: 2 y |

### `rides`

| Column | Class | Encrypted at rest | In logs? | Cross-surface visibility | Retention |
|---|:-:|:-:|:-:|---|---|
| `id` | C2 | — | Yes | All involved parties | 7 y (CRA tax record) |
| `rider_id` | C2 | — | Yes (hashed) | Self, driver (ride scope), admin+ | 7 y |
| `driver_id` | C2 | — | Yes (hashed) | Self, rider (ride scope), admin+ | 7 y |
| `pickup_address` | C3 | pgsodium | **Redacted** | Rider self, driver until drop-off, admin+ | 7 y |
| `destination_address` | C3 | pgsodium | **Redacted** | Rider self, driver until drop-off, admin+ | 7 y |
| `pickup_lat`, `pickup_lng` | C3 | — | **Redacted** | Same as above | 7 y |
| `status` | C2 | — | Yes | All | 7 y |
| `fare_amount` | C3 | — | OK | Parties + admin-finance | 7 y |
| `surge_multiplier` | C1 | — | Yes | All | 7 y |
| `gst_amount`, `pst_amount` | C3 | — | OK | Admin-finance | 7 y (CRA) |
| `created_at`, `completed_at` | C2 | — | Yes | All | 7 y |
| `cancellation_reason` | C2 | — | OK | Parties + admin | 7 y |

### `payments` / `payment_methods`

| Column | Class | Encrypted at rest | In logs? | Notes |
|---|:-:|:-:|:-:|---|
| `stripe_payment_intent_id` | C3 | — | OK | Internal; no card data |
| `stripe_customer_id` | C3 | — | OK | — |
| `payment_method_last4` | C4 | — | **Never** | Display only to owning user |
| `card_brand` | C2 | — | OK | — |
| `amount_cents` | C3 | — | OK | Parties + admin-finance |

**Rule:** No full PAN (card number) ever touches Spinr systems. Stripe Elements /
Payment Intents are used exclusively. This keeps Spinr in PCI-DSS SAQ-A scope.

### `wallets` / `wallet_transactions`

| Column | Class | Encrypted at rest | In logs? | Notes |
|---|:-:|:-:|:-:|---|
| `user_id` | C2 | — | Yes | — |
| `balance_cents` | C3 | — | OK (user self only) | Tied to identity; not public |
| `delta_cents` | C3 | — | OK | — |
| `reason` | C2 | — | OK | Enum |
| `idempotency_key` | C2 | — | OK | UUID |

### `stripe_events`

| Column | Class | In logs? | Notes |
|---|:-:|:-:|---|
| `event_id` | C2 | Yes | Idempotency dedup |
| `payload` (raw) | C3 | **No** | Payload may contain email; treat as C3 |

### `audit_logs` (admin actions, DSARs, SOS events)

| Column | Class | Retention | Immutability |
|---|:-:|---|---|
| `actor_id`, `action`, `target_id`, `timestamp` | C2 | 3 y (SOC2) | Append-only; admin cannot delete |
| `before_json`, `after_json` | C3 (may contain PII) | 3 y | Encrypted |

---

## Redis Keys

| Key pattern | Class | TTL | Notes |
|---|:-:|---|---|
| `otp:{phone}` | C3 | 5 min | SHA-256 hashed; never log value |
| `otp_lockout:{phone}` | C2 | 24 h | Boolean/counter |
| `rate_limit:{key}` | C2 | varies | — |
| `ride_offer:{ride_id}:{driver_id}` | C3 | 30 s | Contains pickup lat/lng |
| `ws:dispatch:{channel}` | C2 | pubsub | No PII |
| `session:{user_id}` | C3 | 15 min | Token hash |

---

## Log Fields — Allowed vs Forbidden

**Always allowed:**
- `request_id`, `trace_id`
- `user_id` (hashed if emitted externally)
- `ride_id`
- HTTP method, path (with path params), status code, latency
- Error class name (not full message if it may contain PII)

**Redact (never emit raw):**
- `phone` — show `+1***XXXX` only
- `email` — show first char + domain: `j***@gmail.com`
- `home_address`, `work_address`, `pickup_address`, `destination_address`
- `licence_number`, `sin`, `bank_account`
- `current_lat`, `current_lng` (live location) — round to 2 decimals if emitted for ops
- `otp_code`, `jwt`, `refresh_token`, `stripe_secret_*`

**Verify with:** `backend/utils/log_redactor.py` (to be created) + test corpus in
`backend/tests/test_log_redaction.py`.

---

## Cross-Border Data Flow

| Destination | Country | Data class transferred | Legal basis | Disclosure |
|---|---|---|---|---|
| Stripe | US | C3 (amount, email), C4 (card via Stripe Elements never touches Spinr) | Stripe DPA + SCCs | Privacy policy |
| Firebase (Auth + FCM + Crashlytics) | US | C3 (phone, FCM token, crash stack with user_id) | Google DPA + SCCs | Privacy policy |
| Twilio (SMS) | US | C3 (phone, OTP-containing message) | Twilio DPA | Privacy policy |
| Google Maps | US | C3 (addresses queried) | Google DPA | Privacy policy |
| Google Gemini | US | C2/C3 (user-supplied text for support features) | Google DPA | **Privacy policy — DV-16 open** |
| Supabase | **Canada (required)** | All C2–C5 | DPA + hosting region attestation | Privacy policy |

---

## Retention Horizons (authoritative)

| Data | Retention | Basis | Deletion mechanism |
|---|---|---|---|
| Ride records | 7 years post-ride | CRA + OPC guidance | Cron (DV-8 open) |
| Driver T4A data | 7 years post-account-close | CRA s.230 | Cron (DV-8 open) |
| Rider account PII | 2 years post-deletion | PIPEDA s.5(3) | Cron (DV-8 open) |
| Audit logs | 3 years | SOC2 | Append-only + archival export |
| GPS traces | 2 years | OPC guidance | Cron |
| Stripe events | 90 days | Idempotency only; Stripe retains | TTL job |
| OTP codes (Redis) | 5 minutes | Auth freshness | TTL |
| Session tokens | 30 days | Refresh window | TTL |
| Support chat (Gemini-processed) | 90 days | Ops | Cron |

---

## Changelog

| Date | Change | Author |
|---|---|---|
| 2026-04-24 | Initial classification map | audit-framework |

---

## Open Gaps Against This Map

- **DV-8** — No scheduled hard-delete at retention horizons (P2, open).
- **DV-16** — Gemini not disclosed as sub-processor in privacy policy (P2, open).
- **PII-in-logs corpus scan** not yet run against production logs.
- **Redis key audit** not yet run — confirm no un-listed keys carry C3 data.
