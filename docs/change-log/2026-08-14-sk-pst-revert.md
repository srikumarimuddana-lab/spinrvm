# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-14 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend (fare calculation), production data (`service_areas`), `.claude/context/regulatory-sk.md` |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Reverses `docs/change-log/2026-08-11-sk-pst-enable.md` |

## 1. Issue / gap identified

`docs/change-log/2026-08-11-sk-pst-enable.md` recorded a user-confirmed determination (2026-08-11) that PST applies to Saskatchewan rideshare, and enabled `service_areas.pst_enabled`/`pst_rate` for Saskatoon, Saskatoon Airport, and Regina Airport accordingly (Regina itself was left/found `pst_enabled: false` — the 08-11 doc claimed all 4 were updated, but live data showed only 3 actually were).

Three days later (2026-08-14), the same user gave the **opposite** determination: GST only, no PST applies to Saskatchewan rideshare. Neither determination cited a primary regulatory source (SK PST Act, a tax advisor, or SGI/provincial guidance) — both were verbal confirmations in a Claude Code session. This is the second flip on the same fact in one week.

## 2. Root cause

Not independently verified either time — this fix executes the user's 2026-08-14 determination, which explicitly reverses the 2026-08-11 one. Root cause of the flip itself is unknown (the two sessions did not cite conflicting sources against each other); flagging this plainly rather than picking a side, since neither this doc nor its predecessor has an authoritative citation. See "What was NOT verified" below — this is the load-bearing caveat of this entire change.

## 3. Fix / remediation

- **Data**: `service_areas.pst_enabled` set back to `false`, `pst_rate` set back to `0` for all 4 real Saskatchewan rows (Saskatoon, Saskatoon Airport, Regina, Regina Airport — including Regina, which the 08-11 doc claimed was already updated but live data showed otherwise; this fix makes all 4 consistent).
- **Code**: `backend/features.py`'s tax-calculation comment corrected again — this time deliberately written to **not** assert PST-applicability as settled fact either way, and to point future engineers at the change-log history + `.claude/context/regulatory-sk.md` instead of re-asserting a one-line claim that's already been wrong once.
- **Docs**: `.claude/context/regulatory-sk.md`'s tax-display section rewritten to flag this as **unresolved pending an actual regulatory-source confirmation**, not settled either direction — this is the most important part of this fix. The prior version of that file stated PST-applicability as fact; that pattern is exactly what let the 08-11 comment go unchallenged. This version explicitly tells the next reader not to trust either flip without a primary source.
- **No unit test changes** — `backend/tests/test_calculate_all_fees_tax.py`'s three tests (GST-only, GST+PST, HST-override) exercise the calculation *branches* generically via an arbitrary `matched_area` dict; they don't assert what Saskatchewan's real config should be, so they remain valid and unchanged by this revert.

## 4. Risk & impact on existing functionality

- **Blast radius**: identical to the 08-11 change — `service_areas.pst_enabled`/`pst_rate` feed exactly one consumer, `features.calculate_all_fees`'s ride-fare tax calculation (re-confirmed by grep this session). Not shared with Spinr Pass subscription tax (`service_areas.subscription_tax_config`, a separate JSONB column, migration 185) — unaffected by this change.
- **Zero rides were affected by the 3-day PST-enabled window.** Verified live: exactly **one non-legacy (real, live-app) ride exists in all of production**, dated **2026-08-08** — three days *before* PST was ever enabled. No rider was ever quoted or charged a PST-inclusive fare. **This means no refund/credit remediation is needed** — the 08-11 doc's rollback plan anticipated needing one "if PST turns out to have been enabled in error"; that scenario doesn't apply here because nothing was ever charged under the enabled state.
- Driver earnings math is unaffected either direction — PST is additive to the tax line, never touches `driver_earnings` (same finding as 08-11, re-confirmed by code read).
- Surge, promo, corporate billing: not touched, unaffected.

## 5. User-experience effect

