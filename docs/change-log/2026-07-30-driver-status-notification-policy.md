# Change Impact & Risk Log — Driver status notification policy + reject action

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers, admin |
| PR / commit link | `5c7924b`, `3baff3f`, `f0d9326` |
| Related issue or gap ID | Follow-on from the onboarding-reminder trace; gaps 1–6 in `docs/driver-lifecycle-status-flow.md` |

## 1. Issue / gap identified

Six defects in the driver lifecycle notification path:

1. `reject` accepted by `DriverActionRequest` but no `if/elif` branch → `400 Unknown action: reject`.
2. `rejected` also missing from the status-override endpoint's `valid` set → `400`. **`rejected` was unreachable through the entire API.**
3. `needs_review` present in that `valid` set but absent from the `Literal` → `422`. Mirror-image bug.
4. Status-override sent **no push at all** — an admin could suspend a driver there and the driver learned only via a 403 on their next go-online.
5. Driver-triggered `needs_review` (own vehicle edit / doc re-upload) forced `is_online=False` **silently**.
6. Lifecycle pushes had no `deleted_at` guard, so a soft-deleted driver could still be pushed.

## 2. Root cause

Defects 1–3 are a copy/paste divergence between three places that all
enumerate driver statuses (the action `if/elif` chain, the override `valid`
set, the pydantic `Literal`) with nothing keeping them in sync. Defects 4–6
are the absence of any shared notification policy: each call site decided
independently whether to notify, and two of them decided not to.

## 3. Fix / remediation

- New `backend/utils/driver_status_notifications.py` — single source for lifecycle copy, delivery tier, and the recipient guard. Two lookups: `action_message` (admin action) and `status_message` (status entered).
- New `account` push priority: bypasses the `push_enabled` opt-out and falls back to the retry queue, for `rejected`/`suspended`/`banned` only. Migration 272 widens the `push_retry_queue.priority` CHECK to match.
- `reject` branch added (reason required, same contract as suspend/ban).
- `Literal` and `valid` set reconciled, with a comment on each pointing at the other.
- Status-override and both driver-triggered `needs_review` paths now notify.

Product decisions taken by the user on 2026-07-30: rejected drivers get a
one-time notice and **no** recurring re-apply nudge; blocking notices bypass
the push opt-out.

## 4. Risk & impact on existing functionality

**Blast radius: cross-cutting within backend.**

`send_push_notification` (`backend/features.py`) is the shared surface —
grepped for callers: 40+ across dispatch, payments, safety, corporate, rides,
admin. The change to it is a **single tuple widening**:
`priority in ("dispatch", "safety")` → `("dispatch", "safety", "account")`.
Only callers that pass `priority="account"` change behaviour, and the only
ones are the three new lifecycle call sites. Every existing caller is
byte-for-byte unaffected.

`push_retry_queue.priority` CHECK — widening a constraint is
forward-compatible: all existing rows satisfy the new predicate, no table
rewrite, safe with traffic in flight. Old replicas keep writing the three old
values, which remain valid.

**What could regress:**

- **Notification volume up.** Three paths that previously sent nothing now send. Worst case is an extra push; no state change, no money, no ride flow.
- **Opt-out is now overridden for three statuses.** Deliberate and user-approved, but it is a real consent-posture change — an opted-out driver will now receive suspension/ban/rejection pushes. The in-app inbox row was always written; only device delivery changes.
- **Test patch target moved.** The send moved out of `routes.admin.drivers` into the policy module, which imports `send_push_notification` from `features` at call time. Two tests patching `routes.admin.drivers.send_push_notification` broke and were repointed at `features.send_push_notification`. `admin_verify_driver` still sends directly, so its patch target is unchanged — noted in the test.

Not touched: ride state machine, dispatch, fare/settlement, wallet, insurance
periods. `record_period_transition` on the `needs_review` path is unchanged;
the notify call sits after it.

## 5. User-experience effect

- **Driver-facing.**
  - Rejected applicants now actually get rejected (and told), instead of the admin seeing a 400.
  - Suspending via status-override now notifies; previously silent.
  - A driver whose own edit knocks them offline now gets "Changes Under Review" explaining it. **This is visible mid-session** — a driver who was online and saves a vehicle change receives it immediately.
