# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (subtask agent) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (this commit) |
| Related issue or gap ID | PIA finding R-002 (`docs/audit/2026-08-21-emergency-contact-pia-memo.md`) |

## 1. Issue / gap identified

`backend/services/sos_contact_consent.py` (STOP-keyword suppression list for
SOS emergency-contact SMS, migration 358) existed but was never called from
either SOS trigger endpoint — a contact who opted out via STOP still
received every subsequent SOS SMS.

## 2. Root cause

The suppression service and the two SOS SMS-sending call sites
(`trigger_emergency`, `trigger_emergency_rideless` in
`backend/routes/rides/safety.py`) were built in separate pieces of work; the
service was never wired into the send path.

## 3. Fix / remediation

In both `trigger_emergency` and `trigger_emergency_rideless`, after building
`_sms_targets` (contacts with a phone number) and before sending, check each
target concurrently via `sos_contact_consent.is_suppressed(phone)`
(`asyncio.gather`, matching the SMS-send gather already used) and drop any
contact whose phone is currently suppressed. `is_suppressed` is fail-open by
design (returns `False` on any lookup error); the new suppression-check step
around it is *also* wrapped fail-open (falls back to "nobody is suppressed"
on any unexpected error, logged at `logger.error(..., exc_info=True)`) so an
unrelated failure in this new step can never block the SOS SMS itself. A
suppressed contact is excluded from `contacts_notified` and marked
`"status": "suppressed"` in the per-contact status list (that key is only
added when true, so the existing `{id, name, notified}` shape is unchanged
for every non-suppressed contact). An info log records how many contacts
were skipped per request.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to the SMS-sending block inside these two
  functions. No other route, background loop, or table write reads/writes
  `sos_contact_suppressions` except `sos_contact_consent.py` itself (used
  today, per grep, only from these two new call sites — no STOP-webhook
  consumer wired yet, so the suppression list can currently only be
  populated by direct/admin action, not by an inbound STOP reply).
- No change to the idempotency logic, the `safety_incidents` insert, WS
  broadcast, `notify_safety_team`, `page_sos_on_call`, or the push
  confirmation blocks — all untouched.
- Existing consumers of the response's `contacts` array (grepped
  `rider-app`/`driver-app`): `rider-app/store/__tests__/rideStore.sos.test.ts`
  reads individual fields (`contacts[i].notified`), not the whole-object
  shape, so the additive `status` key does not break it. No admin-dashboard
  consumer found.
- Failure mode analyzed explicitly: if `sos_contact_consent` becomes
  unreachable (import break, DB outage reaching the module in an
  unexpected way), the wrapping try/except in each endpoint falls back to
  an empty suppressed set — i.e. the alert still sends to everyone. This
  was proven end-to-end with a test that breaks `is_suppressed` itself and
  asserts all contacts still receive the SMS.

## 5. User-experience effect

Rider/driver-facing: none visible to the app UI. Emergency-contact-facing:
a third party who previously texted STOP to an SOS SMS will now actually
stop receiving future SOS SMS (this closes the gap the PIA flagged, it does
not introduce new behavior beyond what the suppression list already
promised). Not visible mid-session to anyone in the app itself.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/safety.py` | Added dual-import for `sos_contact_consent`; filter `_sms_targets` against `is_suppressed()` (concurrent, fail-open) in both `trigger_emergency` and `trigger_emergency_rideless`; mark suppressed contacts in the per-contact status list | Wire the existing suppression service into the SOS SMS send path (PIA R-002) |
| `backend/tests/test_p2_sos.py` | Added `test_suppressed_contact_excluded_from_sos_sms`, `test_suppression_check_failure_fails_open_sends_to_all` | Pin suppression filtering and its fail-open guarantee for the in-ride path |
| `backend/tests/test_sos_rideless.py` | Same two tests, mirrored for the rideless path | Same guarantee, rideless variant |

## 7. Before / after

```python
# Before (both functions)
_sms_targets = [c for c in contacts if c.get("phone")]
_sms_results = await asyncio.gather(
    *(_deps.send_sms(c["phone"], sms_body, ...) for c in _sms_targets),
    return_exceptions=True,
)
```

```python
# After
_sms_targets = [c for c in contacts if c.get("phone")]

_suppressed_ids: set = set()
if _sms_targets:
    try:
        _suppression_flags = await asyncio.gather(
            *(sos_contact_consent.is_suppressed(c["phone"]) for c in _sms_targets)
        )
        _suppressed_ids = {
            c.get("id") for c, suppressed in zip(_sms_targets, _suppression_flags, strict=False) if suppressed
        }
    except Exception:
        logger.error("... failing open (sending SOS SMS to all contacts)", exc_info=True)
        _suppressed_ids = set()

if _suppressed_ids:
    logger.info(f"SOS: skipped {len(_suppressed_ids)}/{len(_sms_targets)} emergency contacts due to SOS suppression ...")
    _sms_targets = [c for c in _sms_targets if c.get("id") not in _suppressed_ids]

_sms_results = await asyncio.gather(
    *(_deps.send_sms(c["phone"], sms_body, ...) for c in _sms_targets),
    return_exceptions=True,
)
```

## 8. Rollback plan

Pure code change, no migration, no data mutation, no feature flag gating
this (the underlying `sos_contact_suppressions` table, migration 358, is
additive and already live). A `git revert` of this commit is a complete and
sufficient rollback: it removes the suppression check and restores
"send to every contact with a phone" exactly as it behaved before. No
data-level remediation is needed because this change never writes to
`sos_contact_suppressions` — it only reads it.

## 9. Verification performed

- [x] Automated tests run (unit, mocked Supabase): `backend/tests/test_p2_sos.py`,
      `backend/tests/test_sos_rideless.py`, `backend/tests/test_sos_expired_token.py`,
      `backend/tests/test_sos_paging.py` — 55 passed. Broader SOS-adjacent
      suite also run for regression: `backend/tests/test_e2e_sos_flow.py`,
      `backend/tests/test_coverage_rides.py`,
      `backend/tests/test_rate_limit_metric_cardinality.py`,
      `backend/tests/test_rate_limit_user_keying.py`,
      `backend/tests/test_sos_contact_consent.py` — 220 passed. `ruff check`
      clean on all changed files.
- [ ] Manual repro in staging — not performed (no staging access from this
      session).
- [x] Blast-radius grep performed: grepped `rider-app`/`driver-app` for
      `contacts_notified`/response-shape consumers (see section 4); grepped
      the repo for other importers of `sos_contact_consent` (none besides
      this change and its own test file).
- [x] Reviewed against relevant CLAUDE.md convention: "do not silently
      swallow errors" — the new try/except logs loudly (`logger.error`,
      `exc_info=True`) on every fallback path rather than swallowing
      silently; the fail-open choice itself is an explicit, documented
      safety decision (mirrors `is_suppressed`'s own docstring), not a
      soft-handled error.
- [ ] Feature-flagged — not applicable/not done. This is a strict narrowing
      of an already-live send path (fewer SMS sent, never more), with no
      new endpoint, no new user-visible surface, and a same-request
      same-commit rollback (git revert) — judged not to need a flag. Not
      independently confirmed with the user; flagging this call out per the
      "escalate, don't silently ship" gate.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data path)
- [x] Blast radius is stated, not assumed (isolated to the two SOS SMS
      send blocks; only other reader/writer of `sos_contact_suppressions`
      is `sos_contact_consent.py` itself)
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in (section 5 filled in; the only end-user-visible
      effect is that suppression now actually works, which is the intended
      fix, not an incidental side effect)
