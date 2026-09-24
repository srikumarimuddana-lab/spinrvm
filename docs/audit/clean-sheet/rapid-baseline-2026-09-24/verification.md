# Verification — Wave B Verifier (random 10% re-check + dedupe)

**Model:** opus / general-purpose (deliberately a different model from every lane author) · **Returned:** 2026-09-24 ~14:44 UTC · **Orchestrator note:** report pasted verbatim below; only this header and the "Corrections applied" section were added.

**Headline numbers**
| Measure | Value |
|---|---|
| VERIFIED cards enumerated | 42 |
| Random sample (deterministic pick) | 5 (A3-004, DRIFT-003, OBS-002, RECURRENCE-005, SEC-A2-004) |
| REFUTED / sampled | 0 / 5 = **0%** |
| (PARTIALLY + REFUTED) / sampled | 2 / 5 = **40%** — both partials are citation/evidence-type defects (a wrong file path, doc-only evidence), not wrong conclusions |
| Extra spot-checks (outside the rate) | MONEY-001 CONFIRMED · OBS-001 PARTIALLY CONFIRMED (count misattributed to one file; total holds; existing item R4 not cited) · A3-001 CONFIRMED with caveats |

**Corrections the orchestrator applied after this report** (recorded, not silently fixed):
1. `standards-verified.md` — the path `backend/routes/admin/drivers/drivers.py` cited in DRIFT-003/004 and RECURRENCE-005 does not exist; the file is `backend/routes/admin/drivers.py`. The C73 row's citation (`claude-review.yml:44-47`) does not support the branch-protection claim; the claim stands on ACTION_ITEMS C73/A43 text only (INFERRED). The C43 "CRITICAL" wording is a misread — see §5(a) below.
2. `A6-observability.md` — OBS-001's "122 in matching.py" is wrong: `matching.py` has 31 `.error(` calls; the rides package has 121 and `routes/drivers/*.py` 167 (total ≈ 288–290 holds). OBS-001/OBS-006 overlap open item **R4** ("Sweep `.bind(domain=...)` across the remaining Sentry-bound ERROR logs"); they are the larger, quantified instance of R4, not "previously undocumented".
3. `DEFERRED.md` — loop count normalised to the registry: 45 catalog entries including the watchdog (44 loops + watchdog); A4 read 5 in full.
4. `EXECUTIVE_SUMMARY.md` — OBS-001 reported at HIGH (master §4 scale), and MONEY-002's harm is stated as latent (production is GST-only today), per §5(a).

---

# Wave B Verifier report (rapid baseline 2026-09-24)

This was a read-only pass inside the 10-minute box. No files were written.

**Headline:** the random sample found no refuted findings. 2 of 5 were partially confirmed, and both problems were in the citations, not the substance. The extra spot-checks found one real numeric misattribution (OBS-001). The dedupe pass found 4 cards that re-find or overlap existing ACTION_ITEMS entries without saying so (OBS-001/R4, SEC-A2-003/R4, SAFETY-005/C23, MONEY-002/corporate PDF entry).

## 1. Sample and results

**What counts as a VERIFIED card:** 42 cards have a Status line that starts with VERIFIED. That includes mixed labels such as "VERIFIED / INFERRED".
- **Excluded:** SEC-A2-005, A3-002, A3-007 and DISPATCH-003 (INFERRED), MONEY-003 (ASSUMED), SAFETY-005 (UNKNOWN), A11Y-003 (PARTIAL-VERIFIED), and INFO-005 (no Status line).
- **Selection:** I sorted the 42 IDs in ASCII order and numbered them 1–42. With k = ⌈42/5⌉ = 9, the picks are positions 3, 12, 21, 30 and 39: **A3-004, DRIFT-003, OBS-002, RECURRENCE-005, SEC-A2-004.**

