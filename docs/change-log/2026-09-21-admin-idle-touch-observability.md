# Change Impact & Risk Log — admin idle-timeout failures become ERROR + metric

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session, on the repository owner's request |
| Surface(s) | backend |
| Domain (Sentry tag) | `auth` |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review finding **E3**; still-open Major #8 from the 09-15 review |

## 1. Issue / gap identified

`_verify_admin_payload` has two best-effort branches around the 30-minute admin idle timeout, and
both ended in `logger.warning(...)` and carried on:

```python
except Exception as _ts_err:
    logger.warning(f"Malformed last_activity_at for staff {user_id} — letting through: {_ts_err}")
...
except Exception as _upd_err:
    logger.warning(f"Could not update last_activity_at for staff {user_id}: {_upd_err}")
```

Neither is a recoverable anomaly:

- **Branch 1** — if `last_activity_at` fails to parse, the `> _IDLE_SECONDS` comparison never runs.
  The idle timeout is **silently disabled** for that request. A security control turns itself off
  and says so at `warning`.
- **Branch 2** — a DB write failure on the admin auth path, which CLAUDE.md's "Do not silently
  swallow errors" section states explicitly must never be `logger.warning(...)`-and-continue.

`server.py:674` sets the Sentry `event_level="ERROR"` threshold, so **both sat below it and
produced no signal anywhere** — no Sentry event, no metric, nothing to alert on. Migration 233
documents this exact pattern as a real past incident.

## 2. Root cause

Both branches were written for their *local* failure ("don't break this request") and the log level
was chosen to match that intent — the request genuinely does survive. What was missed is that the
*consequence* is neither local nor recoverable: branch 1 leaves a session that never expires, and
branch 2 is cumulative — `last_activity_at` stops advancing, so every later request measures
idleness against an ever-older timestamp and the operator gets logged out ~30 minutes after the
writes started failing, **while actively working**. Without a signal that reads as a random logout
bug, not as a DB fault.

## 3. Fix / remediation

Level and observability only — **the let-through behaviour is unchanged and deliberate**:

- `logger.warning(...)` → `logger.bind(domain="auth", user_id=user_id).opt(exception=True).error(...)`
  on both branches, so a real traceback is captured and the event crosses the Sentry threshold
  **with a usable `domain` tag**.
- Both increment `spinr_auth_admin_idle_touch_failed_total{reason=malformed_timestamp|touch_write_failed}`,
  so the failure is countable/alertable even where nobody reads the log.

**The `.bind()` is load-bearing, not decoration.** `server.py`'s loguru→Sentry sink promotes tags
only out of `record["extra"]` (via `tags_from_log_extra()` in `utils/sentry_scrub.py`). `surface`
and `env` are stamped automatically, but `domain` is not — `dependencies/__init__.py` had zero
`logger.bind(` calls. Without the bind, raising the level to ERROR would have produced exactly the
untriageable, domain-less Sentry event that CLAUDE.md's Sentry-tag rule exists to prevent,
defeating the purpose of the change. `user_id` is bound too, so it becomes a filterable tag rather
than only appearing inside the message string; it is an ID, which PIPEDA sanctions as the
*replacement* for name/email. This was caught by `spinr-observability-reviewer` on the real diff,
not by me — the first draft had the level fixed and the tag missing.

**Domain is `auth`, not `admin`** — and the metric name matches it, deliberately. Both values are
legal in CLAUDE.md's list and the first draft of this log hedged "auth / admin" without deciding.
The event is an *authentication/session control* failing to enforce, which makes
`spinr_auth_otp_lockout_total` its closest existing sibling; the `admin_` kept inside the metric
name preserves the scoping information that it only ever fires in the admin staff branch. Tagging
one `auth` and naming the other `admin_` would have been its own future confusion.

`.opt(exception=True)`, not `exc_info=True`: this module logs via loguru, which has no `exc_info`
parameter — it is swallowed as a `str.format` keyword and no traceback is ever captured
(CLAUDE.md Observability Conventions; statically gated by `tests/test_loguru_call_conventions.py`,
which my calls pass — `{}` placeholders, no `exc_info=`, no `extra=`).

**Why not also fail closed (reject the request)?** Because the failure modes argue the other way.
A bad column value or one failed touch costing an operator the dashboard *during an incident* is a
worse outcome than a late idle timeout, and an attacker cannot induce either branch — both require
either a corrupt `admin_staff` row or a DB fault. The control that actually matters for a stolen
token (`token_version`, checked just above) is untouched and still hard-fails.

