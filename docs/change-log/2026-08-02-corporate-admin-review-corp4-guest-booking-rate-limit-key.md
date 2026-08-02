# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Corporate #4 |

## 1. Issue / gap identified

`company_booking_limit` (`POST /company/{id}/bookings`, the corporate
guest-booking endpoint) was rate-limited via `default_limiter`, whose
`key_func` is `get_real_client_ip` — i.e. the cap was keyed purely by
source IP address, not by company or member. Each guest booking sends
2-3 customer SMS, so this limit exists as an SMS-cost/abuse bound as much
as a booking-volume bound. An attacker who already holds a valid
company-member session (the endpoint requires `require_company_member`)
could bypass the 30/hour cap entirely by rotating source IPs — cheap via
any consumer VPN/proxy pool — and SMS-bomb arbitrary phone numbers at an
effectively unbounded rate.

## 2. Root cause

`company_booking_limit = default_limiter.limit("30/hour")` never passed
a `key_func` override, so it fell through to `default_limiter`'s
module-wide default (`get_real_client_ip`) — appropriate for anonymous,
unauthenticated endpoints where IP is the only available signal, but
wrong for an authenticated, company-scoped endpoint where a much more
meaningful and harder-to-rotate identifier (`company_id`, from the URL
path) is already available.

## 3. Fix / remediation

- Added `get_company_booking_key(request)` to `utils/rate_limiter.py`,
  reading `company_id` from `request.path_params` (available without any
  extra DB round-trip, since the route pattern is
  `/company/{company_id}/bookings`) and falling back to the existing
  IP-based key only if `company_id` is somehow absent (defensive; every
  route this limiter is applied to has it).
- Changed `company_booking_limit`'s construction to
  `default_limiter.limit("30/hour", key_func=get_company_booking_key)`.
- Scoped to `company_id` rather than `member_id`: `AsyncLimiter`'s
  `key_func` only receives the `Request` object, not the resolved
  `ctx`/`member` dependency (which FastAPI resolves separately, after
  the rate-limit decorator's check already ran) — company_id is
  available directly from the URL with no additional dependency
  resolution or DB call, while member-level keying would require either
  a second membership lookup inside the key function (extra I/O on
  every request, including ones that get rejected downstream) or
  threading state through `require_company_member`. company-level
  scoping already closes the IP-rotation bypass this finding named;
  member-level granularity is a reasonable follow-up, not implemented
  here.

## 4. Risk & impact on existing functionality

- **Blast radius: one new key function, one limiter's construction
  line.** No change to the 30/hour rate value, to any other limiter
  built from `default_limiter`, or to the booking route itself.
- A single company's members now share one 30/hour budget instead of
  each source IP getting its own. This is a deliberate trade-off named
  in the finding: several members behind the same office/NAT IP were
  already sharing a budget before this fix (IP-keyed), so this doesn't
  introduce a new false-positive shape — it just also correctly caps
  the same company's traffic when members are on different IPs (home,
  mobile data, VPN), which the old IP-only key would have treated as
  30 *separate* budgets.
- Tests: existing test suites globally disable `default_limiter`
  (`conftest.py`'s `reset_rate_limiters` autouse fixture sets
  `enabled = False`) so no existing test exercised real limiting
  behavior for this endpoint before or after this change — confirmed
  via `test_corporate_company_bookings_routes.py` (18) and
  `test_corporate_company_bookings_coverage.py` (23), both passing
  unmodified.
- Added a dedicated new test file
  (`test_corporate_booking_rate_limit_key.py`, 5 tests) that exercises
  the real `AsyncLimiter`/in-memory-storage machinery directly (bypassing
  the disabled shared `default_limiter`) to prove the actual fix
  behavior: two requests for the same company from different source IPs
  share one bucket and the second is blocked; a different company gets
  an independent, unblocked bucket.

## 5. User-experience effect

**None for legitimate use; closes an abuse vector for corporate
guest-booking desks.** A busy showroom desk (the documented normal case
for this limiter) making bookings from one or a few IPs behind the same
office network sees no change — 30/hour per company is unaffected. An
attacker attempting to bypass the cap via IP rotation is now blocked
after the company's 30/hour budget, not after each individual IP's.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rate_limiter.py` | New `get_company_booking_key` function; `company_booking_limit` now built with `key_func=get_company_booking_key` | Close the IP-rotation bypass by keying on the authenticated caller's company instead of raw source IP |
| `backend/tests/test_corporate_booking_rate_limit_key.py` (new) | 5 tests: key-function unit tests + one end-to-end test proving cross-IP dedup and cross-company independence using the real limiter machinery | Cover the fix with a test that would fail against the old (IP-only) key |

## 7. Before / after

```python
# Before — keyed by client IP via default_limiter's module-wide key_func
company_booking_limit = default_limiter.limit("30/hour")
```

```python
# After
def get_company_booking_key(request: Request) -> str:
    company_id = request.path_params.get("company_id")
    if company_id:
        return f"company_booking:{company_id}"
    return f"ip:{get_real_client_ip(request)}"

company_booking_limit = default_limiter.limit("30/hour", key_func=get_company_booking_key)
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores the prior (IP-keyed) behavior. No feature flag — this is a
narrow, mechanical fix to an already-shipped rate limiter's key
function, using data (`company_id`) already present on every request
this limiter guards.

## 9. Verification performed

- [x] Automated tests: `test_corporate_booking_rate_limit_key.py` (5
      new), `test_corporate_company_bookings_routes.py` (18),
      `test_corporate_company_bookings_coverage.py` (23),
      `test_rate_limit_response_shape.py` (4), `test_promo_rate_limit.py`
      (8), `test_rate_driver.py` (10), `test_rate_tip_abuse.py` (12) — 80
      passed, run via the session's `/tmp/spinr_venv` venv from repo
      root.
- [x] `ruff check` on both touched files — clean.
- [x] Blast-radius grep performed (see §4): confirmed no test exercised
      real limiting behavior for this endpoint before this change
      (globally disabled), and no other limiter references
      `get_company_booking_key`.
- [ ] Manual repro in staging — not performed, no staging access; the
      new end-to-end test exercises the real `AsyncLimiter` +
      `MemoryStorage` machinery directly as the closest available
      substitute.
- [x] Dry-run scenario: an attacker with a compromised company-member
      session sends 31 guest bookings within an hour, rotating source IP
      on every request. Before this fix: every request gets a fresh
      IP-keyed bucket, so all 31 succeed (and beyond — no effective cap).
      After this fix: all 31 requests key to the same `company_booking:
      {company_id}` bucket regardless of source IP; the 31st is rejected
      with 429.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — every test file touching
      this limiter/route grepped and run
- [x] User-experience effect stated: legitimate multi-IP company traffic
      now shares one correctly-company-scoped budget instead of one
      budget per source IP — documented as the intended behavior, not an
      accidental tightening

## What was NOT verified

Not tested against a live/staging Redis-backed rate-limit storage (the
production `RATE_LIMIT_REDIS_URL` path) — only the in-memory
`MemoryStorage` backend used by the new test and by local dev. Did not
implement member-level (as opposed to company-level) rate-limit
granularity — see §3 for why company-level was chosen as the minimal fix
that closes the specific IP-rotation bypass this finding named; a
malicious *member* account (as opposed to a malicious outside IP) could
still exhaust their own company's 30/hour budget, which is a narrower,
harder-to-execute abuse path (requires actual company membership, not
just IP rotation) not addressed by this fix. Did not add a dedicated
Prometheus metric for company-booking rate-limit violations specifically
(SOC gap #46, tracked separately in this same review, covers rate-limit
violation metrics generally).
