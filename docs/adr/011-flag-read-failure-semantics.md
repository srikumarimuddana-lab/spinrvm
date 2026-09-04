# ADR-011: Flag read failure semantics on money paths

- Status: Accepted
- Date: 2026-09-03
- Deciders: Claude Code (WS-1 executing session), per plans/2026-09-03-path-to-a-implementation-plan.md
- Domain: payments
- Affects: `backend/services/payment_service.py` (`settle_corporate`, `_atomic_settle_enabled`), `backend/utils/metrics.py`

## Context

`backend/services/payment_service.py` reads two `app_settings` flags via
`settings_loader.get_app_settings()`, each wrapped in its own
`try/except Exception`:

- `corporate_billing_enabled`, in `settle_corporate` — a kill switch that
  stops the corporate allowance + master-wallet settlement saga.
- `ledger_atomic_settle_enabled`, in `_atomic_settle_enabled` — selects
  between the atomic-RPC settle path and the legacy (already-safe,
  already-shipped) settle path.

Before this change, **both** read failures were handled identically:
`logger.warning(...)` and silently proceed as if the flag were at its
default (`corporate_billing_enabled` → treated as enabled;
`ledger_atomic_settle_enabled` → treated as off, i.e. use the legacy path).
This is the exact anti-pattern `CLAUDE.md`'s "Do not silently swallow
errors" section forbids: a DB/settings read error was logged at `warning`
and the code continued as if nothing happened, without incrementing any
counter or otherwise making the failure observable.

For `corporate_billing_enabled` this is more than a logging nit: the flag's
entire purpose is to let an operator stop corporate money movement during
an incident (ACTION_ITEMS.md E5). If the `app_settings` read itself starts
failing — plausibly *because* the incident is a Supabase/DB degradation —
the kill switch would fail open at exactly the moment it's needed most,
silently continuing to move corporate money while an operator believes
billing is paused.

Found and fixed while executing WS-1 (Correctness) of
`plans/2026-09-03-path-to-a-implementation-plan.md`, itself derived from
`docs/audit/2026-09-03-engineering-director-teardown-round2.md`. Tracked as
`ACTION_ITEMS.md` C54.

The same `logger.warning(...`, proceeding as enabled")` pattern exists,
nearly verbatim, in six other unrelated kill switches: `utils/
allowance_reset.py`, `utils/surge_engine.py`, `utils/
corporate_low_balance.py`, `utils/scheduled_rides.py`, `routes/
promotions.py`, and `routes/rides/booking.py`'s `new_ride_requests_enabled`
(tested explicitly in `tests/test_booking_new_ride_requests_kill_switch.py`
as an intentional fail-open). Those are out of scope for this change — see
Alternatives Considered.

## Decision

Money-path `app_settings` flags do **not** get one universal
read-failure rule. Each flag's failure mode is chosen individually, based
on which direction of error is worse for that specific flag, and the
choice is:

1. **Stated explicitly in a code comment at the read site** — never left as
   an implicit accident of a copy-pasted `try/except`.
2. **Observable via a shared counter** regardless of which way the flag
   fails, so the read failure itself is never silent even when the caller
   can't see it: `spinr_payment_settings_read_failed_total{flag=<name>}`
   (`backend/utils/metrics.py`), incremented once per failed read.
3. **Logged at `error` with `exc_info=True`**, not `warning` — a settings
   read failure on a money path is an actionable failure per `CLAUDE.md`,
   not a recoverable anomaly.

Applied here:

- `corporate_billing_enabled` **fails CLOSED**: a read error now returns
  `PaymentResult(success=False, status_code=503)`, identical to the flag
  being explicitly `False`. A kill switch that cannot be trusted during a
  DB degradation is not a kill switch.
- `ledger_atomic_settle_enabled` **keeps failing to the legacy path**: a
  read error still returns `False` (use the legacy, non-RPC settle path).
  That path is itself a fully safe, already-shipped settlement path, so a
  transient flag-read blip must not turn into a hard settlement failure —
  the two failure directions are not symmetric in risk.

## Consequences

