# 07 — Independent re-verification of a finding sample (Step 6)

Report-only. No code, config, or data was changed to produce this file.

## §0 Method

- **Sample selection.** Seeded random draw (seed 20260924), one finding card per report: the 15 lane reports in `02-findings/` plus `03-benchmark.md`, 16 cards in total (about 12% of the 137 finding cards). The nine claims the orchestrator had already hand-checked were excluded from the draw so this sample does not overlap them.
- **Independence.** The re-verifier runs on a different model from the one that wrote the lane reports. For each card, every cited `path:line` was opened fresh and the code was read directly; quotes in the cards were not relied on. Every absence claim ("no test", "no UI", "never called", "not listed") was re-checked with my own greps using several spellings and file-name patterns, because an earlier absence claim (QUAL-003) turned out to be false.
- **Tools.** Read, Grep and Bash (`grep`, `rg`, `sed -n`, `git log`) on the working tree as of 2026-09-24. No database, vendor console, or live-environment access was used. Competitor claims in BENCH-006 were checked only as far as public web pages could be reached.
- **Verdicts.** CONFIRMED (every material claim holds); PARTIALLY CONFIRMED (the core claim holds but a material detail is wrong); REFUTED (the core claim is false); UNVERIFIABLE (needs live data, a database, or a vendor console). Citation drift (right claim, wrong line number) is recorded separately and does not count as a refutation.
- **Limits.** A sample of 16 gives a rough error rate, not a precise one. Claims about live state (production DB contents, vendor dashboards) are marked unverifiable rather than guessed at.

## §2 Per-finding notes

(Written as each check finished. The §1 table and §3/§4 follow at the end of the file.)

### ADMIN-OPS-001 (admin-ops.md), card label VERIFIED, HIGH — **CONFIRMED**
- `backend/routes/admin/wallet.py:52-58`: `AdminCreditRequest.amount` has `le=Decimal("10000")`, a per-request cap only. `AdminDebitRequest` (`:67-70`) has `le=10_000`. Neither handler (`admin_credit_wallet` `:128-229`, `admin_debit_wallet` `:231-337`) has a second-approver check, a cumulative or daily cap, or a self-dealing check.
- `backend/routes/admin/__init__.py:332` mounts the wallet router with `require_module("earnings")`, and `:339` mounts the disputes admin router with `require_module("disputes")`. `backend/routes/admin/staff.py:113-114` shows `"disputes"` in the `support` preset and `"earnings"` in the `finance` preset. Both claims hold.
- `backend/routes/disputes.py:222-227`: the refund is bounded only by `refund_amount > original_fare`. My grep for approver, dual approval and super_admin in `disputes.py` found no second-approver logic.
- One small detail the card leaves out: the credit handler has the `@admin_wallet_limit` decorator, which is `10/minute` per `backend/utils/rate_limiter.py:636`. That limits how fast an admin can act but is not a cumulative cap. "As many times as they want" is therefore slightly loose; the finding itself is unaffected.
- The VERIFIED label is appropriate.

### COMP-018 (compliance.md), card label VERIFIED, MEDIUM — **PARTIALLY CONFIRMED**
- The file list from the card's grep reproduces exactly (`pr-checks.yml`, `renewal-calendar-monitor.yml`, `subprocessor-audit.yml`, `subprocessor-monitor.yml`). No workflow references `spinr-regulatory-compliance-checker`, which exists only at `.claude/agents/spinr-regulatory-compliance-checker.md`. There is no `compliance` pytest marker. The only place `pr-checks.yml` reads the PIPEDA-relevant and SK Transportation Act ticks is the `auto-summary` job (`:606-660`), which posts an advisory comment and never calls `setFailed`. The `required-fields` job (`:107-282`) does not check the compliance boxes. So the specific claims hold.
- **Overstated detail:** every test file the card cites exists: `test_calculate_all_fees_tax.py`, `test_e2e_wav_dispatch.py`, `test_driver_crc_consent.py`, `test_go_online_availability.py`, three `test_data_export_purge*` files, five `test_insurance_period_*` files, and two `test_dsar_export*` files. All of them run in the full CI pytest step (`.github/workflows/ci.yml:237`), which blocks merge, so a PR that breaks tax lines, WAV dispatch, CRC consent or insurance periods already fails CI. The headline "No compliance gate runs in CI" and the phrase "tests that *would* serve as gates" overstate the gap. What is actually missing is a compliance-*labelled* suite, and CI use of the checker agent or the checkboxes. Severity should be judged on that narrower gap.
- The VERIFIED label is appropriate for the narrow claim. The headline needs rewording.

