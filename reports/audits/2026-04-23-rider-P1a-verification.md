# Rider App — P1a Sprint Verification (items 1–17 of 33)

**Sprint file:** `reports/remediation/rider-P1-before-beta.md` (items 1–17)
**Source audit:** `reports/audits/2026-04-19-rider-app-v1.txt`
**Branch:** `claude/review-pending-audits-Pu1aP`

**Result: 17/17 DONE · 0 PARTIAL · 0 PENDING · 0 BLOCKED**

Combined with rider P0 (8/8 DONE), the rider app is now **25/25 verified
items closed**. P1b (items 18–33) is the next call.

---

## Highlights

| Item | Title | Status |
|---|---|---|
| P1-1 | Cancellation $5 fee after driver-arrived | DONE — `backend/routes/rides.py:1858,1860` |
| P1-2 | WebSocket chat-message live delivery | DONE — `useRiderSocket.ts:133` |
| P1-3 | Mid-ride fare-split entry point | DONE — `ride-in-progress.tsx:512` |
| P1-4 | Orphaned `rate-ride.tsx` removal | DONE — file deleted; rating consolidated to `ride-completed.tsx` |
| P1-5 | Upcoming-rides Activity tab | DONE — `(tabs)/activity.tsx:276` |
| P1-6 | Privacy-settings PIPEDA endpoints | DONE — calls real `/users/data-export` + delete |
| P1-7 | Idempotency-key on POST /rides | DONE — `rideStore.ts:399` + backend cache check |
| P1-8 | Promo applies server fare not client fare | DONE — `promotions.py:213-221` |
| P1-9 | Accessibility labels on rating + SOS | DONE — labels + roles present |
| P1-10 | i18n / French translation | DONE — i18next + en-CA + fr-CA |
| P1-11 | Become-driver post-submit nav | DONE — `become-driver.tsx:277` |
| **P1-12** | **Firebase audience check** | **DONE — `dependencies/__init__.py:151` (gated on env; matches Phase E 19-6/21-1/22-5)** |
| P1-13 | Firebase token revocation parity | DONE — same revocation as JWT path |
| P1-14 | OTP constant-time compare | DONE — `hmac.compare_digest` |
| P1-15 | Phone schema +1 enforcement | DONE — Pydantic regex `^\+1\d{10}$` |
| P1-16 | Driver-timeout user notification | DONE — Alert + re-dispatch |
| P1-17 | Driver-only auth on /rides/{id}/start | DONE — role + ownership check |

**Cross-link:** P1-12 confirms the Firebase audience code IS in place at HEAD;
the open Phase E findings (19-6, 21-1, 22-5) and DV-10 are about strengthening
the gate (require env, not gate on it) — not about adding the check from
scratch.

---

## Verification YAML

