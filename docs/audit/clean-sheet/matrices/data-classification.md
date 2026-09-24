# Data classification matrix — PII / financial columns (R10, 2026-09-24)

**Status:** COMPLETE. Supersedes the per-table map and retention table in `docs/data-classification.md` (2026-04-24), which this lane found wrong on encryption (claims `users.*` are pgsodium-encrypted — nothing encrypts `users`), on retention (2 y GPS / 2 y rider PII vs the implemented 3 y / 30 d / 7 y), and on sub-processors (SEC-R10-014). The class scale (C1 Public … C5 Regulated-retention) and the Redis-key and log-field sections of that file remain valid and are not repeated.
**Sources:** `backend/migrations/` (32, 137, 138, 216, 221, 244, 289, 296, 357, 396, 413, 428, 436), `routes/drivers/_shared.py:155` (`_VAULT_PII_FIELDS`), `utils/vault_pii.py`, `utils/retention_purge.py`, `utils/pii.py` (key-name denylist), CLAUDE.md PIPEDA + Regulatory sections, and the live table list (VERIFIED-LIVE 2026-09-24: 131 public tables, RLS on all; row counts quoted are orders of magnitude only). Column names are from migrations/code; exact live column sets were not read (`list_tables verbose` not run).
**Legend — Enc:** `vault` = pgsodium/Vault UUID reference via SECURITY DEFINER RPC · `hash` = one-way (SHA-256 / HMAC / bcrypt) · `plain` = plaintext at rest (Supabase disk encryption only) · **Reads:** who/what reads it (all DB reads are service-role; "role" means application-level gate) · **Log?:** per CLAUDE.md never-log list and `utils/pii.py` denylist · **Label** applies to the whole row.

## 0. Classes and legend

| Class | Meaning (unchanged from the 04-24 doc) |
|---|---|
| C1 | Public |
| C2 | Internal, non-identifying (ids, counts, statuses) |
| C3 | PII / business-sensitive — redact in logs, role-gated |
| C4 | Secret — cards, credentials, government IDs; never in logs; audited read |
| C5 | Regulated retention — mandated keep + delete horizon |

## 1. Identity (users, admin_staff, corporate_*)

| Table.column | Class | Enc | Reads | Retention | Log? | Evidence / label |
|---|---|---|---|---|---|---|
| `users.id` | C2 | — | all surfaces (Firebase uid for driver-app-created rows, `routes/auth.py:1626`) | 7 y with ride attribution (migration 216) | yes (`user_id`) | VERIFIED |
| `users.phone` | C3 | **plain** | self, admin (masked in UI), Zoho Desk egress, Twilio (OTP) | scrubbed at 30 d post deletion request (Step N, migration 296) | **no** — `phone_last4` only | VERIFIED (no encryption migration exists) |
| `users.email` | C3 | **plain** | self, admin, Zoho, SES, Stripe customer | 30 d scrub | **no** — `_log_safe_email` | VERIFIED |
| `users.first_name` / `last_name` | C3 (first name C1 in ride UI) | plain | self, counterpart during ride, admin, Zoho, FCM payloads (excluded via `_FCM_EXCLUDE` sets) | 30 d scrub | **no** (full name) | VERIFIED |
| `users.role`, `is_rider`, `is_driver`, `status`, `token_version`, `sessions_invalid_before`, `current_session_id` | C2 | — | auth path every request | lifetime | yes | VERIFIED |
| `users.consent_version` (334), `email_verified` (252), `deletion_requested_at` (66), `profile_image_url` (23/47) | C2/C3 | plain | self, admin | lifetime / 30 d | image URL: no | VERIFIED (migrations) |
| `users.stripe_customer_id` | C3 | plain | payments path | until deletion | partial ok | INFERRED (column name from code; not read) |
| `admin_staff.email`, `role`, `modules`, `is_active`, `token_version`, `last_activity_at` | C3/C2 | plain | admin auth (`dependencies/__init__.py:355`) | lifetime | email: no | VERIFIED |
| `admin_staff.password_hash` | C4 | **hash** (bcrypt; legacy SHA-256 accepted on change — SEC-A2 row) | admin login only | lifetime | never | VERIFIED (`utils/password.py`) |
| `admin_staff.totp_secret` | C4 | UNKNOWN (not read) | TOTP verify | lifetime | never | UNKNOWN |
| `corporate_accounts.*` (name, billing contact, KYB docs refs), `corporate_members.*`, `corporate_allowed_domains` | C3 (business) | plain | company admins via `require_company_*`, admin | lifetime (no purge step found) | no contacts | VERIFIED (authz) / INFERRED (retention) |
| `corporate_email_otp_records.code_hash` | C4 | **hash** (HMAC) | portal login | ~5 min | never | VERIFIED |
| `rider_email_verification_otp.code_hash` | C4 | **hash** | rider verify | ~5 min | never | VERIFIED |

