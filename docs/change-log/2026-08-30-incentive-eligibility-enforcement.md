# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | backend (driver-app affected only via what it is quoted) |
| Domain (Sentry tag) | drivers / payments |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | The three escalated findings in `2026-08-30-driver-incentive-review-fixes.md` §10 |
| Migration | `375_incentive_eligibility_enforcement_flag.sql` |

## 1. Issue / gap identified

`ride_incentives` has carried `start_date`, `end_date`, `max_budget`,
`budget_used`, `bonus_type` and `conditions` since migration 96 (2026), and
**nothing honoured any of them**. `is_active` was the only gate on every path,
display and settlement alike. In production today:

- a `time_limited` campaign whose `end_date` has passed **keeps paying forever**;
- a campaign with `max_budget` **pays out without limit**, while the admin
  dashboard renders `Budget: $0 / $500` because `budget_used` is never
  incremented by anything (grep: only the `CREATE TABLE` in migration 96);
- `bonus_type='percentage'` is paid as if the amount were dollars — a row
  meaning "10%" pays $10;
- `conditions.min_distance_km: 20` pays on a 2 km ride.

## 2. Root cause

The columns were designed in migration 96 and the enforcement was never
written. The admin API accepts and stores all six; the admin **UI** only ever
sets `max_budget` (plus `is_active`), which is why the budget cap is the one
with real production exposure — see §10.

## 3. Fix / remediation

`services/incentive_service.py` now evaluates the full rule, and **both
settlement paths adopt it** (`routes/drivers/ride_complete.py`,
`routes/rides/lifecycle.py`) alongside the three display paths. One evaluation
produces both the quoted figure and the paid figure, so they cannot diverge.

- **Dates** — `start_date`/`end_date` window; a NULL bound is open-ended; an
  unparseable bound logs at error and is treated as open (a malformed date must
  not silently withhold a bonus a driver was quoted).
- **Budget** — enforced from the **`ride_incentive_claims` ledger**, not the
  `budget_used` column. `budget_used` becomes a denormalized mirror recomputed
  from the ledger after each claim, so it is self-healing, never authoritative,
  and the admin budget bar becomes truthful with no UI change.
- **Percentage** — `bonus_amount` resolved as a percent of the driver's fare
  share (`driver_earnings`, falling back to `total_fare`).
- **Conditions** — `min_distance_km` against the **booked** `distance_km`, and
  `peak_hours` as `[start, end)` hour pairs evaluated in **local time**
  (`America/Regina`, mirroring `utils/quest_tracker.py`); malformed values log
  at error and impose no constraint.
- **Idempotency** — new `record_incentive_claims()` skips any incentive this
  ride has already claimed, so a retried settlement cannot pay twice. Neither
  settlement path had this before.

**All of it is gated on the new `incentive_eligibility_enforced` setting,
default false.** Flag off reproduces the pre-375 behaviour exactly (§9).

## 4. Risk & impact on existing functionality

- **This is a money-behaviour change**, which is why it ships dark. With the
  flag off, nothing changes — verified by differential test, not by assertion.
- **Blast radius: every incentive quote and payout**, once the flag is on.
  Callers, all now on one rule: `routes/rides/matching.py` (dispatch offer +
  FCM title), `routes/offer_card.py` (notification banner),
  `routes/drivers/ride_reads.py` (active ride), and the two settlement paths.
- **When the flag is flipped, drivers stop being quoted — and stop being paid —
  bonuses from expired or budget-exhausted campaigns.** That is the intent, but
  it is a visible reduction for anyone relying on the current unbounded
  behaviour. Check `ride_incentives` for rows with a passed `end_date` or a
  `max_budget` already exceeded before flipping; §10 explains why the size of
  that set is unknown from here.
- **`_refresh_budget_used` writes to `ride_incentives`** — the first write this
  code has ever made to that table outside admin CRUD. It is best-effort and
  wrapped: a failure costs an accurate progress bar, never a claim.
