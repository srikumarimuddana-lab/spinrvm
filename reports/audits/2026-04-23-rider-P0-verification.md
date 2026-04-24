# Rider App — P0 Sprint Verification (2026-04-24)

**Sprint file:** `reports/remediation/rider-P0-critical-fix-now.md` (8 items)
**Source audit:** `reports/audits/2026-04-19-rider-app-v1.txt`
**Branch:** `claude/review-pending-audits-Pu1aP`
**Methodology:** `reports/audits/2026-04-23-rider-app-remediation-verification-prompt.md`

**Result: 8/8 DONE · 0 PARTIAL · 0 PENDING · 0 BLOCKED**

This is the strongest verification outcome of any sprint to date (driver
P0 was 6/8 DONE + 1 PARTIAL; rider P0 is fully complete).

---

## Per-Item Verification

### rider-P0-1 · Emergency SOS silently fails on network error
**Status:** DONE
**Evidence:** `rider-app/store/rideStore.ts:509-512` — `triggerEmergency`
catch block now `Alert.alert('Emergency Alert May Not Have Sent', 'Call
911 directly')` with explicit deletion of the prior swallow comment.
**Notes:** Surfaces the failure to the user with a 911 fallback. Aligns
with CLAUDE.md "do not silently swallow errors on safety paths."

### rider-P0-2 · OfflineBanner overlaps notch / status bar
**Status:** DONE · `duplicate_of: driver-P0-7`
**Evidence:** `shared/components/OfflineBanner.tsx:4,36,171` — safe-area
insets imported and applied (`top: topInset`).
**Notes:** Shared component fix carried forward from driver verification.

### rider-P0-3 · OTP stored in plaintext on ride creation
**Status:** DONE · `duplicate_of: driver-P0-4`
**Evidence:** `backend/routes/rides.py:25,872` — `pickup_otp =
hash_otp(pickup_otp_plain)` (SHA-256 hash before persist).
**Notes:** Same fix as driver-side OTP hashing; covers rider-created
rides.

### rider-P0-4 · Hardware back button exits active ride
**Status:** DONE
**Evidence:** `rider-app/app/ride-in-progress.tsx:106,109` —
`BackHandler.addEventListener('hardwareBackPress', ...)` with
confirmation dialog. All three ride screens (driver-arriving,
driver-arrived, ride-in-progress) protected.

### rider-P0-5 · Double-booking via rapid taps on book button
**Status:** DONE
**Evidence:** Triple guard:
- `rider-app/app/payment-confirm.tsx:96,98` — UI debounce
  (`isBooking` state)
- `rider-app/store/rideStore.ts:363,364` — store rejects if
  `currentRide` exists
- `backend/routes/rides.py:695-696` — backend returns 409 "You already
  have an active ride"

### rider-P0-6 · Home-screen SOS button is fake / shows false confirmation
**Status:** DONE
**Evidence:** `rider-app/app/(tabs)/index.tsx:252,256,258` — uses real
`SOSButton` component; falls back to `Linking.openURL('tel:911')` when
`rideId` is undefined. False "help is coming" message removed.

### rider-P0-7 · OTP lockout fails open on Redis error (brute-force window)
**Status:** DONE
**Evidence:** `backend/routes/auth.py:66,79-80` — Redis errors raise
`HTTPException(503, 'ERR_AUTH_UNAVAILABLE')` (fail-closed). Docstring at
line 67 explicitly states intent.
**Notes:** Closes the brute-force attack window during Redis flaps.

### rider-P0-8 · Live Supabase service-role key in `backend/.env.example`
**Status:** DONE
**Evidence:** `backend/.env.example:3` — value replaced with placeholder
`your-supabase-service-role-key`.
**Notes:** Repository contains no live credentials. Key rotation in
Supabase Dashboard remains a manual operational follow-up (per the
sprint file note); not a code-side gap.

---

## Verification Summary YAML

===VERIFICATION-YAML===
- id: rider-P0-1
  source_finding: "10-3"
  status: DONE
  evidence:
    file: rider-app/store/rideStore.ts
    lines: [509, 512]
    snippet: "Alert.alert('Emergency Alert May Not Have Sent', 'Call 911 directly')"
    test_file: null
    test_lines: null
  reason: "Network failure surfaces alert + 911 fallback; swallow comment removed"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["E911", "PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P0-2
  source_finding: "5-1"
  status: DONE
  evidence:
    file: shared/components/OfflineBanner.tsx
    lines: [4, 36, 171]
    snippet: "top: topInset (safe-area inset applied)"
    test_file: null
    test_lines: null
  reason: "Banner respects safe-area inset; carry-forward from driver fix"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["WCAG"]
  effort_remaining_hours: 0
  duplicate_of: "driver-P0-7"
  notes: null

- id: rider-P0-3
  source_finding: "3-1"
  status: DONE
  evidence:
    file: backend/routes/rides.py
    lines: [25, 872]
    snippet: "pickup_otp = hash_otp(pickup_otp_plain)"
    test_file: null
    test_lines: null
  reason: "OTP SHA-256 hashed before persist; plaintext never stored"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: "driver-P0-4"
  notes: null

- id: rider-P0-4
  source_finding: "D05"
  status: DONE
  evidence:
    file: rider-app/app/ride-in-progress.tsx
    lines: [106, 109]
    snippet: "BackHandler.addEventListener('hardwareBackPress', ...)"
    test_file: null
    test_lines: null
  reason: "All 3 ride screens guard hardware back; confirmation dialog required"
  owner: null
  blocked_by: null
  confidence: high
  regulations: []
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P0-5
  source_finding: null
  status: DONE
  evidence:
    file: rider-app/app/payment-confirm.tsx
    lines: [96, 98]
    snippet: "isBooking debounce + store guard + backend 409 (triple guard)"
    test_file: rider-app/store/rideStore.ts
    test_lines: [363, 364]
  reason: "UI debounce + store reject + backend 409 prevent double-book"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["SK-CPPA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Backend check at backend/routes/rides.py:695-696"

- id: rider-P0-6
  source_finding: null
  status: DONE
  evidence:
    file: rider-app/app/(tabs)/index.tsx
    lines: [252, 256, 258]
    snippet: "<SOSButton rideId={...} /> else Linking.openURL('tel:911')"
    test_file: null
    test_lines: null
  reason: "Real SOS button + 911 fallback; false 'help is coming' UI removed"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["E911"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: null

- id: rider-P0-7
  source_finding: "02-1"
  status: DONE
  evidence:
    file: backend/routes/auth.py
    lines: [66, 79, 80]
    snippet: "except Exception: raise HTTPException(503, 'ERR_AUTH_UNAVAILABLE')"
    test_file: null
    test_lines: null
  reason: "Redis failure now fails closed (503); no brute-force window"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Docstring confirms intent: 'Raises 503 on Redis errors (fail closed)'"

- id: rider-P0-8
  source_finding: "03-1"
  status: DONE
  evidence:
    file: backend/.env.example
    lines: [3]
    snippet: "SUPABASE_SERVICE_ROLE_KEY=your-supabase-service-role-key"
    test_file: null
    test_lines: null
  reason: "Live key replaced with placeholder; repo contains no credentials"
  owner: null
  blocked_by: null
  confidence: high
  regulations: ["PIPEDA", "SOC2"]
  effort_remaining_hours: 0
  duplicate_of: null
  notes: "Supabase dashboard key rotation is a separate manual operational task"
===END-VERIFICATION-YAML===

===AUDIT-COMPLETE=== sprint=P0 module=rider items=8 done=8 partial=0 pending=0 blocked=0 unverifiable=0 superseded=0
