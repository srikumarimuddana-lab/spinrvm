# Change Impact & Risk Log — Rider lifecycle emails

Companion to `2026-08-08-driver-lifecycle-email-channel.md`, which built the
shared infrastructure this reuses. Read that one first for the layout module,
the policy layer, and the logo route.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, auth, corporate, rides |
| PR / commit link | `21e946c`, `943b07c`, `f859d97`, `3c3dc58`, `cd4641f`, `230f66a` |
| Related issue or gap ID | `docs/notification-channel-coverage.md` — R4, R7, R9, R21, R26, R29, R30, R32 |

## 1. Issue / gap identified

The rider lifecycle emitted **exactly one** transactional email: the ride
receipt. Eight specific gaps:

| ID | Gap |
|---|---|
| **R4** | No welcome email. Nothing in the rider flow ever confirmed the address we hold is deliverable. |
| **R7** | Changing the email address on an account sent **nothing** to either the old or the new address. An attacker who took over an account could relocate it silently — the real owner's last remaining contact point got no warning. |
| **R9** | A PIPEDA deletion request confirmed nothing. The in-app response explains the 7-year retention carve-out, but the account locks immediately, so the rider cannot go back and re-read it. |
| **R21** | No-show fee charged with push only. The most disputable charge in the product, with no written record of amount or ride. |
| **R26** | Corporate **guest** rides settled with **no receipt at all** — and therefore no GST/PST line-item disclosure, which CLAUDE.md requires on every rider receipt. |
| **R29** | Refund push-only. Money back on a card with nothing to reconcile against a statement. |
| **R30** | Wallet top-up push-only. |
| **R32** | Payment retries exhausted fired an admin WS broadcast and an admin push. The **rider whose account it had just blocked from booking was told nothing** — they met a booking failure with no explanation and nothing to act on. |

## 2. Root cause

Two distinct causes.

**For most of these: push was the path of least resistance.** `send_push_notification`
is one call and also writes the in-app inbox row, so "notify the rider" meant
"push". Email required hand-building HTML at each call site, so nobody did. The
driver-side branch built the missing layer; this branch is the rider half of
using it.

**R26 is structural, not neglect.** Every other ride receipts from
`/process-payment`, which a guest — who has no app — never calls.
`auto_settle_guest_corporate` was written as the server-side substitute for that
endpoint but only carried the settlement, not the receipt. The same class of
omission was already found and fixed once on this path for Meta conversions;
the receipt hook sits directly beside that one now.

## 3. Fix / remediation

New `backend/utils/rider_emails.py` — the rider counterpart to
`utils/driver_status_notifications.py`. All rider copy in one module, every send
through the existing policy layer, so route files and webhook handlers stay one
call long.

| Gap | Where it now fires |
|---|---|
| R4 | `routes/users.py` `create_profile`, on first completion (`profile_complete` was false) |
| R7 | Same endpoint, on a later edit where the address changed — sent to the **old** address |
| R9 | `routes/users.py` `delete_account_pipeda`, after the tombstone commits |
| R21 | `routes/drivers/ride_cancel.py`, beside the existing no-show push |
| R26 | `services/payment_service.py` `auto_settle_guest_corporate`, beside the Meta conversion hook |
| R29 | `routes/webhooks.py` `charge.refunded` |
| R30 | `routes/webhooks.py` wallet-top-up branch |
| R32 | `utils/payment_retry.py` `_alert_admins_payment_exhausted`, before the admin alerts |

`send_lifecycle_email` gains `to_override` for R7: by the time the notice sends,
the user row already holds the *new* address, so there is no other way to reach
the person who owned the account. It keeps every other guard and picks up an
explicit tombstone check, so an override cannot resurrect a soft-deleted account
as a delivery target.

**Everything here is TRANSACTIONAL** — financial records, security notices,
regulatory confirmations. Under CASL those are implied-consent messages a
preference toggle must not suppress.

## 4. Risk & impact on existing functionality