| ID | Claim checked | path:line opened | Verdict | Note |
|---|---|---|---|---|
| A3-004 | Maps breaker defaults to $5/day, fails open on Redis error, has no early-warning tripwire | `backend/utils/maps_budget.py:1-18,116,301-370`; `core/config.py:193`; `.github/workflows/billing-usage-monitor.yml:17-28` | CONFIRMED | Code matches. The $5 is the default in `config.py`, and the production value (Fly secrets) can't be seen from the repo, so "$5 cliff in prod" is INFERRED. The blast-radius list leaves out `utils/route_distance.py:817,924` (the fare-path Directions call), `routes/rides/_deps.py:294` and `utils/address_verification.py`. The Existing-item cite (C104, ACTION_ITEMS:27159) is right, and there is no tripwire item. |
| DRIFT-003 | `admin_verify_driver` docstring wrongly says there is no `verified_at` column; 2 approval paths don't stamp it | `backend/routes/admin/drivers.py:1973-1981, 2147, 2284+`; `migrations/12_driver_lifecycle_status.sql:9` | PARTIALLY CONFIRMED | The substance is correct: the stale docstring is still there, only `admin_driver_action` writes `verified_at`, and the override path writes only `is_verified`. But the card cites a path that doesn't exist (`routes/admin/drivers/drivers.py`), and its Evidence line is change-log docs only, not code. There is no ACTION_ITEMS entry for `verified_at`, so it is untracked. |
| OBS-002 | 3 SLA rows have no latency metric: location write, token refresh, Stripe webhook | `routes/drivers/location.py:203`; `routes/webhooks.py:659`; repo-wide grep of `*_duration_ms` names | CONFIRMED | None of the 14 `_duration_ms` metrics cover those 3 paths, and there is no generic HTTP-latency middleware. Two caveats: `spinr_drivers_location_write_total` exists (a count, not latency), and Sentry tracing at `traces_sample_rate=0.1` (`server.py:701`) gives sampled transaction latency, which the card doesn't mention. The Evidence line is a grep, not an opened code path. The card also doesn't cite C11 ("nothing scrapes production metrics"), which is relevant. |
| RECURRENCE-005 | Recurring doc-vs-code drift in admin gating | the 4 change-logs exist; `routes/admin/data_transfer_jobs.py` (stale phrase now gone); `admin-dashboard/src/components/sidebar.tsx` exists | PARTIALLY CONFIRMED | The occurrences are real, but 3 of the 4 are already fixed, so "recurrence" is historical. "Files affected" repeats the wrong path `admin/drivers/drivers.py`. It also misreads CLAUDE.md gate 10 as "verifying code comments": gate 10 is about adversarial review and checking fork siblings. Status VERIFIED rests only on change-log docs. |
| SEC-A2-004 | No cert pinning, no ADR; threat model RS-4 says OPEN P1 while the standards doc says "deliberate trade-off" | `rider-app/app.config.ts:75-82`; `driver-app/app.config.ts:80-87`; `docs/threat-model/rider-app.md:52,126,149`; `docs/audit/clean-sheet-prompt/standards-and-scale.md:24,62`; `ls docs/adr` (001–016) | CONFIRMED | No pinning libraries in rider-app, driver-app or shared, and no pinning ADR. "New" is correct: the only ACTION_ITEMS "pinning" hits are Docker and Actions pinning. The "§2.7" cite is list item 7 at line 62. |

## 2. Error rates

- (PARTIALLY + REFUTED) / sampled = 2/5 = **40%**
- REFUTED / sampled = 0/5 = **0%**
- Both partials are citation and evidence-type defects (a wrong path, doc-only evidence), not wrong conclusions. With n=5 the confidence interval is very wide.

## 3. Extra spot-checks (not part of the error rate)

- **MONEY-001 — CONFIRMED.** `backend/routes/drivers/earnings.py:490` has `"total_tips": sum(r.get("tip_amount", 0) or 0 for r in rides)` right next to Decimal-summed `total_earnings`. Line 964 has `"tips": sum(r.get("tip_amount", 0) or 0 for r in rides)` inside a function that otherwise accumulates Decimals. There are no ACTION_ITEMS hits for `total_tips` or `earnings/comparison`, so "new" holds.
- **OBS-001 — PARTIALLY CONFIRMED.**
  - `backend/routes/rides/matching.py` has **31** `.error(` calls, not the 122 claimed. The rides package as a whole (`routes/rides/*.py`) has **121**, and 1 `.bind(` in total.
  - `routes/drivers/*.py` has **167** `.error(` calls (card says 168) and **0** `.bind(`.
  - So the ~290 total is right but misattributed to one file. The mechanism is confirmed: `scrub_event` (`utils/sentry_scrub.py:146-150`) only stamps `surface`, and domain tags come only from `tags_from_log_extra` (`:209`).
  - The dedupe is weaker than the card says: ACTION_ITEMS:17880 **R4 is literally titled "Sweep `.bind(domain=...)` across the remaining Sentry-bound ERROR logs"** (open). The body names only 2 handlers, but the item already exists, so "previously-undocumented" overstates. The card should have said why R4 is stuck.
  - The CRITICAL vs HIGH difference is already noted by the orchestrator.
