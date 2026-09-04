# 2026-09-04 — loguru convention gate: re-export blind spot (C65)

Bundled into PR #4909 (WS-1 Correctness) at the user's direction rather than
shipped separately.

## Issue/gap identified

`backend/tests/test_loguru_call_conventions.py` selected the files it checks
with a substring test — `if "from loguru import logger" in src`. Modules that
obtain loguru's `logger` from a package-local re-export never matched, so they
were never scanned. All 12 `backend/routes/rides/*` modules (221 logger calls,
including the entire dispatch path) were in that blind spot, and **161 of those
calls carried one of the two defects the gate exists to prevent** while CI
stayed green.

## Root cause

The selector tested for a *source string*, not for what `logger` is actually
bound to. `routes/rides/*` does `from ._deps import logger`, and
`routes/rides/_deps.py` is what does `from loguru import logger`. The gate's own
non-vacuity guard (`>20 modules / >200 calls`) passed comfortably on the
39 modules it did see, so nothing signalled the gap.

Two consequences worth stating, because both are counter-intuitive:

1. **A comment was a selector.** Under the substring rule, merely *mentioning*
   the import line in a comment pulled a file into the scan. That happened once
   during PR #4909 and turned the gate red.
2. **The obvious widening would have been wrong.** "Scan anything that isn't
   obviously stdlib" breaks, because the two re-export hubs are different:

   | module | binding | flavor |
   |---|---|---|
   | `routes/rides/_deps.py` | `from loguru import logger` | loguru |
   | `routes/drivers/_deps.py` | `logger = logging.getLogger(__name__)` | **stdlib** |

   The 13 `routes/drivers/*` modules are stdlib. Their `exc_info=` and `%s`
   calls are correct, and "fixing" them would have been the actual regression.

## Fix/remediation

**Selector.** `_loguru_modules()` now resolves each module's `logger` binding
through the AST (`_logger_flavor`), following `from ._deps import logger` to the
defining module, with a cycle guard. It also understands
`from loguru import logger as _raw` + `logger = _raw.bind(...)`. A mention in a
comment or docstring is no longer a selector.

**Recurrence guard.** `test_every_logger_binding_resolves` fails on any module
that calls `logger.*` with a binding the resolver cannot classify. An
unrecognised binding used to mean "silently not scanned" — that is the entire
mechanism of this bug, so it now means a failing test. It earned its keep on the
first run by finding a third shape (`utils/outbox_worker.py`), which the old
substring rule had been matching only by accident.

**Edge pinning.** `test_scan_covers_reexported_loggers_and_only_those` asserts
`routes/rides/*` is scanned and `routes/drivers/*` is not, so neither the blind
spot nor its overcorrection can return silently.

**Call sites.** 161 fixes across 10 files, applied by an AST-guided script that
splices per-call rewrites (preserving formatting and comments) and re-parses
every rewritten literal to confirm it round-trips:

- 78 × `logger.<lvl>(..., exc_info=True)` → `logger.opt(exception=True).<lvl>(...)`
- 83 × `%`-style message → `{}`

## Risk & impact on existing functionality

**This is log-text and log-metadata only. No control flow, no state machine, no
money path, no DB write, and no HTTP response changed.** Every rewrite is
confined to the arguments of a `logger.*` call.

Blast radius — who else can *observe* these log calls:

| Consumer | Affected? |
|---|---|
| `caplog`-based tests (47 files) | **No.** Loguru does not propagate to stdlib `logging` — there is no bridge in `conftest.py`, and the suite documents this itself (`test_rides_matching_coverage.py:184`, `test_p2_sos.py:421`). No caplog test can see `routes/rides` output at all. |
| `tests/test_p2_sos.py` (3 × `patch(".../safety.logger")`) | **One assertion.** `safety.py:442` moved onto the `.opt()` proxy, so its call no longer lands on `mock_logger.error`. Assertion repointed to `mock_logger.opt.return_value.error`. The other two sites (`safety.py:428/435`) are unchanged and stay on `.error`. |
| `tests/test_dispatch_claim_parity.py` (`patch.object(matching.logger, "error")`) | **No.** Both asserted sites (`matching.py:883` "direct pool is not open", `matching.py:1013` "Period 2 transition write failed") are plain `logger.error(` — neither carried `exc_info`. |
| `tests/test_rides_payments_coverage.py` (`patch(".../payments.logger")`) | **No.** The asserted `LATE_TIP_ABSORPTION_THRESHOLD` call (`payments.py:104`) carried no `exc_info`. |
| Log-based alerting / dashboards | **None found.** No runbook or ADR greps these message strings; alerting is on the `spinr_*` Prometheus metrics, which are untouched. |

Direction of change is strictly additive in information: every rewritten line
previously either printed a literal `%s` and **dropped its arguments entirely**,
or reached Sentry as a stack-less `capture_message`. Both now render.

`%d` → `{}` is output-identical: every `%d` argument is a `len()` or a loop
index. `%.3f` → `{:.3f}` deliberately keeps the format spec, so the shadow
fare-distance line still logs 3 decimals rather than full float repr.

