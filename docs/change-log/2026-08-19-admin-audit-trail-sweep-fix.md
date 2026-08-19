# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | local worktree, commits `d4005dc`..`70c9798` (13 commits, not pushed / no PR — see task instructions), all on top of `ab241af` (branch `worktree-agent-a0347b757a49c12e1` / `claude/spinr-app-all-surfaces-de596c`) |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md`, ranked-blocker #18 / baseline #12 ("~12 admin actions have no audit trail"). The single highest-severity instance (`POST /admin/redis/flush-prefix`) was already fixed in an earlier session — see `docs/change-log/2026-08-19-redis-flush-prefix-audit-log-fix.md`, which this sweep follows exactly (same helper, same act-then-log convention, same actor-id-only PIPEDA rule). |

## 1. Issue / gap identified

Across `backend/routes/admin/*.py`, a real count of **50 state-changing admin endpoints** (not the audit doc's rough "~12" estimate) wrote no row to `audit_logs` when an admin performed a mutating action — creates, updates, deletes, uploads, bulk operations. This is a much larger gap than the audit's headline number, discovered by systematically enumerating every `@router.post/put/patch/delete` decorator across the module and cross-referencing which handler bodies already call an audit-write path.

## 2. Root cause

Endpoints were built without the audit-log call as an oversight, following the same pattern noted in the redis-flush-prefix fix's root-cause note: some files (`wallet.py`, `documents.py`, `driver_action`, `settings.py`'s `admin_update_settings`) already had the convention established and used it correctly; many others simply never got it wired in when the endpoint was first written, and no lint/CI check catches a missing audit call. A few files (`ai_console.py`, `data_transfer_export.py`, `sentry.py`) looked "missing" under a naive grep but were confirmed correct on inspection — see Section 4.

## 3. Fix / remediation

For each of the 50 confirmed-missing endpoints, added an `await log_admin_action(admin, action, entity_type, entity_id, details)` call (`utils/audit_logger.py`, same helper/table/schema used everywhere else) immediately after the mutating operation succeeds (act-then-log, matching the redis-flush-prefix precedent and the pattern already used by `drivers.py`'s `admin_driver_action`, `service_areas.py`'s `admin_create_service_area`, etc.). Where an endpoint didn't previously take an `admin: dict = Depends(get_admin_user)` parameter (auth was enforced only at `include_router(..., dependencies=[Depends(require_module(...))])` level, so the endpoint itself never received the caller's identity), added that parameter — FastAPI de-duplicates the dependency resolution within a request, so this is free (no double token verification). `actor_id` only, no admin name/email, per CLAUDE.md's PIPEDA logging rule (unchanged from the existing helper's contract).

Business logic, response shapes, and auth gating are unchanged in every file — this is purely additive observability.

### Full list of endpoints fixed (file:line references the pre-fix line where the missing call was found)

