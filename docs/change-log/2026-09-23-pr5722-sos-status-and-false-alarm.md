# PR 5722 SOS status and false-alarm corrections

- SOS false-alarm confirmation now moves an incident to the schema-supported `resolved` state, records `resolved_at`, `resolved_by`, and a resolution note, and returns idempotent success for an incident already resolved as a false alarm.
- The shared SOS button keeps the alert active until false-alarm confirmation succeeds. Rejections retain the SOS state and show a localized retry prompt; a single-flight guard prevents overlapping false-alarm requests. Rider ride-based SOS now uses the shared API fallback, while the driver's existing callback remains supported.
- Validation: `backend/tests/test_p2_sos.py` passes (31 tests); `rider-app/__tests__/SOSButton.test.tsx` passes (11 tests). The first frontend regression run reproduced the premature state clear. Jest emitted existing async `act()` warnings; the suite passed.
