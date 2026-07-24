# Critical Bugs — Pending Work Report

Companion to `CRITICAL_BUGS_IMPLEMENTATION_PLAN.md`. Status as of PR #2238
(branch `claude/critical-bugs-implementation-plan-3q1qa4`).

This is the "what's left" list: everything still open, why it matters, and what
blocks it. Items marked **needs a human** cannot be closed by a code change.

---

## 1. Done — merged into PR #2238 + follow-on commits

| WS | Findings | What shipped |
|---|---|---|
| WS-1 | 5, 6, 7 | Driver-cancel ownership guard (403) + `driver_id` in the atomic cancel filter. Closes the insurance-audit corruption as a side effect. |
| WS-2 | 9 | `get_active_ride` rider object projected to `_RIDER_PUBLIC_FIELDS` allowlist; `password_hash` / `fcm_token` / session state no longer reach the driver client. |
| WS-3 | 14 | `notify_safety_team` added to the fallback import branch — safety reports now actually page on-call. |
| WS-4 | 1 | `_CircuitBreaker.release_probe()` wired into the three `run_sync` bypass paths; a deadline-aborted probe can no longer wedge every DB call in 503. |
| WS-5 | 16 | Migration 248: `ride_debit` (master −amount, used +amount) replaces the mis-signed `apply_rollback`; new `ride_debit_reversal` for the compensation path. |
| WS-6 | 2, 3, 8, 10 | Migration 249 `wallet_apply_delta` (locked, idempotent, signed delta); admin credit/debit, no-show fee and cancellation fee all routed through it. |
| WS-7 | 4 | Migration 250: partial unique index `(driver_id) WHERE status IN ('reserved','pending','transfer_completed')`. Standard + instant payouts reserve before transfer; balance deducts reservations automatically. |
| WS-8 | 11 | Migration 251: `cancel_fee_payment_intent_id` column. All three cancel paths (rider, driver, search-timeout) call `cancel_authorization` when auth_status is authorized/fare_only, set auth_status='released', and store fee PI in the new column. |
| WS-9 | 12 | `le=500` cap on `RideRatingRequest.tip_amount`; dedupe guard (409 if already rated); payment-status guard (reject tip after settlement); `@idempotent_endpoint(scope="ride_rate")`; set-once tip semantics. |
| WS-10 | 13 | `_reestimate_fare_for_stops` now async, calls `calculate_all_fees` to recompute grand_total/tax/fees/driver_earnings/admin_earnings and refreshes `fare_breakdown_snapshot` atomically. WS payload includes `grand_total`. |
| WS-11 | 15 | Migration 252: `email_verified` + `email_verified_at` on users. `/corporate/join-domain` gated on `email_verified` (403 ERR_EMAIL_UNVERIFIED). Email change resets the flag. |
| WS-12 | C3 | Migration 253: `record_insurance_period_transition` RPC (atomic close+open, SECURITY DEFINER, period-3-requires-ride enforced in SQL). Python calls RPC instead of two-step UPDATE+INSERT. |

Twelve workstreams, all seventeen findings from the original audit plus C3.

---

## 2. Pending — code work, ordered by priority

### P0 — money loss or exploitable: ALL DONE

WS-7 through WS-10 are complete. See section 1.

### P1 — identity and compliance: ALL DONE

WS-11 and WS-12 are complete. See section 1.

### P2 — systemic hardening (the recurrence-prevention layer)

These clear whole classes rather than single findings. Counts are findings
touching each pattern across the full audit.

| WS | Pattern | Count | Central fix |
|---|---|---|---|
| **WS-13** | Swallowed / fail-open DB & auth errors | 57 | Classified `logger.warning` sweep → `logger.error` + 5xx; pre-commit guard requiring an explicit `# fail-open-reviewed:` annotation. |
| **WS-14** | Receipts / tax lines don't reconcile | 26 | Recompute tax at settlement; signed discount line item everywhere; `sum(line_items) == grand_total == amount_charged` invariant + metric. |
| **WS-15** | Filter/paginate after fetch; silent row caps | 16 | Push predicates into the query; emit `spinr_db_row_cap_hit_total` when a result equals its cap. |
| **WS-16** | Stripe idempotency key omits amount | 15 | Shared `stripe_idempotency_key(scope, entity, amount_cents, salt)` helper; sweep every `stripe.` call site. |
| **WS-17** | Raw GPS / PII in logs & analytics | 15 | Root-logger filter + Sentry `before_send` scrubber as the guaranteed floor; proxy third-party payloads through it. |
| **WS-18** | Missing ownership check / IDOR | 13 | `require_ride_party()` helper used both as the guard and as the atomic-filter predicate; parametrized non-owner test across every id-taking endpoint. |
| **WS-19** | Missing admin audit log on money/config | 13 | `@log_admin_action` decorator; audit-write failure fails the request for money actions. |
| **WS-20** | Background-loop replay hazards | 12 | Claim-before-side-effect ordering; `leader_lock(name, ttl)` helper asserting `ttl < interval` at registration. |

