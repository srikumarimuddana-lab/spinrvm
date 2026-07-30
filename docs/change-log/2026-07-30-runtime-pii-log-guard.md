# Change Impact & Risk — runtime PII guard at the log sinks (T2)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** backend (logging) · **Risk:** medium — touches every log line in the process
**Related:** T1 (`cb6cc67`), T1c (`8530b88`)

---

## Issue / gap identified

T1 and T1c fixed the *call sites* that were leaking PII into stdout and Sentry.
Nothing prevented the next one. The only existing guard,
`.claude/hooks/pre-commit`'s "PII in logs" step, is a six-pattern **source-text**
denylist (`print.*lat.*lng`, `logger\.(info|debug).*coordinates`, …), and it
reported `✅ Clean` for the entire life of the T1 leak — because the leaking line
was `logger.info(f"… payload={update_data}")`, where none of `lat`, `lng` or
`coordinates` appears in the source and the values arrive at runtime inside a dict.
Source-text matching structurally cannot do this job.

Two further defects were found while building the guard, both pre-existing and both
worse than the gap being closed:

1. **`ai.pii.scrub_pii` corrupted observability data.** The separatorless phone
   pattern was `(?<!\d)\+?1?\d{10}(?!\d)` — any bare 10-digit run. Unix timestamps
   are 10 digits until the year 2286, so `ts=1769817600` became `ts=[PHONE]`, and
   `duration_ms=1234567890` became `duration_ms=[PHONE]`. This function is applied
   to Sentry event text by `utils/sentry_scrub`, so **production Sentry events have
   been having their timestamps and millisecond durations rewritten.**
2. **`scrub_pii` could not detect the shape that actually leaked.** A Python
   mapping repr — `payload={'lat': 52.1332, 'lng': -106.67}` — defeats *both*
   coordinate patterns. The labelled pattern needs `lat` immediately followed by
   `=` or `:`, but a dict repr puts a quote between them (`'lat':`). The bare-pair
   pattern needs the two numbers adjacent, but `, 'lng': ` sits between them. Found
   by writing a test for the exact T1 leak string and watching it fail.

## Root cause

The guard gap: privacy enforcement lived entirely at authoring time (a pre-commit
grep) with nothing at emission time, so correctness depended on every future
`logger.*` call being written carefully.

The two `scrub_pii` defects share a cause: the patterns were written against
*human-authored chat text* (the module's docstring says it exists for AI assistant
traffic), then reused for machine-generated log and Sentry text without revisiting
the assumptions. Chat messages do not contain unix timestamps, and they contain
coordinates as `52.13,-106.67`, not as `{'lat': 52.1332}`.

## Fix / remediation

**`utils/log_guard.py`** — installed once in `server.py` as a loguru
`configure(patcher=...)`, which covers **both** loguru sinks (the stderr JSON sink
and the loguru→Sentry bridge) from one place. Two mechanisms, because the two
carriers fail differently:

| Carrier | Mechanism | Why |
|---|---|---|
| `record["extra"]` | redact by **key name** | The stderr sink runs `serialize=True`, which emits `extra` as JSON, and loguru puts **keyword arguments** into `extra` — so `logger.info("msg {x}", x=phone)` ships the raw phone even when the rendered message is clean. Key-name redaction also catches coordinates split across `lat`/`lng` keys, which no value pattern can match. |
| `record["message"]` | redact by **value pattern**, behind a cheap screen | Applied narrowly, because rewriting log text is how observability dies. |

**It reports rather than launders.** Every redaction increments
`spinr_logging_pii_redacted_total{carrier=…}` and, **once per call site**, prints a
warning naming `module:function:line`. A guard that quietly cleans up after bad log
calls makes them permanent; the goal is to fix the call site.

**`ai/pii.py`** — two pattern fixes:

- Phone pattern is now NANP-aware: `(?<![\d+])(?:\+1?\d{10}|1\d{10}|[2-9]\d{9})(?!\d)`.
  The discriminator is exact — a NANP area code cannot begin with 0 or 1, and a
  plausible unix timestamp always begins with 1.
