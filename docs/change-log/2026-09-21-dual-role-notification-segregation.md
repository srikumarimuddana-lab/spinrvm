# Change Impact & Risk Log — dual-role notification segregation

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | mkkreddy52@gmail.com (with Claude Code) |
| Surface(s) | backend (rider-app / driver-app are affected but **not modified** — see §3) |
| Domain (Sentry tag) | `rides`, `payments`, `drivers`, `corporate` (the push paths touched span all four) |
| PR / commit link | branch `claude/peaceful-cray-lymlbz`, base `38a3826`, 10 commits |
| Related issue or gap ID | User bug report (notifications duplicated across both apps); extends `ACTION_ITEMS.md` N10 |

## 1. Issue / gap identified

A user whose phone number is registered as **both** a rider and a driver reported seeing the same
notifications in the rider app and the driver app. Reported directly by the product owner during
live app testing, not by an audit or an automated check.

## 2. Root cause

Three independent defects, all rooted in the same fact: **one phone number is one `users` row.**
`routes/auth.py:1057` reuses the row returned by `get_user_by_phone` on OTP verify and sets
`is_rider` / `is_driver` as flags, so a dual-role person has a single `user_id`. Everything
downstream keys off that id.

1. **The in-app inbox had no app dimension at all.** `notifications` (migration 48) has no
   audience column; `features.py::_record_inbox_notification` wrote `user_id` only; and
   `GET /notifications` filtered on `{"user_id": ...}` alone. Both apps render that same list
   through the shared `useNotifications` hook, both defaulting to an "All" tab whose
   `matchesCategory` returns `true` for every type. **This is the guaranteed duplication** — it
   reproduces 100% of the time, not as a race.
2. **~35 of 95 push call sites did not declare `target_app`.** They fell through to the legacy
   `users.fcm_token` column, which `register_push_token` overwrites on every registration from
   either app — so that column holds whichever app was opened most recently, and those pushes
   landed in an effectively random app.
3. **A latent third path:** `POST /api/v1/users/fcm-token` (`features.py`) was mounted and live,
   wrote only the generic column with no `client_type`, and was called by nothing.

A knock-on of (2) worth recording: `features.py::_build_fcm_message` picks the Android channel as
`"ride-updates" if target_app == "rider" else "ride-offers"`. An undeclared **rider** push was
therefore addressed to the **driver** app's offer channel — which the rider app does not create,
so Android drops it outright. Some of these were not just misrouted, they were undelivered.

## 3. Fix / remediation

- **Migration 436** adds `notifications.audience` (`'rider' | 'driver' | 'both'`, default `'both'`).
- `_record_inbox_notification` derives `audience` from the caller's existing `target_app`, at the
  single choke point every push already flows through — not at 95 call sites.
- `GET /notifications`, `PUT /read-all` and `DELETE /notifications` scope to
  `audience IN (<calling app>, 'both')`.
- **~35 call sites swept** to declare `target_app`; 6 deliberate account-level exceptions remain,
  each with a written reason.
- **Migration 437** adds RLS to `notifications` (pre-existing gap, found while working; see §4).
- Dead `POST /users/fcm-token` removed.

**No rider-app or driver-app change was needed.** The scoping keys off `X-App-Platform`, which both
apps already send on *every* request via `shared/api/client.ts`'s `setAppIdentity()` (verified all
five verbs — get/post/put/patch/delete — spread `appVersionHeader()`), and which
`core/middleware.py`'s `ForcedUpgradeMiddleware` already reads. That is why this ships as a
backend-only diff.

**Alternatives considered** (CLAUDE.md gate 10): filtering by the existing free-form `type`
taxonomy (rejected — several types, e.g. `promotion` / `safety` / `general`, are genuinely shared,
so it would misfile), and client-side filtering in each app (rejected — data still crosses apps,
and the unread badge count stays wrong). The additive column wins because it reuses the
`target_app` signal that already exists and is the only option that also fixes the badge and the
destructive clear-all.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), but it touches a table and a function that nearly every
domain writes to.** Stated explicitly, not assumed — grepped before changing anything:

| Shared thing changed | Who else touches it | Verdict |
|---|---|---|
| `features.py::_record_inbox_notification` | 1 production caller (`send_push_notification`) + 10 test references that pass 4 positional args | Safe — new param is optional and 5th |
| `notifications` table | `features.py` (insert), `routes/notifications.py` (all CRUD), `routes/admin/faqs.py` (insert + read), `db.py`/`routes/admin/auth.py` (module name lists only) | `faqs.py` inserts directly and does **not** set `audience`, so its rows take the `'both'` DB default and stay visible in both apps — correct for an admin broadcast |
| `users.fcm_token` | `features.py`, `utils/push_retry.py`, `routes/auth.py` (logout clear), `routes/admin/vehicle_fleet.py`, `routes/drivers/tax_exports.py` (PII strip lists) | Unchanged behavior — still written on every registration |
| `send_push_notification` | 95 call sites | 89 now declare `target_app`; 6 intentional |

Specifically checked and found **not** to regress:
- `utils/push_retry.py` already honours `target_app` when picking a token (`push_retry.py:148-155`),
  so a retried push routes the same way as its first attempt. It does **not** write an inbox row,
  so there is no double-write.
- `send_dispatch_offer_pushes_batch` is unaffected: `new_ride_assignment` is in
  `_TRANSIENT_NOTIFICATION_TYPES`, so it never wrote an inbox row to begin with.
- No ride-state-machine, money, wallet, or insurance-period code path is touched. No background
  loop's replay-safety is altered (the two loops touched — `payment_retry`, `t4a_annual_job` —
  changed only which token column their push reads).

**Residual risks:**
1. **Deploy ordering is load-bearing — this is a lockstep deploy, NOT git-revert-safe.**
   **Correction (post-review):** an earlier draft of this log said the pre-migration failure mode
   "degrades rather than breaks". That is true only of the *write* path and was wrong as a general
   claim. The `spinr-migration-reviewer` pass caught it and the correction is material:

   - **Write path — degrades.** `_record_inbox_notification` runs as a spawned fire-and-forget
     task whose `_write()` catches and logs at ERROR level. Push *delivery* is unaffected; the
     Notifications page just stops gaining rows.
   - **Read path — hard-fails.** `get_notifications`, `mark_all_read` and `clear_notifications`
     call `get_rows` / `update_one` / `delete_many` with an unconditional `audience` filter and
     **no try/except**. Against a database without the column, `repositories/_base.py` raises,
     `utils/error_handling.py` wraps it as a `DatabaseError` → **503**, and it propagates to the
     global handler. Both apps send `X-App-Platform` on every request today, so `_audience_filter`
     returns a filter on essentially all real traffic — meaning **a full outage of the
     Notifications tab, the unread bell, mark-as-read and clear-all, for every user on both apps**,
     for the entire code-deployed-but-migration-not-applied window.

   This matters because nothing in the repo sequences the two: no workflow invokes
   `run_migrations.py`, Railway and Fly auto-deploy from `main` on merge, and migrations are a
   separate manual step. **Migration 436 must be applied before this code reaches production.**
   Treat the PR as requiring a coordinated deploy, not as revert-safe.
2. **A notification narrowed to the wrong app disappears from the other one.** This is why an
   unset or unrecognised `target_app` maps to `'both'` and is never guessed from `data['type']` or
   the user's role flags: a wrong narrowing hides a message, which is worse than the duplicate it
   replaces. The 6 undeclared sites are left undeclared on purpose.
3. **RLS on `notifications` (437) is new.** It cannot affect live traffic — the backend uses the
   service-role key, which bypasses RLS, and nothing else reads the table (verified: rider-app and
   driver-app contain no Supabase client at all; no `from('notifications')` call exists in any
   surface). Like the rest of the schema's policies these are dormant-but-correct
   (`ACTION_ITEMS.md` C108) — **do not cite this as evidence they are live-enforced.**

