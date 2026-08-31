# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers / payments |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | Follow-up to `2026-08-30-incentive-eligibility-enforcement.md` (PR #4780, merged) |
| Migration | `376_service_area_incentive_eligibility_flag.sql` |

## 1. Issue / gap identified

Two gaps in the enforcement switch shipped in migration 375:

1. **No UI.** `settings.incentive_eligibility_enforced` was settable only by API or a
   direct DB write. The prior change log said "settable from admin settings without a
   redeploy" — true of the endpoint, but it implied a control that did not exist.
   Confirmed: zero references to the flag anywhere in `admin-dashboard/`.
2. **Wrong granularity.** It was one global switch. Incentives are configured *per
   service area* (`ride_incentives.service_area_id`, managed in the per-area Incentives
   tab), so the switch governing them should be too — and a change to what drivers are
   paid should roll out city by city, not fleet-wide in one flip.

## 2. Root cause

Migration 375 chose the simplest thing that could ship dark. The per-area shape was not
considered against how incentives are actually administered.

## 3. Fix / remediation

- **Migration 376** adds `service_areas.incentive_eligibility_enforced` (boolean, default
  false).
- **Resolution is OR:**
  `enforce = settings.incentive_eligibility_enforced OR service_areas.incentive_eligibility_enforced`.
  The per-area column is the staged rollout; the global one remains the fleet-wide master
  switch. **AND was rejected**: it would make a freshly-enabled area silently do nothing
  until the global was also on, which reads as a broken toggle.
