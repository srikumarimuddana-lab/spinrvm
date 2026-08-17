# Change Impact & Risk Log — rider_import_service.py created_at bug corrupts new_user_days promo eligibility (CR-4105)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (promo redemption is money-adjacent; closest fit — could also be argued as `corporate`/`rides`-adjacent, but the exposure is discount dollars, not a corporate wallet) |
| PR / commit link | (this PR) |
| Related issue or gap ID | Fixes #4105; corrects the stale note in `ACTION_ITEMS.md` A34 ("Dual-run cutover readiness audit") |

## 1. Issue / gap identified

`backend/services/rider_import_service.py`'s `build_plan()` stamps
`created_at = datetime.now(timezone.utc).isoformat()` on every net-new
imported rider (line 315). `backend/routes/promotions.py`'s
`new_user_days`-type promo eligibility check (`_validate_promo_for_user`
rule 7, and the equivalent filter in `list_available_promos`) gates purely
on `(now - users.created_at).days`. Combined, an old-app customer imported
via CSV today reads as a brand-new signup and qualifies for `new_user_days`
promos — typically higher-value first-ride incentives — regardless of how
long they've actually been a Spinr/old-app customer.

## 2. Root cause

The rider CSV importer has no source column to recover a real historical
signup date from. Confirmed by reading the schema directly:
`REQUIRED_COLUMNS = {"phone"}`, `OPTIONAL_COLUMNS = {"customer_id", "email",
"gender", "ratings", "temp_email", "timezone"}` — no date field of any kind.
`build_plan()`'s net-new branch therefore falls back to "now" for
`created_at`, which is a reasonable default for a row that must have *some*
`created_at` to satisfy the `users` table's NOT NULL/default expectations,
but that default silently doubles as "this is a new signup" everywhere else
in the codebase that reads `created_at` for tenure — `promotions.py` being
the one live consumer that actually branches on it.

## 3. Fix / remediation