## 5. User-experience effect

**Rider and driver, and yes — visible mid-session.** A dual-role user already using either app will
see their Notifications list get shorter as the other role's entries stop appearing. That is the
intended fix, and it is a real, visible change to an already-shipped screen.

- **Single-role users (the overwhelming majority) see no difference.** Their rows are either
  correctly scoped to their one app or default to `'both'`.
- **Already-stored rows are untouched** — they all take the `'both'` default, so nothing vanishes
  from anyone's history when this lands. Only rows written after the deploy are scoped.
- **The unread bell badge** now counts only what the app will actually show. Previously it counted
  the other app's notifications too, which the user could never reconcile by opening the list.
- **"Clear all" stops deleting the other app's history.** Before, a dual-role driver tapping
  "Clear all" in the driver app permanently deleted their rider receipts and refund notices — rows
  that app never displayed and that nothing can restore.
- **No notification copy changed.** Not one title or body string was edited, so there is nothing
  to review against the customer-centric tone standard.
- **Known limit — the two histories are separated, not independent.** An `audience='both'` row is
  by construction shown in both apps, so "Clear all" from either deletes it from both, and
  "mark all read" from either flips its read state for both (`is_read` is one column per row, not
  per app). This is not a regression — before this change *every* row behaved that way — but the
  fix does not reach account-level rows, and `'both'` is also where any future undeclared call
  site lands. Genuinely separating them needs per-app rows or per-app read state, i.e. a schema
  change beyond this diff's scope.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/436_notifications_audience.sql` | New: `audience` column + CHECK | Gives the inbox the per-app dimension it never had |
| `backend/migrations/437_notifications_rls.sql` | New: RLS + owner policies | Pre-existing gap found while working; kept separate so it rolls back independently |
| `backend/features.py` | `_record_inbox_notification` takes/records `target_app`; `scheduled_dispatch` → rider; removed dead `/users/fcm-token`; documented the generic-column branch | Derive audience once at the choke point; close the third clobber path |
| `backend/routes/notifications.py` | `_audience_filter` helper; scoped GET / read-all / clear; documented `register_push_token`'s generic write | The read/mutate half of the segregation |
| `backend/routes/webhooks.py` | 8 sites declare `target_app`; wallet top-up documented as account-level | Stripe-driven pushes were routing at random |
| `backend/routes/drivers/ride_flow.py` | 5 rider-facing sites → `rider` | Rider notifications sent from driver-side handlers |
| `backend/routes/drivers/ride_cancel.py` | 2 sites → `rider` | Same class |
| `backend/routes/drivers/ride_complete.py` | 1 site → `rider` | Same class |
| `backend/routes/drivers/subscriptions.py` | 4 sites → `driver` | Spinr Pass is driver-only |
| `backend/routes/rides/booking.py` | 1 site → `rider`; corrected a stale comment | Comment claimed siblings omit `target_app`; all 7 now declare it |
| `backend/services/corporate_member_offboarding_service.py` | 1 site → `rider` | Rider-facing auto-cancellation |
| `backend/services/corporate_suspension_service.py` | 1 site → `rider` | Same |
| `backend/utils/auto_payout.py` | `_notify` helper → `driver` | Every caller passes a drivers row |
| `backend/utils/payment_retry.py` | 3 sites declared; admin alert documented | Mixed rider/driver/admin recipients |
| `backend/utils/t4a_annual_job.py` | 1 site → `driver` | T4A is a driver tax slip |
| `backend/tests/test_notification_inbox_audience.py` | New (6 tests) | Write-path derivation |
| `backend/tests/test_notification_inbox_app_scoping.py` | New (9 tests) | Read/mutate-path scoping |
| `backend/tests/test_push_target_app_declared.py` | New (4 tests) | Standing static guard for the whole sweep |