- **A3-001 — CONFIRMED in repo, with caveats.**
  - `resolve_process_role` defaults to `"all"` (`background_loop_registry.py:86`), and `role=="all"` spawns every loop (`:112-113`).
  - `LOOP_CATALOG` has **45 entries: 15 api / 3 worker_wave1 / 27 deferred**. That includes `loop_watchdog`, so it is 44 loops plus the watchdog.
  - `backend/fly.toml [env]` (lines 26-31) has no `SPINR_PROCESS_ROLE`.
  - Caveat 1: whether a Fly secret sets it is UNKNOWN from the repo.
  - Caveat 2: the card leaves out `backend/fly.worker-canary.toml:26-28`, a staged role-split config. It defaults api/burst to `all` and runs one `worker` process with `push_retry` only.
  - No ACTION_ITEMS entry for process role or the worker canary, so "new" holds.

## 4. Dedupe table

| Finding ID | Lane | Severity | Dedupe class | ACTION_ITEMS ref | Note |
|---|---|---|---|---|---|
| SEC-A2-001 | A2 | (per card) | NEW | — | No HS256/RS256/kid hits |
| SEC-A2-002 | A2 | (per card) | NEW | — | No pgsodium-deprecation hits |
| SEC-A2-003 | A2 | (per card) | EXISTING-OPEN (partial) | R4 (:17880-17884) | R4 names the "Redis revocation-denylist fail-open" handler, but for tagging, not for the posture. The card says "no open item"; it should cite R4. |
| SEC-A2-004 | A2 | MEDIUM | NEW | — | Verified (sample) |
| SEC-A2-005 | A2 | (per card) | LANE-ALREADY-DEDUPED | C3 / C5 area (:13797-13806) | Cite is correct |
| SEC-A2-006 | A2 | (per card) | NEW | — | |
| SEC-A2-007 | A2 | LOW | NEW | — | Fork not in `known-forks.md`. ACTION_ITEMS:18417 describes users.py as reusing auth.py's OTP helpers, but the `"1234"` bypass branch is still duplicated (`users.py:1255` vs `auth.py:463,803`), so the finding stands. |
| SEC-A2-008 | A2 | (per card) | NEW (doc drift) | ACTION_ITEMS:3835 repeats the same stale "fully trusted" wording | The stale claim is copied into ACTION_ITEMS too |
| A3-001 | A3 | HIGH | NEW | — | See §3 |
| A3-002 | A3 | (per card) | NEW | — | |
| A3-003 | A3 | (per card) | NEW | — | Only an unrelated gzip hit at :25276 |
| A3-004 | A3 | MEDIUM | LANE-ALREADY-DEDUPED | C104 (:27159) | Tripwire gap is new |
| A3-005 | A3 | (per card) | NEW | C5 (closed) is related | |
| A3-006 | A3 | (per card) | EXISTING-OPEN | renewal-calendar gap (:20033-20067) | Lane cited the runbook, not the ACTION_ITEMS lines |
| A3-007 | A3 | (per card) | NEW | — | |
| DISPATCH-001 | A4 | (per card) | NEW | — | |
| DISPATCH-002 | A4 | (per card) | NEW | — | :2346 mentions the decline Redis cooldown only in a test context |
| DISPATCH-003 | A4 | INFO | NEW (scope note) | — | |
| DISPATCH-004 | A4 | PASS | n/a | — | Positive control |
| MONEY-001 | A5 | HIGH | NEW | — | Confirmed |
| MONEY-002 | A5 | (per card) | LANE-ALREADY-DEDUPED (partial) | :687; also :4373 (sibling `corporate_statement_pdf.py` fallback) | The card missed the :4373 corporate-statement sibling |
| MONEY-003 | A5 | HIGH | LANE-ALREADY-DEDUPED | B26 (:7369, closed), G9 (:17748, open) | Correct |
| MONEY-004 | A5 | (per card) | NEW | — | |
| MONEY-005 | A5 | REC | NEW | SR-03 mentions at :709, :26076 | |
| OBS-001 | A6 | CRIT/HIGH | EXISTING-OPEN | R4 (:17880) | The title covers the sweep, so not "previously undocumented" |
| OBS-002 | A6 | HIGH | NEW (related) | CR-2026-008/#3295 (:28335); C11 not cited | |
| OBS-003 | A6 | (per card) | NEW | — | |
| OBS-004 | A6 | (per card) | NEW | — | |
| OBS-005 | A6 | (per card) | NEW | Code tag "[B27]" suggests a B27 lineage | Check B27 before filing |
| OBS-006 | A6 | (per card) | EXISTING-OPEN (sub-part) | R4 | The CI-enforcement half of R4's sweep |
| CONCURRENCY-001 | A7 | (per card) | LANE-ALREADY-DEDUPED | C66 (:24142), different scope | Correct |
| RELIABILITY-002 | A7 | (per card) | NEW | — | |
| A11Y-003 | A7 | (per card) | NEW (partial overlap) | :741-745 (WAV toggle a11y fix, 2026-08-19) | The card says "not found", but a WAV a11y fix entry exists |
| INFO-004 | A7 | INFO | LANE-ALREADY-DEDUPED | `known-forks.md` | |
| INFO-005 | A7 | PASS | n/a | — | |
| SAFETY-001 | A8 | (per card) | EXISTING-CLOSED | B15 (:6343, CLOSED 2026-08-22) | Lane cited B15(a) as decided, so correctly not re-filed |
| SAFETY-002 | A8 | (per card) | NEW (vs ACTION_ITEMS) | — | Tracked only in `domain-safety.md` |
| SAFETY-003 | A8 | LOW-MED | NEW | — | |
| SAFETY-004 | A8 | (per card) | NEW (vs ACTION_ITEMS) | — | Baseline in the agent file only |
| SAFETY-005 | A8 | UNKNOWN | EXISTING-CLOSED (adjacent) | C23 (:17045, CLOSED 2026-08-18) | Lane didn't cite C23. C23 covers chargeback ops tooling, not repeat-abuser detection, so the residual is narrower than the card implies. |
| RECURRENCE-001 | Std | — | LANE-ALREADY-DEDUPED | B28 (:7581), A28 (:4184) | |
| RECURRENCE-002 | Std | — | LANE-ALREADY-DEDUPED (ambiguous) | C100 appears **twice** (:13519 and :26888); C101 (:26932) | Duplicate ID in ACTION_ITEMS |
| RECURRENCE-003 | Std | — | LANE-ALREADY-DEDUPED | A26/A31-A33 | |
| RECURRENCE-004 | Std | — | LANE-ALREADY-DEDUPED | C12 (:14699), B42 (:24639, closed) | |
| RECURRENCE-005 | Std | — | NEW (pattern) | — | See §1 |
| DRIFT-001 / 002 | Std | — | EXISTING-CLOSED | change-logs 2026-07-28 | Stale phrase is gone from `data_transfer_jobs.py`, so the fix holds |
| DRIFT-003 | Std | — | NEW (untracked open) | — | No ACTION_ITEMS entry; escalated only in the change-log |
| DRIFT-004 | Std | — | EXISTING-CLOSED | change-log 2026-07-29 | Not re-verified in code |
| DRIFT-005 | Std | — | EXISTING-CLOSED | A40 | **A40 appears twice** (:251 and :5141); ambiguous cite |

