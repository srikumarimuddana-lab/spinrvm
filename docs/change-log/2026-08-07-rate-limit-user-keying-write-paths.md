# Change Impact & Risk Log — per-user rate-limit keying (write paths: rides, safety, payments)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (session: postgres-scaling-supabase) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides, safety, payments |
| PR / commit link | branch `claude/postgres-scaling-supabase-ypnwiy` |
| Related issue or gap ID | Follow-on to `2026-08-07-rate-limit-user-keying.md` (read paths); same class as gap #41 and ACTION_ITEMS AI1 |

## 1. Issue / gap identified

The write-path limiters were still keyed on client IP after the read-path fix.
Behind carrier-grade NAT, where hundreds of a carrier's subscribers share one
egress IP, this meant:

- **`ride_request_limit` — 5 bookings/minute across an entire carrier.** In a
  Saskatoon burst (event letting out, airport arrivals bank), the sixth rider on
  Rogers to tap "Book" in a given minute was refused, regardless of who they
  were.
- **`ride_action_limit` guards `POST /rides/{ride_id}/emergency`
  (`routes/rides/safety.py:38`) — an SOS could be refused because unrelated
  strangers behind the same carrier IP had spent the 20/minute bucket on
  ordinary ride actions (start, complete, add stop, share trip).** That is a
  safety defect, not a capacity one, and it is the reason this commit is not
  deferred behind a flag rollout.
- `cancel_ride_limit`, `ride_message_limit`, `ride_rating_limit`, and
  `payment_action_limit` (tips, card charges, payment-method changes) had the
  same shape: a per-user policy enforced as a per-carrier one.

## 2. Root cause

Identical to the read-path case: these limiters inherited `default_limiter`'s
`get_real_client_ip` key function and never overrode it. IP keying is right for
unauthenticated surfaces (OTP, login) and wrong for authenticated ones, and the
distinction was never applied to the ride/payment write paths.

The SOS exposure specifically arises because `ride_action_limit` is a *shared*
limiter object covering ten routes of very different criticality — nine
ordinary ride actions and one emergency call — so noise on the ordinary ones
consumed budget the emergency one needed.

## 3. Fix / remediation

Pass the existing `get_user_or_ip_key` (added in the read-path commit) as
`key_func` to all six write-path limiters. Authenticated callers key on user id;
anonymous callers keep IP keying.

**Numeric limits unchanged** — 5 bookings/min, 20 actions/min, 5 payment
actions/min are all sane *per user*; they were only ever unreasonable as
per-carrier totals.

Note on the SOS route specifically: it authenticates with
`get_current_user_allow_expired`, because a token that lapsed mid-trip still
identifies the caller. `_extract_unverified_user_id` decodes without verifying
signature **or expiry**, so the key function agrees with that policy — an
expired-but-valid token still keys to its real user rather than silently
dropping back into the shared carrier-IP bucket at exactly the wrong moment.
Covered by `test_expired_token_still_keys_to_its_user`.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface — 25 decorated routes across rides, safety, and
payments.** Full enumeration by grep (`^@<limiter>` across `backend/routes/`):

| Limiter | Limit | Route sites |
|---|---|---|
| `ride_request_limit` | 5/min | `rides/booking.py:294` |
| `cancel_ride_limit` | 10/hour | `rides/cancellation.py:43`, `:534` |
| `ride_action_limit` | 20/min | `rides/lifecycle.py:63`, `:127`; `rides/stops.py:41`, `:101`, `:163`; **`rides/safety.py:38` (SOS)**, `:214`; `rides/sharing.py:261`; `rides/receipts.py:152`; `rides/lost_found.py:33` |
| `ride_message_limit` | 30/min | `rides/chat.py:108` |
| `ride_rating_limit` | 5/hour | `rides/rating.py:37` |
| `payment_action_limit` | 5/min | `payments.py:154`, `:314`, `:486`, `:527`, `:573`, `:671`, `:809`, `:874`, `:962`; `rides/payments.py:53`, `:209` |