**Blast radius: backend only, additive at every call site.** No existing send
was modified, removed, or reworded. Every change is a new call placed beside an
existing one.

Grepped before each change:

- `send_lifecycle_email` — 3 callers now (`driver_status_notifications`,
  `document_expiry`, `rider_emails`). The `to_override` parameter is keyword-only
  with a `None` default, so the two existing callers are unaffected.
- `send_ride_receipt` (`services/payment_service.py:1194`) — 5 callers:
  `routes/rides/payments.py`, `routes/rides/receipts.py`, `routes/webhooks.py`
  (×2), `routes/admin/rides.py`, and now `auto_settle_guest_corporate`. The
  function itself is **unmodified**.
- `_alert_admins_payment_exhausted` — 1 caller, inside the retry loop.
- `routes/drivers/_deps.py` — re-export surface for the drivers package;
  adding one name cannot affect existing importers.
- `create_profile` / `delete_account_pipeda` — the new sends are after the DB
  write and outside its `try`, so neither can turn a successful profile save or
  tombstone into an error.

**What could regress:**

- **Duplicate mail on a Stripe replay.** The two riskiest sites, guarded:
  wallet top-up is gated on `not credit.get("deduped")` (`wallet_apply_credit`
  is idempotent on the payment_intent, so a replay returns the original balance
  without crediting — mailing a receipt for that would claim a top-up that did
  not happen); the guest receipt is gated on `not result.already_paid`, the same
  guard the neighbouring Meta hook uses. The refund path needs no gate —
  `claim_stripe_event(event_id)` already deduped the whole handler upstream.
- **Latency.** `create_profile` is a user-facing request, so both of its sends
  are `spawn()`ed. `auto_settle_guest_corporate` runs inline because its caller
  in `ride_complete.py:747` already backgrounds the whole coroutine. The webhook
  and retry-loop sends are inline: those handlers are not on a rider-visible SLA,
  and Stripe's webhook budget (500 ms P95) is measured to the ack, which happens
  after — worth watching in the first days.
- **Volume.** Eight rider events that sent no email now send one. Bounded per
  event; none is recurring.
- **R32's send goes first**, before the admin alerts, deliberately: it is the
  only notice reaching the person actually affected. It carries its own
  try/except so the admin WS broadcast and pushes fire regardless — the
  guarantee is local, not inherited from the sender's internal guard.
- **R32 exposed a pre-existing duplicate.** Three branches reach payment
  exhaustion and only one claimed before alerting, so every exhausted ride
  produced **two** admin alerts five minutes apart, and the two transition
  branches were never replay-safe across the loop's replicas. Tolerable while
  it only paged admins; not once the rider gets an email. All three now gate on
  the same compare-and-swap (`_claim_exhausted_alert`). This is a **behaviour
  change to an existing admin alert** — it halves the volume — and is the one
  change in this batch that is not purely additive.
- **The `_money` display helper** is display-only — no arithmetic — and coerces
  via `Decimal(str(...))`, so a value that reached us as a float cannot pick up
  representation drift on the way to the rider's eyes. The pre-commit
  float-arithmetic hook passes on every commit in this batch.

Not touched: the ride state machine, dispatch, fare calculation, surge, wallet
delta logic, `corporate_wallet_apply_delta`, or any Stripe charge/capture path.
No money amount is computed, only formatted.

## 5. User-experience effect

**Rider-facing, and several are visible mid-session:**

- A new rider gets a welcome email the moment they finish profile setup.
- Changing an email address sends a security notice to the address just
  replaced. **A rider who legitimately changes their address will receive an
  unexpected message at the old one** — that is the intent, and the copy says so.
- Deleting an account produces a written confirmation naming the retention
  window and the purge date.
- Refunds, wallet top-ups and no-show fees now arrive as emails as well as
  pushes. A rider mid-ride when a no-show fee lands gets both.
- A rider blocked by exhausted payment retries is finally told why, and what to
  do about it.