| # | File | Method | Path | What it does |
|---|---|---|---|---|
| 1 | `driver_appeals.py:70` | POST | `/driver-appeals/{appeal_id}/resolve` | Approve/deny a driver deactivation appeal (explicitly named by the audit as still-open) |
| 2 | `faqs.py:88` | POST | `/faqs` | Create FAQ entry |
| 3 | `faqs.py:107` | PUT | `/faqs/{faq_id}` | Update FAQ entry |
| 4 | `faqs.py:140` | DELETE | `/faqs/{faq_id}` | Delete FAQ entry |
| 5 | `faqs.py:151` | POST | `/notifications/send` | Send admin notification (user/broadcast) |
| 6 | `incentives.py:120` | POST | `/incentives` | Create driver incentive |
| 7 | `incentives.py:153` | PATCH | `/incentives/{incentive_id}` | Update driver incentive |
| 8 | `incentives.py:178` | PATCH | `/incentives/{incentive_id}/toggle` | Toggle incentive active/inactive |
| 9 | `incentives.py:207` | DELETE | `/incentives/{incentive_id}` | Delete driver incentive |
| 10 | `legal_documents.py:49` | PUT | `/legal-documents` | Upsert legal document (ToS/Privacy Policy, PIPEDA consent-version-bearing) |
| 11 | `maintenance.py:65` | POST | `/maintenance/cleanup-location-history` | Bulk-delete old GPS history rows |
| 12 | `maintenance.py:104` | POST | `/maintenance/rollup-driver-daily` | Nightly driver-stats rollup (bulk upsert to `driver_daily_stats`) |
| 13 | `messaging.py:277` | POST | `/cloud-messaging/send` | Send/schedule cloud message |
| 14 | `messaging.py:484` | POST | `/marketing/suppressions` | Add marketing suppression |
| 15 | `messaging.py:497` | DELETE | `/marketing/suppressions/{id}` | Remove marketing suppression (docstring already claimed audit existed — it did not) |
| 16 | `messaging.py:512` | DELETE | `/cloud-messaging/{message_id}` | Cancel scheduled cloud message |
| 17 | `monitoring.py:264` | POST | `/migrate-profile-images` | Base64→storage profile-image backfill |
| 18 | `rides.py:389` | POST | `/rides/{ride_id}/cancel` | Admin force-cancel an in-flight ride |
| 19 | `rides.py:3279` | POST | `/payouts/{payout_id}/retry` | Retry a single failed payout |
| 20 | `rides.py:3328` | POST | `/payouts/bulk-retry` | Bulk-retry failed/cancelled payouts |
| 21 | `rides.py:3533` | POST | `/rides/regenerate-imported-snapshots` | Bulk-regenerate ride map-snapshot images |
| 22 | `service_areas.py:998` | POST | `/areas/{area_id}/fees` | Create area fee |
| 23 | `service_areas.py:1018` | PUT | `/areas/{area_id}/fees/{fee_id}` | Update area fee |
| 24 | `service_areas.py:1042` | DELETE | `/areas/{area_id}/fees/{fee_id}` | Delete area fee |
| 25 | `settings.py:739` | PUT | `/settings/heatmap` | Update heat-map display settings |
| 26 | `support.py:266` | POST | `/disputes` | Create dispute |
| 27 | `support.py:299` | PUT | `/disputes/{dispute_id}` | Update dispute |
| 28 | `support.py:343` | DELETE | `/disputes/{dispute_id}` | Delete dispute |
| 29 | `support.py:377` | POST | `/tickets` | Create support ticket |
| 30 | `support.py:442` | POST | `/tickets/{ticket_id}/close` | Close support ticket |
| 31 | `support.py:453` | PUT | `/tickets/{ticket_id}` | Update support ticket |
| 32 | `support.py:471` | DELETE | `/tickets/{ticket_id}` | Delete support ticket |
| 33 | `support.py:552` | DELETE | `/flags/{flag_id}` | Permanently delete a flag |
| 34 | `support.py:562` | POST | `/rides/{ride_id}/complaint` | Create rider/driver complaint |
| 35 | `support.py:639` | DELETE | `/complaints/{complaint_id}` | Delete complaint |
| 36 | `drivers.py:1916` | POST | `/drivers/{driver_id}/notes` | Add driver note |
| 37 | `drivers.py:1939` | DELETE | `/drivers/notes/{note_id}` | Delete driver note |
| 38 | `drivers.py:3772` | PUT | `/drivers/{driver_id}/area` | Assign driver to service area |
| 39 | `vehicle_fleet.py:174` | POST | `/vehicle-types` | Create vehicle type |
| 40 | `vehicle_fleet.py:196` | PUT | `/vehicle-types/{type_id}` | Update vehicle type |
| 41 | `vehicle_fleet.py:231` | POST | `/vehicle-types/{type_id}/upload-illustration` | Upload vehicle illustration image |
| 42 | `vehicle_fleet.py:303` | POST | `/vehicle-types/{type_id}/upload-marker` | Upload vehicle map-marker image |
| 43 | `vehicle_fleet.py:370` | DELETE | `/vehicle-types/{type_id}` | Delete vehicle type |
| 44 | `vehicle_fleet.py:446` | POST | `/fare-configs` | Create fare config |
| 45 | `vehicle_fleet.py:469` | PUT | `/fare-configs/{config_id}` | Update fare config |
| 46 | `vehicle_fleet.py:495` | DELETE | `/fare-configs/{config_id}` | Delete fare config |
| 47 | `vehicle_fleet.py:507` | POST | `/rides/{ride_id}/lost-and-found` | Report lost item |
| 48 | `vehicle_fleet.py:569` | PUT | `/lost-and-found/{item_id}/resolve` | Resolve/unresolve lost item |
| 49 | `vehicle_fleet.py:630` | PUT | `/lost-and-found/{item_id}` | Update lost item |
| 50 | `vehicle_fleet.py:652` | DELETE | `/lost-and-found/{item_id}` | Delete lost item |

