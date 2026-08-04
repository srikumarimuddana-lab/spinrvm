# Change Impact & Risk Log — B15(b) SOS paging made configurable, + settings schema-drift guard

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-04 |
| Author | Claude Code (spinr agent) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | safety |
| PR / commit link | `3983a2a`, `89589f3`, `77f3b2a`, `d26edc1` |
| Related issue or gap ID | `ACTION_ITEMS.md` B15(b) |

## 1. Issue / gap identified

B15(b) shipped `utils/safety_paging.py::page_on_call` plus `sos_paging_webhook_url` /
`sos_paging_routing_key` on the `AppSettings` model and the admin settings API — but **no
migration ever added the backing columns to `public.settings`**, and no admin dashboard field
was ever built. The feature was documented as "shipped dark, ready to enable"; in fact it was
not enableable at all, and the first attempt would have broken the settings page.

Found while auditing B15 against the code rather than against its own status notes.

## 2. Root cause

Two compounding causes.

**The defect itself:** the read path masks it. `settings_loader.get_app_settings()` merges
`AppSettings()` defaults over the DB row, so a missing column silently resolves to its default
forever, with no error anywhere. The write path does not: `admin_update_settings` builds
`settings.model_dump(exclude_none=True)` and passes it straight to PostgREST, so an unknown
column returns `PGRST204` and fails the **entire** settings save. Because nothing had a UI
field, nothing ever exercised the write path, so the omission stayed invisible.

**Why no guard caught it:** there was no test asserting that settings model fields have backing
columns. `test_schema_contract.py` only checks money-field serialization.

## 3. Fix / remediation

- **Migration 277** adds `sos_paging_webhook_url`, `sos_paging_routing_key`, and
  `driver_sos_discreet_enabled` (B16's flag, sharing the migration rather than colliding on the
  next slot).
- **Migration 278** adds six *more* orphaned admin-writable columns found by the new guard —
  see §4.
- **`spinr_safety_sos_paging_total{outcome=sent|failed|disabled}`** added to
  `safety_paging.py`. The module docstring already claimed metric parity with
  `meta_capi.send_meta_event` and did not have it; the disabled branch logged at `DEBUG` only,
  which production does not emit, so the dark state was undetectable.
- **Admin dashboard** Safety-alerts card now carries the webhook URL, routing key, and the
  discreet-SOS toggle.
- **`test_settings_schema_drift.py`** — the guard, scoped to admin-*writable* fields.

**Deliberately not changed:** the inline `await page_sos_on_call(...)` in `trigger_emergency`,
which will add up to 5 s to the SOS response once paging is enabled.
`asyncio.create_task` is the established alternative right next door (`routes/safety.py:114`),
but moving it is a live behaviour change on the SOS path and belongs in its own change with its
own verification. **Flagged as a follow-up — this is a known, accepted cost of enabling paging.**

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend settings + admin settings page), plus one shared table.**

`public.settings` is a single-row table (`id = 'app_settings'`) read by `get_app_settings()`,
which is cached 60 s and consumed very widely. But all nine columns added across 277/278 are
**new and additive** — nullable, no `DEFAULT` except `driver_sos_discreet_enabled BOOLEAN NOT
NULL DEFAULT FALSE` — so every existing reader resolves them to exactly the same value it
resolves to today. No column is repurposed, renamed, or type-changed.

**What migration 278 found, and why it matters more than it looks.** Running the new guard
against `main` surfaced six further admin-writable fields with no column:

| Field | Consequence today |
|---|---|
| `ai_disabled_mode` | AI kill switch's presentation half — flipping it 500s the settings save |
| `apns_key_id` / `apns_team_id` / `apns_bundle_id` / `apns_p8_key` | iOS push credentials — unconfigurable |
| `stripe_auto_heal_processing` | Reconciler mark-paid gate |

The last is the sharpest: its own docstring says it is *"shipped dark on purpose… must be
reviewed and validated in staging before an operator enables it in production"* — but the
operator could not have enabled it, because the write 500s. It gates a path that **marks rides
paid and credits driver tips**. Adding the column does **not** enable it; the flag remains
falsy by default, so behaviour is unchanged. It only makes the intended switch reachable.

**Background loops:** none touched. `utils/stripe_reconcile.py` reads
`stripe_auto_heal_processing` via `settings.get(..., False)` — a NULL column and an absent
column both yield falsy, so the reconciler's behaviour is identical before and after.

**Ride state machine / money deltas:** untouched. No wallet, fare, or ride-status code changed.

**Paging remains off.** `sos_paging_webhook_url` is empty by default, so `page_on_call` still
makes zero HTTP calls. Nothing about the SOS response path changes until an operator
deliberately configures a webhook.

**Grep performed:** `sos_paging` across `backend/`, `admin-dashboard/src`, `backend/migrations/`;
`ALTER TABLE public.settings` across all migrations; every `AppSettings` and
`SettingsUpdateRequest` field cross-referenced against declared columns (that cross-reference is
now the automated test).

## 5. User-experience effect

- **Rider / driver: no change.** Nothing on either mobile surface is affected by this log's
  changes. (The discreet-SOS flag is added here but its client behaviour ships in the B16
  change — see that log.)
- **Internal admin: visible change** to Settings → Safety alerts. Three new controls, and the
  card's help text corrected — it previously claimed "log-aggregator paging keeps working",
  describing a fallback nobody was actually watching.