- **`match_ride_incentives()` gains `service_area=`.** Dispatch passes the row it already
  fetched (`matching.py`'s `_ride_area`), so per-area resolution costs **no extra query on
  the offer→accept path**. Other callers omit it and the row is fetched — and only when
  the global switch has not already decided the answer.
- **Admin UI:** a toggle at the top of the per-area Incentives tab, with copy stating
  plainly what changes and that it affects driver pay.
- **Admin API:** the field is added to `ServiceAreaUpdateRequest` **and to the handler's
  explicit field allowlist** — without the second, it would have been accepted and
  silently dropped.

## 4. Risk & impact on existing functionality

- **Still ships dark.** Both flags default false, so behaviour is unchanged until someone
  toggles an area. The 375 flag-off differential guarantee is untouched.
- **Failure directions are deliberately asymmetric.** A settings-read failure *or* an
  area-read failure leaves enforcement **off**. Denying a bonus a driver was already
  quoted is the more damaging error, so every failure path falls back to not enforcing.
- **`ServiceAreaCreateRequest` deliberately does not accept the field** — a brand-new area
  must not start with a payout-affecting flag on. The column default supplies false; an
  admin enables it afterwards via the toggle.
- **Read path needed no change:** `admin_get_service_areas` uses `get_rows` (select \*), so
  the new column is returned automatically.
- **Blast radius:** `match_ride_incentives` has five callers (dispatch, offer-card,
  active-ride read, and both settlement paths). Only dispatch changed — the rest pick up
  per-area resolution through the shared matcher with no edit. Settlement therefore agrees
  with display for free, which is the invariant the module exists to hold.
- No ride-state, wallet, Stripe or insurance-period path touched.

### Known limitation, documented rather than worked around

A **globally-scoped** incentive (`service_area_id IS NULL`) applies in every area, and its
`max_budget` is a single shared pot. While the fleet is partially enabled, rides in a
still-unenforced area keep drawing on that pot with no cap check — so such a cap is only
fully honoured once every area is on, or the global switch is. The alternative is refusing
partial rollout entirely, which is worse for a change to driver pay. Called out in the
migration header and here.

## 5. User-experience effect

- **Internal-admin facing.** A new toggle in each service area's Incentives tab, off by
  default. Copy says what it changes and warns it affects driver pay.
- **Driver-facing: nothing changes until someone enables an area.** Once enabled for an
  area, incentives there stop paying when expired, over budget, or failing their
  conditions — and a `percentage` incentive is finally charged as a percentage.
- No rider-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/376_…sql` | New defaulted boolean on `service_areas` | Per-area rollout |
| `backend/services/incentive_service.py` | `_enforcement_enabled(area)` ORs the two flags; `match_ride_incentives(service_area=…)` accepts or fetches the row | One resolution shared by display and settlement |
| `backend/routes/rides/matching.py` | Passes the already-fetched `_ride_area` | Keeps the dispatch SLA path query-free |
| `backend/routes/admin/service_areas.py` | Field on `ServiceAreaUpdateRequest` **and** the persistence allowlist | Otherwise silently dropped |
| `admin-dashboard/…/service-areas/page.tsx` | Toggle in the Incentives tab + wiring | The control that did not exist |
| `backend/tests/test_incentive_service.py` | `_DB.find_one`; 7 new resolution tests | Pin OR semantics, both fallbacks, and the no-extra-query guarantee |

## 7. Before / after

```python
# Before — one global switch, no way to stage a rollout
if enforce is None:
    enforce = await _enforcement_enabled()

# After — area first, global as master switch, fetched only if needed
if enforce is None:
    area = service_area
    if area is None and sa_id:
        try:
            area = await db.find_one("service_areas", {"id": sa_id})
        except Exception:
            area = None          # failure leaves the decision to the global switch
    enforce = await _enforcement_enabled(area)
```

## 8. Rollback plan

**Toggle off in the admin UI** — no deploy, effective within the 60s settings/area read.
That is the intended rollback.

Code rollback is `git revert` plus the migration's documented `DROP COLUMN`. Nothing
durable is written by this change; claims already recorded are correct rows in an
append-only ledger. An older backend ignores the new column and reads the global switch
alone, which is exactly pre-376 behaviour.

## 9. Verification performed

- [x] **All 34 tests in `test_incentive_service.py` executed and pass**, including the 7
      new resolution tests, via the same real-source harness used for migration 375 (only
      the `httpx`-dependent imports stubbed; `utils/money.py` and `utils/datetime_utils.py`
      loaded for real; the fake query builder applies `eq`/`in_` filters for real).
- [x] Verified the field is in **both** the request model and the handler allowlist — the
      allowlist is easy to miss and would have made the toggle a silent no-op.
- [x] Confirmed `admin_get_service_areas` returns the column without a read-path change.
- [x] Pre-ran `migration-check.yml`'s CHECK C/D and the naming rule against 376 locally
      before pushing (CHECK C false-positives on the literal `CREATE TABLE` in a comment —
      the failure that hit PR #4780).
- [x] `ruff check` clean on every changed backend file; all changed Python compiles.
- [x] `tsc --noEmit --noResolve` on the admin page: **0 syntax errors**; the 11 `TS18046`
      type warnings are pre-existing on `main` (identical count before and after).
- [ ] **pytest NOT run; admin-dashboard has no `node_modules`, so no typecheck, lint or
      `npm run build`.** PyPI and the npm registries are blocked by this session's egress
      policy. CI is the first real execution.
- [ ] **The migration has NOT been applied or dry-run.** No database in this session.

## 10. What was NOT verified

- **The toggle has never been rendered.** No `node_modules` for admin-dashboard, no
  browser, and admin-dashboard's Playwright visual-regression job still has no committed
  baselines (`ACTION_ITEMS.md` B38), so layout and the disabled/pending state were reasoned
  from the JSX, not seen. A reviewer should click it once.
- The harness is not pytest: conftest fixtures, the autouse Supabase patching and the real
  `get_app_settings` cache are unexercised. The 34 passes are strong evidence the logic is
  right, not proof the suite is green.
- **The OR-versus-AND choice is a judgment call**, not a stated requirement. If the intent
  was "global is a kill-switch that must also be on", the semantics are inverted from what
  shipped here — it is one line in `_enforcement_enabled` to change.
- No load test of the dispatch path. The "no extra query when the caller supplies the area"
  guarantee is pinned by a unit test, not measured against the < 2s offer→accept SLA.
- Still unmeasured from here: how many live `ride_incentives` rows are expired or
  over-budget. That query (in PR #4780's body) remains the thing to run before enabling
  any area.
