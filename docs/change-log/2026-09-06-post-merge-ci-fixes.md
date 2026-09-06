# Change Impact & Risk Log — Post-merge CI fixes for #5048

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-06 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, rides |
| PR / commit link | follow-up to [#5048](https://github.com/srikumarimuddana-lab/spinrvm/pull/5048) (merged 02:58:46Z) |
| Related issue or gap ID | CI run 34007800478 on `f8dedf0` |

## 1. Issue / gap identified

#5048 merged 47 seconds after creation, before any test job finished. When the
suite completed it reported **8 failed, 13989 passed, 41 errors**. Three of the
eight are caused by #5048; five are pre-existing and unrelated.

The job nonetheless reported `conclusion: success` — see §4, "the gate itself".

**Mine (fixed here):**

1. `test_pickup_otp_bruteforce_lockout.py::test_rejects_non_four_to_six_digit_input[١٢٣٤]`
   — `DID NOT RAISE ValidationError`. **A real defect in shipped code, not a bad
   test.** `RideOTPRequest.otp` used `pattern=r"^\d+$"`, and `\d` is
   Unicode-aware in both Python's `re` and pydantic v2's Rust regex, so the
   field accepted Arabic-Indic `١٢٣٤`, Devanagari and fullwidth digits.
2. + 3. `test_routes_webhooks_coverage.py::…::test_driver_lookup_exception_is_swallowed`
   and `test_webhooks_coverage_gap.py::…::test_driver_lookup_exception_is_swallowed`
   — both `Exception: db down`/`db blip`. **These exposed a worse defect than
   the failure text suggests** — see §2.

**Not mine (left alone):** `test_prematch_driver_location_privacy.py::test_pseudonyms_are_opaque_and_uniform`
(asserts `'d' not in` a hex pseudonym — fails whenever the hash contains that
hex digit) and four `test_utils_extended.py::TestEstimateToken` cases. Verified
against `git show --stat` on the merge commit: neither `utils/estimate_token.py`
nor the prematch privacy path is in #5048's diff.

The 41 errors are all `tests/rls/*` — `psycopg2.OperationalError` on the
Postgres socket. CLAUDE.md says the RLS tier *self-skips* when no DB is
reachable; it is erroring instead, which is a decayed gate, not a result. Also
not this PR's, and not fixed here.

## 2. Root cause

**The OTP pattern:** `\d` was reached for reflexively as "digit". It is the
wrong tool when the intent is "the characters `generate_pickup_otp` can emit",
which is ASCII `0-9` only.

**The webhook reads:** the N1 fix added a `get_ride` CAS re-read at the top of
the `payment_intent.payment_failed` branch, unguarded. The branch already had a
*second* `get_ride` further down for a best-effort driver notification, wrapped
in its own try/except. Both pre-existing tests blanket-patched
`db_supabase.get_ride` to raise, intending to exercise that guarded driver
lookup; the new unguarded read now intercepts them.

That is the shallow reading. The real defect: **the event is already claimed by
`claim_stripe_event` when the CAS re-read runs.** An exception there propagated
out with nothing calling `unclaim_stripe_event`, so Stripe's retry hit the
duplicate check and returned `{"received": True, "duplicate": True}` — and the
payment failure was **lost permanently**. On a transient DB blip the ride would
never be marked failed, `payment_retry.py` would never see it, and the rider
would never be told. That is a strictly worse outcome than the mislabelling N1
was written to prevent, introduced by the fix for it.

## 3. Fix / remediation

1. `pattern=r"^[0-9]+$"`. Verified directly: `^\d+$` matches `١٢٣٤`,
   `^[0-9]+$` does not.
2. The CAS re-read is wrapped: on exception, log at `error`, **`unclaim_stripe_event`
   first**, then raise `503` (CLAUDE.md: a DB error is a 503 the client retries).
   This matches the `current is None` path directly below it, which already
   unclaimed before raising.
3. The two pre-existing tests now use `side_effect=[<ride row>, Exception(...)]`
   so the first (CAS) call succeeds and the second (driver lookup) raises —
   each test exercises exactly the call its name and docstring describe, rather
   than accidentally hitting a different one. **Neither test was weakened,
   skipped or quarantined**; they are more precise than before.
4. New `test_ride_read_failure_unclaims_and_503s` pins the unclaim-then-503
   behaviour, which nothing covered.

## 4. Risk & impact on existing functionality

**Blast radius: two files, both already in #5048's diff.**

- `RideOTPRequest` — one consumer (`verify_pickup_otp`). Narrowing the pattern
  can only reject inputs that were already guaranteed to fail
  `hmac.compare_digest` against an ASCII code, so no previously-working
  submission stops working. The driver app's numeric keypad emits ASCII.
- `payment_intent.payment_failed` — the new try/except only adds a path that
  did not exist (previously: propagate un-unclaimed). Every other branch is
  untouched, and the `current is None` → unclaim + 500 path is unchanged.

**A note on the gate itself, not fixed here:** the job reported
`conclusion: success` with 8 failing tests. Coverage passed (87.67% ≥ 60%) and
the pytest exit code did not fail the check. That is how a red suite reaches
`main` looking green, and it is the mechanism behind the audit's critical #1
("14 consecutive merges shipped without passing tests"). Per CLAUDE.md gate 8
this warrants a `[CR]` rather than an unilateral workflow edit — **filing that
CR is left for a human, and it matters more than any single fix in this PR.**

Also worth a reviewer's eye: `test_ride_update_returns_none_raises_500` still
passes, but for a different reason than its name implies — it patches
`update_ride`, which this branch no longer calls, and now passes because the
unpatched `get_ride` returns `None` and hits the ride-not-found 500. The
assertion is still valid; the name is now misleading. Left alone to keep this
change minimal.

## 5. User-experience effect

- **Driver:** a pickup code containing non-ASCII digits is now rejected at
  validation (422) rather than reaching the comparison and failing as a wrong
  code (400) — and, importantly, no longer burns one of the 5 lockout attempts.
  Not reachable through the shipped numeric keypad.
- **Rider:** on a DB blip during a `payment_failed` webhook, the failure is now
  retried and recorded instead of being silently dropped. Previously such a
  rider could be left with a ride that never showed as failed and never entered
  payment retry.
- No copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/_shared.py` | `^\d+$` → `^[0-9]+$` | `\d` is Unicode-aware; the field accepted non-ASCII digits |
| `backend/routes/webhooks.py` | CAS re-read wrapped: unclaim then 503 | An un-unclaimed exception lost the event permanently |
| `backend/tests/test_routes_webhooks_coverage.py` | Driver-lookup test targets the second `get_ride` | It was hitting the new first call instead |
| `backend/tests/test_webhooks_coverage_gap.py` | Same | Same |
| `backend/tests/test_webhook_payment_failed_guard.py` | Adds `test_ride_read_failure_unclaims_and_503s` | Nothing covered the unclaim path |
| `docs/change-log/2026-09-05-pickup-otp-bruteforce-hardening.md` | Records the pattern correction | The entry documented the buggy pattern |

## 7. Before / after

```python
# Before — routes/drivers/_shared.py
pattern=r"^\d+$"          # matches ١٢٣٤, Devanagari, fullwidth …
```
```python
# After
pattern=r"^[0-9]+$"       # exactly what generate_pickup_otp emits
```

```python
# Before — routes/webhooks.py (event already claimed at this point)
current = await db_supabase.get_ride(ride_id)     # raises -> propagates, no unclaim
```
```python
# After
try:
    current = await db_supabase.get_ride(ride_id)
except Exception as _read_err:
    logger.error(..., exc_info=True)
    await unclaim_stripe_event(event_id)          # or the retry is deduped away
    raise HTTPException(status_code=503, detail="Ride lookup failed — Stripe will retry") from _read_err
```

| A DB blip during `payment_failed` | Before | After |
|---|---|---|
| Exception propagates | yes | yes (as 503) |
| Event unclaimed | **no** | yes |
| Stripe retry re-processes | **no — deduped** | yes |
| Failure eventually recorded | **never** | on the retry |

## 8. Rollback plan

No migration, no schema change, no data written. `git revert` is a complete
rollback — it restores the two defects, so it is not advisable.

No feature flag: both changes strictly remove failure modes.

Not repaired by this change: any `payment_intent.payment_failed` event already
dropped by the unguarded read while #5048 was live on `main` (02:58Z onward).
Such events are identifiable in `stripe_events` as claimed-but-not-processed
rows for that type, and the admin replay endpoint can re-drive them. **Scoping
that sweep against production is left for a human.**

## 9. Verification performed

- [x] The OTP pattern bug reproduced and the fix verified directly against
      Python's `re` (`^\d+$` matches `١٢٣٤`; `^[0-9]+$` does not).
- [x] Confirmed the other 5 failures are outside #5048's diff via
      `git show --stat` on the merge commit.
- [x] Confirmed `card_hold_release.OPEN_AUTH_STATES` semantics unchanged.
- [x] `ruff check`, `ruff format --check`, `py_compile` clean on all changed files.
- [ ] **The webhook tests were NOT run** — see below.

## What was NOT verified

**Still no local `pytest`.** PyPI remains blocked (403) in this environment, so
`fastapi`/`pydantic` cannot be installed. The three test files changed here have
**not been executed**; the reasoning about which `get_ride` call each patch now
hits is read from the code, not observed. CI on this PR is again the first real
run.

Specifically unverified: that `side_effect=[row, Exception]` lands on the two
calls in the order assumed (it depends on nothing else in the branch calling
`get_ride` between them — read and believed, not proven); that the new
`test_ride_read_failure_unclaims_and_503s` reaches the branch through
`_dispatch_stripe_event`'s earlier subscription pre-checks; and that the
pydantic `pattern` change behaves identically under pydantic's Rust regex as
under Python's `re` (the ASCII class `[0-9]` is identical in both, but this was
reasoned, not executed against pydantic).