- **Admin-facing.** The Reject button works. `rejected` and `needs_review` are settable from status-override.
- **New copy**: "Changes Under Review" / "We're reviewing your updated details. You've been taken offline until an admin approves them." All other strings are carried over byte-identical and pinned by tests.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_status_notifications.py` | New — copy, tiers, recipient guard, shared async sender | One policy, three call sites |
| `backend/features.py` | `account` added to the `time_critical` tuple | Bypass opt-out + retry-queue for blocking notices |
| `backend/migrations/272_push_retry_queue_account_priority.sql` | New — widens the `priority` CHECK | Without it the retry enqueue violates the constraint |
| `backend/routes/admin/drivers.py` | `reject` branch; `Literal`/`valid` reconciled; `rejection_reason` on override; both endpoints use the shared sender | Gaps 1–4 |
| `backend/routes/drivers/profile.py` | Notify on driver-triggered `needs_review` | Gap 5 |
| `backend/documents.py` | Same, for the doc-upload path | Gap 5 |
| `backend/tests/test_driver_status_notifications.py` | New — 23 tests | Policy + copy pinning |
| `backend/tests/test_driver_needs_review_notification.py` | New — 4 tests | Gap 5 |
| `backend/tests/test_admin_drivers_coverage.py` | Flipped 2 tests that pinned the broken behaviour; added 6 | Gaps 1–4, 6 |

## 7. Before / after

```python
# Before — reject fell through to the else
    elif req.action == "reactivate":
        ...
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
```

```python
# After
    elif req.action == "reject":
        if not req.reason:
            raise HTTPException(status_code=400, detail="Reason is required when rejecting")
        updates["status"] = "rejected"
        updates["is_verified"] = False
        updates["rejection_reason"] = req.reason
        updates["is_online"] = False
        updates["is_available"] = False
```

```python
# Before — status-override notified nobody
    return {"message": f"Driver status set to {req.status}"}
```

```python
# After
    if req.status != driver.get("status"):
        await notify_driver_status_change(
            driver, status_message(req.status, req.reason), f"status_override:{req.status}"
        )
    return {"message": f"Driver status set to {req.status}"}