## 2. Driver regulatory & government-ID

| Table.column | Class | Enc | Reads | Retention | Log? | Evidence / label |
|---|---|---|---|---|---|---|
| `drivers.license_number` | C4 | **vault** (migration 32; `_VAULT_PII_FIELDS`) | admin/self via `_decrypt_driver_pii` (`drivers/status.py:280`); driver-licence export (change-log 09-15) | 7 y post-close (SGI/CRA) | never | VERIFIED |
| `drivers.sin` | C4 | **vault**, write-only (migration 289; no app decrypt path); dormant-driver purge (`dormant_driver_sin_purge_service.py`, 413) | T4A job only (future) | 7 y (CRA) then purge | never | VERIFIED |
| `drivers.vehicle_vin` | C3 | **plain** (migration 244 reversed encryption "by operational request") | admin, exports | lifetime | no | VERIFIED |
| `drivers.vehicle_plate`, make/model/colour | C3/C1 | plain | rider during active ride, admin | lifetime; `driver_vehicle_history` append-only | plate: ok | VERIFIED-LIVE (table comment) |
| `drivers.licence_expiry`, `insurance_expiry`, `registration_expiry` | C3 | plain | go_online gate, expiry loop | 7 y | ok | INFERRED (columns per CLAUDE.md) |
| `drivers.<home address>` | — | **does not exist** | — | — | — | VERIFIED: migration 221:18 "Full driver addresses are intentionally kept out"; CLAUDE.md sentence is wrong |
| `drivers.stripe_connect_id` / `stripe_account_id` | C3 | plain | payouts | until disconnect | partial ok | INFERRED |
| `drivers.bank_account_last4` | C4 | UNKNOWN (04-24 doc says vault; no migration found) | driver self, finance | until rotated | never | UNKNOWN — likely not stored (Stripe Connect holds banking) |
| `driver_documents.*`, `document_files.*` (licence/insurance/registration scans) | C4 | plain rows; objects in private Storage bucket | admin documents module; driver self | 7 y | never (keys only) | VERIFIED-LIVE (RLS on `document_files` now enabled) |
| `driver_crc_consents`, `driver_crc_consent_events` | C5 | plain | onboarding, admin | 7 y (append-only events) | no | VERIFIED-LIVE (table comments) |
| `driver_insurance_periods`, `driver_period_distances`, `driver_insurance_period_corrections` | C5 | plain | insurance audit export, admin | 7 y, append-only, UPDATE/DELETE blocked | ids ok | VERIFIED-LIVE |
| `driver_csv_import`, `driver_bank_import` (staging) | C4 | — | — | **dropped** | — | VERIFIED-LIVE (absent from public schema); history rewrite incident → COMP-013 |
| `legacy_id_crosswalk` | C2 | plain | migration tooling | lifetime | ids ok | VERIFIED-LIVE ("IDs only, no PII") |
| `driver_appeals`, `driver_notes` | C3 | plain | admin | lifetime | no free text | VERIFIED-LIVE (comments) |

## 3. Location & trip