- Corporate guest riders **with an email on file** now get a receipt. A
  phone-only guest booking still gets none — the receipt path skips silently
  when there is no address, which is the common guest case.

**Driver-facing:** none. **Admin-facing:** none; the existing exhausted-payment
alerts are unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rider_emails.py` | New — all rider copy + 7 senders | One place for rider copy and the delivery contract |
| `backend/utils/email_notifications.py` | `to_override`; explicit tombstone guard | R7 needs the old address |
| `backend/routes/users.py` | Welcome / email-change / deletion sends | R4, R7, R9 |
| `backend/routes/webhooks.py` | Refund + wallet-top-up sends | R29, R30 |
| `backend/routes/drivers/ride_cancel.py` | No-show fee send | R21 |
| `backend/routes/drivers/_deps.py` | Re-export `send_no_show_fee_email` | Matches the package's existing dependency seam |
| `backend/utils/payment_retry.py` | Rider notice in the exhausted-payment alert; `_claim_exhausted_alert` gating all three exhaustion branches | R32 + the duplicate-alert defect it exposed |
| `backend/tests/test_payment_exhausted_alert_once.py` | New — 7 tests | Exactly-once across all three branches |
| `backend/services/payment_service.py` | Receipt hook in `auto_settle_guest_corporate` | R26 |
| `backend/tests/test_rider_account_emails.py` | New — 13 tests | R4, R7, R9 |
| `backend/tests/test_rider_money_emails.py` | New — 23 tests | Amounts, copy, contract |
| `backend/tests/test_guest_corporate_receipt.py` | New — 7 tests | R26 incl. replay |
| `docs/notification-channel-coverage.md` | Parts B/C/D updated | Keep the audit true |

## 7. Before / after

```python
# Before — retries exhausted told admins, and nobody else
async def _alert_admins_payment_exhausted(ride: dict) -> None:
    """Notify admins when a ride's payment retries are exhausted."""
    ride_id = ride.get("id", "")
    rider_id = ride.get("rider_id", "")
    total_fare = ride.get("total_fare", 0)

    try:
        await manager.broadcast_to_admins({...})
```

```python
# After — the blocked rider is told first
    ride_id = ride.get("id", "")
    rider_id = ride.get("rider_id", "")
    total_fare = ride.get("total_fare", 0)

    if rider_id:
        await send_payment_blocked_email(rider_id, total_fare, ride=ride)

    try:
        await manager.broadcast_to_admins({...})
```

```python
# Before — guest settlement fired a conversion and stopped
    elif not result.already_paid:
        await _fire_guest_purchase_conversion(ride, ride_id, result.charged_amount)
    return result
```

```python
# After — and receipts the ride, same guard
    elif not result.already_paid:
        await _fire_guest_purchase_conversion(ride, ride_id, result.charged_amount)
        try:
            await send_ride_receipt(ride, ride.get("rider_id") or "", Decimal("0"))
        except Exception:
            logger.opt(exception=True).error("[PAYMENT] guest receipt failed for ride {}", ride_id)
    return result