**Money paths.** `payment_action_limit` covers tips and card charges. This
commit changes *only which bucket a request counts against* — no fare
arithmetic, no `Decimal` handling, no Stripe idempotency key, no wallet delta,
no settlement ordering is touched. Stripe idempotency (`claim_stripe_event`)
remains the actual double-charge guard; the rate limit was never that guard, and
its role is unchanged.

**Ride state machine.** Untouched. `ride_action_limit` gates *access* to
transition endpoints; the transitions themselves and their
`_require_ride_in_state()` guards are unmodified.

**Direction of the security change: tighter.** A per-user bucket cannot be
reset by rotating IPs, which is the free evasion IP keying invited (the same
argument the repo already accepted for gap #41 and AI1). Anti-abuse limits like
`cancel_ride_limit` (cancellation farming) and `ride_rating_limit` (rating spam)
become *harder* to evade, not easier, because the abuser can no longer cycle
through a proxy pool to mint fresh budgets.

**Where it does loosen, deliberately:** a *single* carrier IP can now issue more
total requests than before, because each user on it has an independent budget.
That is the intended fix — the old ceiling was an accidental aggregate cap on
unrelated people, not a designed anti-abuse control. Per-user abuse ceilings are
unchanged, and the Fly connection limits plus the DB circuit breaker remain the
infrastructure-level backstops.

**Shared Redis:** key cardinality shifts from active-IPs to active-users.
Same consideration as the read-path commit; counters are small and expire on the
limiter's own window.

**Background loops:** untouched.

## 5. User-experience effect

- **Riders:** fewer spurious 429s on booking, cancelling, messaging, rating, and
  paying. Most consequentially, **an SOS is no longer refusable because of
  someone else's traffic**.
- **Drivers:** same benefit on the ride-action routes they share
  (`lifecycle.py`, `stops.py`).
- **Visible mid-session?** Yes, and only in the permissive direction — a rider
  or driver currently being throttled mid-ride stops being throttled. Nothing
  they must learn or do differently.
- **Corporate admin / internal admin:** no change; corporate and admin limiters
  were not touched.
- **No copy, notification, or UI changes.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rate_limiter.py` | `key_func=get_user_or_ip_key` on `ride_request_limit`, `cancel_ride_limit`, `ride_action_limit`, `ride_message_limit`, `ride_rating_limit`, `payment_action_limit`; comment on `ride_action_limit` recording the SOS exposure and the expired-token interaction | Makes each limit per-user instead of per-carrier-NAT |
| `backend/tests/test_rate_limit_user_keying.py` | Extended: SOS-not-blocked-by-a-stranger end-to-end test, and expired-token keying test | The safety case and the `allow_expired` interaction both need explicit coverage |
| `docs/change-log/2026-08-07-rate-limit-user-keying-write-paths.md` | This log | CLAUDE.md mandate — safety and payments surfaces |

## 7. Before / after

```python
# Before — per carrier-NAT IP
ride_request_limit  = default_limiter.limit("5/minute")
cancel_ride_limit   = default_limiter.limit("10/hour")
payment_action_limit= default_limiter.limit("5/minute")
ride_rating_limit   = default_limiter.limit("5/hour")
ride_message_limit  = default_limiter.limit("30/minute")
ride_action_limit   = default_limiter.limit("20/minute")   # includes SOS
```

```python
# After — per authenticated user, IP only for anonymous callers
ride_request_limit  = default_limiter.limit("5/minute",  key_func=get_user_or_ip_key)
cancel_ride_limit   = default_limiter.limit("10/hour",   key_func=get_user_or_ip_key)
payment_action_limit= default_limiter.limit("5/minute",  key_func=get_user_or_ip_key)
ride_rating_limit   = default_limiter.limit("5/hour",    key_func=get_user_or_ip_key)
ride_message_limit  = default_limiter.limit("30/minute", key_func=get_user_or_ip_key)
ride_action_limit   = default_limiter.limit("20/minute", key_func=get_user_or_ip_key)
```

Concrete SOS scenario, several riders on one carrier IP:

```
Before:  20 ride actions from other riders exhaust "ip:203.0.113.7"
         -> rider in distress POSTs /rides/{id}/emergency -> 429
After:   each rider has "user:<id>"
         -> rider in distress has an untouched 20/min budget -> SOS dispatched
```

## 8. Rollback plan

**No redeploy required** — the same kill switch as the read-path commit, since
both use the one key function:

```bash
fly secrets set RATE_LIMIT_USER_KEYING=off -a spinr-backend-yyz
```

This reverts every limiter changed in both commits to IP keying, restoring
pre-change behavior including the CGNAT flaw and the SOS exposure. **Because
that re-introduces a safety defect, this switch should be treated as a
break-glass lever for a demonstrated bug in the keying itself, not a routine
tuning knob** — that guidance is in `docs/runbooks/capacity-scaling.md` §3 and
§7.

No live data is touched — no Stripe charge, wallet delta, ride-state row, or
insurance-period row — so a config revert is a complete rollback.

## 9. Verification performed

- [x] **Blast-radius grep performed** — `^@<limiter>` for all six limiters
      across `backend/routes/`: 25 decoration sites, enumerated in §4. Confirmed
      `ride_action_limit` covers the SOS route.
- [x] **Automated tests run** (`backend/.venv`, created from `requirements.txt`
      because the container had no backend deps):
      - `tests/test_rate_limit_user_keying.py` — **25 passed** (2 new: SOS
        end-to-end, expired-token keying)
      - `tests/test_ride_state_machine.py`, `test_process_payment_card.py`,
        `test_create_ride_remaining_branches.py`, `test_ride_complete_coverage.py`
        — **97 passed**
      - `pytest -k "safety or emergency or sos or cancel"` — **422 passed,
        1 skipped**
- [x] **Reviewed against CLAUDE.md conventions** — money (no arithmetic or
      Stripe idempotency touched), ride state machine (no transition logic
      touched), auth/JWT trust model (keying never grants authorization; role is
      still re-read from `users`), PIPEDA/observability (bucket key is a user id
      — the identifier CLAUDE.md prescribes instead of names/phones/emails).
- [x] **State-machine / money dry-run consideration** — the mandated dry run
      applies to changes in transition or settlement *logic*; this commit
      changes neither, and the suites covering both paths were run green above.
- [x] **Feature-flag consideration** — user-visible but strictly permissive, and
      one of the defects fixed is a safety defect. Staging it behind a
      percentage rollout would mean knowingly leaving some riders' SOS
      429-able. Env kill switch instead.
- [ ] **Manual repro in staging** — not possible; no staging environment exists
      (ACTION_ITEMS E1).

## What was NOT verified

- **No end-to-end SOS was exercised against a real backend.** The safety fix is
  verified at the rate-limiter layer (the layer that was broken) via the real
  `AsyncLimiter` + storage, not by triggering an actual emergency through the
  full stack with Twilio/FCM and the safety team's paging path.
- **Not tested against live Supabase or a real carrier NAT** — CGNAT is
  reproduced with synthetic `cf-connecting-ip` headers, which is the header the
  key function actually reads.
- **No load test** — `loadtest/locustfile.py` needs a staging target
  (ACTION_ITEMS E1) and refuses to run against production.
- **Stripe flows were exercised against mocked fixtures only**
  (`mock_supabase_client` / mocked Stripe), never live Stripe. This commit does
  not alter payment logic, but the payment-route coverage backing it is mocked.
- **Redis key-cardinality impact at burst scale is reasoned, not measured.**

## 10. Sign-off

- [x] Rollback plan is concrete and testable, with an explicit warning that it
      re-introduces the SOS exposure
- [x] Blast radius is stated, not assumed (25 sites enumerated by grep)
- [x] No silent behavior change to an already-shipped flow — UX field filled in;
      user-facing delta is fewer 429s, including on SOS
