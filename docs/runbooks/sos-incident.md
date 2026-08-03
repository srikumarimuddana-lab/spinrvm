# Runbook — SOS / Safety Incident Response

**Owner:** `security` + `compliance` + `product` · **Severity:** CRITICAL (rider / driver safety)
**Regs:** E911 (CRTC), SK-HRC, PIPEDA, SK-CPPA · **Target response time:** Immediate

---

## Purpose

When a rider or driver triggers SOS (emergency), Spinr's systems must:
1. Log the event with location, parties, and timestamp.
2. Surface an alert to a trained on-call responder.
3. Hand off to law enforcement or emergency services appropriately.
4. Preserve evidence for the regulator and for any investigation.

---

## SOS Event Flow

```
User taps SOS button (rider or driver)
    ↓
Mobile app:
  - Calls POST /rides/{ride_id}/emergency with {lat, lng, timestamp}
  - On success: shows "Emergency services contacted" + reveals Call-911 button
  - On failure (DV-P0-1 fix): shows "SOS not sent — please call 911 directly" +
    reveals fallback Linking.openURL("tel:911")
    ↓
Backend POST /rides/{id}/emergency:
  - Creates row in sos_events table (rider_id, driver_id, ride_id, lat, lng, ts)
  - Emits WebSocket event to admin dashboard (role=security, on-call)
  - Emits SMS via Twilio to on-call responder phone (primary + backup)
  - Records audit-log entry (append-only)
  - Returns 200 OK (do NOT surface internal errors to mobile caller)
```

---

## On-Call Responder Flow (first 5 min)

1. [ ] Acknowledge SOS alert in on-call tool (PagerDuty / OpsGenie / equivalent)
2. [ ] Open admin dashboard → Security tab → active SOS event
3. [ ] View: rider name, phone, driver name, phone, vehicle plate, current lat/lng,
   route polyline
4. [ ] Attempt outbound call to rider first, then driver (if no rider response)
5. [ ] If no contact in 2 min OR the user confirms danger → call 911 on their behalf
   with location
6. [ ] Log every action in the incident doc: `reports/incidents/YYYY-MM-DD-sos-<slug>.md`

---

## Law Enforcement Handoff

When Spinr contacts 911 (or law enforcement reaches out):

- Share: rider name + phone, driver name + phone + vehicle plate + licence, ride
  route polyline, SOS timestamp, current GPS
- Preserve evidence: snapshot the `rides`, `sos_events`, `audit_log`, GPS trace
  for the affected ride into `reports/legal-hold/YYYY-MM-DD-ride-<id>/`
- Do NOT share data beyond what is legally required; if under court order,
  route through `legal` and follow chain of custody

---

## Rider SOS vs Driver SOS

| | Rider-initiated | Driver-initiated |
|---|---|---|
| Likely scenario | Attack by driver, vehicle unsafe, medical | Attack by rider, medical, accident |
| Contact priority | Rider first | Driver first |
| Passengers possibly present | No (unless fare-split) | Yes (rider + any passengers) |
| Additional data to share | Driver identity | Rider identity |

---

## Dispatch Response Adjustments

While a rider's SOS is active:
- Do not offer them additional rides (surface "Active safety incident — contact support")
- Do not offer the involved driver additional rides until compliance clears
- Flag both accounts for review in admin

While a driver's SOS is active:
- Remove driver from dispatch pool
- Do not surface the driver to other riders' searches
- Flag rider account for review

---

## Post-Incident (within 72 h)

- [ ] Incident post-mortem filed (use `docs/templates/postmortem.md`): `reports/incidents/YYYY-MM-DD-sos.md`
- [ ] Regulatory assessment: does SK-CPPA or E911 filing apply?
  - Serious harm or investigation opened → likely YES
  - File OPC breach notification only if PII was disclosed beyond legal
    necessity or improperly
- [ ] Data preservation confirmed (7-year hold for safety incidents)
- [ ] If technical failure contributed: file OPEN-ITEMS-TRACKER row with CRITICAL
  severity
- [ ] Trauma support offered to affected rider/driver per `docs/trauma-support.md`
  (to be created by `product` + `hr`)

---

## Abuse / False SOS Handling

SOS must never be rate-limited or blocked (E911 spirit). However:
- Log repeated false-SOS from the same account as a fraud-signal (D19)
- After 3 false-SOS in 7 days, flag for compliance review (not auto-suspend)
- Educational in-app messaging after 1 false SOS; no punishment

---

## Known Gaps

- `sos_events` table schema — confirm it exists with correct columns
- Twilio fallback chain if primary responder phone is down — document backup rotation
- Trauma-support vendor onboarding — open item
- Quarterly tabletop: "SOS triggered during ride, responder unreachable" scenario

---

## SLAs

- Responder acknowledgement: ≤ 2 min from alert
- 911 contact (if unreachable by rider/driver): ≤ 5 min
- Incident doc started: ≤ 30 min
- Post-mortem: ≤ 72 h
- Regulator notification (if applicable): per PIPEDA 72 h rule