```

## 8. Rollback plan

**This change is NOT feature-flagged.** Stating that plainly rather than
implying coverage:

- Gaps 1–3 are broken-endpoint fixes. A flag would mean "keep the 400" — there is no sensible off position.
- Gaps 4–6 close silent failures. Shipping dark would leave the bug in place and deliver nothing.
- The blast radius is bounded: worst case is an unwanted push. No state, money, or ride flow is touched, so "observe and revert" is proportionate here in a way it would not be for a dispatch or payment change.

Rollback paths, in order of preference:

| Scenario | Action |
|---|---|
| Copy is wrong / too noisy | `git revert f0d9326` — removes only the `needs_review` notices, leaves the reject fix intact |
| `account` tier misbehaving | `git revert 5c7924b` reverts the tier; notices fall back to `normal` and honour the opt-out again |
| Migration 272 | Rollback SQL is in the file header. **Note the caveat**: it must `DELETE` any unsent `priority='account'` rows first, because they would fail the narrowed constraint. Those are undelivered suspension/ban notices — dropping them loses the notice, so re-notify from the admin dashboard |

If a flag is wanted before this reaches production, the natural seam is
`notify_driver_status_change` — a single early return gated on an
`app_settings` key would disable every lifecycle push at once.

## 9. Verification performed

- [x] Automated tests — new: 23 (`test_driver_status_notifications.py`) + 4 (`test_driver_needs_review_notification.py`); modified: `test_admin_drivers_coverage.py` (2 flipped, 6 added). Targeted sweep `-k "driver or document or admin or push or notification"`: **1475 passed, 1 skipped, 0 failed**
- [x] `ruff check` clean on every changed file
- [x] Blast-radius grep — `send_push_notification` callers, `priority=` call sites, `push_retry_queue.priority`, `action_push_map`, `DriverStatusOverride`, `valid =`
- [x] Reviewed against `CLAUDE.md` — no PII in logs (driver_id/user_id only), no silent error swallowing (push failures logged at warning with the state change already committed), migration is append-only + forward-compatible + reversible on paper
- [ ] Feature-flagged — **no**, justified in §8
- [ ] Manual repro in staging — **not done**, see §10

## 10. What was NOT verified

- **Migration 272 was not applied anywhere.** Not against staging, not against a local Postgres. The `ALTER TABLE ... DROP CONSTRAINT / ADD CONSTRAINT` pair is written from the migration conventions and reviewed by reading only. **If it has not run before the code deploys, every `account`-tier retry enqueue will violate the old CHECK** — the immediate send still works, so the failure mode is "notice lost only when the first send fails", which is quiet. Apply the migration first.
- **No real push was sent.** Every test asserts against an `AsyncMock` at the `send_push_notification` boundary. FCM/Expo delivery, the `account` tier's behaviour in the actual retry loop, and how the new copy renders on a device are all unverified.
- **Not run against live or staging Supabase.**
- **The opt-out bypass was verified at the policy layer, not end to end.** Tests assert `priority == "account"` is passed; that `features.send_push_notification` then actually skips the `notification_preferences` lookup is covered by the tuple change being a one-line edit, not by a test that sets `push_enabled=False` and asserts delivery. That gap is worth closing.
- **No production build run** — backend-only change, no frontend surface touched.
- **Copy not reviewed by anyone but me.** "Changes Under Review" and its body are new customer-facing strings written in this session. They follow the existing tone but have had no product/copy review.
- **`admin_verify_driver` still bypasses the policy module** — it sends its own push and therefore lacks the `deleted_at` recipient guard. Left as a documented gap rather than widened into this change.

---

## 11. Follow-up review (2026-07-30, same day)

A self-review of this branch found five defects in the work above. All fixed;
recorded here rather than silently amended so the history is auditable.

| ID | Finding | Fix |
|---|---|---|
| H1 | The reminder repeat cap counted claim-log rows client-side under a limit sized at exactly the worst case (200 drivers × 2 types × cap+1 = 3200, zero headroom). The log also holds rows from before the cap existed, so a page could exceed 12,000 rows; PostgREST truncates with no `ORDER BY`, counts came back low, and capped drivers would be pushed again — the exact spam the cap exists to stop, with nothing logged. | Migration 273 adds a `GROUP BY` RPC so counting happens in the DB with no limit in the path. The client-side count survives as a pre-migration fallback but now detects saturation and **fails closed**, suppressing the page rather than over-notifying (`ca2c905`) |
| M1 | Migration 272 dropped the `priority` CHECK by its assumed auto-generated name with `IF EXISTS`. A name mismatch would no-op silently, the `ADD` would create a second constraint, and the old narrow one would keep rejecting `account` — a migration reporting success while doing nothing | Drops by `pg_constraint` lookup instead; name-independent and idempotent. Also fixed the rollback comment, which ordered the `DELETE` after the constraint it must precede (`22c8a05`) |
| M2 | Moving the send into the policy module left 10 test patches of `routes.admin.drivers.send_push_notification` dead — verified with a spy: patch awaited 0 times, real sender running against unpatched mocks. `test_push_notification_failure_does_not_fail_request` was asserting nothing as a result | Repointed the `/action` and `/status-override` tests; left `verify`/`photo-review`/`nudge-expiry` alone since those still send directly (`172345e`) |
| L1 | `_push_token_columns_to_clear` returned `{}` when no per-app column was set, so pre-migration-102 rows holding only the legacy `fcm_token` were never cleared — the logout fix did nothing for them | Clears the legacy column for single-role accounts (attributable); leaves it and logs for dual-role accounts where clearing could kill the other app's pushes (`eedb281`) |
| L2 | `send_push_notification`'s docstring still described only dispatch/safety as bypassing the opt-out and retry-queueing | Updated for the `account` tier, plus a pointer to the CHECK constraint that must list every tier (`eedb281`) |

Also closed in `eedb281`: the opt-out bypass is now asserted end to end
(`test_push_opt_out_does_not_block_dispatch_safety_or_account` drives
`send_push_notification` with `push_enabled=False`), not just at the policy
layer — a gap §10 had flagged as worth closing.

**Deploy ordering now involves two migrations**, neither yet applied anywhere:

1. `272_push_retry_queue_account_priority.sql` — must precede the code, or `account`-tier retry enqueues violate the old CHECK.
2. `273_driver_onboarding_reminder_counts_fn.sql` — the code tolerates its absence (fallback fails closed), but the cap is only exact once it is applied.
