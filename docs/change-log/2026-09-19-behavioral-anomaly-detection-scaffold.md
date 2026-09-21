# Change Impact & Risk Log — Behavioral Anomaly Detection Scaffold (Phase 4b)

**Date:** 2026-09-19
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** scripts/ (new, standalone — no application code touched)
**Domain:** security / fraud detection
**Related:** `docs/audit/2026-09-19-security-automation-roadmap.md` Phase 4b

## Issue/gap identified
No tooling flags auth-attempt bursts (credential stuffing / OTP brute
force) or payment-retry patterns (card testing) as candidate signals for
human review — this is the "behavioral anomaly flags" half of the threat-
hunting phase, extending the same conceptual check `spinr-fraud-auditor`
already applies to GPS-ping plausibility into two new domains.

## Root cause
Never built, and genuinely blocked on a live data source (same Sentry MCP
authorization gap as Phase 3) — no live auth/payment event stream is
connected to this session.

## Fix/remediation
Scaffolded the detection *logic* now, against mocked JSON fixtures, ready
to wire in real data later:
- `scripts/threat-hunting/anomaly_detection.py` — four detectors:
  `detect_auth_bursts` (one device_id/ip_hash hitting many distinct
  phone_last4/user_id targets — credential stuffing shape, explicitly not
  flagging a single user retrying their own OTP), `detect_payment_retry_storms`
  (one card, many declines), `detect_card_testing_rings` (one source, many
  distinct cards — the mirror-image pattern), and an
  `_assert_safe_event()` guard that raises loudly if a caller ever passes
  a raw IP or full phone number into any identifier field.
- `scripts/threat-hunting/test_anomaly_detection.py` — 24 tests.

PIPEDA safety is by input schema (every field is already an id/hash/last-4
value per CLAUDE.md's conventions), not by after-the-fact redaction —
deliberately different from Phase 3's approach, and reviewed as the
stronger design for this domain.

## Risk & impact on existing functionality
- **Blast radius: isolated.** New standalone scripts, zero application-code
  changes, not wired into any CI workflow or live data source.
- **`spinr-fraud-auditor` review (round 1) found 4 real design gaps** before
  this was safe to point at live data later:
  1. Auth-burst grouping fell back `device_id or ip_hash`, letting an
     attacker evade detection by randomizing a spoofable device_id per
     request while a stable, server-derived ip_hash went unchecked — fixed
     by tracking both as independent dimensions.
  2. `_assert_safe_event` only checked 2 of 5 identifier fields — fixed to
     check all of them (device_id, user_id, card_fingerprint too).
  3. The phone-number guard only matched bare digit strings, missing
     formatted numbers like "306-555-1234" — fixed to strip punctuation
     first. IP guard gained a loose IPv6 heuristic alongside IPv4.
  4. The payment detector only caught "one card, many declines," missing
     the mirror-image "one attacker, many distinct stolen cards" pattern —
     fixed by adding `detect_card_testing_rings` as a companion detector,
     with `device_id`/`ip_hash` added to the payment event schema to
     support it.
  All 4 confirmed closed on re-review (**SAFE TO COMMIT AS SCAFFOLDING**),
  each with a regression test that fails if the fix is reverted.

## User experience effect
None — no rider/driver/corporate-admin/internal-admin-facing change.

## Files modified
| File | What changed | Why |
|---|---|---|
| `scripts/threat-hunting/anomaly_detection.py` | New file | 4 detectors + PII-safety guard |
| `scripts/threat-hunting/test_anomaly_detection.py` | New file, 24 tests | Coverage incl. evasion-case regression tests |

## Before/after snippet
Before: no anomaly detection existed.
After (the evasion fix that was the most substantive finding):
```python
# Before: key = e.get("device_id") or e.get("ip_hash")  -- one falls back to the other
# After: both tracked independently, so a spoofed device_id can't hide a stable ip_hash
by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
by_ip: dict[str, list[dict[str, Any]]] = defaultdict(list)
for e in failures:
    device_id, ip_hash = e.get("device_id"), e.get("ip_hash")
    if device_id:
        by_device[device_id].append(e)
    if ip_hash:
        by_ip[ip_hash].append(e)
```

## Rollback plan
`git revert` is a complete rollback — standalone scripts with no persisted
state, not wired into CI, not imported by any application code.

## Verification performed
- `pytest scripts/threat-hunting/test_anomaly_detection.py -v` — 24/24 pass.
- Manual CLI smoke test end-to-end against synthetic auth/payment fixture
  files — confirmed correct detection and Markdown rendering for all four
  anomaly categories, both before and after the design fixes.
- `spinr-fraud-auditor` agent run (round 1): found 4 real design gaps
  (detailed above), verdict "needs design revision before wiring live
  data" (not a merge blocker for the scaffold itself since nothing was
  live). Fixes applied.
- `spinr-fraud-auditor` agent run (round 2), verifying the fixes against
  live file content and a fresh independent test run: **SAFE TO COMMIT AS
  SCAFFOLDING**, all 4 gaps confirmed closed, each backed by a regression
  test.

## What was NOT verified
- Never run against real auth/payment event data — blocked on a live data
  source (Sentry MCP authorization, or a direct Supabase query neither
  available in this session).
- The detection thresholds (5 distinct targets/cards in a 5-10 minute
  window) are reasonable starting defaults, not tuned against real traffic
  volume or false-positive rate — expect to adjust once real data flows
  through this.
- Noted but deliberately not fixed now (non-blocking per reviewer): the
  per-key window scan is O(n²) per key, fine at today's scaffold/mock
  volume, worth revisiting before pointing this at a high-volume live
  stream.
