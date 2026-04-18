# Dimension 09 — Test Coverage

**Question:** Does the test suite catch real bugs? Are the critical paths tested?

---

## Checklist

### Backend Tests (pytest)
- [ ] Happy path tested for every major endpoint
- [ ] Error paths tested: 401, 403, 404, 422, 500
- [ ] Auth boundary: protected endpoint rejects unauthenticated request
- [ ] State machine: invalid state transitions return correct error
- [ ] Rate limiting: nth+1 request returns 429
- [ ] OTP lockout: 5 failed attempts → 24h block (integration test, not mock)
- [ ] Token version: incremented version rejects old token
- [ ] Race condition: double-accept test fires two concurrent requests

### PII / Compliance Tests
- [ ] All FORBIDDEN_FIELDS parametrically tested — not just 2 of 14
- [ ] Rider-facing response verified for every sensitive field
- [ ] PCI guard tested for all field name variants (snake_case + camelCase)

### Test Fixtures
- [ ] Supabase, Redis, and Stripe are mocked — tests don't hit production
- [ ] Fixture isolation: patches cleaned up between tests (no global state bleed)
- [ ] Async test configuration explicit (`@pytest.mark.asyncio` or `asyncio_mode = "auto"`)
- [ ] No hardcoded credentials in conftest.py — use environment variables or fixtures

### Coverage Thresholds
- [ ] Jest coverage threshold set: `lines ≥ 70, functions ≥ 60`
- [ ] pytest coverage threshold set: `--cov-fail-under=70`
- [ ] Coverage reports generated in CI and stored as artifacts
- [ ] Coverage trend visible (not just pass/fail)

### Mobile Tests (Jest + React Native Testing Library)
- [ ] Store unit tests cover: all state transitions, error paths, optimistic updates
- [ ] Component tests exist for critical UI: RideOfferPanel, ActiveRidePanel, TripCompletedPanel
- [ ] No snapshot tests without a clear rationale (snapshots rot quickly)

### E2E Tests (Maestro)
- [ ] Login and OTP flow
- [ ] Go online / go offline
- [ ] Accept ride
- [ ] Verify pickup OTP
- [ ] Complete trip
- [ ] View earnings / request payout
- [ ] Each flow has `assertVisible` assertions (not just taps with no verification)

### CI Integration
- [ ] Tests run on every PR
- [ ] Failed tests block merge
- [ ] Flaky tests marked and tracked — not silently retried
- [ ] Test results visible in PR checks

---

## Severity Guide

| Finding | Severity |
|---|---|
| No tests at all | CRITICAL |
| Core auth path (login, token refresh) not tested | HIGH |
| PII test only checks 2 of 14 fields | HIGH |
| No coverage threshold — coverage can drop to 0 undetected | MEDIUM |
| OTP lockout only tested with mocks — never integration tested | MEDIUM |
| Fixtures bleed state between tests | MEDIUM |
| Zero component tests | MEDIUM |
| E2E covers only login — no ride flow | HIGH |
| Hardcoded credentials in test fixtures | LOW |