## 7. Before / after

```python
# Before — features.py: the inbox row had no app dimension
row = {
    "id": str(uuid.uuid4()),
    "user_id": user_id,
    "title": title, "body": body,
    "type": notification_type,
    "data": data or {}, "is_read": False,
}
```

```python
# After — derived from the target_app the caller already passes
row = {
    ...,
    "audience": target_app if target_app in ("rider", "driver") else "both",
}
```

```python
# Before — routes/notifications.py: every app saw every row
filters: Dict[str, Any] = {"user_id": current_user["id"]}
```

```python
# After — scoped to the calling app, 'both' always included
filters: Dict[str, Any] = {"user_id": current_user["id"]}
audience = _audience_filter(x_app_platform)   # None when header absent/unknown
if audience:
    filters.update(audience)                  # {"audience": {"$in": [platform, "both"]}}
```

```python
# Before — routes/drivers/ride_flow.py: a RIDER push with no target_app
_deps.send_push_notification(
    ride["rider_id"], "Driver Assigned! 🚗",
    "Your driver has accepted the ride and is on the way.",
    data={"type": "driver_accepted", "ride_id": str(ride_id)},
)   # → users.fcm_token → whichever app registered last
```

```python
# After
_deps.send_push_notification(
    ride["rider_id"], "Driver Assigned! 🚗",
    "Your driver has accepted the ride and is on the way.",
    data={"type": "driver_accepted", "ride_id": str(ride_id)},
    target_app="rider",
)   # → users.fcm_token_rider
```

## 8. Rollback plan

No second deploy needed, and **no data-level remediation is required** — this change writes no
money, wallet, ride-state or insurance-period data. The only data it writes is a per-row label on
new notification rows.

1. **Fastest, no deploy:** the scoping is inert without the header value matching. There is no
   feature flag (see §9 for why), so the honest fastest path is (2).
2. **Code revert:** `git revert` the branch. Old code ignores the `audience` column entirely, and
   the column is nullable-by-default with every row set to `'both'` — so a reverted backend reads
   and writes the table exactly as it does today. **Leaving the column in place after a revert is
   safe and is the recommended order** (revert code first, drop the column later or never).
3. **Migration 436 rollback** (only if the column must go):
   ```sql
   ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_audience_check;
   ALTER TABLE notifications DROP COLUMN IF EXISTS audience;
   ```
   Safe at any time: no FK, nothing else reads it, and every row is reconstructible as `'both'`.
4. **Migration 437 rollback** (RLS): drop the 4 policies and
   `ALTER TABLE notifications DISABLE ROW LEVEL SECURITY;` — full statements in the migration's
   own header comment.

Ordering note: because the code writes `audience`, rolling back the *migration* without also
rolling back the *code* reintroduces the failure in §4.1. Revert code first.

## 9. Verification performed

- [x] **Blast-radius grep performed.** Searched: `_record_inbox_notification` callers,
      `"notifications"` table readers/writers, `fcm_token` readers, `send_push_notification` call
      sites (AST, not regex), `useNotifications` callers in both apps, `appVersionHeader()` call
      sites in `shared/api/client.ts`, direct Supabase-client usage in rider-app/driver-app,
      `from('notifications')` across all surfaces, and `RegisterFcmTokenRequest` / `users/fcm-token`
      references before deleting the dead endpoint.
- [x] **Reviewed against CLAUDE.md conventions:** additive-over-destructive (gate 2), surgical
      changes, RLS pattern (followed migration 432's, not migration 06's superseded admin-role
      one), migration naming/append-only/reversibility, and the "don't silently swallow errors"
      rule (no error handling was softened).
- [x] **AST-level verification** of the sweep: 95 call sites, 89 declaring `target_app`, 6
      documented exceptions.
- [x] **Static guard mutation-tested** — removing one `target_app` makes
      `test_push_target_app_declared.py` fail with the expected message, proving it is not vacuous.