### CORP-002 (corporate.md), card label VERIFIED, RECOMMENDATION — **CONFIRMED** (citation drift)
- `backend/migrations/258_corporate_allowance_cap_in_rpc.sql` introduces the `v_cap` / `allowance_cap_exceeded` guard. `277_corporate_allowance_grant_no_master_debit.sql` has no `v_cap` or `allowance_cap_exceeded` at all (0 grep hits), and its header at `:68` says "last defined in 248 ..., originally 29", so it never mentions 258. `297_corporate_rpc_ride_idempotency.sql:67-70` documents the regression and restores the guard. `319_late_tip_debit_types.sql:186,211,292-293` carries the guard forward and extends it to `late_tip_debit`. My grep of every `FUNCTION ... corporate_allowance_apply_delta` definition gives 29, 203, 214, 248, 258, 261, 277, 297 and 319, and none after 319; the mentions in 282 and 376 are comments only. `payment_service.py:1387` catches `allowance_cap_exceeded`. The false-assurance note in `test_corporate_allowance_cap_race.py:35-44` is present.
- **Citation drift:** the card cites `test_money_rpc_races.py:73-82`, but the file is at `backend/tests/rls/money/test_money_rpc_races.py`. The cited lines match that file's content, and it runs in CI's RLS step (`ci.yml:316`).
- **Omission that softens the recommendation:** `backend/tests/test_corporate_rpc_ride_idempotency.py:325` (`TestMigrationRideIdempotencyContract`) statically parses migration 297 to assert the guard is present. That is a partial version of the proposed "diff against the last body" check, although it pins 297 rather than the latest body (319). The recommendation still stands.
- The VERIFIED label is appropriate.

### DISPATCH-001 (dispatch.md), card label VERIFIED, MEDIUM — **CONFIRMED**
- `backend/routes/rides/matching.py:1621` sends `dispatch_payload` only to `driver_{user_id}`. I grepped every `send_`, `emit`, `publish`, `notify` and `rider` token in the offer loop (`:1531-1719`) and found no rider-keyed send; the rider fields in that range are payload fields about the rider. `rider_{` occurs exactly three times in `matching.py` (`:1868`, `:2168`, `:2335`) and zero times in `services/driver_offer_service.py` and `services/dispatch_service.py`, matching the card.
- `.claude/context/domain-dispatch.md:40` does document `driver_assigned | backend → rider`. `broadcast_ride_status(` has 16 non-test call sites, so "6+" is correct.
- The VERIFIED label is appropriate. No drift.

### DRIVER-004 (driver-journey.md), card label VERIFIED, MEDIUM — **CONFIRMED** (two call sites mis-described)
- `backend/services/dispatch_service.py:76-91` ranks by `eta / max(acceptance_rate, 0.1)`, as claimed. Its only live caller is `matching.py:1039`, on the ETA-ranking branch (`if use_eta:` at `:1022`; `use_eta_ranking` defaults to True per `routes/admin/service_areas.py:230`). On the haversine fallback (`:1041-1044`) acceptance rate is not used. The card does not mention this condition, but the default path applies the penalty.
- `backend/repositories/driver_repo.py:360-381` is an EWMA with alpha 0.1. The explicit-decline call is `routes/drivers/ride_flow.py:798` (`accepted=False`), and `ride_flow.py:804` resets the miss streak on decline. Both are correct.
- **Mis-described call sites:** `matching.py:2074` is in the offer-**expiry** path (`_expire_offer_v2` / `process_expired_offer`, after `increment_miss_streak`), not an explicit decline. `matching.py:2092` is the reset that runs *after* an auto-offline, not a decline reset. The card's conclusion, that a decline and a timeout both lower the rate and only silence triggers auto-offline, still holds on the correctly-cited `ride_flow.py` lines.
- Absence claim re-checked: "acceptance" in `docs/driver-faqs-saskatchewan.md` gives 0 hits. Across `driver-app/app` and `driver-app/components`, "acceptance" appears only in unrelated consent and cancel copy, and nothing says declining affects priority. `driver-app/types/index.ts:31` declares an `acceptance_rate` field, but no `.tsx` renders it. The absence holds.
- Side note for the synthesizer (not a card error): the docstring at `backend/routes/admin/drivers.py:2489` says `drivers.acceptance_rate` "is not a column at all". That is stale: `migrations/100_batch_dispatch.sql:52` adds it, and dispatch reads and writes it.
- The VERIFIED label is appropriate.