WS-13 and WS-18 are exploit-class and should start in parallel with the P0 items.

---

## 3. Pending — needs a human, not a code change

| # | Item | Why it can't be automated |
|---|---|---|
| **H-1** | **Corporate wallet balance correction (WS-5 backfill).** Historical `corporate_wallet_transactions` rows with `type='allowance_rollback'` and `notes LIKE 'ride:%:allowance'` are mis-signed; each overstates the master balance by 2× the fare. Reporting query is in the header of migration 248. | Customer-facing billing correction. Needs finance sign-off and a per-company reconciliation statement before any balance moves. Deliberately not automated. |
| **H-2** | **Grant semantics follow-up.** `allowance_grant` also debits master when raising a member's headroom, so grant-funded spend is now debited twice (at grant and at ride). Decision taken: fix grant separately, making it a pure limit raise. | Product/finance decision on what a "grant" means, plus a matching backfill of historical grants. |
| **H-3** | **`CODECOV_TOKEN` repo secret.** `backend-test` fails at its upload step with `Token length: 0` → `"Token required - not valid tokenless upload"`, even when pytest itself completes. | Repo settings. Alternatively make the upload non-blocking. |
| **H-4** | **`review` workflow credentials.** Fails with `Either ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, or workload identity federation ... is required`. | Repo secret. |
| **H-5** | **Dependency & container advisories.** `yarn audit`, `npm audit`, `pip-audit`, `G6 Trivy container scan` are red on surfaces this work doesn't touch. | Dependency upgrades with their own compatibility testing; out of scope per CLAUDE.md. |
| **H-6** | **Uncharged tips already in `driver_earnings`.** `/rate` has been accruing tips that were never collected, and those feed the T4A earnings snapshot. | Tax-adjacent correction; scope with finance alongside H-1. |

---

## 4. Pre-existing test-suite breakage (blocks confident verification)

**The backend suite is red independently of this work: 203 failed / 18 errors.**
Confirmed against commit `a8f0ee7`, which changed one Markdown file and no code —
it was already 201 failed / 18 errors there.

Dominant causes:

| Count | Signature |
|---|---|
| 27 + 6 | `AttributeError: module 'routes.rides' has no attribute 'corporate_allowance_service'` (and `_record_payment_event`) — monkeypatch targets that broke when `routes/rides.py` became the package `routes/rides/__init__.py` |
| 17 | `DatabaseError: Database operation failed` |
| 17 | `assert None is not None` |
| 9 | `TypeError: object MagicMock can't be used in 'await' expression` |
| 18 (all errors) | `tests/test_breadcrumb_persistence.py` collection failure |

**Why this matters beyond hygiene:** the corporate-settlement tests that should
have caught the WS-5 sign bug were themselves failing for unrelated reasons, and
they mocked `apply_rollback` so the sign was never exercised. A red suite is why
a live money leak survived. Repairing the `routes.rides` patch-target drift is
high-leverage and pairs naturally with WS-13.

Verification status of the new work: none of the 15 tests added in this PR appear
in any FAILED or ERROR line; 11 of the 15 have no skip path, so they passed.
Local `pytest` could not be run at all — `pypi.org` is outside this environment's
egress allowlist — so SQL and state-machine logic was validated by executing the
contract logic standalone.

---

## 5. Suggested sequencing

1. ~~**Now:** WS-7 → WS-8 → WS-9 → WS-10 (P0 money).~~ **DONE.**
2. **Now:** WS-13 and WS-18 sweeps (exploit-class, batched ~10 sites per commit).
3. ~~**Then:** WS-11, WS-12 (identity + insurance-period atomicity).~~ **DONE.**
4. **Then:** WS-14 through WS-17, WS-19, WS-20.
5. **Independently, and soon:** repair the `routes.rides` patch-target drift so the
   suite can gate anything at all; and close H-1/H-2 with finance.

Migration numbers 250–253 are now taken. Next free slot is **254** — re-verify with
`ls backend/migrations | sort -V | tail -1` before each merge.