- **Concurrency:** the budget check is read-then-write, not a row lock. Two
  completions settling simultaneously against the same nearly-exhausted
  campaign can both see headroom, overshooting the cap by at most one bonus
  each. Accepted for a marketing budget; a hard guarantee needs a locking
  Postgres function (like `corporate_wallet_apply_delta`), which is a
  follow-up, not something to ship untested here.
- **Dispatch SLA:** the budget ledger read is issued **only when a candidate
  actually has a `max_budget`**, so an uncapped fleet adds zero queries to the
  offer path (pinned by a test). The flag read is `get_app_settings()`, already
  called in the same dispatch function and cached 60s in-process.
- No ride-state, wallet, Stripe, or insurance-period path touched.

## 5. User-experience effect

- **Driver-facing, but only once the flag is flipped.** Until then: no change
  anywhere.
- After flipping: offers, the offer-card banner, the in-trip panel and the
  post-trip total all quote the same, now-correctly-gated bonus. A campaign that
  has ended or spent its budget simply stops appearing.
- **Admin-facing:** the incentive budget bar starts showing real spend instead
  of `$0 / $500`. This happens as soon as a claim is written — i.e. it is *not*
  gated on the flag, because it only makes an existing display truthful.
- No rider-facing change. No copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/375_…sql` | `settings.incentive_eligibility_enforced` + two indexes on `ride_incentive_claims` | Ship dark; the ledger reads by `incentive_id` and `ride_id` were unindexed |
| `backend/services/incentive_service.py` | Dates, conditions, percentage, budget cap, `MatchedIncentive`, `record_incentive_claims`, `_refresh_budget_used` | One rule, one evaluation, quoted == paid |
| `backend/routes/drivers/ride_complete.py` | Settlement uses the shared matcher + claim writer | Was hand-rolling its own rule |
| `backend/routes/rides/lifecycle.py` | Same | Was hand-rolling a *third*, already-drifted variant |
| `backend/schemas.py` | `AppSettings.incentive_eligibility_enforced = False` | Default merge for every settings reader |
| `backend/routes/admin/settings.py` | Flag on `SettingsUpdateRequest` | Flip without a redeploy |
| `backend/tests/test_incentive_service.py` | Rewritten: 27 tests across both flag states | Pin the rule and the no-op guarantee |

## 7. Before / after

```python
# Before — settlement, in two hand-rolled copies
for inc in inc_result.data or []:
    if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
        continue
    ba = Decimal(str(inc.get("bonus_amount") or 0))
    if ba <= 0:
        continue
    await db_supabase.insert_one("ride_incentive_claims", {...})   # no dates,
    _total_bonus += ba                    # no budget, no conditions, no retry guard
