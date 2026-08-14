# Change Impact & Risk Log — 24 Admin Settings Could Not Be Saved

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-14 |
| Author | Claude Code |
| Surface(s) | backend (schema + admin API) |
| Domain (Sentry tag) | admin, safety, dispatch, corporate, payments |
| Severity | **Live defect** — kill switches, SOS paging config and three corporate money settings could not be changed through any supported path |
| Found by | Verifying migrations 310–312 against the live schema after the operator applied them |

## 1. Issue / gap identified

`PUT /api/admin/settings` accepts 102 fields. **24 of them had no column in the
`settings` table.** Attempting to change any one of them returns 500 and loses
the entire save.

The settings that could not be changed:

| Setting | Domain |
|---|---|
| `surge_engine_enabled` | surge recalculation **kill switch** |
| `scheduled_dispatch_enabled` | scheduled dispatch **kill switch** |
| `driver_discreet_sos_enabled` | **safety** |
| `sos_paging_webhook_url`, `sos_paging_routing_key` | **safety** — paging escalation |
| `min_driver_app_version`, `min_rider_app_version` | force-upgrade gates |
| `corporate_billing_enabled` | corporate money path |
| `corporate_subscription_billing_enabled` | corporate money path |
| `corporate_wallet_admin_adjust_daily_cap` | corporate money path (abuse cap) |
| `corporate_kyb_reverification_enabled`, `..._after_months` | corporate compliance |
| `stripe_auto_heal_processing` | payments |
| `promo_redemption_enabled` | promotions |
| `driver_heatmap_v2_enabled`, `heatmap_internal_driver_ids` | heatmap v2 flag + dark-launch allowlist |
| `apns_bundle_id`, `apns_key_id`, `apns_p8_key`, `apns_team_id` | iOS push credentials |
| `resend_api_key`, `resend_from_email` | email provider |
| `ai_disabled_mode`, `company_app_name` | misc |

## 2. Root cause

`settings` is a single row (`id='app_settings'`) with **flat columns** — there
is no JSON catch-all. The write path builds its payload straight from the
request model with no column allowlist:

```python
update_fields = {k: ... for k, v in settings.model_dump(exclude_none=True).items()}
await db_supabase.update_one("settings", {"id": "app_settings"}, update_payload)
```

PostgREST rejects an unknown column with **PGRST204** — the same failure
`CLAUDE.md` already documents for `service_areas.updated_at` ("Adding it causes
PGRST204 → 500 error"). So this is not a silently-dropped field: it 500s the
whole request, including the valid fields sent alongside it.

**Why nobody hit it:** `exclude_none=True`. A field only enters the payload once
an admin actually sets it. Every one of these 24 stayed absent from every save
until someone tried to change that specific setting — which, for a kill switch,
means during an incident.

Reads were unaffected throughout: `get_app_settings()` merges the DB row with
code defaults, so a missing column silently resolved to its default. The system
has been running on those defaults correctly; they simply could not be changed.

**My own contribution to this:** migration 311's header comment asserted that
`driver_heatmap_v2_enabled` and `heatmap_internal_driver_ids` already existed
and deliberately left them alone. They never existed. That assumption was
wrong, and it is corrected here.

## 3. Fix / remediation

Migration `313_settings_missing_columns.sql` adds all 24 columns.

**Every default was read from the code that consumes the setting, never chosen
here** — `schemas.py`'s `AppSettings` where the field is declared, otherwise the
consuming module's own fallback constant (`_DEFAULT_BILLING_ENABLED`,
`_DEFAULT_ADJUST_DAILY_CAP`, `_DEFAULT_THRESHOLD_MONTHS`, …). So applying it
changes no behaviour: each setting keeps the value the system already used, and
only becomes persistable.

Three deliberate choices worth stating, because a default *is* a behavioural
decision:

- **Kill switches default to running.** `surge_engine_enabled`,
  `scheduled_dispatch_enabled` and `corporate_billing_enabled` are `TRUE`. A
  schema migration must not switch off a live system as a side effect.
- **Opt-in flows default off.** `driver_discreet_sos_enabled` (`FALSE`, per
  `AppSettings`) and `corporate_subscription_billing_enabled` (`FALSE`, per
  `routes/corporate_subscriptions.py`, which explicitly holds that money path
  off until verified in staging). Defaulting either on would enable an
  unreviewed safety flow / money path via a migration.
- **Version gates default empty.** A non-empty `min_*_app_version` would lock
  out every client below it the moment the migration applied.

