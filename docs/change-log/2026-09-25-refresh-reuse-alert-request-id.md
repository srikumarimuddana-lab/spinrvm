# Change Impact & Risk Log — refresh-token reuse alert missing request_id correlation key

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (daily `/sentry-triage --severity-only` scan) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (this branch, `fix/refresh-reuse-alert-request-id`) |
| Related issue or gap ID | Sentry CRIMSON-SMOKE-7445-9; relates to open ACTION_ITEMS.md C2 |

## 1. Issue / gap identified

While investigating CRIMSON-SMOKE-7445-9 ("REFRESH TOKEN REUSE DETECTED") during the daily
severity-scoped Sentry triage scan, the investigator had to hand-correlate the Sentry event to
its `audit_logs` row by matching `user_id` + timestamp, because `scripts/incident-analysis/
correlate_incident.py`'s `request_id`-keyed join returned zero clusters for this alert type.
Neither the Sentry capture (`_capture_reuse_event`) nor either of the two `audit_logs` inserts
in `backend/utils/refresh_tokens.py` (`_handle_refresh_token_reuse`, `_record_post_revoke_race`)
attached a `request_id` — the one correlation key every other `audit_logger.py` write already
carries (migration 279).

## 2. Root cause

`refresh_tokens.py` writes its own `audit_logs` rows directly via `db.insert_one(...)` rather
than going through `utils/audit_logger.py`'s `log_admin_action`/`log_user_action` helpers (it
can't — the actor here is `system:refresh_reuse_detector`, not an authenticated admin/user), so
it never picked up the `request_id` convention those helpers apply automatically. The Sentry
capture in the same file was written independently and never added the tag either.

## 3. Fix / remediation

Per the user's explicit choice (via `AskUserQuestion`, offered against widening the grace window
or suppressing background retries — see "Not changing" below): **the reuse-detection/grace-window
security logic itself is unchanged.** Only the correlation gap is closed:

- `_capture_reuse_event`'s Sentry `tags` dict now includes `"request_id": get_request_id() or
  "unknown"` — a top-level, searchable tag (not buried in `contexts`), matching what
  `correlate_incident.py` and ad-hoc Sentry search actually join on.
- Both `audit_logs` inserts (`_handle_refresh_token_reuse`, `_record_post_revoke_race`) now
  include `"request_id": get_request_id() or None` as a top-level column — the same column
  (migration 279) and the same `or None` (never a literal `""`) convention `audit_logger.py`
  already uses, so the table's partial index (`WHERE request_id IS NOT NULL`) stays meaningful.
- `get_request_id()` reads the same request-scoped `ContextVar` (`utils/log_context.py`)
  populated by `RequestIDMiddleware` on every request, including `/auth/refresh` — the call this
  entire code path runs under, so this is populated for every real occurrence in production. The
  `"unknown"`/`None` fallback exists only for full correctness, not because it's expected to fire
  on this path.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two audit-log payload shapes and one Sentry capture's tags.** No
  change to `_is_benign_rotation_replay`, `REFRESH_REUSE_GRACE_SECONDS`, the cascade steps
  (token_version bump, refresh-token revoke-all, WS kick), or the `lookup_refresh_token` control
  flow. The function signatures, return values, and every existing caller are untouched.
- Grepped every other reader of these two audit rows: `_reuse_already_handled` (reads `details`
  JSON only — `replayed_row_id`, `cascade_ok`, `cascade_revoked_row_ids` — never touches the new
  top-level `request_id` column) and the admin dashboard's `routes/admin/sentry.py`/audit views
  (read by `action`/`entity_id`/`details`, tolerate any additional top-level column). No consumer
  reads or assumes the absence of `request_id` on these two `action=refresh_token_reuse_detected`
  row shapes.
- `db.insert_one` passes the dict through to Supabase/PostgREST as-is; adding a recognized column
  (`request_id` already exists on `audit_logs` since migration 279) cannot fail the insert.

## 5. User-experience effect

None — this is a backend-only observability change. No rider/driver/admin-facing behavior,
copy, or timing changes. Not visible mid-session to anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/refresh_tokens.py` | Added `request_id` to `_capture_reuse_event`'s Sentry tags and to both `audit_logs` inserts | Close the correlation gap `correlate_incident.py` hit during today's Sentry triage |
| `backend/tests/test_refresh_token_reuse_detection.py` | 2 new regression tests: `request_id` present when set, `None`/`"unknown"` fallback when absent | Pin the fixed behavior; confirmed to fail without the fix via `git stash` |

## 7. Before / after

