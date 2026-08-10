# Change Impact & Risk Log — loop-staleness alerts suppressed for the first hour of uptime

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (session: postgres-scaling-supabase) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (observability) |
| PR / commit link | branch `claude/postgres-scaling-supabase-ypnwiy` |
| Related issue or gap ID | Found while building `capacity_watchdog`; flagged in `docs/change-log/2026-08-07-capacity-watchdog.md` §3 as a follow-up |

## 1. Issue / gap identified

`utils/loop_alert.py` — the background-loop staleness alerter, and **the only
alerting path currently live in production** (ADR-010's Grafana pipeline is
still `Proposed`) — silently posted **no alerts at all for the first hour after
every process start**.

The cooldown compared the current `time.monotonic()` against a `0.0` default for
loops never yet alerted:

```python
last_sent = _last_alerted.get(name, 0.0)
if now - last_sent < COOLDOWN_SECONDS:   # COOLDOWN_SECONDS = 3600
    continue
```

`time.monotonic()` counts from an arbitrary origin that is near zero early in a
process's life. For the first 3,600 seconds of uptime, `now - 0.0 < 3600` is
true for **every** loop, so every stale-loop alert was thrown away.

Why this matters more than a generic "alert delayed by an hour": that first hour
is precisely when a loop is most likely to have failed to start at all — a bad
deploy, a crash-looping process, a Fly machine resuming from suspend. The
alerter's whole purpose is to report a loop that isn't running, and the window
in which that is most likely was the window it was blind in.

The blast radius grows with this branch's other work: `auto_stop_machines =
"suspend"` means machines now wake from suspend during bursts, each starting a
process whose alerting is mute for its first hour.

## 2. Root cause

A sentinel-value bug: `0.0` was used to mean "never alerted", but `0.0` is also
a *valid and recent* monotonic timestamp early in a process's life. The two
meanings are indistinguishable, and the comparison resolves the ambiguity the
wrong way.

**Why it survived review and testing:** `tests/test_loop_alert.py` defined
`_FAKE_NOW = 10_000.0` with the comment *"Use a fixed 'now' larger than
COOLDOWN_SECONDS so the throttle window check is deterministic regardless of
actual system uptime."* Every existing test therefore ran at a monotonic value
comfortably past the cooldown, and none exercised the early-uptime path. The
test suite was, in effect, configured around the bug.

I found this because the identical pattern appeared in the first draft of
`utils/capacity_watchdog.py`, where two new tests failed on it immediately.

## 3. Fix / remediation

Use an explicit `None` for "never alerted":

```python
last_sent = _last_alerted.get(name)
if last_sent is not None and now - last_sent < COOLDOWN_SECONDS:
    continue
```

Plus a parametrized regression test at realistic early-uptime values
(0.4 s, 12 s, 300 s, 3599 s), and a comment on `_FAKE_NOW` recording that its
value is what let the bug hide, so the next person does not reintroduce the
blind spot by copying the pattern.

Also added `test_failed_post_does_not_consume_the_cooldown`, pinning existing
(already-correct) behavior that had no coverage: `_last_alerted[name] = now` sits
*after* `raise_for_status()` inside the `try`, so a failed webhook POST does not
stamp the cooldown and the next check retries. Without a test, a future
refactor could easily move that assignment and silence a stale loop for an hour
on one flaky HTTP call.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the alerting path.** `_last_alerted` is module-private
to `loop_alert.py` and read in exactly one place (the line changed). Verified by
grep. No caller signature, no loop, no route, no query is affected.

Repo-wide check for the same pattern elsewhere — the answer is "nowhere else":

| Site | Verdict |
|---|---|
| `utils/kyb_reverification.py:96` | Correct already — guards `if last_dt and ...` against a DB-stored datetime, not a monotonic zero default |
| `utils/breadcrumb_buffer.py:112` | Correct — compares against `opened_at`, set explicitly at buffer creation |
| `utils/driver_presence.py:194`, `utils/redis_client.py:39,59` | Not cooldowns — TTL/expiry arithmetic against explicitly-set values |
| `repositories/_base.py:146` | Correct — `_opened_at` set explicitly when the breaker opens |
| `utils/capacity_watchdog.py` | Already uses the `None` sentinel (fixed during authoring) |

**Direction of change: strictly more alerting.** The only behavioral difference
is that genuinely-stale loops now alert during the first hour of uptime instead
of being dropped. Throttling after a first alert is unchanged —
`test_throttles_repeat_alert` still passes untouched.

**Expected operational consequence, stated plainly:** if a loop has been stale
across restarts and nobody knew, **this will surface alerts that were previously
being swallowed.** That is the fix working, not a regression. The first hour
after deploying this may be noisier than usual, and that noise is the backlog of
signal the old code was discarding.

**Alert volume ceiling:** unchanged at one alert per loop per hour per replica
after the first — the cooldown still functions, it simply no longer starts in a
pre-triggered state.

**Money, ride state machine, dispatch, auth, PII:** untouched. Alert payloads
carry loop names and elapsed seconds only — no user data.

## 5. User-experience effect

- **Riders / drivers / corporate admins: none.** No endpoint, response, or copy
  changes.
- **Visible mid-session?** No.
- **Internal / on-call:** stale-loop alerts now arrive during the first hour of
  a process's life. Expect a possible one-off burst on first deploy representing
  previously-suppressed conditions; treat those as real findings and work them,
  rather than assuming the alerter is misfiring.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/loop_alert.py` | `_last_alerted.get(name, 0.0)` → `.get(name)` with an explicit `None` check; comment explaining the monotonic-origin trap | Restores alerting during the first hour of uptime |