## 5. Contradictions and label issues

**(a) Disagreements between lanes or with the code**
- **Loop count:**
  - A3-001 and A3-005 say 45, CLAUDE.md and A4's DISPATCH-003 say 42, and the capacity runbook says 18.
  - Actual: `LOOP_CATALOG` has 45 entries including `loop_watchdog`, so 44 loops plus the watchdog.
  - A4's "full 42-loop catalog … `:31-77`" is wrong against the file it cites. `DEFERRED.md:21` says "37 of 45", which contradicts A4's own "37 of 42".
  - CLAUDE.md's "42" is stale.
- **RLS / C43:**
  - Standards-verified (A1, table at :103 and :195) calls C43 CRITICAL, with "unprotected schema access by any authenticated backend code". That misreads it: backend code uses the service role, which bypasses RLS whether or not it is enabled.
  - C43's actual risk (ACTION_ITEMS:21184) is anon/publishable-key reads through PostgREST of `settings` (which holds API keys).
  - A2 lists only "RLS dormancy / C108 documented" and **never mentions C43**, an open P0 security item. A2 has a coverage gap there; the two lanes are describing different things (dormant policies vs. RLS disabled), not contradicting each other.
  - Migration 379 is "PREPARED, NOT APPLIED", which is consistent with C43 being deferred.