- **Not visible mid-session** to anyone using the rider or driver app.
- Copy: new admin-facing help text only. It states plainly what the paging payload does and does
  not contain, because the person pasting a third-party webhook URL is the person who needs to
  know what will be sent to it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/277_settings_sos_paging.sql` | **new** — 3 columns | The actual blocker: paging had no backing columns |
| `backend/migrations/278_settings_orphaned_columns.sql` | **new** — 6 columns | Same defect found elsewhere by the new guard |
| `backend/utils/safety_paging.py` | `spinr_safety_sos_paging_total` counter; docstring corrected | Dark state was undetectable; docstring claimed a metric that didn't exist |
| `backend/schemas.py` | `driver_sos_discreet_enabled` on `AppSettings` | B16 flag |
| `backend/routes/settings.py` | flag on the public projection | Driver app must read it before an emergency |
| `backend/routes/admin/settings.py` | flag in the update allowlist | Ops needs to toggle it |
| `backend/tests/test_settings_schema_drift.py` | **new** | The guard that would have caught this |
| `backend/tests/test_sos_paging.py` | +4 metric tests | Pin all three outcomes |
| `backend/tests/test_utils_extended.py` | +3 public-settings tests | Flag default/exposure, and that paging creds never leak publicly |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | paging fields + discreet toggle; corrected help text | Ops had no route to these at all |

## 7. Before / after

The disabled branch, which is the state production is actually in:

```python
# Before — invisible. logger.debug is not emitted in production.
    if not config.configured:
        logger.debug("safety_paging: sos_paging_webhook_url not configured — skipping page ...")
        return False
```

```python
# After — countable, so "we shipped dark and forgot" can be alerted on.
    if not config.configured:
        logger.debug("safety_paging: sos_paging_webhook_url not configured — skipping page ...")
        _metric_inc(_PAGING_METRIC, {"outcome": "disabled"})
        return False
```

## 8. Rollback plan

- **Paging config:** blank `sos_paging_webhook_url` in `app_settings` → `page_on_call` returns to
  a zero-HTTP no-op within the 60 s settings TTL. No deploy.
- **Discreet flag:** set `driver_sos_discreet_enabled = false`. No deploy.
- **Migrations:** both carry `DROP COLUMN IF EXISTS` rollback stanzas in their headers. Dropping
  is safe and non-destructive of live data here — every column is new, additive, and read
  through an `AppSettings` default, so dropping restores prior behaviour exactly. No live data
  depends on them (no Stripe charge, wallet delta, or ride state is keyed off any of these).
- **Admin UI:** `git revert` of the dashboard commit is sufficient; it is presentational and
  writes nothing the backend doesn't already validate.

## 9. Verification performed

- [x] **Automated tests run.** `pytest backend/tests/test_sos_paging.py` (14 passed, incl. 4 new),
      `test_settings_schema_drift.py` (3 passed), `test_utils_extended.py -k Settings` (7 passed),
      `test_admin_settings_lms_gate.py` (passed). All unit tier.
- [x] **The guard was verified to actually fail.** Migration 277 was temporarily removed and the
      drift test failed with the expected two field names, then passed again on restore.
      A test that cannot fail is not a guard.
- [x] **Blast-radius grep performed** — see §4 for the exact searches.
- [x] **Production build run** for admin-dashboard: `npm run build`, exit 0. Not a dev server,
      not `tsc --noEmit` alone.
- [x] `ruff check` + `ruff format --check` on every changed backend file. (Note: ruff is
      `continue-on-error` in CI and gates nothing, so this was run locally or it would not have
      been run at all.)
- [x] **Reviewed against `CLAUDE.md`** — migration conventions (append-only, rollback stanza, RLS
      stance, no `CONCURRENTLY` given open B0), observability metric naming
      (`spinr_<domain>_<metric>_<unit>`, counter `_total`), Settings-in-DB, PIPEDA (payload
      carries IDs + geohash only; new test asserts paging credentials never appear on the public
      projection).
- [x] **Feature-flagged**: `driver_sos_discreet_enabled` defaults false; paging defaults off.

## 9a. What was NOT verified

- **No migration was applied to any database.** Neither 277 nor 278 has been run against real
  Postgres — not production, not staging (there is no staging environment, `ACTION_ITEMS.md` E1),
  and `migrate.py --dry-run` was not run because open item **B0** means the runner mishandles
  files containing `CONCURRENTLY` and a clean dry-run would not prove a clean apply anyway. Both
  files were checked to be free of that string. **The PGRST204 failure and its fix are reasoned
  from the code path, not observed against a live PostgREST.**
- **The admin settings save was not exercised end-to-end.** The build compiles and the fields
  render, but no one has clicked Save against a real backend with the migrations applied. That
  round trip — set a webhook URL, expect 200 not 500 — is the single check that proves this
  change worked, and it remains outstanding.
- **Paging has never fired against a real provider.** There is still no PagerDuty/Opsgenie
  account. The payload shape is pinned by unit tests against a mocked `httpx`, not by a real
  200 from PagerDuty.
- **The `super_admin`-only 403 and credential masking were not manually re-checked** in the new
  UI; they are covered by existing backend tests (`test_admin_settings_lms_gate.py`) but the
  dashboard's handling of that 403 was not exercised by hand.
- **No visual regression tooling exists** for admin-dashboard (`visual-regression-test` is
  `continue-on-error` with no committed baselines), so the Safety-alerts card layout was
  reasoned about and compiled, not screenshotted or diffed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — two `app_settings` values, both no-deploy.
- [x] Blast radius is stated, not assumed — §4, including the six unrelated fields the guard
      surfaced and why adding their columns changes no behaviour.
- [x] No silent behaviour change to an already-shipped flow. The only user-visible change is the
      admin settings card (§5); paging stays off, and the discreet flag stays false.