| Table.column | Class | Enc | Reads | Retention | Log? | Evidence / label |
|---|---|---|---|---|---|---|
| `rides.pickup_lat/lng`, `dropoff_lat/lng` | C3/C5 | plain | rider, assigned driver, admin, dispatch, Maps proxy | GPS anonymised at **3 y** (296 Step A), row hard-deleted at 7 y (Step B) | **never raw** — geohash only | VERIFIED (migration 296:165-198) |
| `rides.pickup_address`, `destination_address` | C3 | plain | rider, driver (during ride), admin, receipts, FCM (excluded sets) | 3 y / 7 y as above | **never** — city/area only | VERIFIED |
| `rides.rider_id`, `driver_id`, `status`, fare columns, `gst_amount`, `pst_amount`, `surge_multiplier` | C2/C3 | plain | parties, admin finance | 7 y attributable (216) | ids/amounts ok | VERIFIED |
| `rides.pickup_otp` | C3 (low) | **plain** by design (rider must read it) | rider, driver verify (`ride_flow.py:1204`) | with ride | never | VERIFIED |
| `rides.shared_trip_token` | C3 (capability) | plain, 32-byte random, 24 h | public track page | 24 h effective | never | VERIFIED (`rides/sharing.py`) |
| `ride_routes.*` (polylines, OSRM line) | C5 | plain | admin map modal | geometry cleared at 3 y; row cascades at 7 y | never | VERIFIED-LIVE (comment) |
| `ride_route_snapshot_objects` | C5 | Storage objects | admin | as above | never | INFERRED |
| `driver_location_history.*` | C5 | plain | dispatch analytics, distance finaliser | **90 d** (296 Step C) | never raw | VERIFIED |
| `driver_daily_stats` | C2 | — | admin analytics | lifetime ("without keeping raw GPS") | ok | VERIFIED-LIVE |
| `ride_location_gap_events`, `ride_distance_*` | C2 | — | integrity audit | append-only | ok | VERIFIED-LIVE ("no latitude or longitude") |
| `saved_addresses.address/lat/lng` | C3 | plain | self only (`addresses.py:56` user-scoped) | 30 d scrub (INFERRED) | never | VERIFIED (authz) |
| `price_searches` | C3 (origin/dest) | plain | analytics | purge (migration 228) | never | VERIFIED (migration) |
| `safety_incidents.*` (+ `safety_incident_photos`) | C3/C5 | plain; photos in private bucket | admin safety, SOS loop | **do not purge** (SK Transportation Act) | never coords/free text | VERIFIED-LIVE (comment) |
| `venues.pickup_points` | C1 | — | rider app via backend | lifetime | ok | VERIFIED-LIVE |
| Redis `ride_offer:*`, presence keys | C3 | in-memory | dispatch | 30 s / TTL | never | per 04-24 doc (unchanged) |

## 4. Financial

| Table.column | Class | Enc | Reads | Retention | Log? | Evidence / label |
|---|---|---|---|---|---|---|
| `financial_events`, `financial_event_entries` | C5 | plain | finance, reconciliation loop | 7 y, append-only, DELETE only in purge Step H | amounts/ids ok | VERIFIED-LIVE (comments; 290 grant lockdown) |
| `wallets.balance`, `wallet_transactions`, `corporate_wallets`, `corporate_wallet_transactions` | C3 | plain | owner, company admin, finance | 7 y | amounts ok | VERIFIED (authz) |
| `payouts`, `driver_stripe_payouts`, `driver_stripe_ledger`, `auto_payout_batches`, `referral_payouts` | C3/C5 | plain | finance, driver self | 7 y (T4A) | amounts ok | VERIFIED-LIVE (ledger comment "never sum into T4A") |
| `driver_statements` | C5 | plain (+ PDF objects) | driver self, finance | 7 y | no | VERIFIED-LIVE |
| `stripe_events.payload` | C3 | plain (raw Stripe JSON, may contain email) | webhook handler, admin stripe_events | 90 d (04-24 doc) — **INFERRED**, no purge step confirmed | never | INFERRED |
| `stripe_disputes`, `stripe_orphan_refunds`, `reconciliation_discrepancies` | C3 | plain | finance | 7 y | ids ok | VERIFIED-LIVE |
| `subscription_payments`, `driver_subscriptions`, `corporate_subscriptions` | C3 | plain | finance, owner | 7 y | amounts ok | VERIFIED-LIVE |
| Card PAN | — | **never stored** (Stripe Elements / PaymentSheet) | — | — | never | VERIFIED (04-24 rule, still true — `rider-app/app/_layout.tsx:969` StripeProvider) |
| Rider receipts (GST 5 % / PST 6 % lines) | C3 | PDF/email | rider | 7 y | no | VERIFIED (`rides/receipts.py`) — tax logic is R9/R12 |

## 5. Auth secrets & credentials