**None, live.** No rider has fare-quoted in a Saskatchewan service area since before the 08-11 change existed, so there is no "a rider will see a different number" effect to describe — the theoretical rider-facing impact from the 08-11 doc never materialized in practice. Going forward, new fare quotes in Saskatoon/Regina will show GST only (no PST line), matching the pre-08-11 and now-current state.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `service_areas` table (production data, not a file) | `pst_enabled: true→false`, `pst_rate: 6→0` for all 4 real Saskatchewan rows (Regina included, correcting the 08-11 doc's inaccurate claim that Regina was already updated) | Reverse the 08-11 enablement per 2026-08-14 user determination |
| `backend/features.py` | Rewrote the tax-calculation comment to state current GST-only behavior without re-asserting it as unchallengeable fact; points to change-log history + regulatory-sk.md instead | Prevent a third silent flip based on a one-line code comment |
| `.claude/context/regulatory-sk.md` | Tax-display section rewritten to flag PST-applicability as **unresolved**, not settled — explicitly calls out the two-flips-in-a-week history | The prior confident wording is exactly what let the 08-11 comment go unchallenged; this version forces a future reader to seek a primary source |

## 7. Before / after

```sql
-- Before (as of 2026-08-11 fix, still live through 2026-08-14)
-- Saskatoon:        pst_enabled=true,  pst_rate=6
-- Saskatoon Airport: pst_enabled=true, pst_rate=6
-- Regina Airport:   pst_enabled=true,  pst_rate=6
-- Regina:           pst_enabled=false, pst_rate=6   (inconsistent with the other 3 — 08-11 doc's claim it was updated was inaccurate)

-- After (this change)
-- Saskatoon:         pst_enabled=false, pst_rate=0
-- Saskatoon Airport: pst_enabled=false, pst_rate=0
-- Regina Airport:    pst_enabled=false, pst_rate=0
-- Regina:            pst_enabled=false, pst_rate=0   (now consistent across all 4)
```

## 8. Rollback plan

- **Data**: set `pst_enabled = true`, `pst_rate = 6` on the same 4 `service_areas` rows via a direct Supabase update or the admin dashboard's Service Areas settings — no migration or redeploy needed, matching the `app_settings`-in-DB pattern.
- **Code (comment)**: `git revert` — no logic changed, comment-only.
- **What this rollback would NOT need to undo**: unlike the 08-11 change, this revert had zero rides in its live window, so there is nothing to "un-refund" or reconcile if it's flipped back again — the next flip (whichever direction) will be the first one to actually touch a real fare, once live ride volume picks up. **That's the real risk going forward, not this specific change**: the next flip, whenever it happens, will be the first one with actual financial consequences, so it should not repeat this pattern of an unsourced verbal confirmation.

## 9. Verification performed

- [x] Confirmed via direct Supabase query that all 4 real Saskatchewan `service_areas` rows now read `pst_enabled: false, pst_rate: 0` consistently (including Regina, which the 08-11 doc had incorrectly claimed was already set)
- [x] Confirmed via direct Supabase query that **zero** non-legacy rides have been created since 2026-08-11 (in fact, since 2026-08-08) — the basis for the "no remediation needed" conclusion in §4
- [x] Re-confirmed the single-consumer blast radius (`calculate_all_fees` only, not subscription tax) by grep, same method as the 08-11 doc
- [x] Confirmed `test_calculate_all_fees_tax.py`'s existing 3 tests remain valid (they test the branch logic generically, not Saskatchewan's specific config) — no test changes needed or made
- [ ] Did not run the full backend test suite as part of this change (comment-only code edit, no logic touched) — recommend running it in CI on the PR regardless
- [ ] Did not seek or obtain a primary regulatory-authority source (SK PST Act text, a tax advisor, or SGI guidance) for either the 08-11 enablement or this 08-14 reversal — this is the central open gap, called out deliberately in both this doc and the rewritten `regulatory-sk.md` section, not silently assumed resolved

## What was NOT verified

- **The actual, authoritative answer to "does Saskatchewan PST apply to rideshare services."** This document implements a user determination, not a tax-law finding. Two conflicting verbal confirmations in one week, neither cited against a primary source, is not a resolved compliance question — it's an open one that happens to currently be set to GST-only. **Recommend an actual tax/legal consultation before the next real Saskatchewan ride is quoted**, given the next flip (whichever direction) will be the first one with real financial consequences.
- Whether any other jurisdiction Spinr may expand into has a similarly unverified tax-applicability assumption baked into `service_areas` config — out of scope for this fix, flagged as a pattern to watch for.
