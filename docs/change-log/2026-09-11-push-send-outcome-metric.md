# Change Impact & Risk Log — push-send delivery-outcome metric

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers (cross-cutting: pushes span dispatch/rides/wallet/admin) |
| PR / commit link | branch `mvapps/blissful-thompson-u14frt` |
| Related issue or gap ID | ACTION_ITEMS.md C97, recommendation #3 |

## 1. Issue / gap identified

C97 (driver-app push notifications reported "not visible") found that no
delivery-outcome metric exists for pushes at all — `spinr_dispatch_offer_sent_total`
is incremented at offer-*claim* time (`routes/rides/matching.py:1270`), not at
push-send outcome, so a silent FCM/Expo failure leaves zero observability trail.
The backend half of C97 (silent Firebase Admin SDK init failure) was already
fixed in a prior session; this closes the next item on that same investigation's
own "recommendations, in leverage order" list — the one that needed no ops
access and no iOS build to implement.

## 2. Root cause

Never built — `backend/features.py::_deliver_push_now`/`_send_expo_push` return
a bare `bool` to the caller and log at each outcome, but no counter was ever
incremented alongside those log lines.

## 3. Fix / remediation

Added `_record_push_outcome(outcome: str)` in `backend/features.py`, a thin
wrapper around `utils.metrics.inc("spinr_push_send_total", {"outcome": outcome})`,
and called it at every existing return/log point in `_deliver_push_now` and
`_send_expo_push`:

- FCM: `success` (delivered), `stale_token` (`NotFoundError`, token purged),
  `sdk_unavailable` (firebase_admin import failure), `failed` (any other
  exception).
- Expo: `success`, `stale_token` (`DeviceNotRegistered`), `failed` (any other
  non-ok response or exception).

No existing log line, return value, or control flow changed — this is purely
an additive counter increment alongside code that already existed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `backend/features.py`.** `_deliver_push_now` and
  `_send_expo_push` are called from within `send_push_notification` and
  `utils/push_retry.py`'s retry loop (which imports these same two functions
  from `features.py` — confirmed via grep, not a separate implementation) —
  neither caller's signature, return type, or behavior changed.
- `utils/metrics.inc` is a plain in-process dict increment under a lock —
  cannot raise, cannot block, cannot affect delivery. Confirmed by reading
  `utils/metrics.py`: no I/O, no exception path.
- No other code reads `spinr_push_send_total` today (grepped repo-wide) — this
  metric has no downstream consumer yet inside this repo; it becomes visible
  once scraped from `/metrics` (`utils/metrics.render_prometheus`), same as
  every other counter in this module.
- No PII in the label set — `outcome` is one of 4 fixed enum-like strings,
  never a token, user id, or message content.

## 5. User-experience effect

None. Backend-only observability addition; no rider, driver, or admin-facing
behavior changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | New `_record_push_outcome` helper; called at every `_deliver_push_now`/`_send_expo_push` return point | Emit the metric C97 recommendation #3 asked for |
| `backend/tests/test_p3_push_notifications.py` | New `TestPushSendOutcomeMetric` (4 tests: FCM success/stale_token/failed, Expo success) | Cover the new metric wiring |
| `docs/change-log/2026-09-11-push-send-outcome-metric.md` | New — this file | Change Impact Log |
| `ACTION_ITEMS.md` | C97 addendum | Mark recommendation #3 done |

## 7. Before / after

```
# Before
except firebase_exceptions.NotFoundError:
    logger.warning(f"Stale FCM token for user {user_id} (target_app={target_app}) — purging")
    try:
        ...
    return False
```

```
# After
except firebase_exceptions.NotFoundError:
    logger.warning(f"Stale FCM token for user {user_id} (target_app={target_app}) — purging")
    _record_push_outcome("stale_token")
    try:
        ...
    return False
```

## 8. Rollback plan

`git-revert-safe` — a plain revert removes the helper and its four call sites;
no data was written anywhere (the metric lives only in each replica's
in-process memory and is never persisted), so there is nothing to clean up.

## 9. Verification performed

- [x] Automated tests run — unit only: `pytest backend/tests/test_p3_push_notifications.py`
  (47/47 passed, including the 4 new tests), `pytest backend/tests/test_c_push_retry_atomic.py
  backend/tests/test_push_retry_coverage.py backend/tests/test_notification_preferences.py`
  (50/50 passed — confirms `utils/push_retry.py`'s re-import of these two
  functions is unaffected). `ruff check` clean; `ruff format` applied (one
  cosmetic reformat of the new test code, re-verified green after).
- [x] Blast-radius grep performed — confirmed `_deliver_push_now`/`_send_expo_push`
  have exactly one real implementation (`features.py`), re-exported (not
  duplicated) into `utils/push_retry.py`; confirmed `spinr_push_send_total`
  has no existing reader anywhere in the repo.
- [x] Reviewed against relevant CLAUDE.md conventions — Observability
  Conventions section explicitly calls for exactly this kind of metric
  ("State transitions → info log + metric"); dual-import pattern not
  applicable here (single-import `.utils` reference, matching this file's
  own existing local-import style for `.utils.background`).
- [x] Feature-flagged if user-visible and non-trivial (or justify why not) —
  not user-visible; a pure additive metric needs no flag.

## What was NOT verified

- Not tested against a real Firebase project or a real Expo push — every test
  mocks the SDK/HTTP boundary, matching this file's own existing convention
  (`TestNativePushDelivery`'s own docstring: "native delivery path... cannot
  be tested without live credentials").
- The `sdk_unavailable` outcome (firebase_admin import failure) has no
  automated test — simulating a genuine `ImportError` on an installed package
  reliably (without a fragile `sys.modules` deletion trick that could mask
  unrelated import failures in the same test run) was judged not worth the
  complexity for a defensive-only branch; covered by code review/reading
  instead. If this path matters enough to verify mechanically later, it needs
  a dedicated `importlib`-level test, not attempted here.
- Whether this metric is actually scraped anywhere (Prometheus/Grafana) —
  out of scope; this PR only makes the data available at `/metrics`, per the
  existing pattern every other counter in this module already follows.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data written)
- [x] Blast radius is stated, not assumed (isolated to `features.py`, one
  re-exporting caller confirmed unaffected)
- [x] No silent behavior change to an already-shipped flow — purely additive