**Alternative considered:** dedupe the ERROR to once-per-staff-per-window to bound Sentry volume.
Rejected as speculative (CLAUDE.md "simplicity first") — see §4 for the volume analysis that makes
it unnecessary.

## 4. Risk & impact on existing functionality

**No behaviour change.** No status code, no control flow, no DB access pattern, no response shape
is altered. The diff is two log levels, two log-call forms, one new import, and two counter
increments.

**Blast radius — `_verify_admin_payload` has exactly two production callers**, both of which get
identical behaviour:

| Caller | Path |
|---|---|
| `dependencies/__init__.py:566` (`get_current_user`) | every admin HTTP request |
| `routes/websocket.py:633` | admin WebSocket auth |

Everything else that mentions the function only references the `_admin_verified` marker it sets
(`ai/mcp_server.py:92`, `documents.py:1305`, `routes/admin/auth.py`) — none call it, none read the
log or the metric.

**New metric has no consumers** — `spinr_auth_admin_idle_touch_failed_total` is new, so no dashboard
or alert can break. Name follows the `spinr_<domain>_<metric>_<unit>` convention with `_total` for a
counter.

**Sentry volume (the one real risk, and why it is acceptable):**
- `touch_write_failed` is bounded by the existing 60-second write-coalescing window
  (`_ACTIVITY_TOUCH_INTERVAL_S`), so it is at most ~1 event/minute/admin — and only while the DB is
  failing, when far louder alarms are already firing.
- `malformed_timestamp` is *not* rate-limited and would fire on every request for as long as the
  bad row persists. Accepted because the branch is near-unreachable in practice: the only writer is
  `datetime.now(timezone.utc).isoformat()`, which cannot produce an unparseable value, and NULL is
  already handled by the `if last_active_raw:` guard above. Reaching it requires a hand-edited row
  or a bad migration default — precisely the case where a flood of ERRORs is the correct outcome.

**PII:** logs carry `user_id` only — no email, no name. `email` is present in the payload and is
deliberately not logged (CLAUDE.md PIPEDA: "Full names / Email addresses — use user_id").

## 5. User-experience effect

None for riders, drivers, corporate admins, or internal admins. Nothing is visible mid-session to
anyone using the app; an admin whose touch write fails sees exactly what they saw before.
The change is visible only to whoever watches Sentry and the metrics endpoint.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/dependencies/__init__.py` | 2 × `logger.warning` → `logger.opt(exception=True).error` + metric; `_metric_inc` import added to both dual-import branches | E3 |
| `backend/tests/test_admin_idle_touch_observability.py` | new — 7 tests | pins level, metric, and unchanged let-through |
| `docs/change-log/2026-09-21-admin-idle-touch-observability.md` | this file | |

## 7. Before / after

**Before** — a `warning`, below the Sentry threshold, with no traceback and no counter:

```python
except Exception as _upd_err:
    logger.warning(f"Could not update last_activity_at for staff {user_id}: {_upd_err}")
```

**After** — ERROR with a real traceback, plus a countable signal:

```python
except Exception as _upd_err:
    logger.bind(domain="auth", user_id=user_id).opt(exception=True).error(
        "[auth] last_activity_at touch failed for staff {} — idle-timeout state is now stale: {}",
        user_id,
        _upd_err,
    )
    _metric_inc("spinr_auth_admin_idle_touch_failed_total", {"reason": "touch_write_failed"})