(50 distinct endpoints fixed. `maintenance.py`'s `rollup-driver-daily` at #12 was a bonus find — it reads, not writes, `audit_logs`, so it wasn't caught by the initial detection script and was added to scope during investigation of that file.)

## 4. Endpoints deliberately NOT touched, with reason

Per the task's "no silent caps" requirement, every endpoint my enumeration script initially flagged as "missing" but that I did **not** change is named here, with why:

**Genuinely read-only / no state change (correctly out of scope per CLAUDE.md's GET-vs-mutation framing, even though implemented as POST due to body-carried filters):**
- `drivers.py` `admin_search_drivers` (POST `/drivers/search`) — read-only search
- `users.py` `admin_search_users` (POST `/users/search`) — read-only search
- `rides.py` `admin_promo_preview` (POST `/promo/preview`) — read-only fare/promo preview
- `support_tickets.py` `test_connection` (POST `/config/test`) — read-only connectivity probe, no persisted state change

**Validate-only / dry-run endpoints (no commit, no state change — their paired `.../commit` endpoint already had audit coverage before this sweep and was left as-is):**
- `booking_import.py` `validate_booking_import`
- `data_transfer_import.py` `validate_bundle_import`
- `driver_import.py` `validate_driver_import`
- `rider_import.py` `validate_rider_import`
- `stripe_import.py` `validate_stripe_import`
- `stripe_payout_sync.py` `validate_payout_sync`
- `tax_id_import.py` `validate_tax_id_import`

**False positives — already audited via an indirection my regex-based enumeration script missed (verified by reading the code, no fix needed):**
- `data_transfer_export.py` `export_entities` (POST `/data-transfer/export`) — the actual gather/build/upload work runs in a `BackgroundTasks`-scheduled function `_run_export_job`, which already calls `log_admin_action` with `doc_file_types` in its details (act-then-log, since the outcome — success/failure, entity count — is only known after the background job completes). The route itself only records a `pending` job row and returns immediately.
- `sentry.py` `update_sentry_issue_status` (POST `/issues/{issue_id}/status`) — delegates to `_update_issue_status`, which already writes an audit row with `{status, surface, project}` before returning.
- `settings.py` `admin_upload_ride_offer_sound` and `admin_update_settings` — both already write directly to `audit_logs` via `db_supabase.insert_one` (not through the `log_admin_action` helper, which is why the script's initial coarse regex pass flagged them, but the refined pass confirmed they're covered).
- `wallet.py` `admin_credit_wallet` / `admin_debit_wallet` — same pattern, already write `audit_logs` rows directly with `transaction_id`, `old_balance`, `new_balance`.
- `users.py` `admin_update_user_status` / `admin_update_user_flags` / `admin_update_dsar_status`, `staff.py`'s 4 endpoints, `ai_console.py`'s `admin_ai_chat` — all already audit via a direct `db_supabase.insert_one("audit_logs", ...)` call or a local `_audit()` helper wrapping the same table/schema.

None of the above needed any code change; all were verified by reading the actual handler body, not assumed safe from the initial grep.

## 5. Risk & impact on existing functionality

- **Blast radius: additive, backend-only, 13 files.** Every change is either (a) inserting a new parameter with a FastAPI `Depends(get_admin_user)` default into a handler signature, or (b) inserting a new `await log_admin_action(...)` call after the existing mutation. No existing line of business logic, no response field, no status code, and no auth-gating dependency was altered.
- **Every endpoint already ran behind admin auth** — either via `Depends(get_admin_user)` on the handler itself, or via `dependencies=[Depends(require_module(...))]` at `include_router()` time (which itself depends on `get_admin_user`). Adding `admin: dict = Depends(get_admin_user)` as an explicit handler parameter where it wasn't already present does not change auth: FastAPI resolves and caches a dependency once per request, so a second `Depends(get_admin_user)` call is a no-op lookup, not a second token verification. Confirmed by grep: `require_module()`'s own implementation (`dependencies/__init__.py`) itself depends on `get_admin_user` and returns its result, so both spellings resolve to the identical cached object within one request.
- **`audit_logs` table**: reuses the existing table/schema/helper used by dozens of other admin routes already (see the many pre-existing correct callers named in Section 4). No migration, no schema change. Adds roughly one row per successful admin mutation across these 50 endpoints — negligible volume relative to existing admin-action audit traffic (these are all manual, human-triggered admin actions, not high-frequency background writes).
- **Could the new audit write ever block, corrupt, or delay the underlying mutation?** No. In every case the mutation (`insert_one`/`update_one`/`delete_many`/`delete_one`/storage upload) is fully awaited and completed *before* the `log_admin_action` call is reached — act-then-log, matching the established convention. `log_admin_action()` itself is `try/except`-wrapped internally (`utils/audit_logger.py`) and never raises; a failed audit write is self-logged at `error` level and returns `None`, but the calling endpoint's response is unaffected. No endpoint's response shape changed to include a new required field — several already had `audit_log_id` in their return payload from before this sweep (e.g. `support.py`'s `admin_resolve_dispute`), and I did not add that field to endpoints that didn't already have it, to keep this purely additive without changing any documented response contract.
- **Driver appeals specifically**: `driver_appeals.py`'s resolve endpoint already reuses `admin_driver_action` for the approve→reactivate/unban path, which independently writes its own `driver_{action}` audit row. The new `driver_appeal_{approved|denied}` row added here is a *different* action on a *different* entity (`driver_appeals`, not `drivers`) — it covers the appeal-decision event itself (including the `denied` path and `needs_review`/`other` appeal types where no driver-status transition happens at all, so no other audit row would exist for those). No double-counting of the same fact; these are two distinct facts about two distinct tables.
- **No other consumer of any touched function's signature was found** — every added `admin: dict = Depends(...)` parameter is additive (FastAPI resolves it from the request, not from any caller-supplied positional argument), so no other backend module that might import and call these functions directly (none were found via grep) would break. The admin-dashboard frontend calls these exclusively over HTTP, which is unaffected by a Python-level parameter added with a `Depends` default.
- **Rate limiting, feature flags, ride/dispatch state machine, wallet deltas, Stripe flows**: untouched. None of these 50 endpoints are part of the ride state machine, dispatch, or a Stripe webhook path; the one ride-state-adjacent endpoint (`admin_cancel_ride`) had its cancellation logic, WS events, and push-notification behavior left byte-for-byte unchanged — only the new audit call was appended after the existing `manager.broadcast_to_admins` best-effort block.

## 6. User-experience effect

- Rider/driver/corporate-admin facing: **none.** All 51 endpoints are internal-admin-only surfaces (admin dashboard).
- Internal-admin facing: **none, from the operator's point of view.** Every endpoint's request/response contract, status codes, and validation behavior are byte-for-byte unchanged — the admin dashboard UI will not notice any difference. Not visible mid-session to any rider or driver.

## 7. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/driver_appeals.py` | Added `log_admin_action` import + call after appeal resolution | Close audit gap for the explicitly-named driver-appeals resolve endpoint |
| `backend/routes/admin/faqs.py` | Added imports + audit calls to 4 endpoints (create/update/delete FAQ, send notification) | Close audit gap |
| `backend/routes/admin/incentives.py` | Added imports + audit calls to 4 endpoints (create/update/toggle/delete incentive) | Close audit gap |
| `backend/routes/admin/legal_documents.py` | Added imports + audit calls to upsert endpoint (create + update branches) | Close audit gap on a PIPEDA-consent-relevant document |
| `backend/routes/admin/maintenance.py` | Added `admin` param + audit calls to GPS cleanup + driver-daily rollup | Close audit gap |
| `backend/routes/admin/monitoring.py` | Added audit call to profile-image backfill (import already present) | Close audit gap |
| `backend/routes/admin/settings.py` | Added audit call to heatmap-settings update (import already present) | Close audit gap |
| `backend/routes/admin/drivers.py` | Added `admin` param + audit calls to note add/delete + area assignment (3 endpoints) | Close audit gap |
| `backend/routes/admin/messaging.py` | Added imports + audit calls to 4 endpoints (cloud-message send/cancel, suppression add/delete) | Close audit gap |
| `backend/routes/admin/service_areas.py` | Added audit calls to 3 area-fee endpoints (import already present) | Close audit gap |
| `backend/routes/admin/rides.py` | Added audit calls to ride cancel, payout retry (single+bulk), snapshot regeneration (4 endpoints; import already present) | Close audit gap |
| `backend/routes/admin/support.py` | Added `admin` param + audit calls to 10 endpoints (disputes x3, tickets x4, flag delete, complaints x2) | Close audit gap |
| `backend/routes/admin/vehicle_fleet.py` | Added imports + audit calls to 12 endpoints (vehicle types x5, fare configs x3, lost-and-found x4) | Close audit gap |
| 20 `backend/tests/test_*.py` files | Extended/added tests asserting each new audit call fires with expected `(admin, action, entity_type, entity_id, details)`; fixed pre-existing tests broken by the new required-but-`Depends`-defaulted `admin` parameter (direct function calls in unit tests bypass FastAPI's dependency resolution, so `admin=<value>` must now be passed explicitly where the function reaches the new audit call) | Regression coverage; keep the existing suite green |

## 8. Before / after

Representative example (`vehicle_fleet.py`'s `admin_delete_fare_config` — same shape across all 51):

```python
# Before
@router.delete("/fare-configs/{config_id}")
async def admin_delete_fare_config(config_id: str):
    """Delete fare configuration."""
    await db_supabase.delete_many("fare_configs", {"id": config_id})
    await invalidate_fare_cache()
    return {"message": "Fare configuration deleted"}
```

```python
# After
@router.delete("/fare-configs/{config_id}")
async def admin_delete_fare_config(config_id: str, admin: dict = Depends(get_admin_user)):
    """Delete fare configuration."""
    await db_supabase.delete_many("fare_configs", {"id": config_id})
    await invalidate_fare_cache()
    await log_admin_action(admin, "fare_config_deleted", "fare_configs", config_id, {})
    return {"message": "Fare configuration deleted"}
```

## 9. Rollback plan

No feature flag / `app_settings` toggle applies here — every change is a pure observability addition (new `audit_logs` rows + one new `Depends` parameter per endpoint) with zero effect on business logic, ride state, driver state, or money. If any of this needs to be reverted:
- `git revert <commit-sha>` for the specific file's commit (13 separate commits, one per file/small group, listed above) restores prior behavior exactly for that file — safe because none of these commits touch live data rows beyond adding new `audit_logs` rows going forward; no existing row in `drivers`, `rides`, `fare_configs`, `vehicle_types`, `support_tickets`, `disputes`, `complaints`, `flags`, `lost_and_found`, `area_fees`, `incentives`, `faqs`, `legal_documents`, `cloud_messages`, or `marketing_suppressions` is mutated differently than before.
- No migration was added anywhere (all 51 fixes reuse the existing `audit_logs` table), so there is no migration rollback needed.
- Reverting any subset of the 13 commits independently is safe — they touch disjoint files with no cross-file dependency introduced by this sweep.

## 10. Verification performed

- [x] **pytest run via the project venv**, per file group and as one combined run:
  - `/tmp/spinr-venv/bin/pytest tests/test_admin_*.py -q --no-cov` → **1447 passed, 1 skipped** (full admin test suite, includes every file touched in this sweep plus every other pre-existing admin test — confirms no regression anywhere in `routes/admin/`).
  - Additionally ran the specifically-touched non-`test_admin_*`-prefixed files together (`test_legal_documents.py`, `test_email_deliverability.py`, `test_ride_accept_flow.py`, `test_messaging_fan_out.py`, `test_n10_admin_push_target_app.py`, plus the new/extended `test_admin_*` files) → **75 passed**.
  - Every new/extended test asserts the audit call fires with the expected `(admin, action, entity_type, entity_id, details)` tuple, following the exact `patch.object(module, "log_admin_action", AsyncMock(...))` / `patch(module.log_admin_action, ...)` pattern established by the redis-flush-prefix fix's own tests.
- [x] `ruff check` on every one of the 13 modified `routes/admin/*.py` files plus all 20 modified/added test files → **all clean**. The only ruff findings anywhere in the diffed file set are 4 pre-existing `B904` findings in `drivers.py` at lines 3144/3171/3370/3386, confirmed via `git diff --stat` to be outside this sweep's actual diff (my changes there are 3 endpoints at lines ~1913-1943 and ~3774-3787, nowhere near the flagged lines) — these are noted per CLAUDE.md's "CI red for a reason unrelated to your diff" guidance, not fixed here (out of scope, pre-existing).
- [x] Enumeration re-run after all fixes: the same regex-based scan across every `routes/admin/*.py` file that originally found the 50 gaps was re-run at the end and confirms **zero remaining state-changing endpoints without an audit call**, modulo the explicitly-named exclusions in Section 4.
- [x] Blast-radius grep performed for every touched function's signature: no other backend module calls any of the 50 route functions directly (they are only ever invoked via FastAPI routing from the admin dashboard over HTTP), so the added `Depends(get_admin_user)` parameters have no other caller to break.
- [x] Reviewed against relevant CLAUDE.md conventions: Observability ("Security-relevant events ... admin actions → audit table + info log"), PIPEDA logging (actor_id only, no name/email — unchanged, inherited from the existing helper), "do not silently swallow errors" (unaffected — no error-handling paths were touched), batch-size rule (13 commits, each scoped to one file or a tightly related small group, all under ~200 lines diff except `vehicle_fleet.py`'s 12-endpoint file which is a single logical "close every gap in this file" unit).
- [x] Feature-flagging: not applicable — purely additive observability to already-shipped, already-admin-gated endpoints; no new user-visible behavior, no new validation rule, no UX to gate.
- **Not run**: no `admin-dashboard` frontend build (`npm run build`) — this sweep is 100% backend Python; no `.ts`/`.tsx` file was touched, and no response field consumed by the frontend was added, removed, or renamed on any of the 50 endpoints.

## 11. What was NOT verified

- **Not tested against a real production build or staging Supabase instance** — all verification is via `pytest` against `mock_supabase_client`/`AsyncMock`-patched `db_supabase` calls, per this repo's unit-test convention. No manual repro against live Supabase was performed.
- **No visual/UI regression tooling exists for the admin dashboard** (a standing gap per CLAUDE.md's Pre-merge release gates #6, tracked separately in `ACTION_ITEMS.md`) — since this sweep changed zero frontend code and zero response shapes, there is nothing UI-visible to screenshot, but this is stated explicitly rather than left to silent inference.
- **The exact wording/taxonomy of each new `action` string** (e.g. `"vehicle_type_created"`, `"fare_config_deleted"`) was chosen to match the naming convention of nearby already-correct endpoints in the same file, but was not cross-checked against any downstream consumer of `audit_logs.action` values (e.g. an admin-dashboard audit-log viewer's filter dropdown, if one hardcodes an action-name allowlist) — grep found no such hardcoded allowlist in `backend/`, but the `admin-dashboard` frontend was not searched, since no frontend file was touched or needed changing for this backend-only fix.
- **Rate-limit/throughput impact of the new audit writes was not load-tested** — these are all low-frequency, manual, human-triggered admin actions (not per-request or per-ride hot paths), so this is judged low-risk without a formal benchmark, consistent with how the redis-flush-prefix fix reasoned about the same category of risk.