`corporate_wallet_admin_adjust_daily_cap` is `NUMERIC(12,2)` — it is a money
cap, so the float ban applies to the schema too. A CHECK constraint bounds it
and the KYB re-verification window, matching migration 311's defence-in-depth
pattern (a direct console edit bypasses the API's Pydantic validation).

`heatmap_internal_driver_ids` is `JSONB DEFAULT '[]'` — an empty allowlist is
"nobody", i.e. today's behaviour.

## 4. Risk & impact on existing functionality

**Blast radius:** additive DDL on a single-row config table. Every column is
nullable-with-default, so PG11+ applies the default as catalog metadata — no
table rewrite, no lock beyond a brief `ACCESS EXCLUSIVE` for the catalog update.
Even a rewrite would be trivial on one row.

**No existing reader changes behaviour.** `get_app_settings()` already returned
these values from code defaults; after the migration it returns them from
columns holding the identical values. Verified default-by-default against each
consuming module.

**What could regress:**

- **A default that doesn't match its reader's fallback would silently change
  behaviour on apply.** This is the real risk of this migration and the reason
  every default was sourced rather than picked. Four tests pin the ones that
  matter (kill switches TRUE, opt-in flows FALSE, version gates empty, money cap
  NUMERIC), so a future edit to those defaults fails rather than quietly
  flipping a switch.
- **The CHECK constraint can reject a pre-existing value** — not possible here,
  since the columns are new and their defaults are inside the bounds.
- **These settings become changeable, which is the point** — but it also means
  an admin can now turn off surge or scheduled dispatch from the UI, which
  previously 500'd. That is the intended capability, and both are audit-logged.

**Explicitly unaffected:** no existing column is altered or dropped, no data is
migrated, and no read path changes.

## 5. User-experience effect

- **Internal admin only.** No rider, driver, or corporate-admin surface.
- **Mid-session visible:** an admin who previously got a 500 saving one of these
  now succeeds. No layout or copy change.
- The heatmap v2 flag and its allowlist become usable, which is what makes the
  dark-launch rollout possible at all.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/migrations/313_settings_missing_columns.sql` | New: 24 columns + 1 CHECK constraint | The fix |
| `backend/tests/test_settings_column_parity.py` | New: 31 tests | Prevent recurrence |

No application code changed — the API and the readers were already correct; only
the schema was missing.

## 7. Before / after

```
Before — admin toggles the surge engine off during an incident:
  PUT /api/admin/settings {"surge_engine_enabled": false}
  → update_one("settings", ..., {"surge_engine_enabled": false, ...})
  → PGRST204: column "surge_engine_enabled" does not exist
  → 500. Nothing saved, including any other field in the same request.

After:
  → column exists, value persists, audit row records the change.
```

## 8. Rollback plan

The migration's header carries the full `DROP COLUMN IF EXISTS` list.

**But rolling back restores the broken state** — saves 500 again — so it is only
appropriate alongside a revert of whatever made these settable. Since the
migration is purely additive and changes no behaviour on apply, there is no
scenario where the schema itself needs reverting; a bad *value* is fixed by
changing the value, not by dropping the column.

If a default turns out to be wrong for a given deployment, correct it with an
`UPDATE` on the single `app_settings` row rather than re-running DDL.

## 9. Verification performed

- [x] **Read the live schema directly** (Supabase, `ca-central-1`, project
      healthy) and diffed all 96 `settings` columns against
      `SettingsUpdateRequest`'s 102 fields — that diff is where the 24 came from.
      This was a measurement, not an inference.
- [x] Confirmed migrations 310/311/312 landed: `service_areas.heatmap_config`
      (jsonb) and six `settings` heatmap columns exist, and
      **`idx_rides_area_created` is VALID** — the `CREATE INDEX CONCURRENTLY`
      completed, so there is no `INVALID` leftover that `IF NOT EXISTS` would
      silently skip. No invalid index on `rides` or `drivers` at all.
- [x] Confirmed the write path has no column allowlist by reading it end to end.
- [x] Traced every default to its consuming module rather than inventing one.
- [x] 31 new tests pass; `ruff check` + `format` clean.
- [x] Migration number 313 verified free.

## 10. What was NOT verified

- **The migration has not been applied.** It is written and tested statically;
  nobody has run it against any database, including staging.
- **The 500 was not reproduced.** It is inferred from the write path having no
  allowlist plus PostgREST's documented PGRST204 behaviour (which `CLAUDE.md`
  records as producing exactly this failure for another column). Nobody typed
  into the settings UI and watched it fail. **If you want one check before
  applying: open Settings, toggle one of these — the surge engine switch —
  and confirm it 500s today.**
- **Only the `settings` table was audited.** Other admin write paths that build
  a payload from a Pydantic model without a column allowlist may have the same
  class of defect; `service_areas` has a persisted-field allow-list and is
  therefore not exposed, but nothing else was checked.
- **The pre-existing columns were not diffed against their readers.** This
  covers fields the API can send that have no column, not columns holding values
  no code reads.
- **No staging apply, no production apply, no UI run.**