```

## 8. Rollback plan

`git revert` + redeploy. Nothing is persisted, migrated, or written to any table by this diff; no
settings row, no schema, no feature flag. Reverting restores the previous log levels exactly, and
because behaviour is unchanged there is no state to reconcile — a revert cannot leave anything
half-applied. The new metric simply stops being emitted; nothing consumes it yet.

## 9. Verification performed

- `ruff check` and `ruff format --check` clean on both changed files.
- `python3 -m py_compile` clean on both.
- Verified `logger` in `dependencies/__init__.py` is **loguru** (`from loguru import logger`,
  line 18), which is what makes `.opt(exception=True)` mandatory rather than stylistic.
- Verified my log calls satisfy all three `test_loguru_call_conventions.py` rules by reading the
  scanner: `{}` placeholders (not `%s`), no `exc_info=`, no `extra=`.
- Traced the exact code path the tests exercise to confirm the `assert not spy.warning.called`
  assertion is sound: a payload with `role="operations"` (in `ADMIN_STAFF_ROLES`,
  `dependencies/__init__.py:258`) and neither `break_glass` nor `user_id == "break-glass"` reaches
  the staff `else:` branch, and `redis_get` is patched so the denylist's own `logger.error` at
  `:300` never fires. The new call is the only `logger.*` on that path.
- Confirmed the metric import resolves to the **same module object** the test reads: `conftest.py`
  (lines 71–165) deliberately mirrors bare `sys.modules` keys to qualified ones, which is why
  existing tests (`test_dispatch_push_batch.py:104`, `test_p3_push_notifications.py:505`) read
  counters via `from backend.utils import metrics` for code that imported `utils.metrics`. Without
  that mirroring the two would be separate modules with separate counter dicts and the assertions
  would silently read zero.
- Blast radius enumerated by grep (§4), not assumed.

## 10. What was NOT verified — read this before merging

- **The tests were not executed.** `pytest` is not installed in this environment and PyPI is
  blocked by policy (`pip install pytest` → "Could not find a version that satisfies the
  requirement"), exactly as npm is. **CI is the first thing that will actually run them.** They
  were reasoned through against the real code path (§9) and are `ruff`-clean and compile-clean,
  but "compiles and lints" is not "passes".
- No integration or staging check. The ERROR log reaching Sentry, and the counter appearing in
  `/metrics` exposition, are inferred from `server.py:674`'s `event_level="ERROR"` and the existing
  `render_prometheus` path — not observed.
- The malformed-timestamp branch was not reproduced against a real Postgres row; the test induces
  it with a literal `"not-a-timestamp"` string through a mocked `get_rows`.
- Sentry volume in §4 is an argument from the code, not a measurement — there is no production
  data here on how often either branch actually fires.
- This is a backend-only change: no `admin-dashboard` build, and none of the 6 seeded
  visual-regression baselines are touched.

## 10b. Reviewer findings and how each was handled

`spinr-observability-reviewer` was run against the real diff before commit (CLAUDE.md gate 10).
Verdict **OBSERVABLE**, no blockers. Its findings:

| Finding | Action |
|---|---|
| ERROR reaches Sentry with **no `domain` tag** — the sink only promotes `record["extra"]` | **Fixed** — `.bind(domain="auth", user_id=user_id)` on both calls (§3) |
| Metric domain `admin` vs `auth` was never actually decided, just written down twice | **Fixed** — committed to `auth`, metric renamed to match (§3) |
| Should this also write an `admin_audit_log` row? | **Not done, deliberately.** Argued as debatable, not a blocker: nobody was denied, no RLS fired, no admin took an action — this is an infrastructure fault *in* a control, not one of CLAUDE.md's three named security-relevant events. Recorded here so a follow-up can decide with the argument in hand rather than rediscovering it. |
| Level ERROR vs WARNING | **Confirmed ERROR.** The specific "never `logger.warning`-and-continue on a DB/auth error" rule beats the general degraded-but-recovered row; these are DB reads/writes on the auth path with open-ended consequences, not bounded self-healing anomalies. |
| PII audit | **Clean.** Only `user_id`. `diagnose=False` is set globally (`server.py`) and pinned by `test_loguru_sink_frame_vars.py`, so the traceback cannot leak local frame values — which matters here, because this function handles the decoded JWT payload a few lines up. |

## 11. Noted, not changed (out of scope)

- `backend/dependencies/__init__.py`'s dual-import **try branch is dead**. The file is
  `dependencies/__init__.py`, so `from . import db_supabase` resolves to
  `backend.dependencies.db_supabase`, which does not exist (the package holds only `__init__.py`
  and `company_guard.py`) — the `except ImportError` fallback always runs. It works because
  `backend/` is on `sys.path` in both run modes, so this is latent, not broken. The correct
  spelling would be `..`, as `routes/rides/_deps.py` uses `...utils.metrics`. **I matched the
  file's existing (dead) one-dot style rather than fixing it**, per CLAUDE.md's surgical-changes
  rule — a one-file import-depth correction is its own change with its own blast radius, not
  something to smuggle into a logging fix.
- **The same defect class survives twice more in this very function**, flagged by the reviewer and
  left alone as out of scope: the Redis revocation-denylist fail-open handler
  (`dependencies/__init__.py:299-304`) and the break-glass allowlist fail-closed handler (`:321-325`)
  are both already at the correct ERROR level, but neither uses `.opt(exception=True)` (so no
  traceback reaches Sentry — they arrive as `capture_message`, stackless) and neither binds a
  `domain`. E3 was scoped to the two idle-timeout branches, and widening it mid-change would breach
  the surgical-changes rule — but a reviewer should know the sweep is not complete.
