# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | ACTION_ITEMS.md C23, Action item 1 of 5 |

## 1. Issue / gap identified

`charge.dispute.created` records a chargeback and nothing else happens. Specifically (item 1 of C23's 4-part finding): Stripe sends `dispute.evidence_details.due_by` on every dispute-created event — the evidence-submission deadline (7–21 days depending on card network) — and the webhook handler drops it entirely. Miss that date and the dispute is lost automatically with no evidence considered; today nothing tracks or warns as it approaches.

## 2. Root cause

`charge.dispute.created`'s `stripe_disputes` insert (`backend/routes/webhooks.py`) was written to capture only the fields needed for the immediate ride-status flip (`amount`, `reason`, `status`) — the evidence deadline was never added to the insert payload or the table schema.

## 3. Fix / remediation

Scoped to Action item 1 only, per C23's own stated priority order (items 2–5 — alerting, admin UI, evidence-pack endpoint, submission path — are separate, larger follow-ups, each with their own tradeoffs already discussed in the ACTION_ITEMS.md entry):

- **Migration 326**: additive, nullable `evidence_due_by timestamptz`, `evidence_submitted_at timestamptz`, `fee_cents integer` columns on `stripe_disputes`. Only `evidence_due_by` is populated by this change; the other two are placeholders for later C23 actions (evidence submission, ledger fee reconciliation) so a future narrower change doesn't need its own migration.
- **`charge.dispute.created` handler**: reads `data_object["evidence_details"]["due_by"]` (Stripe's Unix-seconds timestamp), converts to an ISO `timestamptz` string via `datetime.fromtimestamp(due_by_epoch, tz=timezone.utc)`, and includes it in the `stripe_disputes` insert. Absent/malformed `due_by` leaves the column `NULL` and logs a warning (never silently swallowed, never a 500 — matches this webhook's established never-fail posture for defensive branches).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped `backend/routes/`, `backend/services/`, `backend/repositories/` — `webhooks.py` is the only reader/writer of `stripe_disputes` anywhere in the backend. No admin route, no other service, nothing else touches this table (matches C23's own finding: "Card-network chargebacks are visible only via SQL or the Stripe Dashboard").
- **Purely additive**: three new nullable columns, no existing column/index/RLS policy touched. `stripe_disputes` already has RLS from migration 88 (admin-read policy); this migration doesn't need its own RLS changes since it isn't creating a new table.
- **No behavior change to any existing code path** other than one new key in one `insert_one` call's payload dict — the `charge.dispute.closed`/`charge.dispute.updated` handlers (B27) and the admin-facing `disputes` page (a different table, rider-raised refund requests, unaffected) are all untouched.
- **No index added yet**, deliberately — no query filters on `evidence_due_by` exists in this change, and CLAUDE.md's migration convention only calls for an index alongside an actual new query pattern. The natural first reader (a T-3-days alerting loop) is Action item 2, a separate follow-up; the index is deferred to that change (commented out in the migration with an explicit note).

## 5. User-experience effect

None rider/driver-facing. None admin-facing yet either — this change only starts capturing the deadline; nothing surfaces it in any UI (that's Action items 2–3, not in scope here).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/326_stripe_disputes_evidence_tracking_columns.sql` | New — adds `evidence_due_by`/`evidence_submitted_at`/`fee_cents` to `stripe_disputes` | C23 Action 1 |
| `backend/routes/webhooks.py` | `charge.dispute.created` now parses and stores `evidence_details.due_by` | Same |
| `backend/tests/test_routes_webhooks_coverage.py` | 3 new tests: happy-path conversion, absent-due_by leaves NULL, malformed-due_by logs and leaves NULL | Regression coverage |
| `docs/change-log/2026-08-17-c23-dispute-evidence-due-by.md` | This file | Mandatory Change Impact Log |

## 7. Before / after

```python
# Before
await db_supabase.insert_one(
    "stripe_disputes",
    {
        "id": str(__import__("uuid").uuid4()),
        "stripe_dispute_id": dispute_id_stripe,
        "payment_intent_id": payment_intent_id,
        "ride_id": ride_id,
        "amount_cents": dispute_amount_cents,
        "reason": dispute_reason,
        "status": dispute_status,
        "stripe_event_id": event_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    },
)
```

```python
# After
evidence_due_by = None
due_by_epoch = (data_object.get("evidence_details") or {}).get("due_by")
if due_by_epoch:
    try:
        evidence_due_by = datetime.fromtimestamp(due_by_epoch, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError) as due_by_err:
        logger.warning("Dispute %s: could not parse evidence_details.due_by=%r: %s", ...)

await db_supabase.insert_one(
    "stripe_disputes",
    {
        ...,
        "evidence_due_by": evidence_due_by,
        ...
    },
)
```

## 8. Rollback plan

`git-revert-safe`. Migration's own header states the exact rollback: `ALTER TABLE stripe_disputes DROP COLUMN IF EXISTS evidence_due_by, DROP COLUMN IF EXISTS evidence_submitted_at, DROP COLUMN IF EXISTS fee_cents;` — safe at any point, no other column/function/view depends on these three. No data was mutated by this change (only new rows going forward carry a value in the new columns).

## 9. Verification performed

- [x] `pytest backend/tests/test_routes_webhooks_coverage.py backend/tests/test_webhooks_main.py backend/tests/test_webhooks_coverage_gap.py backend/tests/test_disputes_admin_coverage.py backend/tests/test_routes_disputes_coverage.py backend/tests/test_dispute_refund_cents.py -q --no-cov` — 186/186 pass.
- [x] `ruff check` + `ruff format --check` on both touched Python files — clean.
- [x] Blast-radius grep: `stripe_disputes` has exactly one reader/writer in the backend (`webhooks.py`) — confirmed isolated.
- [x] `spinr-migration-reviewer` review requested for this migration + handler change before PR creation (Codex auto-review off per CLAUDE.md C7/C9). Verdict: SAFE TO APPLY, no blockers. One nitpick fixed before merge: `if due_by_epoch:` would have treated a literal Unix epoch (`0` = 1970-01-01) as "absent" instead of parsing it — changed to `if due_by_epoch is not None:`, with a new regression test (`test_dispute_created_due_by_epoch_zero_is_parsed_not_treated_as_absent`) pinning it.
- [ ] Not run against a real Stripe test-mode webhook or live Supabase — verified via unit tests with a hand-constructed `evidence_details.due_by` payload matching Stripe's documented shape (Unix seconds).

## What was NOT verified

- Whether Stripe's actual `due_by` value is always an integer (vs. occasionally a float or string in some edge case) — the conversion handles `TypeError`/`ValueError`/`OSError` defensively, but this wasn't checked against a real captured webhook payload.
- Items 2–5 of C23 (alerting, admin UI, evidence-pack endpoint, submission path) — explicitly out of scope for this change, left open in `ACTION_ITEMS.md`.
- `evidence_submitted_at`/`fee_cents` — added as placeholder columns for future changes, not populated or exercised by anything in this change.
