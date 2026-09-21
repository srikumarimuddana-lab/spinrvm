# Change Impact & Risk Log — booking kill switch falls back to last-known-good

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session, on the repository owner's request |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review finding **E2** (kill switch); ACTION_ITEMS.md **G5** (the flag itself) |

## 1. Issue / gap identified

`POST /rides` checks the `new_ride_requests_enabled` kill switch before any DB write. On a
settings-read error it logged a `warning` and **proceeded as enabled**:

```python
except Exception:
    logger.warning("[BOOKING] app_settings lookup failed for new_ride_requests_enabled; proceeding as enabled")
```

The flag's default is `True` ("not killing anything"), so a read failure silently **resumed
bookings during exactly the incident an operator had paused them for**. A kill switch that stops
working when things break is not a kill switch — and the pause and the read failure are strongly
correlated, since operators pause bookings precisely when infrastructure is degrading.

## 2. Root cause

Not an oversight — a deliberate, documented, tested decision. The module docstring of
`tests/test_booking_new_ride_requests_kill_switch.py` argued fail-open explicitly, contrasted it
with `settle_corporate`'s fail-closed `corporate_billing_enabled` (ACTION_ITEMS.md C54 / WS-1), and
called the difference "by design, not an accidental inconsistency." The reasoning was sound as far
as it went: this flag gates **every** new booking platform-wide, so a degraded `app_settings` read
must not by itself take the product's demand side down.

What it missed is that fail-open is not the only alternative to fail-closed. The choice was framed
as "block everything" vs "allow everything", and both options throw away information the process
already has — `get_app_settings()` caches, and the cached value is the operator's actual last
instruction.

## 3. Fix / remediation

A third option that satisfies both concerns, rather than trading one for the other.

- `backend/settings_loader.py` — new `get_last_known_app_settings()`: returns the last
  **successfully loaded** settings dict however old, or `None` if this process has never completed
  a load. Deliberately ignores `_SETTINGS_TTL`, which is the only difference from the pre-existing
  `get_cached_app_settings()` (that one goes quiet past 60 s, for sync render paths).
- `backend/routes/rides/_deps.py` — re-export it, matching how `get_app_settings` is surfaced.
- `backend/routes/rides/booking.py` — on a read error, fall back to the last known value of the
  flag; `logger.opt(exception=True).error(...)` (was `warning`) and increment
  `spinr_rides_settings_read_failed_total{flag, fallback=last_known_good|cold_start}`.

This works because `get_app_settings()` writes its cache **only on the success path, after the
await** — a failed read never overwrites it. Resulting behaviour:

| Scenario | Last known | Outcome |
|---|---|---|
| Operator paused bookings, then the DB degrades | `False` | **still paused** — the hole is closed |
| Steady state, one transient read blip | `True` | booking proceeds — no platform-wide outage |
| Cold start, never read settings successfully | `None` | proceeds, matching the original posture |

The cold-start case is the narrow residual, and it errs the right way: a process that has just
booted cannot have observed a pause.

**Alternatives considered.** (a) Plain fail-closed, as the review originally recommended — rejected,
because a sustained settings-read fault would stop every booking platform-wide, the exact outcome
the existing decision was protecting against, and it would have overridden a written decision on the
highest-blast-radius surface in the app. (b) Leave it as-is — rejected, because the hole is real and
the correlation between "paused" and "degraded" makes it likeliest to bite when it matters most.
(c) Last-known-good — chosen: it closes the hole **without** taking on fail-closed's downside, and
needs no new configuration. This was escalated to the repository owner rather than decided
unilaterally, per CLAUDE.md pre-merge gate 9, and (c) was their call.

## 4. Risk & impact on existing functionality

**Blast radius of the new shared function:** `get_last_known_app_settings` is **purely additive** —
a new name in `settings_loader.py` with exactly one caller (`booking.py`, via `_deps`). No existing
function's behaviour, signature, or return value changed. `get_app_settings` and
`get_cached_app_settings` are untouched; I read both to confirm the new one cannot perturb the
cache (it only reads `_settings_cache`, never writes it). Grepped for other consumers: none, since
it did not exist before this commit.

**Blast radius of the booking change:** confined to the `create_ride` guard. Verified by grep that
`new_ride_requests_enabled` has exactly these readers repo-wide — `schemas.py:431` (default),
`routes/admin/settings.py:474` (admin write), and this guard. No background loop, no other route,
no client reads it.

**Behaviour change on the success path: none.** When the settings read succeeds — which is the
overwhelmingly common case, backed by a 60 s cache — the logic is identical to before.

**The one way this is stricter than before:** a booking that the old code would have allowed is now
rejected when (and only when) the read fails *and* the last successful read said `False`. That is
the intended fix, not a side effect.