- **MONEY-002 vs MONEY-003:** MONEY-002 says the fallback line shows one "Tax" line "instead of GST 5% and PST 6%". MONEY-003 and the CRA table say production runs GST-only (`pst_enabled=false` on all 4 SK areas), so today's bundling harm is nil unless PST is enabled.
- **OBS-001 severity:** CRITICAL (lane) vs HIGH (orchestrator). Already recorded.
- **Standards-verified C73 row** cites `.github/workflows/claude-review.yml:44-47` for branch-protection state. Those lines are about the `synchronize` trigger, so the citation doesn't support the claim.
- **Admin JWT trust:** SEC-A2-008 says the "admin JWT fully trusted" wording is stale, but ACTION_ITEMS:3835 and `known-forks.md:40` repeat it. The drift spreads beyond CLAUDE.md.

**(b) Status VERIFIED, but the evidence is a grep count or a doc, not opened code**
- OBS-001 (grep counts; one count also misattributed)
- OBS-002 (grep)
- OBS-006 ("as above")
- SAFETY-002 (a `domain-safety.md` cite; the card itself says "not independently re-verified in code")
- SAFETY-004 (a grep with zero hits, in `auth.py` only)
- INFO-004 (`known-forks.md`)
- RECURRENCE-001 to 005 and DRIFT-001 to 005 (change-log and ACTION_ITEMS citations only; DRIFT-005 even says "(implied)")
- SEC-A2-002 (a web doc, which is fine for the deprecation fact; impact correctly INFERRED)
- A3-006 ("VERIFIED (absence)", acceptable)

**(c) Tax, legal or regulatory statements without a primary source**
- MONEY-002: the "GST/PST must be separate receipt lines" requirement cites CLAUDE.md and `domain-payments.md` only (internal). It needs ASSUMED on the regulatory premise; the code fact itself is verified.
- The A5 CRA table is correctly labelled ASSUMED/INFERRED throughout. Note that the T4A $500 row relies on "general knowledge", which the lane disclosed.
- SAFETY-003: correctly INFERRED on SGI coverage.
- A3-004 names a "regulator (WAV/accessibility)" adversary with no source. That is minor.
- MONEY-003 is correctly ASSUMED.

## 6. What the verifier did not check

- Only 5 of the 42 VERIFIED cards were re-opened (plus 3 extra spot-checks). The other ~37 cards' path:line evidence was not opened; they were only deduped by keyword grep.
- Dedupe used keyword grep on ACTION_ITEMS.md (about 28k lines). Items filed under different wording could be missed. I did not check B27 lineage for OBS-005.
- I did not look at production env or secrets (Fly `SPINR_PROCESS_ROLE`, `MAPS_DAILY_BUDGET_USD`), Sentry, Supabase or the live DB.
- I did not check whether `LOOP_CATALOG` loops are conditionally spawned (`_active_loop_names`), which could lower the effective runtime count below 45.
- I did not re-verify DISPATCH-001/002/004, SEC-A2-001/003/006/008, or the A7 scenario table rows.
- Severity values marked "(per card)" were not independently re-scored.
