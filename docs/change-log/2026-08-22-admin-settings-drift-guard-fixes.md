# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch: `fix/admin-settings-write-allowlist-gaps`) |
| Related issue or gap ID | surfaced by PR #4438's `test_admin_settings_write_allowlist_drift.py` |

## 1. Issue / gap identified

8 `settings` table columns were read by live application code but had no admin-write field on
`SettingsUpdateRequest` (`backend/routes/admin/settings.py`): `company_city`, `company_province`,
`company_postal_code`, `lifecycle_emails_enabled`, `marketing_from_email`,
`route_location_gap_alert_seconds`, `fare_distance_basis`, `route_integrity_v2_mode`. Same class of
bug as `legacy_consent_notice_enabled` (PR #4418) — each had no supported way to be changed except a
direct DB write.

## 2. Root cause

Each column was added by a migration that wired the *read* side (a util or route consuming the
setting) but never updated the admin *write* allow-list in the same change — nothing in the codebase
enforced that the two stay in sync. This is a recurring pattern (also seen with `rideless_sos_enabled`
and the "GPS tracking-overhaul rollout flags" block already in this file, per its own comment).

## 3. Fix / remediation

Added all 8 fields to `SettingsUpdateRequest`, grouped near their semantically related existing
fields (company address fields near `company_address`; email flags near the AWS SES/Resend block;
`fare_distance_basis` near `fare_lock_enabled`; `route_integrity_v2_mode`/
`route_location_gap_alert_seconds` near the other GPS-rollout flags). No other code path needed
changes — `update_fields` is built generically from `settings.model_dump(exclude_none=True)`.

Two of the eight are typed as closed `Literal` enums rather than free `str`, because their readers
either affect money (`fare_distance_basis`) or fail closed on an unrecognized value
(`route_integrity_v2_mode` 503s rather than weakening the guard):
- `fare_distance_basis: Literal["road", "shadow", "haversine"]` — matches
  `routes/rides/_shared.py`'s `select_fare_distance()`'s three handled modes exactly.
- `route_integrity_v2_mode: Literal["off", "shadow", "on"]` — matches
  `routes/drivers/ride_complete.py`'s `_get_route_integrity_mode()`'s validated set exactly.

`route_location_gap_alert_seconds` is constrained `gt=0` (the reader raises on `<= 0`).

Removed the now-empty `KNOWN_UNFIXED_GAPS_2026_08_22` set from the drift-guard test rather than
deleting it, so a future gap has an obvious place to land.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the admin-write path.** All 8 fields already existed as live DB columns
  with existing, unchanged read logic — this PR only adds the ability to set them through the normal
  admin API instead of a direct DB write. No read-side code changed.
- Grepped every consumer of each field before adding it (see Section 3 above and the code comments
  next to each new field) — one reader per field, all already documented at the read site.
- `fare_distance_basis` money-adjacency: this field controls which distance a fare is billed on
  (`road` vs `shadow` vs `haversine`). The *default* behavior is unchanged (still whatever the DB
  column's current value already is — `"road"` per its column default); this PR only adds the ability
  for an admin to *change* that value through the API. The `Literal` type prevents an admin from
  setting an unrecognized string that the reader would silently treat as non-`"road"` (any value other
  than the literal string `"road"` currently falls through the reader's `if mode == "road"` check to
  haversine-adjacent behavior — the enum closes off values like a typo `"raod"` doing that silently).
- `route_integrity_v2_mode`: the reader already 503s on an invalid value read from the DB (defensive,
  pre-existing). The `Literal` constraint here moves that same validation earlier (rejected at write
  time with a 422, instead of only failing the next ride-completion request) — a strict improvement,
  not a new risk.
- No interaction with ride state machine or wallet/allowance deltas. `route_integrity_v2_mode` is
  read at ride-completion time but this PR doesn't change ride completion logic itself.

## 5. User-experience effect

- **Nobody sees a difference from this change alone.** All 8 fields keep their current live DB values
  (unchanged) until an admin explicitly edits one through the Settings page — a separate, later,
  intentional action. This PR only makes that edit path work; it was previously a no-op (silently
  dropped) for these 8 fields.
- If an admin later sets `fare_distance_basis` away from `"road"`, riders would be billed on
  haversine/shadow distance instead of the road route — a real fare-calculation change, but one that
  was always possible via direct DB write and is unchanged in *effect* by this PR, only in *how easy*
  it is to trigger deliberately through the UI instead of a DB console.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/settings.py` | Added 8 fields to `SettingsUpdateRequest`; added `Literal` import | Close the missing admin-write path for 8 already-live, fully-wired DB columns |
| `backend/tests/test_admin_business_logic.py` | Added a parametrized regression test (8 fields) asserting each reaches the DB write, plus a test asserting the 2 enum fields reject invalid values with 422 | Prevent this exact class of bug recurring undetected, and lock in the enum-validation guard |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Emptied `KNOWN_UNFIXED_GAPS_2026_08_22` (all 8 gaps it named are now fixed) | Keep the drift guard's own bookkeeping accurate |

## 7. Before / after

```python
# Before (routes/admin/settings.py, SettingsUpdateRequest)
company_address: Optional[str] = None
company_phone: Optional[str] = None
...
fare_lock_enabled: Optional[bool] = None
driver_matching_algorithm: Optional[str] = None
...
route_booked_dropoff_anchor_enabled: Optional[bool] = None
idle_breadcrumb_retention_hours: Optional[int] = Field(default=None, ge=24, le=2160)
```

```python
# After
company_address: Optional[str] = None
company_city: Optional[str] = None
company_province: Optional[str] = None
company_postal_code: Optional[str] = None
company_phone: Optional[str] = None
...
fare_lock_enabled: Optional[bool] = None
fare_distance_basis: Optional[Literal["road", "shadow", "haversine"]] = None
driver_matching_algorithm: Optional[str] = None
...
route_booked_dropoff_anchor_enabled: Optional[bool] = None
idle_breadcrumb_retention_hours: Optional[int] = Field(default=None, ge=24, le=2160)
route_integrity_v2_mode: Optional[Literal["off", "shadow", "on"]] = None
route_location_gap_alert_seconds: Optional[int] = Field(default=None, gt=0)
```
(`lifecycle_emails_enabled`/`marketing_from_email` added near the AWS SES block — omitted here for brevity, see file diff.)

## 8. Rollback plan

- `git revert` is sufficient — no live-data side effect from this PR alone (the DB columns already
  existed with their current values; this PR only adds an API path to change them, which nobody has
  used yet since it didn't exist before this PR).
- If an admin sets any of these 8 fields to an unwanted value after this ships, that's a config
  revert, not a code rollback: `PUT /api/admin/settings {"<field>": <previous_value>}`, independent of
  this PR.

## 9. Verification performed

- [x] Automated tests run — unit: `pytest backend/tests/test_admin_business_logic.py
      backend/tests/test_admin_settings_write_allowlist_drift.py` (57 passed, includes 10 new
      regression tests: 8 field-forwarding + 2 enum-rejection). Also ran the full admin-settings
      suite (113 passed) and the fare-distance-basis/route-gap-monitor/route-integrity suites most
      likely to interact with the two Literal-typed fields (52 passed, all pre-existing, none
      touched by this diff) — confirms no read-side regression.
- [ ] Manual repro steps followed in staging — **not performed**, no staging environment access in
      this session.
- [x] Blast-radius grep performed — every one of the 8 fields' single read-site consumer was located
      and cited in code comments before the field was added (Section 3/4 above).
- [x] Reviewed against relevant `CLAUDE.md` convention — `fare_distance_basis` is money-adjacent;
      closed-enum typing was chosen specifically to satisfy the "don't let an admin set a value the
      fare-calc reader would silently mistreat" concern. Not a ride-state-machine or wallet/allowance
      change, so the state-machine/money dry-run requirement (mock_supabase_client fixtures + a
      concrete before/after scenario) is covered by the parametrized regression test forwarding each
      value through the mocked DB write path.
- [x] Feature-flagged / non-trivial — N/A, this PR does not change any field's current live value,
      only restores the write path; each field's *default behavior* stays exactly what it already was.
- Ran `ruff check` and `ruff format --diff` on all 3 modified files — clean.

## 10. What was NOT verified

- No staging or live-Supabase exercise of the actual `PUT /api/admin/settings` call with these new
  fields — verification was unit-test-only (mocked `db_supabase.get_rows`/`insert_one`), consistent
  with this repo's standing convention for admin routes.
- Did not verify whether the admin-dashboard frontend Settings page renders form controls for any of
  these 8 fields — this PR only unblocks the backend API path; a frontend UI gap (if any) is a
  separate, not-yet-scoped follow-up, same caveat as PR #4418's `legacy_consent_notice_enabled` fix.
- No visual/screenshot check — backend-only change, no rendered UI touched by this diff; this repo
  has no automated visual-regression tooling regardless (standing gap, `ACTION_ITEMS.md`).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (git revert; no live-data side effect from this PR alone)
- [x] Blast radius is stated, not assumed — 8 isolated fields, each with one cited read-site consumer
- [x] No silent behavior change to an already-shipped flow — every field keeps its current live value
      until an admin takes a separate, later, explicit action through the now-working write path
