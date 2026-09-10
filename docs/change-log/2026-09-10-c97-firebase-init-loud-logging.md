# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code session |
| Surface(s) | backend (`core/security.py`) |
| Domain (Sentry tag) | admin (closest fit — init/observability, not a request-path domain) |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | `ACTION_ITEMS.md` C97 (audit: `docs/audit/2026-09-10-driver-app-notification-delivery-audit.md`) |

## 1. Issue / gap identified

`init_firebase()`'s no-service-account-JSON (Application Default Credentials) branch had its own
local `except Exception: pass` — a failure there was completely silent: no log line, no startup
failure, nothing. On a non-GCP host (this backend runs on Fly.io and Railway, both non-GCP) with
`FIREBASE_SERVICE_ACCOUNT_JSON` unset, ADC reliably fails to resolve, so the app would boot
looking fully healthy while every FCM push notification silently failed from that point on.

## 2. Root cause

Found during the C97 driver-app-notifications audit: two separate `except` blocks exist in
`init_firebase()` (`core/security.py`). The outer one (function-level) already logs loudly via
`logger.error(..., exc_info=True)`. The inner one, specific to the ADC fallback branch, caught
and discarded the exception before it could ever reach the outer handler — so that specific
failure mode never produced any signal at all, unlike every other failure branch in the same
function (invalid JSON, malformed service-account dict, a genuine non-`ValueError` failure on the
configured path all log correctly).

## 3. Fix / remediation

Replaced the inner `except Exception: pass  # noqa: S110` with a `logger.error(...)` call
(matching the outer handler's message, extended to name which specific condition triggered it)
and `exc_info=True` for a full traceback. The function's "must not raise" contract is unchanged —
this only adds a log line, it does not propagate the exception or crash app startup. That's a
deliberate, narrower scope than the audit's "ideally" suggestion of a hard production fail-fast
(matching the existing `JWT_SECRET`/data-residency pattern in `config.py`) — a startup crash is a
bigger behavioral change with its own risk profile and was left as a separate, not-yet-approved
follow-up rather than bundled into this fix.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one `except` block in one function.** `init_firebase()` is called
  from exactly two places: `server.py:157` (app startup) and `worker.py:113` (background worker
  startup) — both already call it unconditionally and already tolerate it never raising; this
  fix doesn't change that contract, only adds a log line on an already-existing failure path.
- **What else reads/writes this?** Nothing — `init_firebase()` has no return value and no side
  effect beyond calling into the Firebase Admin SDK and now logging. No caller inspects its
  behavior beyond "call it, don't crash."
- **Could this regress a working flow?** No — the only observable change is a new ERROR-level log
  line appearing in a scenario that previously produced zero output. On a correctly-configured
  host (valid `FIREBASE_SERVICE_ACCOUNT_JSON`, or genuinely-working ADC in a GCP-hosted
  environment) this branch's `except` never fires, so nothing changes there.
- **Interaction with the push-notification send path:** none directly — this only affects
  whether initialization *failure* is observable, not whether initialization succeeds or how
  sends behave.

## 5. User-experience effect

None directly user-facing. Operationally: ops/on-call now gets a log signal for a failure mode
that previously had none — this is the intended effect, not a side effect to disclose as a risk.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/security.py` | Replaced silent `except Exception: pass` with `logger.error(..., exc_info=True)` in the ADC-fallback branch of `init_firebase()` | Close the C97 audit's highest-leverage backend finding — this failure mode previously produced zero log output |
| `backend/tests/test_core_security_coverage.py` | Renamed/rewrote `test_unconfigured_initialize_app_failure_is_swallowed_by_local_bare_except` → `test_unconfigured_initialize_app_failure_is_logged_loudly`, flipped its assertion from "not in caplog.text" to "in caplog.text"; updated the module docstring's description of this branch | The old test explicitly pinned the silent behavior as correct — it needed to flip along with the fix, not just stay green by accident |

## 7. Before / after

```python
# Before
        else:
            try:
                firebase_admin.initialize_app()
            except Exception:  # noqa: S110
                pass
```

```python
# After
        else:
            try:
                firebase_admin.initialize_app()
            except Exception:
                logger.error(
                    "Firebase initialization failed (no FIREBASE_SERVICE_ACCOUNT_JSON "
                    "set, and Application Default Credentials are also unavailable) — "
                    "all FCM pushes will be silently dropped",
                    exc_info=True,
                )
```

## 8. Rollback plan

`git revert`-safe. Reverting restores the prior silent-swallow behavior exactly — no live data,
no running process state, no already-triggered deploy is affected either way; this is a pure
logging-behavior change.

## 9. Verification performed

- [x] `python3 -m py_compile` on both edited files — syntactically valid.
- [x] Manually simulated both the fixed branch (ADC failure → now logs `"Firebase initialization
      failed"`) and an adjacent still-silent branch (`ValueError` "already initialized" on the
      configured/certificate path → still silent, unchanged) by stubbing `firebase_admin` and
      importing `core/security.py` directly — both behaved as intended.
- [x] Re-read the existing test file's other 6 test cases (`test_configured_*`,
      `test_unconfigured_happy_path_*`) to confirm none of them touch the branch this fix
      changes — none do, only the one renamed test needed updating.
- [ ] **Full pytest run** — not available in this sandboxed session (no `pytest`/`firebase_admin`
      installed, same disclosed gap as every other backend change this session). This PR's own
      `backend-test` CI run is the first real execution of the updated test file.

## What was NOT verified

- **Real pytest execution of the updated/renamed test** — verified the underlying logic
  manually (see above) since no test runner is available in this session; CI is the real
  verification.
- **Whether Sentry actually receives this new ERROR-level log line** via the loguru→Sentry
  bridge this repo documents (`core/security.py` uses stdlib `logging`, not loguru — the bridge's
  applicability to stdlib `logging.error` calls specifically was not re-verified here; if it only
  hooks loguru, this log reaches application logs but not Sentry, which would be a natural
  follow-up, not blocking this fix).
- **Whether `FIREBASE_SERVICE_ACCOUNT_JSON` is actually set/valid on Fly and/or Railway today** —
  this fix makes the failure observable if it's happening; it does not itself confirm or rule out
  whether it currently is. That's the audit's separate "ops check" recommendation, unchanged by
  this PR.