- New mapping-repr coordinate pattern, matching each axis **independently** rather
  than requiring a pair (adjacency is precisely the assumption that failed). Keeps
  the key in the output (`lat=[COORD]`) so the line stays debuggable. A lookbehind
  prevents `latency=12.34` and `flat=1.5` matching on a `lat` substring.

**`utils/pii.py`** — `SENSITIVE_KEY_RE` / `KEY_ALLOWLIST` / `is_sensitive_key()`
moved here from `utils/sentry_scrub.py`, which now re-exports them. There are three
consumers now, and "what counts as PII" must have one definition. The allowlist
gained observability keys (`domain`, `surface`, `trace_id`, `span_id`,
`correlation_id`, `idempotency_key`) so correlation context is never redacted.

## The audit table is deliberately NOT scrubbed

My earlier framing of this task said "all three log sinks" and counted `audit_logs`
as one. **That was wrong**, and `utils/audit_logger.py` now carries a docstring
explaining why, so nobody "finishes the job" later:

`audit_logs` is primary storage in the same Canadian-region Supabase project as
`users`, under the same RLS and the same retention and deletion rules. CLAUDE.md's
prohibition names "logs, Sentry events, or analytics payloads" — egress paths.
Recording that an admin changed a rider's phone number, including the value, *is*
the audit trail; redacting it yields rows proving something happened but not what,
which is worse than no audit trail because it resembles one. Saskatchewan retention
also pulls the other way: driver/vehicle linkage at trip time and insurance-period
transitions must be auditable for 7 years, and `[REDACTED]` cannot satisfy an SGI or
Privacy Commissioner request.

The real boundary is that a `details` blob must not be *echoed into a log line* —
enforced at that sink, not by weakening this one.

## Risk & impact on existing functionality

**Blast radius: every log line in the backend process.** That is the point, and it
is also the risk. Specifics:

- **Over-redaction of `extra`.** Any `extra={...}` key matching `SENSITIVE_KEY_RE`
  now becomes `[REDACTED]` in stdout **and** in Sentry. A key like `driver_name` in
  an existing structured log will change. `KEY_ALLOWLIST` protects the correlating
  IDs, with a test per key, but the denylist is substring-based and will catch
  keys not anticipated here.
- **Message rewriting.** A log line containing a labelled coordinate, email, NANP
  phone or Canadian postal code is altered. Money (`amount=12.50`), timestamps,
  durations, ratios and version strings are covered by explicit pass-through tests.
- **Consumers of `scrub_pii` beyond logging.** It is shared with the AI chat path
  (`routes/support.py`, `ai_messages` persistence) and `utils/sentry_scrub`. The
  phone fix makes it *strictly less* aggressive (timestamps stop being redacted);
  the new coordinate pattern makes it *more* aggressive on mapping reprs. AI chat
  text rarely contains a dict repr, and `keep_trip_pins=True` (bracketed
  `[lat,lng]`) is unaffected — that exemption is stashed before pattern
  substitution and restored after, verified by the existing `test_ai_pii.py` suite
  (30 tests, all passing).
- **`sentry_scrub` import surface changed** but its private names are re-exported,
  and its 39 tests pass unmodified.
- **No change to the stdlib `diag_logger` / `_goonline_logger` sinks.** They set
  `propagate = False` and bypass loguru entirely, so the patcher does not reach
  them. Audited their ~20 call sites: all emit IDs only (`user_id`, `driver_id`,
  `ride_id`, `client_id`, `msg_type`) or explicitly allowlisted summaries
  (`ride_reads.py:104-111` projects to `id`/`status`/`driver_id`). Currently clean,
  **but unguarded** — noted below.

## Performance

Measured, because this runs on every log line and the SLA table budgets 100–150 ms
for WS fan-out and location writes:

| Log line | Baseline | With guard | Δ |
|---|---:|---:|---:|
| clean message, no `extra` | 39.3 µs | 45.8 µs | +6.5 µs |
| clean message + 3 `extra` keys | 40.8 µs | 47.9 µs | +7.1 µs |
| message with a fare (screen hits, scrub no-ops) | 38.2 µs | 59.3 µs | **+21.0 µs** |
| `extra` needing redaction | 41.6 µs | 51.6 µs | +10.0 µs |

Worst case is +21 µs, when a money amount trips the screen's decimal branch and the
full scrub then finds nothing. Money amounts are common in these logs, so this path
is common. It was left deliberately: tightening the screen to 3+ decimal places
would skip a real 2-decimal coordinate (`52.13`), and 21 µs is ~0.01% of a 150 ms
budget. Detection wins over the microseconds.

The cheap screen is still worth keeping: full `scrub_pii` costs ~436 µs on a 2.4 KB
log line versus ~92 µs for the screen, so it matters most exactly where the scrub is
most expensive.

## User experience effect

**None.** No rider-, driver-, corporate-admin-, or internal-admin-facing behaviour
changes. No API response, DB write, ride state, or money path is touched. The only
observable difference is in backend log and Sentry output.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/log_guard.py` | New — the patcher, screen, key redaction, once-per-site reporting | Guard the sink, not just today's call sites |
| `backend/ai/pii.py` | NANP-aware phone pattern; new mapping-repr coordinate pattern | Stop corrupting timestamps; detect the shape that actually leaked |
| `backend/utils/pii.py` | `SENSITIVE_KEY_RE`, `KEY_ALLOWLIST`, `is_sensitive_key()` moved here; allowlist gained observability keys | One definition of "what is PII" for three consumers |
| `backend/utils/sentry_scrub.py` | Re-exports the above from `utils/pii` | De-duplicate without breaking its tests |
| `backend/utils/audit_logger.py` | Docstring recording why `details` is deliberately not scrubbed | Prevent a later "fix" destroying the audit trail |
| `backend/server.py` | `install(logger)` before the first `logger.add(...)` | Cover both sinks from one place |
| `backend/tests/test_log_guard.py` | New — 31 tests | Pin the guard, the pass-throughs, and the wiring |
| `backend/tests/test_ai_pii.py` | +4 test groups | Regression-pin both pattern fixes |

## Before / after

```python
# BEFORE — server.py: sinks added with no guard
logger.remove()
logger.add(sys.stderr, level="INFO", format="…", serialize=True)

# AFTER — guard installed first, so it covers this sink AND the Sentry bridge
logger.remove()
from utils.log_guard import install as _install_log_guard
_install_log_guard(logger)
logger.add(sys.stderr, level="INFO", format="…", serialize=True)
```

```
# The T1 leak shape, now caught at the sink:
- update_one executed: payload={'lat': 52.1332, 'lng': -106.67, 'name': 'Jane Doe'}
+ update_one executed: payload={lat=[COORD], lng=[COORD], 'name': 'Jane Doe'}

# A keyword argument, which renders clean but ships raw via extra (serialize=True):
- {"message": "rider lookup for +13065551234", "extra": {"phone": "+13065551234"}}
+ {"message": "rider lookup for [PHONE]",      "extra": {"phone": "[REDACTED]"}}

# And what must NOT change:
  ts=1769817600 ride_id=r1                        (was: ts=[PHONE] — corrupted)
  spinr_dispatch_offer_to_accept_duration_ms=1847 (unchanged)
  settled amount=12.50 tax=1.25                   (unchanged)