## User experience effect

None. No rider-, driver-, corporate-admin- or internal-admin-facing surface
changes. The effect is on operators reading logs and Sentry: dispatch, booking,
cancellation and SOS error logs now carry their arguments and their tracebacks.

## Files modified

| file path | what changed | why |
|---|---|---|
| `backend/tests/test_loguru_call_conventions.py` | Substring selector → AST binding resolver; +3 tests (`test_every_logger_binding_resolves`, `test_scan_covers_reexported_loggers_and_only_those`, thresholds raised) | The gate's selector was the defect |
| `backend/routes/rides/matching.py` | 20 `exc_info`, 20 `%` | Dispatch hot path — largest offender |
| `backend/routes/rides/booking.py` | 9 `exc_info`, 20 `%` | |
| `backend/routes/rides/cancellation.py` | 7 `exc_info`, 12 `%` | |
| `backend/routes/rides/estimates.py` | 2 `exc_info`, 18 `%` | Includes the `%.3f` → `{:.3f}` shadow fare-distance line |
| `backend/routes/rides/safety.py` | 18 `exc_info` | SOS error paths |
| `backend/routes/rides/lifecycle.py` | 6 `exc_info`, 8 `%` | |
| `backend/routes/rides/queries.py` | 7 `exc_info` | |
| `backend/routes/rides/payments.py` | 6 `exc_info`, 3 `%` | |
| `backend/routes/rides/_shared.py` | 2 `exc_info`, 2 `%` | |
| `backend/routes/rides/lost_found.py` | 1 `exc_info` | |
| `backend/tests/test_p2_sos.py` | One assertion repointed to the `.opt()` proxy | `safety.py:442` now attaches a traceback |
| `ACTION_ITEMS.md` | C65 closed; C69 filed | |

## Before/after snippet

```python
# before — emits the literal "%s", drops ride_id and the exception, and reaches
# Sentry with no stack
logger.error(
    "[CANCEL] cancellation-fee write failed after the cancel was "
    "persisted for ride %s; releasing the driver anyway — fee needs "
    "reconciliation: %s",
    ride_id,
    getattr(_fee_exc, "details", {}).get("original", _fee_exc) if hasattr(_fee_exc, "details") else _fee_exc,
    exc_info=True,
)

# after
logger.opt(exception=True).error(
    "[CANCEL] cancellation-fee write failed after the cancel was "
    "persisted for ride {}; releasing the driver anyway — fee needs "
    "reconciliation: {}",
    ride_id,
    getattr(_fee_exc, "details", {}).get("original", _fee_exc) if hasattr(_fee_exc, "details") else _fee_exc,
)
```

## Rollback plan

`git revert` is a complete rollback here, with no caveat — unusually for this
repo. Nothing in this change touches live data, Stripe, wallet deltas, or ride
state, and there is no migration and no flag to unset. Reverting restores the
previous log text exactly.

Partial rollback is also safe: reverting only
`backend/tests/test_loguru_call_conventions.py` returns the gate to its narrow
selector and leaves the 161 corrected call sites in place (they satisfy both the
old and the new gate).

## Verification performed

- The widened gate was executed against the **pre-fix** tree (`git stash` on
  `backend/routes/rides`) and **fails** both detectors there, listing the
  offenders. It is therefore not vacuously green.
- All 7 tests in the gate pass on the fixed tree. Scan coverage went 39 → 50
  modules and ~560 → 722 calls; the diff of the scanned set is exactly the
  12 `routes/rides/*` modules added, **none removed**, and no module binding
  `logging.getLogger` was pulled in.
- `ruff check` clean on all 12 modified files; `python3 -m py_compile` clean.
- The transformer verified each rewritten string literal by re-parsing it and
  asserting the resulting value equals the substituted original; it reported
  zero skips (no arity mismatch, no pre-existing braces, no `%%`) and zero
  unmapped conversion specifiers.
- Blast radius established by enumerating *every* mechanism that can observe a
  log call — the 5 `logger`-mock sites and the 47 `caplog` files — and reading
  each candidate, not just grepping for it.

## What was NOT verified

- **`pytest` was never run.** PyPI is blocked from this environment
  (`403 Host not in allowlist`), so the suite cannot be installed. The loguru
  gate itself is pure `ast` + `re` + `pathlib` and was executed directly through
  a minimal `pytest` stub; **every other test named in this document was
  reasoned about statically, not run.** In particular the repointed
  `test_p2_sos.py` assertion is verified by construction
  (`MagicMock.opt(...)` returns `opt.return_value` for any arguments), not by
  execution. CI is the first real run.
- No staging or production log output was inspected; the claim that lines now
  render their arguments rests on loguru's documented `str.format` behaviour
  plus the static scan, not on an observed log line.
- Sentry was not exercised. That `logger.opt(exception=True)` makes the
  `server.py` bridge attach a stack is read from that bridge's
  `record["exception"]` branch, not confirmed against a captured event.
- No frontend surface is touched, so the standing gap around visual-regression
  tooling does not apply here.
