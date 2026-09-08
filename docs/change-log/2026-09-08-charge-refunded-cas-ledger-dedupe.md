# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session implementing PR #5085's hardening plan) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/pr-5085-5079-hardening-5a2aj7` |
| Related issue or gap ID | F1, validated in PR #5085 (`docs/audit/2026-09-07-pr-5079-validation-and-hardening-plan.md`, "PR-3"); tracked as `ACTION_ITEMS.md` C77 |

## 1. Issue / gap identified

The `charge.refunded` webhook handler advanced `rides.refund_amount` and wrote a `financial_events` ledger row as two independent, unsynchronized steps, with no compare-and-swap on the ride row and no dedupe key on the ledger write. A concurrent delivery for the same ride could overwrite another event's already-applied cumulative total, and a crash/failure between the ride update and the ledger write could silently and permanently drop the ledger row while the ride already showed the refund as applied — a real gap in the 7-year tax/audit ledger with no automatic repair.

## 2. Root cause

`update_one("rides", {"id": ride_id}, {...})` filtered only on `id`, so any two deliveries racing for the same ride could both "succeed" regardless of ordering. `record_refund_event`/`ledger_service.record_event` had no `dedupe_key` for the refund path (unlike the existing dispute path), so a lost-response retry of the same event would either double-book or (with the ride update unconditional) silently skip because the ride already reflected the new cumulative.

## 3. Fix / remediation

Three coordinated changes, developed over three rounds of `spinr-money-auditor` review (each round found a real remaining gap, addressed before merge):

1. **Compare-and-swap on the ride row.** The `rides` `update_one` call now filters on `{"id": ride_id, "refund_amount": prev_refund_amount_raw}` (the exact raw value read moments earlier). Zero rows matched (`None` returned) means a concurrent delivery already won — the handler logs, calls `unclaim_stripe_event`, and raises `HTTPException(500)` so Stripe retries, rather than silently overwriting another event's applied state.
2. **Dedupe key on the ledger write.** `record_refund_event` now takes and forwards a `dedupe_key` to `ledger_service.record_event` (which already supported it — this reuses the dispute path's existing mechanism) and returns the ledger row id (`Optional[str]`, was `None`-only before) so the caller can tell success from failure. A CAS-success-but-ledger-failure no longer silently completes — it also raises 500 + unclaims, and deliberately does **not** revert the ride row (a revert would let a delayed `invoice.paid` re-settle an already-refunded ride, since `payment_status` is treated as terminal elsewhere in this file and in `admin/rides.py`).
3. **Replay recovery.** A non-forward (`delta_cents <= 0`) delivery now checks whether the ledger has actually caught up to the ride's own *currently recorded* cumulative (`previous_refunded_cents`, read fresh) via a new `refund_booked_cents(payment_intent_id)` helper, and books the shortfall if not — keyed off the ride's own current total rather than the triggering event's own asserted amount, so the check self-heals regardless of which event happens to trigger it (round 2 finding: gating on exact equality with the triggering event's own amount could permanently lose a superseded event's row once later events advanced the ride past it).
4. **Dedupe-key collision guard (round 3 finding).** Because the recovery key is derived from the ride's current cumulative, it can be structurally identical to a still-in-flight event's own normal-path key. `ledger_service`'s duplicate-key handling previously treated *any* unique-violation as unconditional success. `_insert_with_retry` now verifies content on a duplicate-key hit (`_verify_duplicate_matches`): reads the existing row back and compares `delta_cents`; a mismatch is reported as a real failure (the caller's existing `escalate()` path alerts) instead of silently no-op'ing over a different money movement. `_insert_many_with_retry` (ledger legs, no single id to compare) is unchanged.

## 4. Risk & impact on existing functionality

- **Blast radius**: the `charge.refunded` handler in `routes/webhooks.py`, `record_refund_event`/new `refund_booked_cents` in `payment_service.py`, and `_insert_with_retry`'s duplicate-key path in `ledger_service.py` (shared by every `record_event` caller — dispute path included, since it also uses a dedupe key; verified its own existing tests still pass unaffected because a legitimate same-content retry always matches on `delta_cents`).
- Readers of `refund_amount`/`payment_status`: `webhooks.py`'s own `_SETTLED_PAYMENT_STATUSES`, `routes/admin/rides.py` (terminal-status guard, refund-amount reporting) — unaffected; the CAS only changes which write "wins" a race, not what downstream readers see once it lands.
- No ride state, dispatch, or driver-payout path touched. Driver `driver_earnings` remains untouched by policy (unchanged).
- Behavior change to disclose: a replayed pre-fix refund with a genuine ledger gap (from before this fix existed) would now be recovered (more correct, but new — the recovery mechanism does not distinguish "gap from before this fix" from "gap this fix itself could still theoretically produce").

## 5. User-experience effect

