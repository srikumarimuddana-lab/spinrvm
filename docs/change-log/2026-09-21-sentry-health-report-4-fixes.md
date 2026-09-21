# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, admin |
| PR / commit link | PR #5646 |
| Related issue or gap ID | Sentry project crimson-smoke-7445 monitoring sweep |

## 1. Issue / gap identified

Four issues surfaced from Sentry health report: (1) admin search TypeError from FastAPI Query descriptor leak, (2) `_build_or_clause` AttributeError on non-dict input, (3) Stripe reconciler flagging 500+ historical ignored events as "stuck" on every daily run, masking real stuck events, (4) Sentry startup `capture_message` at info level creating a Sentry event on every boot.

## 2. Root cause

1. **Admin search TypeError**: POST-body typeahead endpoints call `admin_get_users`/`admin_get_drivers` without passing `offset`. Those functions declare `offset` with `Query(0, ge=0)` which returns a `FieldInfo` object (not the integer default) when called as plain Python rather than via FastAPI HTTP dispatch.
2. **_build_or_clause crash**: No type guard on the `clauses` list elements — a non-dict element (e.g. a string passed by mistake) triggers `AttributeError: 'str' object has no attribute 'items'` instead of a clear error.
3. **Stripe reconciler noise**: Before this fix, ignored Stripe events (21 known-harmless lifecycle types like `payment_intent.created`) were left with `processed_at=NULL` in the webhook handler. The nightly reconciler queries all `processed_at=NULL` rows with no time bound, surfacing hundreds of historical entries that are deliberate, not stuck.
4. **Sentry startup event**: `sentry_sdk.capture_message()` creates a Sentry event regardless of level — the `level="info"` parameter only tags the event, it doesn't suppress creation. Every boot created a new "pipeline verified" issue.

## 3. Fix / remediation

1. Pass explicit `offset=0` when calling `admin_get_users`/`admin_get_drivers` from the POST-body search endpoints.
2. Add `isinstance(clause, dict)` guard at top of `_build_or_clause` loop with a clear TypeError message.
3. Three-pronged Stripe reconciler fix: (a) stamp `processed_at` on ignored events in the webhook handler, (b) add 30-day lookback window to stuck-events query, (c) improve completion log to separate stuck-event count from other discrepancies.
4. Replace `sentry_sdk.capture_message(...)` with `logger.info(...)` in both `server.py` and `sentry_runtime.py`.

## 4. Risk & impact on existing functionality

- **Admin search (Fix 1)**: `admin_get_users` and `admin_get_drivers` are called from two paths each — the GET list endpoint (FastAPI dispatches `offset` correctly) and the POST typeahead endpoint (now passes `offset=0` explicitly). No other callers. Blast radius: isolated.
- **_build_or_clause (Fix 2)**: Called by `_apply_filters` which is the shared filter compiler for `get_rows`, `update_one`, `delete_many`. The guard only raises on invalid input (non-dict) — valid callers are unaffected. Blast radius: isolated (defensive only).
- **Stripe webhook (Fix 3)**: `mark_stripe_event_processed(event_id)` is already called on every handled event type. Adding it to the ignored-events branch is the same operation on the same table. The early `return` prevents falling through to the `else` (genuinely unknown events) branch, which still leaves `processed_at=NULL` as designed. The reconciler's new `received_at >= lookback_cutoff` filter is additive — it narrows the query, never widens it. Blast radius: single-surface (payments reconciliation loop).
- **Sentry startup (Fix 4)**: The `logger.info(...)` still emits to stdout/loguru, so the "started" signal remains visible in logs. It just stops creating a Sentry event. No interaction with background loops, ride state machine, or wallet deltas.

## 5. User-experience effect

- **Admins**: typeahead search in the admin dashboard user/driver pickers will stop returning 500 errors on certain inputs. Not visible mid-session to riders or drivers.
- **No other user-visible change**: Stripe reconciler and Sentry startup fixes are backend-only operational improvements.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/routes/admin/users.py | Added `offset=0` to `admin_get_users()` call | Prevent Query descriptor leak |
| backend/routes/admin/drivers.py | Added `offset=0` to `admin_get_drivers()` call | Same pattern as users.py |
| backend/repositories/_base.py | Added isinstance guard in `_build_or_clause` | Clear error on invalid input |
| backend/routes/webhooks.py | Stamp `processed_at` + early return for ignored events | Stop accumulating as "stuck" |
| backend/utils/stripe_reconcile.py | Added `Optional` import, `settings` param, lookback window, improved log | Reduce reconciler noise |
| backend/tests/test_stripe_reconcile.py | Updated filter assertion to match new lookback filter | Test was asserting old filter shape |
| backend/server.py | `capture_message` → `logger.info` | Stop creating Sentry event on boot |
| backend/utils/sentry_runtime.py | `capture_message` → `logger.info` | Same as server.py for worker process |

## 7. Before / after

```python
# Before (webhooks.py — ignored events fell through with no stamp):
elif event_type in _STRIPE_IGNORED_EVENTS:
    logger.debug("[WEBHOOK] Ignoring routine Stripe lifecycle event %r ...", event_type)
# (no return — fell through to the comment about leaving processed_at NULL)
```

```python
# After (ignored events stamped and returned early):
elif event_type in _STRIPE_IGNORED_EVENTS:
    logger.debug("[WEBHOOK] Ignoring routine Stripe lifecycle event %r ...", event_type)
    await mark_stripe_event_processed(event_id)
    return {"received": True, "ignored": True, "event_id": event_id}
```

```python
# Before (server.py):
sentry_sdk.capture_message("spinr backend started — Sentry pipeline verified", level="info")

# After:
logger.info("spinr backend started — Sentry pipeline verified")
```

## 8. Rollback plan

`git-revert-safe` — all changes are code-only with no schema or data mutations. A revert restores prior behavior exactly. Already-stamped ignored events in `stripe_events` are harmless (they'd just reappear as "stuck" in the reconciler's next run, which is the pre-fix status quo).

## 9. Verification performed

- [x] Automated tests run — `tests/test_stripe_reconcile.py` (45 passed), `tests/test_admin_users_search.py` (1 passed)
- [ ] Manual repro steps followed in staging
- [x] Blast-radius grep performed — grepped for all callers of `admin_get_users`, `admin_get_drivers`, `_build_or_clause`, `mark_stripe_event_processed`, and `capture_message` in the backend
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — money (Stripe idempotency via `claim_stripe_event`), observability (log levels, Sentry tags)
- [x] Not feature-flagged — all fixes are to error paths or operational noise, not user-visible behavior changes

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
