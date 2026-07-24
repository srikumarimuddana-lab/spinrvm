# Critical Bugs — Pending Work Report

Companion to `CRITICAL_BUGS_IMPLEMENTATION_PLAN.md`. Status as of PR #2238
(branch `claude/critical-bugs-implementation-plan-3q1qa4`).

This is the "what's left" list: everything still open, why it matters, and what
blocks it. Items marked **needs a human** cannot be closed by a code change.

---

## 1. Done — merged into PR #2238

| WS | Findings | What shipped |
|---|---|---|
| WS-1 | 5, 6, 7 | Driver-cancel ownership guard (403) + `driver_id` in the atomic cancel filter. Closes the insurance-audit corruption as a side effect. |
| WS-2 | 9 | `get_active_ride` rider object projected to `_RIDER_PUBLIC_FIELDS` allowlist; `password_hash` / `fcm_token` / session state no longer reach the driver client. |
| WS-3 | 14 | `notify_safety_team` added to the fallback import branch — safety reports now actually page on-call. |
| WS-4 | 1 | `_CircuitBreaker.release_probe()` wired into the three `run_sync` bypass paths; a deadline-aborted probe can no longer wedge every DB call in 503. |
| WS-5 | 16 | Migration 248: `ride_debit` (master −amount, used +amount) replaces the mis-signed `apply_rollback`; new `ride_debit_reversal` for the compensation path. |
| WS-6 | 2, 3, 8, 10 | Migration 249 `wallet_apply_delta` (locked, idempotent, signed delta); admin credit/debit, no-show fee and cancellation fee all routed through it. |

Six workstreams, ten of the seventeen findings.

---

## 2. Pending — code work, ordered by priority

### P0 — money loss or exploitable, nothing blocking

| WS | Finding | Work | Migration | Est. |
|---|---|---|---|---|
| **WS-7** | 4 | Payout TOCTOU + no compensation. Reserve the payout row `status='reserved'` **before** `Transfer.create` behind a partial unique index on `(driver_id) WHERE status IN ('reserved','pending')`; transfer second; reverse the transfer if the terminal write fails (mirroring `request_instant_payout`). Make `get_driver_balance` subtract open reservations. | 250 | 1 d |
| **WS-8** | 11 | Booking pre-auth hold never released on cancel. Call `cancel_authorization` before overwriting payment fields; store the fee PI in a new `cancel_fee_payment_intent_id` column instead of clobbering `payment_intent_id`; add a sweeper for `status='cancelled' AND auth_status` still open. Also covers `ride_search_timeout`. | 251 | 1–2 d |
| **WS-9** | 12 | `/rate` credits unbounded uncharged tips. Reject when already rated / tipped, add `le=500` to `RideRatingRequest.tip_amount`, only accept a tip while `payment_status` is pending/failed, add `@idempotent_endpoint(scope="ride_rate")`. | — | 0.5 d |
| **WS-10** | 13 | Mid-trip stop edits never recompute `grand_total` / tax. Recompute fees + taxes + `driver_earnings` / `admin_earnings` and refresh `fare_breakdown_snapshot` in the same atomic update. | — | 1 d |

### P1 — identity and compliance

| WS | Finding | Work | Migration | Est. |
|---|---|---|---|---|
| **WS-11** | 15 | Unverified email trusted as corporate-billing identity. Add `users.email_verified`, verify via the existing email-OTP machinery, gate `/corporate/join-domain` on it, reset the flag on email change. | 252 | 1 d |
| **WS-12** | C3 | Insurance-period transition non-atomic + swallowed. Single-transaction close+open RPC, plus a **ride-state-driven reconciler loop** (loop #17) that rebuilds expected open periods every 10 min. Note the existing `stale_intent_reconciler` explicitly **skips drivers on active rides**, so it does not and cannot cover this. | 253 | 1–2 d |

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

1. **Now:** WS-7 → WS-8 → WS-9 → WS-10 (P0 money), each its own small PR.
2. **In parallel:** WS-13 and WS-18 sweeps (exploit-class, batched ~10 sites per commit).
3. **Then:** WS-11, WS-12 (identity + insurance-period atomicity).
4. **Then:** WS-14 through WS-17, WS-19, WS-20.
5. **Independently, and soon:** repair the `routes.rides` patch-target drift so the
   suite can gate anything at all; and close H-1/H-2 with finance.

Migration numbers 250–253 are reserved above. Re-verify the next free slot with
`ls backend/migrations | sort -V | tail -1` before each merge — parallel PRs will
race on numbering.