- [x] **`test_push_target_app_declared.py` actually executed** (pure stdlib; run against a `pytest`
      stub): all 4 tests pass.
- [x] `ruff check` and `ruff format --check` clean on every changed Python file; `py_compile` clean.
- [x] All 11 pre-commit security checks passed on every commit.
- [ ] **Feature-flagged: NO.** Justification: the change is a correctness fix that only ever
      *narrows* what an app shows, it is inert for single-role users (the large majority), and no
      existing row changes visibility. A flag was offered and explicitly declined in favour of the
      no-behavior-change option for the one genuinely user-visible sub-change (account-level push
      fan-out), which was **not implemented** as a result.

### What was NOT verified

State plainly, because silence would imply coverage that does not exist:

- **The pytest suite was never run.** This container has **no backend dependencies installed at
  all** (`pytest`, `anyio`, `fastapi`, `supabase`, `firebase_admin`, `loguru`, `pydantic` all
  missing) and the network policy returns **HTTP 403 for pypi.org**, so they cannot be installed.
  The 15 tests in `test_notification_inbox_audience.py` and
  `test_notification_inbox_app_scoping.py` are **written but unexecuted** — they need a CI run or a
  local environment before merge. Only `test_push_target_app_declared.py` was actually executed,
  because it is pure stdlib and could be run against a stub.
- **Neither migration was executed against a Postgres instance.** None is reachable from this
  container. Both had static review only (prefix uniqueness, quote balance, statement shape,
  transaction-safety). The `NOT VALID` + `VALIDATE` split and the RLS policies are unproven in
  practice.
- **Nothing was tested against live Supabase**, and no end-to-end dual-role scenario was exercised
  on a device. The core claim — that a dual-role user now sees two distinct inboxes — is **reasoned
  from the code, not observed**. This needs a real two-app manual test with one phone number.
- **No visual-regression coverage applies.** No rider-app or driver-app file was modified, so
  admin-dashboard's seeded Playwright baselines are untouched and irrelevant here. rider-app and
  driver-app have no visual tooling at all — but since neither was changed, there is nothing to
  screenshot.
- **The `X-App-Platform` header was verified by reading `shared/api/client.ts`, not by observing a
  real request.** If an installed build predates `setAppIdentity()`, it sends no header and keeps
  the old unscoped behavior — by design, but unobserved.
- **Load/latency impact of the added `audience` filter is unmeasured.** Reasoned to be negligible
  (the existing `(user_id, created_at DESC)` index still drives the query and `user_id` is highly
  selective) but not benchmarked. See §10 for the reviewer challenge on this.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, with the code-before-migration ordering stated.
- [x] Blast radius is stated, not assumed — every shared consumer named in §4.
- [x] No silent behavior change: the mid-session UX effect is filled in at §5.
- [ ] **Not merge-ready as-is.** Both reviewer passes are complete and all 5 blockers/warnings
      across them are resolved in-branch (§11). Two things still must happen first, neither of
      which can be done from this container: (1) the 15 unexecuted tests must pass in CI, and
      (2) a real dual-role manual test — one phone number, both apps installed. Plus the
      deploy-ordering requirement in §4.1: **migration 436 before the code.**

## 11. Reviewer findings

Both reviewers were run against the actual diff (not the description) per CLAUDE.md gate 10.

### `spinr-migration-reviewer` — verdict: FIX BLOCKERS