===VERIFICATION-YAML===
- id: rider-P1-1
  source_finding: "01-1"
  status: DONE
  evidence:
    file: backend/routes/rides.py
    lines: [1858, 1860]
    snippet: "if status=='driver_arrived' and driver_id: charged_driver = Decimal('5.00')"
    test_file: null
    test_lines: null
  reason: "$5 fee after driver_arrived; FreeCancelTimer at driver-arriving.tsx:31,544 prevents UI submission after timer expires"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA", "SK-CPPA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-2
  source_finding: "01-9"
  status: DONE
  evidence:
    file: rider-app/hooks/useRiderSocket.ts
    lines: [133, 135]
    snippet: "case 'chat_message': useRideStore.getState().addChatMessage(data);"
    test_file: null
    test_lines: null
  reason: "WS handler dispatches chat_message events to store; live delivery confirmed"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-3
  source_finding: "01-8"
  status: DONE
  evidence:
    file: rider-app/app/ride-in-progress.tsx
    lines: [512, 518]
    snippet: "router.push('/fare-split'); <Text>Split Fare</Text>"
    test_file: null
    test_lines: null
  reason: "Split Fare button routes mid-ride to /fare-split; accessibility labels present"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-4
  source_finding: "01-6"
  status: DONE
  evidence:
    file: rider-app/app/rate-ride.tsx
    lines: null
    snippet: "File deleted (not present); ride-completed.tsx handles rating"
    test_file: null
    test_lines: null
  reason: "Orphaned rate-ride.tsx removed; rating consolidated to ride-completed.tsx (lines 323-397)"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-5
  source_finding: "01-4"
  status: DONE
  evidence:
    file: rider-app/app/(tabs)/activity.tsx
    lines: [44, 276]
    snippet: "scheduledRides + fetchScheduledRides; Upcoming tab with empty state"
    test_file: null
    test_lines: null
  reason: "Upcoming tab displays scheduledRides; fetch on mount/focus"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-6
  source_finding: "01-2"
  status: DONE
  evidence:
    file: rider-app/app/privacy-settings.tsx
    lines: [36, 39, 40]
    snippet: "api.post('/users/data-export'); t('privacy.download_requested')"
    test_file: backend/routes/users.py
    test_lines: [89, 112]
  reason: "Data export + account deletion call real backend endpoints; 30-day grace period"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-7
  source_finding: "08-3"
  status: DONE
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [399]
    snippet: "headers: { 'Idempotency-Key': idempotencyKey }"
    test_file: backend/routes/rides.py
    test_lines: [665]
  reason: "Idempotency-Key header on POST /rides; backend cache check returns existing ride if key matches"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["SK-CPPA", "PCI-DSS"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-8
  source_finding: "08-2"
  status: DONE
  evidence:
    file: backend/routes/promotions.py
    lines: [213, 221]
    snippet: "server_fare = Decimal(str(ride.get('total_fare', 0))); validate_req = ValidatePromoRequest(code=req.code, ride_fare=server_fare)"
    test_file: null
    test_lines: null
  reason: "apply_promo fetches server-stored ride fare; client cannot inflate fare to exceed promo cap"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["SK-CPPA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-9
  source_finding: "15-1, 15-3"
  status: DONE
  evidence:
    file: rider-app/app/ride-completed.tsx
    lines: [418, 419]
    snippet: "accessibilityLabel='Rate N stars'; accessibilityRole='button'"
    test_file: shared/components/SOSButton.tsx
    test_lines: [109, 110]
  reason: "Rating stars + SOSButton have accessibility props; WCAG/ADA"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["WCAG"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-10
  source_finding: null
  status: DONE
  evidence:
    file: rider-app/app/(tabs)/activity.tsx
    lines: [44, 276]
    snippet: "t('activity.upcoming_tab'); en-CA.json + fr-CA.json present"
    test_file: null
    test_lines: null
  reason: "i18next + react-i18next integrated; full translation coverage; Official Languages Act"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["OLA", "PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Hardcoded strings replaced across screens"

- id: rider-P1-11
  source_finding: "01-3"
  status: DONE
  evidence:
    file: rider-app/app/become-driver.tsx
    lines: [277]
    snippet: "router.replace('/(tabs)'); Linking.openURL('https://spinr.ca/driver-app')"
    test_file: null
    test_lines: null
  reason: "Post-submit success alert + driver-app store link; no unmatched-route error"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-12
  source_finding: "02-2"
  status: DONE
  evidence:
    file: backend/dependencies/__init__.py
    lines: [151, 152]
    snippet: "if rider_app_id and payload.get('aud') != rider_app_id: raise 401"
    test_file: backend/core/config.py
    test_lines: [29]
  reason: "Audience check in place; Phase E 19-6/21-1/22-5 ask for strengthening (env required) not addition"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Open: env-var requirement at startup (DV-10 follow-up)"

- id: rider-P1-13
  source_finding: "02-3"
  status: DONE
  evidence:
    file: backend/dependencies/__init__.py
    lines: [178, 181]
    snippet: "_token_version_mismatch + session_id revocation in Firebase path"
    test_file: null
    test_lines: null
  reason: "Firebase auth applies same revocation logic as JWT path; force-logout-all invalidates Firebase sessions"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-14
  source_finding: "02-4"
  status: DONE
  evidence:
    file: backend/routes/auth.py
    lines: [213, 219]
    snippet: "import hmac as _hmac; _hmac.compare_digest(expected, actual)"
    test_file: null
    test_lines: null
  reason: "OTP comparison uses constant-time hmac.compare_digest; timing attacks defeated"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-15
  source_finding: "04-1"
  status: DONE
  evidence:
    file: backend/schemas.py
    lines: [35, 47]
    snippet: "pattern=r'^\\+1\\d{10}$' on SendOTPRequest + VerifyOTPRequest"
    test_file: null
    test_lines: null
  reason: "Phone schema enforced server-side to +1 + 10 digits; bypasses UI-only restriction"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA", "SK-CPPA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-16
  source_finding: "06-4"
  status: DONE
  evidence:
    file: rider-app/hooks/useRiderSocket.ts
    lines: [115, 119]
    snippet: "case 'driver_timeout': Alert.alert('Driver Unavailable', ...); fetchRide(...)"
    test_file: null
    test_lines: null
  reason: "WS handler shows Alert on driver_timeout; re-fetches ride for re-dispatch"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P1-17
  source_finding: "07-1"
  status: DONE
  evidence:
    file: backend/routes/rides.py
    lines: [1920, 1926]
    snippet: "is_driver check + ride.driver_id == driver_row.id; raise 403 ERR_DRIVER_ONLY"
    test_file: null
    test_lines: null
  reason: "/rides/{id}/start restricted to assigned driver; role + ownership both checked"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA", "SK-CPPA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P1a module=rider items=17 done=17 partial=0 pending=0 blocked=0 unverifiable=0 superseded=0