| `backend/tests/test_loop_alert.py` | Added `test_posts_during_first_hour_of_uptime` (parametrized: 0.4/12/300/3599 s) and `test_failed_post_does_not_consume_the_cooldown`; annotated `_FAKE_NOW` with why its value hid the bug | Regression coverage, plus a note so the blind spot is not recreated |
| `docs/change-log/2026-08-07-loop-alert-cooldown-suppression.md` | This log | CLAUDE.md mandate |

## 7. Before / after

```python
# Before — 0.0 doubles as "never alerted" AND as a recent timestamp.
# For the first 3600 s of uptime this is true for every loop.
last_sent = _last_alerted.get(name, 0.0)
if now - last_sent < COOLDOWN_SECONDS:
    continue
```

```python
# After — "never alerted" is unambiguous
last_sent = _last_alerted.get(name)
if last_sent is not None and now - last_sent < COOLDOWN_SECONDS:
    continue
```

Behavior for a stale `surge_engine` loop on a process 12 seconds into its life:

```
Before:  monotonic()=12.0, last_sent=0.0 -> 12.0 < 3600 -> alert DROPPED
After:   monotonic()=12.0, last_sent=None            -> alert POSTED
```

## 8. Rollback plan

**Config lever (no redeploy), if the restored alerting proves too noisy:**

```bash
fly secrets unset ALERT_WEBHOOK_URL -a spinr-backend-yyz
```

This silences the alerter entirely — including the `capacity_watchdog`, which
shares the variable. Stated because it matters: this is a blunt lever, and
silencing a stale-loop alerter is silencing the detection of loops that are not
running. Prefer investigating the alerts.

**Code revert:** `git revert` this commit. No live data is written or migrated,
so a code revert is a complete rollback with no data-level remediation. Reverting
restores the hour-long blind window.

## 9. Verification performed

- [x] **Regression test proven to catch the bug.** The fix was temporarily
      reverted and the suite re-run: all 4 parametrized cases of
      `test_posts_during_first_hour_of_uptime` **failed** (6 passed), then
      passed again once restored. A regression test that has never seen the bug
      fail is not evidence, so this was checked rather than assumed.
- [x] **Blast-radius grep performed** — `_last_alerted` is module-private with
      one read site; searched the repo for the same monotonic-vs-zero-default
      pattern across `utils/`, `core/`, `services/`, `routes/`, `repositories/`
      and confirmed no other instance (table in §4).
- [x] **Automated tests run** (`backend/.venv`):
      - `tests/test_loop_alert.py` — **10 passed** (was 5; +5 from the new
        parametrized regression test and the failed-POST test)
      - Full suite prior to this commit: **10,004 passed, 8 skipped, 1 xfailed**
- [x] **Reviewed against CLAUDE.md conventions** — observability (error logged
      with the exception on POST failure, unchanged), no-PII (payloads carry
      loop names and elapsed seconds only), and the "do not silently swallow"
      rule, which this fix directly serves: the old behavior was a silent
      swallow of the alert itself.
- [ ] **Manual repro in staging** — not possible; no staging environment
      (ACTION_ITEMS E1).

## What was NOT verified

- **No alert has been observed arriving in the real Slack channel.** Posting is
  verified against a mocked `httpx.AsyncClient`; the webhook itself was never
  exercised end-to-end.
- **The predicted "burst of previously-suppressed alerts on first deploy" is a
  reasoned expectation, not an observation.** How many, if any, depends on
  whether loops have actually been going stale — which is exactly what nobody
  could see. It may be silent.
- **Not verified against a real Fly machine resuming from suspend**, which is
  the newly-common case that makes this bug matter more after this branch.
- **`COOLDOWN_SECONDS = 3600` was left unchanged** and not re-evaluated; only
  the sentinel was fixed.

## 10. Sign-off

- [x] Rollback plan is concrete, and its blunt side effect (also silencing the
      capacity watchdog) is stated rather than glossed
- [x] Blast radius is stated, not assumed — one read site, plus a repo-wide
      check for the same pattern with results tabulated
- [x] No silent behavior change — the change is strictly more alerting, and the
      expected one-off noise on deploy is called out in §4 and §5 so it is not
      mistaken for a malfunction