```

## 8. Rollback plan

**Feature-flagged, with one documented exception.**

`app_settings.lifecycle_emails_enabled = false` suppresses R4, R7, R9, R21, R29,
R30 and R32 — every send routed through the policy layer — without a redeploy
and without touching push. It takes effect within the 60 s settings cache TTL.

**R26 is NOT covered by that flag.** It reuses the pre-existing receipt pipeline
(`send_ride_receipt`), which does not go through the policy layer. Stating that
plainly rather than implying coverage. Rolling it back is
`git revert 3c3dc58` — acceptable here because the change is a single additive
call whose worst case is one unexpected receipt, and because suppressing it
would restore a state that fails the receipt-disclosure requirement.

| Scenario | Action |
|---|---|
| Any rider email wrong / too noisy | `lifecycle_emails_enabled = false` |
| Guest receipts wrong | `git revert 3c3dc58` |
| Duplicate top-up receipts on Stripe replays | `git revert 943b07c`; the `deduped` gate is the thing to re-examine |
| Duplicate admin alerts wanted back (they will not be) | `git revert 230f66a` restores the two-alerts-per-ride behaviour along with the rider notice's local guard |
| Whole rider batch | Revert `21e946c`, `943b07c`, `f859d97`, `3c3dc58`, `cd4641f`, `230f66a` — no migration, no data change |

No migration and no data change in this batch, so there is nothing applied that
a revert cannot undo.

## 9. Verification performed

- [x] **Targeted tests** — 49 new across 3 new files plus 6 added to
      `test_email_notifications.py` for the `to_override` path. Those six found
      a real bug: a whitespace-only override produced an empty `To:` and the
      provider dropped the message (fixed in `cd4641f`)
- [x] **Per-area sweeps** during development: `-k "users or profile or deletion
      or dsar"` (281 passed), `-k "webhook or refund or wallet or topup"` (575
      passed), `-k "payment_retry or noshow or no_show or cancel"` (322 passed),
      `-k "guest or corporate_guest or auto_settle"` (61 passed)
- [x] **Full backend suite** — `pytest --ignore=tests/perf`, run against the
      final state of the branch: **10 094 passed, 8 skipped, 1 xfailed,
      0 failed** (7 m 00 s)
- [x] `ruff check` and `ruff format` clean on every changed file
- [x] **Pre-commit money hook** passed on all four commits (float-arithmetic gate)
- [x] **Money formatting** unit-tested against `Decimal`, `float`, `str`, `None`
      and `""`, including ROUND_HALF_UP on `12.005 → 12.01`
- [x] **Replay guards** tested: `already_paid` suppresses the guest receipt; a
      lost settlement claim sends nothing; a lost `_claim_exhausted_alert`
      sends nothing, with a source-level assertion that every exhaustion branch
      is gated so a fourth one cannot be added ungated
- [x] **App boot** — `import server` plus every new importer
      (`utils.rider_emails`, `utils.payment_retry`, `routes.webhooks`,
      `routes.drivers._deps`, `services.payment_service`) resolves with no
      circular import
- [x] **Blast-radius grep** — `send_lifecycle_email`, `send_ride_receipt`,
      `_alert_admins_payment_exhausted`, `_deps` re-exports, all listed in §4
- [x] **PIPEDA** — no address in logs; senders take the user id as `log_id`
- [ ] **Manual repro in staging** — not done, see §10

## 10. What was NOT verified

- **No real email was sent.** Every assertion stops at the
  `send_lifecycle_email` / `send_transactional_email` mock. Provider delivery and
  rendering in real clients remain unverified, and there is still no visual
  regression tooling for email in this repo.
- **No end-to-end Stripe test.** The refund and top-up sends were exercised
  through unit tests with the webhook body stubbed, **not** against Stripe test
  mode with a real `charge.refunded` or `payment_intent.succeeded` delivery. The
  `deduped` gate on top-up in particular is reasoned from `wallet_apply_credit`'s
  contract and covered by unit tests, not by an observed duplicate delivery.
- **The R26 receipt uses the pre-settlement `ride` dict**, matching the
  neighbouring Meta hook. `grand_total` is written at ride completion, before
  settlement, so the figures are correct — but this was reasoned from the code,
  not confirmed against a real settled guest ride.
- **Guest coverage is partial by nature.** A phone-only guest has no email and
  is skipped. What proportion of real guest bookings that is has not been
  measured.
- **New customer-facing copy has had no product or copy review.** All of it was
  written in this session.
- **Webhook latency was not measured.** The refund and top-up sends are inline
  in the Stripe handler. Against the 500 ms P95 webhook SLA this is the one
  performance claim in §4 that is reasoned rather than measured — worth watching
  in the first days after deploy, or moving to `spawn()` if it bites.
- **Not run against live or staging Supabase.**
- **No production build run** — backend-only, no frontend surface touched.