**Structural dependency worth stating:** the fix rests on `get_app_settings` writing its cache only
after a successful read. If that is ever refactored into a `finally:`, or the cache is seeded before
the read, the kill switch silently reverts to fail-open with nothing failing nearby.
`tests/test_settings_loader_last_known.py::TestFailedReadDoesNotClobber` pins that property
directly, so the refactor would break a named test instead.

**No migration, no schema change, no new column, no new setting.**

## 5. User-experience effect

- **Rider:** during a settings-read failure where bookings were already paused, a booking attempt
  now returns the same 503 and the same copy ("Ride requests are temporarily unavailable. Please try
  again shortly.") as a normal pause, instead of being accepted. The copy and status are
  deliberately identical to the switch-off path so a rider cannot tell a paused platform from a
  broken one. No new string, no new error code, nothing to translate.
- **Driver / corporate admin / internal admin:** none.
- Nothing changes mid-session for anyone already on a ride — this guard runs only at booking time,
  before any state exists.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/settings_loader.py` | new `get_last_known_app_settings()` | the stale-tolerant accessor the fallback needs |
| `backend/routes/rides/_deps.py` | re-export it (both dual-import branches) | how `booking.py` reaches it, matching `get_app_settings` |
| `backend/routes/rides/booking.py` | guard falls back to last-known-good; `warning` → `opt(exception=True).error` + metric | E2 |
| `backend/tests/test_booking_new_ride_requests_kill_switch.py` | docstring rewritten; fail-open test replaced by 4 covering each fallback case | it pinned the old behaviour |
| `backend/tests/test_settings_loader_last_known.py` | new — 6 tests | pins the no-clobber property the fix depends on |
| `docs/change-log/2026-09-21-booking-kill-switch-last-known-good.md` | this file | |

Six files, above CLAUDE.md's ≤3 guidance. Three are code, and they are not separable: the accessor,
its export, and its one caller only make sense landing together. The rest are tests and this log.

## 7. Before / after

**Before** — the default silently un-pauses the platform:

```python
    except Exception:
        logger.warning("[BOOKING] app_settings lookup failed for new_ride_requests_enabled; proceeding as enabled")
```

**After** — the operator's last actual instruction wins:

```python
    except Exception as _g5_err:
        _g5_last_known = _deps.get_last_known_app_settings()
        _g5_enabled = True if _g5_last_known is None else bool(_g5_last_known.get("new_ride_requests_enabled", True))
        logger.opt(exception=True).error(...)
        _deps._metric_inc("spinr_rides_settings_read_failed_total", {...})
    if not _g5_enabled:
        raise HTTPException(status_code=503, detail="Ride requests are temporarily unavailable. ...")
```

Note the `raise` moved **out** of the `try`, which let the old `except HTTPException: raise`
re-raise guard be deleted — the 503 can no longer be caught by this block's own handler.

**Concrete scenario (state-machine/dry-run per gate 4).** Ops flips `new_ride_requests_enabled` to
`false` at 14:00 during a dispatch incident. At 14:02 the settings read starts failing.
*Before:* the next rider to open the app books successfully, enters `searching`, and joins the
incident. *After:* they get the same 503 the pause has been returning since 14:00, and
`spinr_rides_settings_read_failed_total{fallback="last_known_good"}` starts climbing so the
settings-read fault is visible on its own.

## 8. Rollback plan

`git revert` + redeploy. Nothing is persisted, migrated, or written to any table by this diff — no
schema, no settings row, no flag, no data. A revert restores the previous fail-open guard exactly
and cannot leave anything half-applied.

Faster, without a deploy: the flag itself is the control. Setting `new_ride_requests_enabled = true`
in `app_settings` via the admin dashboard makes the last-known-good value `True`, so the fallback
path allows bookings — i.e. the old behaviour — with no code change. That is available immediately
and is the correct first move if this ever rejects bookings unexpectedly.

## 9. Verification performed

- `ruff check` and `ruff format --check` clean across all five changed/added Python files.
- `python3 -m py_compile` clean on all five.
- **Read the existing test's rationale before overriding it** rather than deleting a red test —
  that docstring is what surfaced this as a deliberate decision and triggered the escalation.
- Confirmed `logger` in `booking.py` is **loguru** (re-exported from `routes/rides/_deps.py:21`),
  which is why `.opt(exception=True)` is required instead of `exc_info=True`; my calls use `{}`
  placeholders with matching positional args and pass all three
  `tests/test_loguru_call_conventions.py` rules.
- Confirmed `_deps._metric_inc` is the established in-file idiom for this module
  (`booking.py:1305` already uses it) rather than adding a fresh import.
- Confirmed the metric name matches its sibling `spinr_payment_settings_read_failed_total` and the
  `spinr_<domain>_<metric>_<unit>` convention, with `rides` as the domain for this surface.
- Read `get_app_settings` line by line to confirm the no-clobber property the fix depends on, and
  pinned it in a test rather than relying on the reading.
- Enumerated every reader of `new_ride_requests_enabled` by grep (§4), not assumed.
- Patched `get_last_known_app_settings` explicitly in every booking test instead of letting the real
  process-global `_settings_cache` leak in — otherwise those assertions would depend on pytest
  ordering and could pass alone while failing in a full run.

## 9b. Reviewer findings and how each was handled

`spinr-dispatch-reviewer` was run against the real diff before commit (CLAUDE.md gate 10). Verdict
**SAFE TO MERGE**, no blockers. It independently re-verified the no-clobber property, the
guard-runs-before-any-DB-write requirement, the dual-import, the metric name, and that
`await_count == 3` is still the right assertion by tracing which `get_rows` calls the handler
actually makes. Its findings:

| Finding | Action |
|---|---|
| **A column present but explicitly `NULL` yields `None`, and `bool(None)` is `False`** — `.get(key, True)` only defaults when the key is *absent*, so a null column would pause **every booking platform-wide**. Pre-existing on the live path; my fallback doubled the surface. | **Fixed** — `_g5_flag is None` is now tested explicitly on both paths, so absent / NULL / never-read all read as enabled. Two new tests pin it. This was a real bug in the exact lines I was rewriting, so shipping it knowingly was not an option. |
| The `try` now has no `except HTTPException: raise` guard, so a future edit adding an HTTPException-raising call inside it would be silently reclassified as a settings failure | **Fixed** — explicit comment pinning "nothing that can raise HTTPException may go inside this try", with the reason. |
| `ACTION_ITEMS.md` G5 (CLOSED) still asserted the fail-open behaviour and listed the now-deleted test | **Fixed** — struck the superseded sentence and added a dated amendment. The reviewer's point was the sharp one: this whole change exists because a written-down decision turned out to be wrong, so leaving the canonical backlog repeating it would be the same failure one level up. |
| Uncommitted `payment_service.py` / `test_settle_card_capture.py` in the tree must not land in this commit | **Respected** — they are a separate logical change (E2's payment-failure push) and are committed separately. |
| No dedup on the ERROR log: a sustained settings-read outage re-emits a full traceback + Sentry event per `POST /rides` at full QPS | **Not done, deliberately** — see §10. |

**On the log-storm finding.** Real, and accepted rather than engineered around. Three reasons: the
events share a fingerprint, so Sentry groups them into one issue (the cost is quota, not triage
noise); `get_app_settings()` failing means the DB is failing, so these requests are already
generating their own errors downstream and this is one more in an already-erroring system; and the
metric — not the log — is the intended alerting signal. Adding a per-process
log-once-per-N-seconds guard is speculative machinery against a failure mode nobody has measured
(CLAUDE.md "simplicity first"). Recorded here so it can be revisited with data rather than
rediscovered.

## 10. What was NOT verified — read this before merging

- **No test was executed.** `pytest` is not installed here and PyPI is blocked by policy
  (`pip install pytest` → "Could not find a version that satisfies the requirement"), the same block
  that stops `npm`. **CI is the first thing that will actually run any of this.** Everything above
  is static reasoning plus `ruff`/`py_compile`. In particular the four rewritten booking tests reuse
  the original's `get_rows` side-effect sequence (`[[], [], RuntimeError(...)]`, asserting
  `await_count == 3`) to prove the guard was passed; that sequence was correct for the old code path
  and I have re-read the handler to confirm it still is, but I have not seen it run.
- Not exercised against a real Supabase, a real settings row, or a real read failure — only mocked
  `get_rows`. The production failure mode (a PostgREST/circuit-breaker error inside `get_rows`) is
  assumed to surface as an exception from `get_app_settings`, which is what the current code's own
  `except Exception` already assumed.
- No staging deploy, no load test, and no measurement of how often settings reads actually fail in
  production — the correlation argument in §2 is reasoning, not data.
- The cold-start window is not separately instrumented beyond the `fallback="cold_start"` metric
  label; nobody has confirmed how long a freshly booted replica goes before its first successful
  settings read.
- Backend-only: no `admin-dashboard` build, and none of the 6 merge-blocking visual-regression
  baselines are touched by this diff.
- **The same fail-open pattern remains in two sibling kill switches** —
  `routes/promotions.py:146` (`promo_redemption_enabled`) and `utils/scheduled_rides.py:734`
  (`scheduled_dispatch_enabled`), both carrying the same "fail-open, same convention as every other
  kill switch" comment this change just invalidated for booking. They are **not** touched here:
  each is a separate domain with its own blast radius and deserves its own decision, and bundling
  them would breach the one-logical-change rule. Flagged as follow-up, not silently left.
