# Data classification matrix — PII / financial columns (R10, 2026-09-24)

**Status:** IN PROGRESS (skeleton). Derived from `docs/data-classification.md`, `backend/migrations/`, `backend/utils/vault_pii.py`, `utils/retention_purge.py` and the CLAUDE.md never-log list. Columns: table.column → class → encryption at rest → who reads (code path / role) → retention → log-allowed? → evidence/label.

## 0. Classes and legend
_pending_

## 1. Identity (users, admin_staff, corporate_*)
_pending_

## 2. Driver regulatory & government-ID (drivers, driver_documents, document_files, imports)
_pending_

## 3. Location & trip (rides, ride_routes, driver_location_history, safety_incidents)
_pending_

## 4. Financial (wallets, financial_events, refresh to Stripe refs, payouts, T4A)
_pending_

## 5. Auth secrets & credentials (otp_records, refresh_tokens, settings/app_settings, admin_staff.totp)
_pending_

## 6. Third parties (emergency_contacts, guest_bookings, shared-ride contacts)
_pending_

## 7. AI / support transcripts
_pending_

## 8. Gaps vs `docs/data-classification.md` and the CLAUDE.md never-log list
_pending_