Per the issue's recommended option (a) — do **not** fabricate/backdate
`created_at` (the source data genuinely doesn't have a real value to put
there; inventing one would be worse than leaving it honest, same principle
CLAUDE.md's insurance-period conventions already apply). Instead, fixed the
consumer: `routes/promotions.py` now excludes riders from `new_user_days`
promo eligibility whenever their `legacy_import_metadata` carries a
`rider_csv_import` key — the exact provenance marker `rider_import_service.py`
already stamps on every row it creates/updates (`IMPORT_SOURCE =
"legacy_rider_csv_import"`) — **regardless of `created_at` age**. This is
additive and structural: no schema change, no new column, just a new
predicate on an existing JSONB field every import batch already writes.

Added a shared helper, `_is_legacy_imported_rider(user)`, used by both call
sites (`_validate_promo_for_user` rule 7 and `list_available_promos`'s
new-user filter) so the exclusion can't drift between the two paths that
already had to be kept manually in sync for the `created_at` check itself.

`rider_import_service.py` itself was **not** changed — the `created_at =
now()` stamp on net-new rows is left as-is (it must be *some* value; there
is nothing more honest to put there).

## 4. Risk & impact on existing functionality

**Blast radius: isolated, backend-only, single function pair.**

- **Grepped every caller of `new_user_days`** (`grep -rn "new_user_days"
  backend/ --include=*.py`, excluding tests): only two runtime call sites
  exist — `_validate_promo_for_user` (`routes/promotions.py:213-222`, used
  by `POST /promo/validate`, `POST /promo/apply`, and
  `apply_promo_for_admin`) and `list_available_promos`
  (`routes/promotions.py:556-564`, used by `GET /promo/available` and the AI
  assistant's fare-quoting tool per its own docstring). Both are fixed in
  this PR; there is no third consumer.
- **Grepped every caller of `get_user_by_id`** (the function whose row shape
  this fix now reads an additional field, `legacy_import_metadata`, from):
  it is called in ~50 other places across `dependencies/__init__.py`,
  `routes/users.py`, `routes/auth.py`, `routes/payments.py`,
  `routes/admin/*.py`, `routes/drivers/*.py`, `routes/rides/*.py`,
  `services/payment_service.py`, `utils/scheduled_rides.py`, etc. **None of
  those call sites are touched by this change** — `get_user_by_id` already
  does `select("*")` (`repositories/auth_repo.py:53`), so
  `legacy_import_metadata` was already present on every returned row; this
  fix only adds a new read of an already-fetched field inside
  `promotions.py`, not a new query or a change to what any other caller
  receives.
- **What else reads/writes `legacy_import_metadata`:**
  `rider_import_service.py` (writer, unaffected — no change to what it
  writes), `driver_import_service.py` / `booking_import_service.py` /
  `stripe_mapping_import_service.py` (writers of other sub-keys on the same
  shared column, unaffected — this fix only reads the `rider_csv_import`
  sub-key, never writes it), and the admin dashboard (confirmed via the
  2026-08-11 audit cited in the rider-provenance-backfill change-log: no
  screen currently renders this field, so no UI surface is affected by this
  read either).
- **Could this regress a flow that currently works?** The only observable
  behavior change is that a `new_user_days` promo becomes *unavailable* to a
  narrower set of users than before (legacy-imported riders lose an
  eligibility they should never have had). No previously-ineligible rider
  becomes eligible, and no non-`new_user_days` promo rule is touched — rules
  1–6 and 8–10 in `_validate_promo_for_user`, and their equivalents in
  `list_available_promos`, are unmodified. A native-signup rider (no
  `legacy_import_metadata->'rider_csv_import'` key, which is every rider who
  signed up through the app rather than a CSV import) sees **zero** behavior
  change.
- **No interaction with the ride state machine, wallet/allowance deltas, or
  any Stripe flow.** Promo discounts affect the *fare quote*, not a
  settled payment; nothing here touches `corporate_wallet_apply_delta` or
  any of the 18 background loops in `core/lifespan.py`.

## 5. User-experience effect

- **Rider-facing.** A rider who was imported via the legacy CSV path (an
  old-app customer, not a new signup) will stop seeing/being able to redeem
  `new_user_days`-restricted promos that they were incorrectly eligible for
  before this fix. This is **not** visible mid-session in the sense of
  interrupting an active ride — it only affects `GET /promo/available`
  (what promos are listed) and `POST /promo/validate`/`POST /promo/apply`
  (whether a code is accepted) on their next promo check. A rider who
  already had such a promo *applied* to a ride before this deploy keeps that
  application; this fix is forward-looking only (no `promo_applications` row
  is touched or reversed).
- No driver-, corporate-admin-, or internal-admin-facing change.
- No copy/notification change — the existing rejection message ("This promo
  is for new users only") is reused verbatim for the new exclusion path, so
  there is nothing new to review against the tone standard.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/promotions.py` | Added `_is_legacy_imported_rider()` helper; both `new_user_days` eligibility checks (`_validate_promo_for_user` rule 7, `list_available_promos`'s new-user filter) now reject/hide the promo when the marker is present, regardless of `created_at` age | Close the promo-eligibility exposure without fabricating a `created_at` the source data doesn't have |
| `backend/tests/test_promotions_coverage.py` | Added 4 regression tests: legacy-imported rider rejected despite today's `created_at` (both call sites), native signup with today's `created_at` still qualifies (both call sites, control case) | Lock in the fix; prove the control case (a real new signup) is unaffected |
| `ACTION_ITEMS.md` | Corrected the stale A34 note that mischaracterized this as "the provenance-stamping gap is still open" — that specific claim was already false when written; documented the actual gap this CR fixes | CLAUDE.md's own instruction (see this issue's implementation plan step 4) — keep the backlog accurate, not just the code |
| `docs/change-log/2026-08-17-rider-import-promo-eligibility-fix.md` | This file | Mandatory Change Impact Log for a behavior change on a live-tested surface (promotions/payments-adjacent) |

`rider_import_service.py` was deliberately **not** modified — see §3.

## 7. Before / after

```python
# Before — backend/routes/promotions.py, _validate_promo_for_user rule 7
new_user_days = promo.get("new_user_days", 0)
if new_user_days > 0:
    user = await db_supabase.get_user_by_id(user_id)
    if user and user.get("created_at"):
        created = parse_iso_utc(user["created_at"])
        if created is not None and (now - created).days > new_user_days:
            raise HTTPException(status_code=400, detail="This promo is for new users only")
```

```python
# After
new_user_days = promo.get("new_user_days", 0)
if new_user_days > 0:
    user = await db_supabase.get_user_by_id(user_id)
    if user and _is_legacy_imported_rider(user):
        raise HTTPException(status_code=400, detail="This promo is for new users only")
    if user and user.get("created_at"):
        created = parse_iso_utc(user["created_at"])
        if created is not None and (now - created).days > new_user_days:
            raise HTTPException(status_code=400, detail="This promo is for new users only")
```

(`list_available_promos`'s new-user filter received the equivalent
`continue`-before-the-date-check addition.)

## 8. Rollback plan

`git-revert-safe`. This is a pure code change to a read-path predicate — no
migration, no data write, no feature flag needed. Reverting the commit
restores the exact prior (buggy) behavior with no data-level cleanup
required, because nothing in this fix ever wrote to `promo_applications`,
`users`, or any other table — it only narrows an eligibility check.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_promotions_coverage.py -q` via
  `/tmp/spinr_venv/bin/python` — **45 passed** (41 pre-existing + 4 new). The
  reported "coverage 60%" failure line in that run is a whole-repo
  `--cov-fail-under` artifact of running a single test file in isolation,
  not a test failure — confirmed all 45 individual tests report `passed`.
- [x] Manual repro reasoning: traced both `new_user_days` call sites via
  grep (see §4) and confirmed both are covered by the fix and by a
  regression test each.
- [x] Blast-radius grep performed: `new_user_days` (2 runtime call sites,
  both fixed), `get_user_by_id` callers (~50, none affected — see §4),
  `legacy_import_metadata` readers/writers (see §4).
- [x] Reviewed against CLAUDE.md's "do not fabricate/backdate" principle
  (same posture as the insurance-period convention) — confirmed by reading
  `REQUIRED_COLUMNS`/`OPTIONAL_COLUMNS` directly that the source CSV has no
  signup-date column, so backdating `created_at` was never a viable option.
- [ ] **Not** run against a real Supabase dev instance — this is a backend
  Python change covered by the `mock_supabase_client`-style patching already
  used throughout `test_promotions_coverage.py`; no `admin-dashboard`/
  `rider-app`/`driver-app` build applies here (backend-only change, no
  frontend files touched).
- [ ] Not exercised end-to-end against a live `/promo/available` or
  `/promo/apply` HTTP call — verified at the function level
  (`_validate_promo_for_user` / `list_available_promos`) per this repo's
  existing test convention for this file.

## 10. What was NOT verified

- **The 918 already-imported riders from the 2026-08-17 provenance
  backfill** (`docs/change-log/2026-08-17-rider-provenance-backfill-executed.md`).
  Per that document's own text, quoted directly: *"no rider CSV batch has
  ever executed against production with a recoverable batch ID, and the
  importer code itself still has the underlying gap"* — meaning the 918
  rows got `legacy_import_metadata->'rider_csv_import'` from a **direct SQL
  backfill**, not from `rider_import_service.py`'s `build_plan()`/
  `commit_plan()` actually running. That backfill's `UPDATE` statement only
  ever set `legacy_import_metadata`; it never touched `created_at` (visible
  in its own before/after snippet — only the JSONB field changes). **This
  means the `created_at = now()` code bug this PR fixes may never have
  actually fired against these 918 rows** — their `created_at` values
  reflect whenever those `users` rows were first created by whatever process
  created them (unknown from the code alone), not necessarily "today's date
  at backfill time." Whether any of them nonetheless have a
  `created_at` young enough to currently pass a live `new_user_days` promo
  check is a live-data question this session has no DB access to answer (no
  Supabase MCP session was authorized/available for this task). **This is a
  genuinely open question, explicitly left for a human/follow-up query**,
  not silently assumed either way — recommend running:
  ```sql
  SELECT id, created_at, legacy_import_metadata->'rider_csv_import'->>'imported_at' AS imported_at
  FROM users
  WHERE legacy_import_metadata ? 'rider_csv_import'
    AND created_at > now() - interval '1 year'  -- or whatever the widest live new_user_days is
  ORDER BY created_at DESC;
  ```
  and cross-checking any hits against currently-active `new_user_days`
  promos before deciding whether retroactive `promo_applications` cleanup
  is warranted.
- **Whether a `new_user_days` promo is currently active in production** —
  not checked (would require a live `promotions` table query); if one is
  active, this fix closes the exposure going forward but does not undo any
  discount already redeemed under the old behavior.
- No visual/UI regression tooling applies — this is a backend-only,
  non-UI change.