None directly visible to a rider/driver. A rider whose refund previously completed with a silently-missing ledger row gets no different experience (the ride/payment status was always correct; only the internal accounting record was at risk). No admin-facing UI changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | `charge.refunded` handler: CAS filter on the ride update; dedupe key on the ledger write; 500+unclaim on CAS conflict or ledger failure; replay-recovery branch in the `delta_cents<=0` path | Close F1's concurrent-overwrite and lost-ledger-row gaps |
| `backend/services/payment_service.py` | `record_refund_event` takes/forwards `dedupe_key`, returns `Optional[str]`; new `refund_booked_cents(payment_intent_id)` helper | Support CAS/dedupe/recovery from the webhook handler |
| `backend/services/ledger_service.py` | `_attempt_insert` gained an optional `verify_duplicate` callback; new `_verify_duplicate_matches` compares `delta_cents` on a duplicate-key hit before declaring success | Close the round-3 dedupe-key collision gap |
| `backend/tests/test_refund_replay_safety.py` (new) | CAS conflict, ledger-failure-after-CAS-success, dedupe-key-from-cumulative, replay recovery (including the round-2 A/B/C supersession counterexample), and end-to-end dedupe-key-collision tests | Regression coverage for all three review rounds |
| `backend/tests/test_refund_ledger.py`, `test_ledger_service.py` | Extended for `dedupe_key` forwarding, `refund_booked_cents`, and duplicate-key content verification (match / mismatch / read-back-failure) | Unit coverage for the new helpers |
| `backend/tests/test_routes_webhooks_coverage.py`, `test_webhooks_coverage_gap.py` | Updated two pre-existing tests whose mocks assumed the old (no-CAS, no-ledger-read) behavior | Keep pre-existing coverage passing under the new CAS filter and recovery ledger read |

## 7. Before / after

```python
# Before
await db_supabase.update_one("rides", {"id": ride_id}, {"payment_status": ..., "refund_amount": ...})
await record_refund_event(ride_id=ride_id, user_id=..., refund_cents=delta_cents, payment_intent_id=..., ride=ride)
```

```python
# After
cas_updated = await db_supabase.update_one(
    "rides", {"id": ride_id, "refund_amount": prev_refund_amount_raw}, {"payment_status": ..., "refund_amount": ...}
)
if cas_updated is None:
    await unclaim_stripe_event(event_id)
    raise HTTPException(500, "Refund state changed concurrently — Stripe will retry")
ledger_id = await record_refund_event(..., dedupe_key=refund_dedupe_key)
if ledger_id is None:
    await unclaim_stripe_event(event_id)
    raise HTTPException(500, "Refund ledger write failed — Stripe will retry")
```

Convergence trace (round 2's counterexample, now fixed): A(1000) CAS succeeds then its ledger write fails; B(2000) and C(3000) both fully supersede the ride before A's Stripe retry arrives (only B's and C's -1000/-1000 ledger rows exist). A's redelivery reads the ride at cumulative 3000, computes `previous_refunded_cents=3000`, checks the ledger (2000 booked), and recovers the missing 1000 under key `stripe_refund|pi|3000` — the same key whichever event last advanced the ride to 3000 would itself use, converging correctly regardless of which event triggers the check.

## 8. Rollback plan

`git revert` — pure application-code change, no schema/migration. Live-data effect is additive only: correct, deterministically-keyed ledger rows (no mutation of existing rows). The pre-fix handler tolerated either id shape, so a rollback does not orphan any data the fix wrote.

## 9. Verification performed

- [x] Automated tests: full refund/ledger/webhook suite (`test_refund_replay_safety.py`, `test_routes_webhooks_coverage.py`, `test_webhooks_coverage_gap.py`, `test_webhooks_main.py`, `test_orphan_refund.py`, `test_stripe_reconcile.py`, `test_refund_ledger.py`, `test_dispute_ledger_replay_safety.py`, `test_ledger_service.py`, `test_ledger_pii.py`) — 271 passed, 0 failed. Full `pytest -m unit -q` — 3493 passed, 1 skipped, 0 failed.
- [x] `ruff check`/`ruff format --check` on every changed file — clean.
- [x] Three full `spinr-money-auditor` review rounds, each finding addressed before the next: (1) initial CAS/dedupe design confirmed sound but flagged the exact-equality recovery gate as a real risk; (2) the redesigned recovery-key logic confirmed to close that gap but surfaced the dedupe-key collision risk; (3) the collision-guard fix confirmed to close it, with an explicit final verdict of "safe to merge pending the live round-trip check."
- [x] Concrete before/after scenario in this log's §7, plus a hand-traced convergence proof for the specific 3-event supersession counterexample the second audit round found, backed by a passing regression test.

## What was NOT verified

**Mandatory before deploy, not performable from this sandboxed session (no live Supabase access):** a read-only round-trip — `get_rows("rides", {"id": <a refunded ride>, "refund_amount": <its read-back value>})` returns exactly that row — to confirm the CAS filter's exact-value match works against the real stored representation of the `refund_amount` numeric column (no migration asserts its exact type/formatting on read-back). If this does not round-trip cleanly in a real environment, fall back to a `{"refund_amount": {"$lte": prev}}`-style filter instead of exact match. Not tested against a live Stripe webhook delivery or real concurrent traffic — the concurrency scenarios are proven via unit-level interleaving simulation (mocked DB calls returning specific sequences), not a real race under load.