```

## Rollback plan

`git revert` is safe — logging emission and regex definitions only. No migration, no
data written, no runtime state or money path touched.

Two notes on partial rollback:

- Reverting **`utils/log_guard.py` + the `server.py` wiring** restores the previous
  behaviour exactly and is the right move if the guard causes log-volume or latency
  trouble. Do that rather than reverting `ai/pii.py`.
- Reverting **`ai/pii.py`** reintroduces timestamp corruption in Sentry, which is an
  active observability bug, not a neutral state. If the new coordinate pattern
  over-redacts, tighten that one pattern instead.

No feature flag: the guard is strictly safer than no guard, and shipping it dark
would only prolong the exposure it closes. If a flag is wanted, `install()` is a
single call site and trivially gate-able on `app_settings`.

## Verification performed

- **New tests:** 31 in `test_log_guard.py`, plus 4 groups added to
  `test_ai_pii.py` (30 total there). Driven through a **real loguru logger with
  `serialize=True`**, not the guard in isolation — which is what surfaced that
  `extra` carries keyword arguments.
- **Mutation-verified, five mutations, all caught** (after two rounds — see below):

  | Mutation | Caught by |
  |---|---|
  | Delete `_install_log_guard(logger)` from `server.py` | `test_server_actually_installs_the_guard_before_adding_sinks` |
  | Move `install()` to *after* the first `logger.add()` | same test (asserts line order) |
  | Stop redacting `extra` | 5 tests |
  | Stop redacting the message | 3 tests |
  | Delete the mapping-repr coordinate pattern | 1 test |
  | Revert the NANP phone fix | 5 tests |

- **The first mutation round found a real hole**, the same one T1c had: removing the
  `install()` call from `server.py` failed **nothing**, because every behavioural
  test installs the guard itself. Added an AST test asserting both that the call
  exists and that it precedes the first `logger.add()` — install order matters,
  since `configure(patcher=...)` after a sink is registered leaves that sink
  unguarded.
- **Anti-vacuity:** every redaction assertion is paired with a positive anchor
  (`ride_id` survives, `attempt` survives, "booking failed" survives), and there are
  8 parametrized pass-through cases asserting observability strings are returned
  **byte-identical**.
- **Never-breaks-logging:** hostile records (cyclic `extra`, a value whose `__repr__`
  raises, non-string message, non-string keys), 50-deep nesting, and a `scrub_pii`
  that raises — all must still emit the record.
- **Full suite:** `pytest -m "not slow"` → **5874 passed, 8 skipped, 1 xfailed,
  1 failed** (5835 before; +39). The one failure is the same pre-existing
  `test_compliance_reports.py` timestamp mismatch, proven unrelated in the T1 log.
- **Lint:** `ruff check` clean on all changed files except the pre-existing `S110`
  in `sentry_scrub.tags_from_log_extra`.
- **Performance measured**, table above.

## What was NOT verified

- **No production build applies** — backend-only; no mobile/admin code touched.
- **The stdlib sinks are audited but NOT guarded.** `diag_logger` and
  `_goonline_logger` set `propagate = False`, so loguru's patcher cannot reach them
  and neither can Sentry's `LoggingIntegration`. Their ~20 call sites were read and
  are ID-only today, but a future `diag_logger.info(f"...{row}")` would leak with no
  guard in front of it. Guarding them needs a `logging.Filter` on those two handlers
  — a small follow-up, deliberately not bundled here. This is the largest remaining
  gap in T2.
- **`SENSITIVE_KEY_RE` is a substring denylist and is inherently incomplete.** A new
  sensitive `extra` key whose name it does not match will pass. The `extra` guard is
  therefore best-effort hardening, not a proof.
- **Not tested against a real Sentry project or a real stderr collector.** All
  assertions are against an in-process `StringIO` sink. Whether the JSON shape the
  Fly/Railway aggregators parse is unchanged was reasoned about (the guard only
  substitutes string *values*, never adds or removes record keys) but not observed
  against either platform.
- **The performance numbers are single-process microbenchmarks** on this container,
  not measured under load or against the real p95s in the SLA table. No load test
  was run.
- **Historical exposure is untouched.** This stops future leaks; it says nothing
  about what already reached the log aggregators, which remains T4.
- **The report warning is emitted with `print()` to stderr**, deliberately (emitting
  a log record from inside the patcher would re-enter it), so it is *not* JSON-shaped
  the way the main sink's records are, and it is not routed to Sentry. If log
  ingestion requires strict JSON on every line, this line will look different — it is
  hand-formatted as JSON but does not go through loguru's serializer.