| Table.column / store | Class | Enc | Reads | Retention | Log? | Evidence / label |
|---|---|---|---|---|---|---|
| `otp_records.code` | C4 | **hash** (HMAC-SHA256, pepper) | verify-otp | 5 min + delete on use | never | VERIFIED (`crypto.py`; 22 live rows) |
| `refresh_tokens.token_hash` | C4 | **hash** (SHA-256 of 384-bit random) | `/auth/refresh` | 30 d; never-revoked rows purged (436); `revocation_reason` (441) | never | VERIFIED |
| `refresh_tokens.user_agent`, `ip` | C3 | plain (truncated) | new-device signal, forensics | 30 d | no | VERIFIED (`refresh_tokens.py:198-204`) |
| `settings` (`app_settings` row): `stripe_secret_key`, `stripe_webhook_secret`, `twilio_auth_token`, `google_maps_api_key`, `ai_api_key_*`, `posthog_api_key`, `zoho_*` | **C4** | **UNKNOWN** (plaintext vs Vault ref — C43's own open sub-question) | `settings_loader.get_app_settings()` every consumer; admin settings page (write) | lifetime | never | VERIFIED-LIVE (RLS now enabled, no policies) / UNKNOWN (enc) — SEC-R10-002 |
| `settings` public subset (`google_maps_api_key`, `stripe_publishable_key`, `track_base_url`, flags) | C1 | — | `GET /api/v1/settings` allow-list (`routes/settings.py:50-75`) | — | ok | VERIFIED |
| `zoho_desk_config` | C4 (OAuth tokens) | UNKNOWN | Zoho service | lifetime | never | VERIFIED-LIVE (table exists) / UNKNOWN (enc) |
| `push_tokens.token` | C3 | plain | FCM/APNs send | until unregister | never | INFERRED |
| Env: `JWT_SECRET`, `OTP_PEPPER`, `ADMIN_PASSWORD` (bcrypt at boot), `BREAK_GLASS_TOKEN_HASH`, `SUPABASE_SERVICE_ROLE_KEY`, `FIREBASE_SERVICE_ACCOUNT_JSON`, `REVIEW_LOGIN_ACCOUNTS` | C4 | provider secret store | process only | rotation: **all TBD** (`docs/runbooks/secret-rotation.md:42-56`) | never | VERIFIED |
| Redis: `otp_fail:*`, `otp_lock:*`, `otp_sendc:*`, `revoked_session:*`, `admin:revoked:*`, `admin:breakglass:*` | C2/C3 (phone-keyed) | in-memory | auth path | TTL | phone last-4 only | VERIFIED (`routes/auth.py:197-210`, `session_revocation.py:60`) |

## 6. Third parties (people who are not Spinr users)

| Table.column | Class | Enc | Reads | Retention | Log? | Evidence / label |
|---|---|---|---|---|---|---|
| `emergency_contacts.name/phone` | C3 (third party) | **vault** (357/359) | owner (`users.py:840-960`, user-scoped), SOS send path | with account (30 d scrub INFERRED) | never | VERIFIED |
| `sos_contact_suppressions.phone` | C3 (third party) | plain (keyed on phone) | SOS send (fail-open read) | lifetime (opt-out record) | never | VERIFIED-LIVE (comment) |
| Shared-trip contacts (`rides/sharing.py` shared-contacts) | C3 | plain (in ride row / Twilio send) | rider self | 24 h link | never | VERIFIED (authz) |
| `guest_bookings.*` (migration 207) | C3 | plain | admin, booking | UNKNOWN purge | never | INFERRED |
| `fare_split_participants` | C3 | plain | split participants | 7 y with ride | never | INFERRED |
| Zoho Desk egress: name, email, phone (`zoho_desk_integration.py:68-71,224-227,271-274,300-323`) | C3 | TLS in transit; at rest at Zoho (DC selectable, `zoho_desk_service.py:45-50`) | support staff via Zoho | Zoho retention (UNKNOWN) | never | VERIFIED — **not in DPA register** (SEC-R10-014) |

## 7. AI / support transcripts / analytics

| Table.column / sink | Class | Enc | Reads | Retention | Log? | Evidence / label |
|---|---|---|---|---|---|---|
| `ai_conversations`, `ai_messages` | C3 | plain, **post-scrub** | self, admin AI console (`require_module`) | 90 d (141) | never text | VERIFIED-LIVE (comments) |
| `ai_tool_audit` | C2 | — | admin console | lifetime? | ids ok | VERIFIED-LIVE (247 rows) |
| `ai_security_events` | C2 | — (tags + ids only, no message text) | admin console, metric | lifetime | ok | VERIFIED (`ai/threat.py:14-15`) |
| LLM providers (Anthropic / OpenAI / Gemini, US) | C3 → scrubbed | TLS | provider | provider policy | — | VERIFIED (scrub) — only Gemini in DPA register (DV-16) |
| `support_tickets`, `zoho_desk_tickets` | C3 | plain | support | lifetime | no free text | VERIFIED-LIVE |
| `lost_and_found`, `lost_and_found_messages` (+ image objects) | C3 | plain | participants (`_require_participant`), admin | UNKNOWN purge | never | VERIFIED (authz) |
| `ride_messages` (in-ride chat) | C3 | plain | participants, admin | with ride (INFERRED) | never | VERIFIED (authz) |
| `notifications.*`, `cloud_messages.*` | C3 (may carry names/addresses in body) | plain | owner (`notifications.py:482,533` user-scoped), admin | UNKNOWN purge | never body | VERIFIED (authz) / INFERRED (content) |
| `email_send_log` | C2 | — ("never stores the email address") | ops | append-only | ok | VERIFIED-LIVE |
| Sentry (US) | C2 after scrub | — | eng | Sentry retention | — | VERIFIED (`sentry_scrub.py`) |
| **LogRocket (US)** — full session replay, iOS production default on, user-id identified, no masking | **C3/C4 (screens: phone entry, addresses, map, earnings)** | TLS | LogRocket console users | LogRocket retention (UNKNOWN) | — | VERIFIED — **not in DPA register**; SEC-R10-003 |
| PostHog session replay | C3 | masked inputs/images (`posthogReplay.ts:163-164`) | — | — | — | dark-launched (428, default off) — VERIFIED |
| `meta_capi_deliveries` (Meta Conversions API, 266) | C3 (hashed identifiers per Meta spec — INFERRED) | hash? | marketing | UNKNOWN | never | INFERRED — not in DPA register |
| `marketing_preferences`, `marketing_consent_events`, `marketing_suppressions`, `email_suppressions` | C3 (CASL) | plain | send path | append-only events | no address | VERIFIED-LIVE (comments) |
| `audit_logs` (`before_json`/`after_json` may carry PII; migration 51 removed `user_email`) | C3 | plain | admin (RLS) + service role | 7 y (migration 56, trigger conflict → C112 `:27742`) | never bodies | VERIFIED-LIVE (comment) |
| `compliance_export_events`, `admin_export_approval_requests`, `data_export_requests/objects`, `data_transfer_export_jobs` | C2/C3 | — (ZIPs in private buckets, 7-day signed links) | admin, dual-approval | 7 y events; 7 d objects | ids ok | VERIFIED-LIVE |
| `agent_action_log` | C2 | — | — | — | ok | VERIFIED-LIVE: **0 rows** (never written) — SEC-R10-012 |

## 8. Gaps vs `docs/data-classification.md` and the CLAUDE.md never-log list

| # | Gap | Evidence | Finding |
|---|---|---|---|
| 1 | 04-24 doc claims `users.phone/email/last_name`, `rides.*_address` are "pgsodium" — **none are**; only `drivers.license_number`, `drivers.sin`, `emergency_contacts.*` are Vault-encrypted; VIN was un-encrypted in 244 | migrations 32/244/289/357; `_VAULT_PII_FIELDS` | SEC-R10-014 |
| 2 | Retention table contradicts implementation: GPS 2 y → **3 y**; rider PII 2 y post-deletion → **30 d profile scrub + 7 y attributable ride record**; driver_location_history missing → **90 d**; audit logs 3 y → **7 y** | migration 296 Steps A/B/C/N; CLAUDE.md Compliance section; B23 | SEC-R10-014 |
| 3 | Cross-border table and DPA register omit LogRocket, Zoho Desk, PostHog, Anthropic, OpenAI, AWS SES, Meta CAPI | `docs/dpa-register.md:28-42` | SEC-R10-003 / -014 |
| 4 | CLAUDE.md: "Driver's full address … stored encrypted in `drivers`" — no such column | migration 221:18 | SEC-R10-014 |
| 5 | `settings` row secrets: encryption state unknown; now includes LLM keys | C43 "NOT verified" note; `ai/providers/__init__.py:62` | SEC-R10-002 |
| 6 | Never-log list is honoured in backend logs/Sentry/AI (verified) but **not** in mobile session replay (LogRocket) | §7 | SEC-R10-003 |
| 7 | Purge horizons unconfirmed for: `guest_bookings`, `notifications`/`cloud_messages`, `lost_and_found*`, `ride_messages`, `stripe_events`, `saved_addresses`, `corporate_*` contacts | no Step found in 296 for these (grep) — INFERRED | R12 / retention owner |
| 8 | `bank_account_last4`, `totp_secret`, `zoho_desk_config` token encryption — not determinable from repo | — | UNKNOWN |
| 9 | `agent_action_log` exists but is never written, so the "agent audit trail" it implies does not exist | live 0 rows | SEC-R10-012 |