```

```python
# After — the same call the offer and the in-trip panel make
_total_bonus = await record_incentive_claims(
    db_supabase, ride_id, driver["id"],
    await match_ride_incentives(db_supabase, ride),
    now=datetime.now(timezone.utc),
)
```

## 8. Rollback plan

**Setting revert, no deploy:** flip `incentive_eligibility_enforced` back to
false in admin settings. Enforcement stops within the 60-second settings cache
TTL and behaviour returns to pre-375 exactly. This is the intended rollback and
the reason the flag exists.

Code rollback (`git revert` + the migration's documented `DROP COLUMN`/`DROP
INDEX`) is only needed for a defect in the shared matcher itself. Claims already
written are correct rows in an append-only ledger and need no remediation;
`budget_used` is recomputed from that ledger, so it self-corrects.

## 9. Verification performed

The registries needed for `pytest` are blocked in this session, so I built a
harness that loads `incentive_service.py` **from real source** with only the
`httpx`-dependent imports stubbed — `utils/money.py` and
`utils/datetime_utils.py` are dependency-free and are loaded for real, so the
logic under test runs against the same helpers production uses. Its fake query
builder **applies `eq`/`in_` filters for real**, so a check that depends on
`ride_id` scoping is genuinely exercised rather than trivially satisfied.

- [x] **All 27 assertions in `test_incentive_service.py` executed and pass**
      through that harness. This is not pytest, and CI is still the real gate,
      but the assertions themselves ran.
- [x] **Flag-off differential test: 400 randomised configurations** (varying
      area, vehicle type, amount, `bonus_type`, both dates, conditions and cap)
      compared against an independently reimplemented pre-375 rule — **0
      mismatches**. This is the evidence for "off changes nothing".
- [x] **Running the tests caught a real design bug**: percentage resolution was
      initially applied unconditionally, so merging would have changed payouts
      for any percentage row *before* anyone flipped the flag, with a redeploy
      as the only rollback. Now gated like every other rule.
- [x] Claim idempotency exercised directly: a retried settlement pays $0 and
      writes nothing; a claim on a *different* ride does not block this one;
      `budget_used` after a claim equals prior-ledger + new claim.
- [x] Blast-radius grep: all five call sites confirmed on the shared matcher;
      `_postgrest_or_value` and `uuid` orphan-checked in both settlement files;
      `db_supabase.insert_one`/`update_one` confirmed exported.
- [x] Verified the `test_error_handling_guards.py` source-grep guard still finds
      `logger.error` within 3 lines of "incentive claim failed" in the rewritten
      `lifecycle.py` block.
- [x] `ruff check backend/` — 43 errors before and after (all pre-existing);
      every changed file clean individually. All changed files `py_compile`.
- [ ] **pytest NOT run.** PyPI, npm and yarn are blocked by this session's
      egress policy. The harness is not a substitute for the real suite —
      fixtures, conftest patching and integration with `get_app_settings` are
      unexercised.
- [ ] **The migration has NOT been applied or dry-run.** No database in this
      session.

## 10. What was NOT verified

- **The size of the production blast radius is unknown from here.** I could not
  query `ride_incentives`, so I do not know how many rows have a passed
  `end_date`, an exceeded `max_budget`, a `percentage` `bonus_type`, or a
  populated `conditions`. **Run that query before flipping the flag** — it tells
  you exactly which campaigns stop paying. The admin UI only ever sets
  `max_budget`, so the budget cap is the likely-live one and `conditions` /
  `percentage` / the dates are probably empty everywhere, but "probably" is
  doing real work in that sentence.
- **The `peak_hours` encoding is inferred, not specified.** Migration 96's only
  documentation is the example `{"peak_hours": [7,9,16,19]}`; I read that as two
  `[start, end)` pairs. If it was meant as a flat list of individual hours, the
  behaviour is wrong. No production row exercises it today (the admin UI cannot
  set `conditions`), and a malformed value fails open — but this is an
  assumption, not a confirmed contract.
- **Percentage base is a judgment call.** It resolves against the driver's fare
  share at the time of evaluation, which at offer time is the booking estimate
  and at settlement the settled value — so a percentage bonus can move between
  quote and payout exactly as the fare can. Flat incentives (everything the
  admin UI can create) are unaffected.
- **`min_distance_km` uses the booked distance, not `actual_distance_km`**, so
  quoted == paid. A driver who is quoted a long-ride bonus keeps it even if the
  actual GPS distance came in under the threshold. That is a deliberate choice
  in the driver's favour; the opposite choice is defensible.
- **Budget enforcement is not concurrency-safe** (§4). Bounded overshoot only.
- **`peak_hours` uses `America/Regina`, not the ride's service-area timezone.**
  `utils/quest_tracker.py` does the per-area lookup; the matcher does not,
  because that would add a query to the dispatch hot path for a condition the
  admin UI cannot set. A second-timezone rollout must pass `tz_name`.
- No admin-dashboard change was made, so the budget bar's new accuracy was
  reasoned from the code path, not observed.
