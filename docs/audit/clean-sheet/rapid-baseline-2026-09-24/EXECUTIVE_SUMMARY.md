# Spinr rapid baseline audit — executive summary (2026-09-24)

**What this is.** A one-hour, read-only health check of the whole Spinr platform: eight specialist reviewers each spent 25 minutes on one area (security, capacity, dispatch, money, monitoring, the apps, safety, and the project's own history), then a second model re-checked a random sample of their claims and an architect turned the findings into ten rebuild moves. Nothing was changed in code, config, or data. This is a baseline, not the full multi-session programme; `DEFERRED.md` lists everything skipped.

**How much to trust it.** Every claim carries a label. VERIFIED means someone opened the code; INFERRED means reasoned from evidence; ASSUMED means it needs a human or a primary source. An independent verifier re-opened 5 of the 42 VERIFIED findings: **none were wrong, two had sloppy citations** (a wrong file path, evidence from docs rather than code). Those were corrected. With only five samples the margin is wide; treat the other 37 VERIFIED findings as "probably right, not double-checked."

## The ten findings that matter most

Ordered by how much damage they can do multiplied by how many other problems they unblock. Severity uses the audit framework's scale.

1. **Every web server also runs all 44 background housekeeping jobs.** (HIGH, VERIFIED, new) The code to split "serve requests" from "run jobs" exists but was never switched on in the deploy config, so each extra server adds database load instead of relieving it. One config line fixes it. *A3-001*
2. **The automated code-review safety net has been off for about two months.** (HIGH, VERIFIED, existing items C7/C9/C73) Claude review is disabled on cost grounds, the Codex reviewer went silent on 2026-07-30, and the list of checks that must pass before merging is stale, so one 34-file change to rides/payments/auth merged 47 seconds after it was opened. *A1 decay table*
3. **Two driver-earnings screens add up tips with floating-point maths.** (HIGH, VERIFIED, new, confirmed by the verifier) Every other line in the same file does it correctly. A driver's "total tips" can differ by cents from the sum of their receipts. A one-line fix each, plus a lint rule so it cannot recur. *MONEY-001*
4. **Whether Saskatchewan PST applies to fares has never been checked against the actual bulletin.** (HIGH, ASSUMED, existing item G9) Production charges GST only, on a verbal determination made three times. If PST does apply, every ride since mid-August has under-collected 6%. This is an accountant's question, not an engineering one. *MONEY-003 → ESCALATIONS E1*
5. **One shared secret both signs and verifies every login token.** (HIGH, VERIFIED, new) Anyone who obtains that secret from either hosting provider can mint an administrator token. The fix is a key pair with a rotation window and no forced logouts. Setting a separate OTP pepper is a zero-code improvement to do today. *SEC-A2-001*
6. **Driver SIN and emergency-contact encryption depend on a database extension the vendor says it will retire.** (HIGH; the retirement notice is INFERRED from a search snippet, the dependency is VERIFIED) Seven-year-retention records must not become unreadable on a vendor's timetable. First step is an inventory and a Vault-only key-rotation runbook. *SEC-A2-002*
7. **About 290 error logs in the dispatch and driver code reach Sentry with no "which part of the system" tag.** (HIGH, VERIFIED, existing item R4 but far larger than R4 describes) At 3 a.m. an on-call engineer cannot filter dispatch errors from everything else. No check stops new code from repeating this. *OBS-001, OBS-006*
8. **Three of the eight published performance promises are not measured at all, and none of the eight has an alert.** (HIGH, VERIFIED, partly existing CR-2026-008) Driver location writes, token refresh, and Stripe webhook timing have no metric. *OBS-002*
9. **Each driver GPS ping costs five or six round-trips to Redis; at ten times today's drivers this is the first thing that breaks.** (HIGH, INFERRED arithmetic — no load test has ever been run) Bandwidth is fine; the per-ping chatter and every server decoding every message are not. *A3-002*
10. **If Redis is down, a driver who just declined a ride can be offered the same ride again.** (MEDIUM, VERIFIED, new; the code's own comments admit it) The durable record of declines already exists in the database but is not consulted. *DISPATCH-002*

Also worth knowing: the Google Maps daily budget is a hard $5 cliff with no early warning (A3-004); the rider gets no signal during the 15-second window while a driver is deciding, although the design doc promises one (DISPATCH-001, a product decision); admin edits to driver records are last-write-wins with no conflict warning (CONCURRENCY-001); no vendor plan tier is recorded anywhere, so no capacity claim can be verified from the repo (A3-006).

## The five rebuild moves (plain language)

1. **Flip the job/server split on.** Config only, reversible by unsetting it, staging first, then one production machine at a time.
2. **Turn the review safety net back on, bounded by cost.** Re-enable automated review only for changes touching money, sign-in, dispatch, migrations and safety; refresh the required-checks list; forbid "continue on error" on gate jobs without a written exception.
3. **Make errors say where they came from and measure what we promise.** A CI check that blocks new untagged error logs, then backfill the ten highest-stakes files; add the three missing latency metrics and the first two alerts.
4. **Close the money-rounding class once.** Fix the two tip totals, extend the lint to catch the pattern, then retire the last floating-point payout column with a dual-write window so nothing is lost if it goes wrong.
5. **Harden identity and encryption in stages.** Key-pair token signing behind a flag with a 30-day dual-accept window; inventory and remove direct use of the retiring database extension; decide and record the certificate-pinning position in an ADR.

## What Spinr already does well (keep it)

- **Ride state machine and race handling.** Two canonical guards, atomic compare-and-set on every status write, a correct lost-race re-read, atomic scheduled dispatch, and a sequence-numbered replay when a phone reconnects.
- **Money discipline where it has been applied.** Decimal helpers, a mature nightly Stripe reconciliation, a blocking lint gate that was promoted by actually running the tool, and no "platform share" variable anywhere — 0% commission is not a setting that could quietly flip.
- **Login fundamentals.** Peppered, constant-time OTP checks with lockout; textbook refresh-token rotation with theft detection; the server refuses to start in production with weak or placeholder secrets.
- **App resilience.** One well-commented API client with de-duplicated token refresh, idempotent booking backed by a real database constraint, app-kill recovery with its own regression test, and a forced-upgrade gate that never strands a passenger mid-trip.
- **Safety.** SOS never auto-dials 911, cannot double-fire, works with an expired token, reports per-contact SMS delivery, and keeps personal data out of logs.
- **Written reasons.** The unusual choices (the 3.5-second fare wait, the retired presence sweeper, the accepted SOS database-outage risk) each have a dated explanation. That is rarer than it should be.

## What was NOT verified

No load test, dynamic test, lint run or test suite was executed. No live Sentry, Stripe, Supabase or Redis data was read. Every vendor limit is from a search snippet because vendor documentation sites were blocked. Every tax and regulatory statement is ASSUMED and listed in `ESCALATIONS.md`. Retention/deletion windows and insurance-period bookkeeping were in scope but not reached (`DEFERRED.md`). The rider and driver apps have no visual tooling, so every UI claim is reasoned from code, not seen. CLAUDE.md's "42 background loops" is stale: the registry holds 45 entries including the watchdog.

## Decisions needed from you

See `ESCALATIONS.md`. The two that cannot wait: send the PST question to an accountant with a deadline (E1), and decide how to restore an automated review gate for money/auth/dispatch changes (E12).