```py
# Before (_capture_reuse_event)
tags={
    "spinr_alert": alert,
    "audience": audience or "unknown",
    "domain": "auth",
    "surface": "backend",
},

# After
tags={
    "spinr_alert": alert,
    "audience": audience or "unknown",
    "domain": "auth",
    "surface": "backend",
    "request_id": get_request_id() or "unknown",
},
```

```py
# Before (both audit_logs inserts)
{
    "id": str(uuid.uuid4()),
    "action": REUSE_AUDIT_ACTION,
    "entity_type": "user",
    "entity_id": user_id or "unknown",
    "actor_id": "system:refresh_reuse_detector",
    "details": json.dumps(details_payload),
}

# After
{
    "id": str(uuid.uuid4()),
    "action": REUSE_AUDIT_ACTION,
    "entity_type": "user",
    "entity_id": user_id or "unknown",
    "actor_id": "system:refresh_reuse_detector",
    "request_id": get_request_id() or None,
    "details": json.dumps(details_payload),
}
```

## 8. Rollback plan

`git revert` is a complete rollback — purely additive tagging, no schema change (the column
already exists), no data migration, no config/flag. Reverting returns to the prior (already
uncorrelatable, not newly-broken) behavior.

## 9a. Adversarial review

`spinr-observability-reviewer` reviewed the diff before commit. Verdict: **SHIP WITH SMALL
CHANGE**, both applied:

1. The Sentry tag's original `get_request_id() or "unknown"` fallback was replaced with omitting
   the `request_id` key entirely when empty. Reasoning: `correlate_incident.py` groups on `if
   req_id:` (truthy check) — a literal `"unknown"` string is truthy, so every event missing a real
   request context would have been bucketed into one artificial merged cluster that no audit_logs
   row (never keyed `"unknown"`) could ever join against. Low real-world severity (every call site
   runs inside `/auth/refresh`'s request-handled flow today) but a needless correlation-fabrication
   risk in code whose entire purpose is fixing correlation.
2. Added a third regression test (`test_post_revoke_race_audit_row_carries_request_id`) covering
   `_record_post_revoke_race`'s own, separately-written `audit_logs` insert literal — the two
   original tests only exercised `_handle_refresh_token_reuse`'s insert, so a typo on the second
   insert site would have shipped with green tests.

All other checks (tag-vs-context placement, migration-279 column match, no PII, no other consumer
broken, security logic untouched) passed clean — see the reviewer's INFO findings.

## 9. Verification performed

- [x] Automated tests run — `python -m pytest tests/test_refresh_token_reuse_detection.py -q
  --no-cov` (40 passed)
- [x] Regression proof — all 3 new/updated tests confirmed to FAIL (`KeyError: 'request_id'`)
  without the fix (`git stash` on `backend/utils/refresh_tokens.py` alone) and PASS with it
- [x] `ruff check utils/refresh_tokens.py` — clean
- [ ] Manual repro against real Supabase/Sentry — not performed; no staging/production access in
  this sandboxed session; verified via the existing mocked unit-test harness only
- [x] Blast-radius grep performed — confirmed no other code path reads these two audit rows'
  `request_id` column or depends on its absence (see §4)
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — Observability Conventions' Sentry
  tagging rules and the `audit_logs.request_id` correlation convention (migration 279); dispatched
  `spinr-observability-reviewer` before commit

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — the cascade's security decisions
  (bump/revoke/kick/grace-window) are byte-for-byte unchanged; only new metadata is added to
  events that already fire

## What was NOT verified

- Not tested against a real Sentry project or Supabase instance — verified via the existing mocked
  unit-test harness only.
- Whether `correlate_incident.py` actually produces a non-empty cluster for a live occurrence of
  this alert end-to-end was not re-run against a real event (none available in this session) — the
  fix targets the exact gap the investigator's live run identified (missing `request_id` on both
  sides of the join), but the join itself was not re-exercised live.

## Not changing but considered

Per the user's explicit decision (`AskUserQuestion`), the security/UX tradeoff underlying
CRIMSON-SMOKE-7445-9 itself — `REFRESH_REUSE_GRACE_SECONDS` duration, or suppressing the
driver-app background refresh retry after a prolonged outage — is deliberately **not** touched by
this PR. This PR ships only the additive, non-behavior-changing correlation guardrail; the
grace-window/retry-suppression question remains open human-triage backlog, tracked against
CRIMSON-SMOKE-7445-9 and the already-open ACTION_ITEMS.md C2 (missing Sentry alert rule for this
message).