### Positive
- The corporate-billing kill switch is now trustworthy during exactly the
  kind of incident (DB/settings degradation) it is likely to be flipped
  for.
- Both flags' read failures are now counted and error-logged instead of
  silently swallowed at `warning`, closing a "do not silently swallow
  errors" gap CLAUDE.md already mandates fixing.
- The counter gives one shared signal
  (`spinr_payment_settings_read_failed_total`) for alerting on
  `settings_loader`/DB degradation from the money-path call sites, without
  needing a bespoke metric per flag.

### Negative / trade-offs
- A transient `app_settings` read blip now makes `settle_corporate` return
  a hard 503 instead of proceeding — a corporate-paid ride's settlement
  will retry (via the existing payment-retry loop) rather than completing
  immediately. Accepted: the alternative is a kill switch that doesn't
  actually kill anything during a real incident.
- The two flags in the same file now have different failure semantics,
  which is easy to get wrong when adding a *third* flag by copy-paste. The
  code comments at each read site (and this ADR) are the mitigation, not a
  lint rule — there is no automated check that a new money-path flag picked
  the right direction.

### Neutral
- No schema, migration, or `app_settings` default changed — this only
  changes behavior on the *read failure* path, which has no test coverage
  gap today for the happy path (flag explicitly `True`/`False`).

## Alternatives considered

### Fail every money-path flag closed on a read error, including the six unrelated ones found during the blast-radius grep
Rejected for this change: `new_ride_requests_enabled` and the other five
gate platform-wide availability (all new ride bookings, the surge engine,
scheduled dispatch, promo redemption, allowance resets, low-balance
nudges), not a single incident-scoped money switch. Failing all of them
closed would mean a single `app_settings` read blip takes down new ride
bookings platform-wide — a far larger blast radius than the problem this
ADR fixes. Left as-is, with `tests/
test_booking_new_ride_requests_kill_switch.py`'s docstring updated to
explain the asymmetry is deliberate rather than an oversight. Revisiting
any of the other six is a separate, per-flag decision, not a mechanical
follow-up from this one.

### One shared "fail-closed-by-default" helper for all `app_settings` flag reads
Rejected: would require every existing call site (dozens) to be
individually re-reviewed for which failure direction is actually safe for
that flag, which is exactly the kind of blast-radius-widening this task
was not scoped for (WS-1 subtask 1-2 named only `payment_service.py`'s two
flags). A typed flag-accessor layer with per-flag `fail_mode` is proposed
separately as WS-7 in `plans/2026-09-03-path-to-a-implementation-plan.md`
(🛑 H5, vendor choice pending) — this ADR's per-flag reasoning is intended
to carry forward into that accessor's `fail_mode="closed"` design, not be
superseded by it.

## Rollout

- Migration path: none — behavior-only change in `payment_service.py`,
  shipped directly (not flagged) because it is strictly safer on the
  `corporate_billing_enabled` path (was silently unsafe; now fails closed)
  and behavior-preserving on `ledger_atomic_settle_enabled` (still falls
  back to the legacy path, only the logging/counting changed).
- Feature flag: none. The flags this ADR is *about* are unaffected in
  their normal (non-error) read path.
- Rollback plan: `git revert` is sufficient — no data was written
  differently on the happy path, and no migration is involved.

## Spinr-specific impact

- Money / payments: `corporate_billing_enabled` read failures now block
  corporate settlement (503, retried later) instead of silently letting it
  proceed unguarded. `ledger_atomic_settle_enabled` read failures are
  unchanged in outcome (legacy path), only in observability.
- Safety / insurance periods: none.
- PIPEDA / retention: none.
- Regulatory (SK/SGI): none.
- Performance SLAs: negligible — one additional lazy import + counter
  increment only on the (rare) error path; no change to the happy-path
  Stripe webhook processing SLA (< 500 ms).

## References

- `ACTION_ITEMS.md` C54
- `plans/2026-09-03-path-to-a-implementation-plan.md` WS-1, subtasks 1-2
- `docs/audit/2026-09-03-engineering-director-teardown-round2.md`
- `docs/change-log/2026-09-03-ws1-correctness.md`