| # | Finding | Resolution |
|---|---|---|
| B1 | **Deploy ordering.** Read path has no try/except, so code-before-migration = 503 outage of Notifications on both apps, not a graceful degrade | **Confirmed by reading the code, not taken on trust.** §4.1 corrected above. Residual decision on whether to add a guard is open — see below |
| W1 | No `lock_timeout` on 436's ALTERs, despite the file itself calling the table append-hot; migration 428 has the idiom | **Fixed** — `SET lock_timeout = '5s'` / `RESET`, matching 428 |
| W2 | "No index" reasoning asserted, not measured; no purge job bounds per-user row growth | **Fixed** — comment now states it as an execution-plan argument with the two caveats, and carries the exact `EXPLAIN` + row-count queries to confirm it |
| W3 | 436's rollback said "safe at any time" two lines after establishing it is not | **Fixed** — replaced with the explicit revert-code-first ordering and why |
| W4 | 437 overclaimed `notifications` as "the one" table without RLS; `notification_preferences` also has none | **Fixed** — corrected, and `notification_preferences` recorded as still open |
| — | NOT VALID/VALIDATE split | **Upheld as correct.** `VALIDATE` takes SHARE UPDATE EXCLUSIVE, which does not conflict with writers' ROW EXCLUSIVE — the lighter lock is the point, independent of the outcome being a foregone conclusion |
| — | RLS (437) | **Upheld.** Reviewer independently reconfirmed no Supabase client in either app and no `from('notifications')` in any surface |

### `spinr-notification-ux-reviewer` — verdict: BLOCKER, FIX BEFORE MERGE

It confirmed every `target_app` value this diff *adds* is correct (it traced each to its actual
recipient variable). All three blockers were about things the diff did **not** change — which is
the more useful result, and two of them were failures of my own process.

| # | Finding | Resolution |
|---|---|---|
| B1 | `routes/admin/users.py` hardcoded `target_app="rider"` on a **generic** suspend/ban/reactivate endpoint. Its docstring claimed rider-only but it has no `is_rider`/`is_driver` filter, and the admin dashboard's single Users page posts there for drivers too. Post-436 that files a driver's own ban notice under `audience='rider'` — **permanently invisible in the only app they open** | **Fixed.** Verified independently (no role filter in the handler). Now account-level (`'both'`). Docstring corrected. **My blast-radius check caused this**: I looked for sites *missing* `target_app` and never re-examined existing *declared* ones that migration 436 newly makes decide visibility |
| B2 | The standing guard only matched literal `send_push_notification`, so pushes via `_push_in_background` were invisible — and its allow-list **asserted the forwarder's callers pass `target_app`, which was false for all three**. `payments.py:308` (tip) and `rating.py:134` (rating), both driver-facing, were live instances of this very bug | **Fixed.** Both now `target_app="driver"`. Scanner walks a `_PUSH_CALLABLES` list; the allow-list entry now says it vouches for the forwarding line only. `sharing.py` added as a genuine exception (recipient is an arbitrary phone lookup — role unknowable) |
| B3 | Two new tests called `clear_notifications()` without `read_only=`. Direct-calling a FastAPI route leaves the param bound to the truthy `Query` marker, so `if read_only:` takes the wrong branch — **the assertions would have failed in CI** | **Fixed.** All 8 direct calls pass every Query/Header param explicitly, matching `test_notifications_delete.py`'s existing convention, with a comment so they are not tidied away. Test-only; production resolves defaults through FastAPI's DI |
| W1 | `clear_notifications` / `mark_all_read` docstrings overclaim: an `audience='both'` row is shown in both apps, so clearing or marking-read from either affects both | **Docstrings corrected** to state the limit plainly. Not a regression (pre-diff every row behaved this way) and not fixable with a per-app filter over shared rows — it needs per-app rows or per-app read state. Recorded as known, see §5 |
| I1 | `routes/admin/wallet.py::_wallet_target_app` narrows admin wallet adjustments to one app by role, while this diff leaves Stripe-initiated top-up at `'both'` — same wallet, two audience outcomes | **Left as-is, flagged.** Pre-existing inconsistency in a file this diff does not touch; worth a human decision, not a unilateral change |

**Process lesson worth recording:** gate 1 (blast radius) as I ran it was one-directional. When a
change makes an existing field newly load-bearing, the check must cover every existing *value* of
that field, not only the places where it is absent. B1 and B2 were both missed for that reason.
