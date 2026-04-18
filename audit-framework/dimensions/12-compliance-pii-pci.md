# Dimension 12 — Compliance: PII, PCI-DSS & Data Retention

**Question:** Does the app handle personal data lawfully? Does it meet Canadian privacy law (PIPEDA)?

---

## Checklist

### PII Field Stripping (Rider-Facing Responses)
- [ ] Driver licence number NOT in any rider-facing response
- [ ] Vehicle VIN NOT in any rider-facing response
- [ ] Insurance expiry NOT in any rider-facing response
- [ ] `stripe_account_id` NOT in any rider-facing response
- [ ] `bank_account` NOT in any rider-facing response
- [ ] `fcm_token` NOT in any rider-facing response
- [ ] Driver phone number NOT in any rider-facing response
- [ ] Driver email NOT in any rider-facing response
- [ ] Stripping enforced at route level — not just in tests

### Document Storage
- [ ] Driver documents (licence, insurance) behind signed URLs — not public permanent URLs
- [ ] Signed URL expiry ≤ 1 hour
- [ ] File magic byte validation (not just extension)
- [ ] File size checked before reading into memory
- [ ] Original filename sanitised — not returned in API response
- [ ] Storage bucket not publicly accessible

### Database Access Controls
- [ ] Row-Level Security (RLS) enabled on sensitive tables
- [ ] Drivers can only read their own rows
- [ ] Riders cannot read driver-only fields
- [ ] Service-role key used only in trusted backend — never in mobile app

### Data Retention
- [ ] Soft-delete columns (`deleted_at`) on drivers, users, rides — hard deletes prevented
- [ ] Retention policy defined: how long is data kept after account closure?
- [ ] Automated purge job for data past retention window
- [ ] Audit log of who accessed what (especially admin access to PII)

### Document Expiry & Compliance
- [ ] Expiry check runs regularly (Spinr standard: every 12 hours)
- [ ] Notifications sent at: 7-day warning, 1-day warning, day-of, post-expiry
- [ ] Post-expiry: driver suspended and taken offline
- [ ] Expiry loop processes both future and already-expired documents
- [ ] Admin alerted on repeated FCM notification failure

### PIPEDA / Canadian Privacy Law
- [ ] Privacy policy accessible from within the app
- [ ] Consent obtained before collecting location data
- [ ] Data export available to drivers on request
- [ ] Data deletion available to drivers on request
- [ ] Breach notification process defined

---

## Severity Guide

| Finding | Severity |
|---|---|
| PII fields (licence, VIN) in rider-facing response | HIGH |
| Driver documents accessible via public permanent URL | HIGH |
| No RLS on sensitive tables | CRITICAL |
| Expired driver documents don't trigger suspension | CRITICAL |
| Expiry loop skips already-expired documents | HIGH |
| No soft-delete — hard deletes destroy audit trail | MEDIUM |
| No data export for drivers (PIPEDA requirement) | MEDIUM |
| Privacy policy not accessible from within app | MEDIUM |
| No retention policy defined | MEDIUM |
| File size checked after read (DoS risk) | HIGH |
