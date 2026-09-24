# A5 — Money, ledger & CRA

**Lane:** A5 · **Model:** sonnet / spinr-money-auditor · **Returned:** 2026-09-24 ~14:26 UTC (partial, 25-minute time box) · **Orchestrator note:** lane output pasted verbatim below; only this header was added. All tax statements in this file are ASSUMED (no primary source in repo) and are carried into ESCALATIONS.md. The Stripe connector was not used; no live Stripe or Supabase data was queried.

---

SPINR MONEY AUDIT — Lane A5 (Money, ledger & CRA), 60-min rapid baseline, repo read-only
Time-boxed pass (~25 min). Scope covered: fare_service.py, receipt_pdf.py, stripe_reconcile.py, ledger_service.py, payment_retry.py, drivers/earnings.py, drivers/payouts.py, surge call sites, corporate settlement paths, pre-commit hook + CI semgrep gate, CRA docs. Not fully covered: routes/tips.py, routes/corporate_wallet.py line-by-line, allowance_reset.py replay logic, receipt PDF full snapshot diff — see NOT VERIFIED.

## FINDING CARDS

### MONEY-001 — Driver earnings "tips" figures sum raw floats, not Decimal, in 2 endpoints
- Hierarchy: L2 Payments › L3 Driver Earnings › L4 GET /earnings, GET /earnings/comparison › L5 tip total display
- Severity: HIGH   Priority score: S×B×L ≈ high (money display, every driver, every period)
- Status: VERIFIED   Existing item: not found in ACTION_ITEMS.md grep for "tip" + float — appears new
- Adversary: auditor / plaintiff's lawyer ("your earnings statement doesn't add up to my receipts")
- Evidence: `backend/routes/drivers/earnings.py:490` (`GET /earnings`) — `"total_tips": sum(r.get("tip_amount", 0) or 0 for r in rides)`; `backend/routes/drivers/earnings.py:964` (`GET /earnings/comparison`) — `"tips": sum(r.get("tip_amount", 0) or 0 for r in rides)`, two lines below a comment on the same function reading "Decimal accumulation (CLAUDE.md money-arithmetic rule); cast to float only at the response boundary" (line 949-950) that this exact line violates.
- What happens (plain language): `tip_amount` is `NUMERIC(12,2)` in Postgres (migration 407) but `supabase-py` deserializes JSON numerics as Python `float`. Every other tip-summation site in this same file (lines 91, 589/591, 667, 730, 779/781, 840, 881/883, 956, 1047, 1054 — 10+ call sites) correctly wraps each value in `_d()`/`Decimal(str(...))` before summing. These two sum the raw floats directly, accumulating binary floating-point drift across however many rides fall in the period — a driver's lifetime "all" period or a busy month could show a tip total off by a cent or more from the sum of their individual ride receipts.
- Root cause: inconsistent helper usage within one file — two call sites were written (or later edited) without going through `_d()`, unlike every sibling call site in the same module.
- Recommendation: `sum((_d(r.get("tip_amount") or 0) for r in rides), Decimal("0"))`, cast to float only at the JSON boundary — matching line 489/951-960's own pattern in the same file.   Alternative considered: leave as-is since it's a "display-only" derived stat, not a payout write — rejected because a driver-visible total that doesn't reconcile to their own receipts is exactly the kind of discrepancy that generates support tickets/disputes, and the fix is a one-line, zero-risk change.
- Blast radius: grepped `routes/drivers/earnings.py` for `tip_amount` — 14 total references, only these 2 are unwrapped. No other file reads these two response fields' exact values downstream in a way that would compound the drift (they're terminal API response fields).
- Rollout: no flag needed — additive precision fix, doesn't change response shape.   Rollback: git-revert-safe (display-only, no persisted-data mutation).
- Verification to close: unit test with ≥3 rides carrying tip amounts like 1.10/2.20/3.30 (values known to not round-trip exactly in binary float) asserting the summed total equals the exact Decimal sum, not a float-summed approximation; also not caught by CI's SR-03 semgrep gate (`.semgrep/spinr-rules.yml`) because its patterns target `float($X)` / `* 1.0` / `/ 1.0`, not raw `sum()` over unwrapped values — worth a semgrep rule addition too.

### MONEY-002 — Receipt PDF fallback path bundles GST+PST into one "Tax" line
- Hierarchy: L2 Payments › L3 Receipts › L4 receipt_pdf.py tax section › L5 missing tax_breakdown fallback
- Severity: MEDIUM   Priority score: medium (compliance-adjacent, edge-case-triggered)
- Status: VERIFIED   Existing item: related to ACTION_ITEMS.md 2026-08-19 entry ("fallback label corrected from 'Tax (GST/PST)' to plain 'Tax'" — line ~687) which fixed the *label* honesty but left the *bundling* itself unresolved; treating the underlying bundling as still-open since that entry only addressed mislabeling.
- Adversary: auditor / SK regulator (CLAUDE.md + domain-payments.md: "GST and PST as separate Decimal line items on receipts. Never roll into the fare" / "Never bundle into 'service fee' or 'other'")
- Evidence: `backend/utils/receipt_pdf.py:174-199` — when `ride.get("tax_breakdown")` is missing/empty, the code falls back to `gap = persisted_grand - subtotal + capped_discount` and appends a single `("Tax", _money(gap))` line instead of separate GST/PST rows.
- What happens (plain language): on any ride where `tax_breakdown` wasn't persisted (legacy rows, or any write path that skips populating it), the rider's PDF receipt shows one combined "Tax" line instead of GST 5% and PST 6% as CLAUDE.md's receipt contract requires — same substance as the "hidden fee" anti-pattern even though the fix is honest about it not being itemized.
- Root cause: `tax_breakdown` is not guaranteed to be populated on every ride row (only fixed the label, not the data-availability gap, in the prior pass).
- Recommendation: find and close the write-path gap that leaves `tax_breakdown` unpopulated (likely legacy-imported rides per the file's other comments), or compute the GST/PST split for the fallback from the same rate constants used at booking time rather than presenting one lump figure.   Alternative considered: leave the honest single "Tax" label as sufficient — rejected because CLAUDE.md's rule is about disclosure granularity, not just label accuracy.
- Blast radius: `email_receipt.py` mirrors `receipt_pdf.py`'s logic per its own header comment — check whether it has the same fallback (not verified this pass, time-boxed).
- Rollout: additive, no flag needed.   Rollback: revert-plus-data-cleanup not needed (display-only).
- Verification to close: a receipt-generation test for a ride with `tax_breakdown=None` but a non-null `grand_total`, asserting two separate GST/PST lines, not one.

### MONEY-003 — SK PST applicability to rideshare still has no primary-source citation (re-surfaced, not new)
- Hierarchy: L2 Payments › L3 Tax config › L4 service_areas.pst_enabled › L5 CRA/SK compliance
- Severity: HIGH (regulatory exposure)   Priority score: high — affects every SK ride's tax line
- Status: ASSUMED   Existing item: **ACTION_ITEMS.md B26 (CLOSED operationally 2026-08-22), follow-up tracked as G9 (open)** — this is a re-found open item, reporting why it's stuck, not re-filing.
- Adversary: SK Ministry of Finance auditor / plaintiff's lawyer (mis-collected or under-collected provincial tax at scale)
- Evidence: `docs/compliance/2026-08-13-sk-pst-rideshare-determination-needed.md` (full history); `backend/features.py:810` code comment "Saskatchewan rideshare is GST-only (5%), no PST"; grep for `canada.ca`/`cra-arc.gc.ca` across `docs/` and `backend/` returns zero hits tying this specific determination to a primary source (only unrelated hits — CRA BN format validators, T4A copy, `docs/compliance/2026-08-13-...`'s own note that `WebFetch` to `saskatchewan.ca`/`canada.ca` was environment-blocked).
- What happens (plain language): the current production config (all 4 SK service areas `pst_enabled=false`) rests on the repo owner's own verbal determination given three times (once for, twice against, net: against/GST-only), never checked against the actual PST-46 bulletin or equivalent primary source because every attempt to fetch it was blocked in-session. If the correct answer is "PST applies," every SK ride since the 2026-08-14 revert has under-collected 6% PST — a live, compounding remittance shortfall, not hypothetical.
- Root cause: no session has had working, unblocked access to `saskatchewan.ca`/`canada.ca` to read the bulletin text directly; the decision has been made three times on secondhand/verbal signals instead.
- Recommendation: route to an actual SK-licensed tax advisor or the Ministry of Finance Revenue Division before the next SK PST-rate change or launch expansion — this is exactly the kind of question G9 already exists to track; escalate G9's priority given live revenue impact either direction.   Alternative considered: keep current GST-only config and treat as settled — rejected because "settled by repeated verbal answer" is explicitly what `sweep-catalog.md` §4 calls ASSUMED, not VERIFIED, until a primary source is cited.
- Blast radius: `backend/features.py` (2 call sites at lines 838, 992), all 4 `service_areas` rows, every SK receipt's tax line, CRA/SK remittance filings.
- Rollout: N/A (config, not code).   Rollback: N/A.
- Verification to close: G9 closed with an actual bulletin citation (URL + retrieval date) per `sweep-catalog.md` §4 methodology.

### MONEY-004 — CLAUDE.md/domain-payments.md overstate what the local pre-commit hook actually blocks
- Hierarchy: L2 Payments › L3 Tooling/CI › L4 money-arithmetic gate
- Severity: LOW (doc drift; real backstop exists)   Priority score: low-medium
- Status: VERIFIED   Existing item: new
- Adversary: none directly — this is a documentation-accuracy gap that could mislead a future contributor into over-trusting local commit safety
- Evidence: `.claude/hooks/pre-commit:114-121` (step "6/11 Checking money arithmetic") only ever `echo`s `WARNING` and never sets `BLOCKED=1`, and its regex (`(fare|price|amount|earning|payout)\s*[+\-\*\/]\s*[0-9]*\.[0-9]+`) is generic across all staged `*.py`/`*.ts`, not scoped to `fare_service.py`/`routes/payments.py` as CLAUDE.md's "Money arithmetic" bullet and `domain-payments.md:25` both state ("Pre-commit hook blocks float arithmetic in fare_service.py and routes/payments.py"). The actual blocking control is CI's `security-gates.yml` "Money-safety gate (SR-03, blocking)" step running `semgrep` against `.semgrep/spinr-rules.yml`'s `spinr-no-float-in-money` rule, scoped to 12 named files (verified 2026-08-14 per that file's own comment) — a materially different, better mechanism than what the docs describe, just not a "pre-commit hook."
- What happens (plain language): a local `git commit` with float arithmetic in money code is never blocked before it leaves the developer's machine — it's only caught at CI/PR time. For anyone who force-pushes past CI or works in an environment where the CI job is skipped/red-for-unrelated-reasons (CLAUDE.md gate 8 scenario), there is no local safety net despite the docs implying one exists.
- Root cause: CLAUDE.md/domain-payments.md text predates (or was never updated to match) the 2026-08-14 promotion of SR-03 from advisory-in-CI to blocking-in-CI; the git hook step 6 was likely the original, weaker mechanism these docs were originally written to describe.
- Recommendation: either flip `.claude/hooks/pre-commit` step 6 to `BLOCKED=1` and scope it to the same 12-file list SR-03 uses (redundant-but-consistent), or correct CLAUDE.md/domain-payments.md to say "CI blocking gate (SR-03)" instead of "pre-commit hook."   Alternative considered: leave docs as-is since CI is a real backstop — rejected because CLAUDE.md is the contract this very audit is instructed to enforce verbatim, and it's currently wrong about the mechanism.
- Blast radius: doc-only; no code behavior change either way.
- Rollout: doc fix, no flag.   Rollback: trivial.
- Verification to close: CLAUDE.md/domain-payments.md text matches whichever mechanism is chosen as authoritative.

### MONEY-005 (RECOMMENDATION) — SR-03 semgrep allowlist knowingly excludes `routes/drivers/earnings.py`
- Severity: LOW (tooling coverage gap, not a confirmed bug beyond MONEY-001)
- Status: VERIFIED (as a coverage gap) / INFERRED (as low-risk)
- Evidence: `.semgrep/spinr-rules.yml:117-124` — explicit, documented exclusion of `backend/routes/drivers/earnings.py` because its `float()` calls are "Decimal->float at the JSON response boundary... not wrapped in _round()/_d(), so the pattern-nots do not clear them," with the fix intentionally deferred as "not a drive-by edit."
- What happens: this file is exactly where MONEY-001 was found — the semgrep gate's own documented blind spot is the same file with the real bug, though MONEY-001 itself (raw `sum()` without any `_d()`/`float()` wrapper at all) is a different pattern than the one the exclusion note describes and wouldn't be caught by SR-03 even if the file were included.
- Recommendation: revisit the earnings.py exclusion now that a real bug (MONEY-001) has been found there; add a semgrep pattern for "unwrapped `sum()` over a raw dict-value generator" or accept manual review as the control for this file going forward.
- Blast radius / Rollout / Rollback: tooling-only.
- Verification to close: N/A (recommendation).

## CRA QUESTIONS (sweep-catalog.md §4.1)

| Question | What code does (path:line) | Primary source in repo? | Label | Escalate to |
|---|---|---|---|---|
| GST/HST registration for ride-share drivers — does the $30k small-supplier exemption apply? | Code asserts it does NOT apply ("taxi business" under Excise Tax Act) and hard-blocks payout without a BN on file: `backend/routes/drivers/payouts.py:762-780` (`_gst_on_file`/`_require_gst_for_payout`) | No canada.ca/CRA citation found in repo (grep `canada.ca`/`cra-arc.gc.ca` across `docs/`, `backend/`) | ASSUMED | Tax advisor / finance — this gates every driver payout, high confidence but uncited |
| Who is the supplier of record — driver or platform? | Implicit: driver is treated as supplier (independent contractor per `docs/legal/terms-of-service.md:102`, `independent-contractor-agreement.md`); Spinr issues T4A-compatible summary, not a GST invoice on the driver's behalf | No explicit CRA citation on this specific point | ASSUMED | Same as above — affects receipt content and who owes GST remittance |
| Rider receipts show GST/PST as separate lines; does SK PST apply to passenger transport? | `backend/features.py:810,838,992`; `backend/utils/receipt_pdf.py:174-199` — currently GST-only in production (`pst_enabled=false` on all 4 SK areas) | No — see MONEY-003; all fetch attempts to saskatchewan.ca/canada.ca environment-blocked | ASSUMED (tracked as G9, open) | SK Ministry of Finance / tax counsel |
| Platform-operator digital-platform reporting (Income Tax Act Part XX.1) — scope, SIN/TIN fields, deadlines | `docs/change-log/2026-07-29-t4a-filer-handoff-export.md` built a SIN-free handoff export explicitly because "Whether Part XX.1 genuinely applies... was described based on published CRA guidance in this session's research, not confirmed by a tax advisor" (author's own words, line 84 of that doc) | No | ASSUMED (self-flagged in its own change-log already) | Tax advisor — self-identified gap, not re-discovered here |
| T4A issuance threshold ($500) correctness | `backend/utils/t4a_pdf.py`, `routes/admin/compliance.py` apply a $500 threshold (per `t4a-filer-handoff-export.md`'s own text) | No canada.ca citation found in either file | ASSUMED (plausible — matches CRA's published T4A box-048 $500 threshold from general knowledge, but not cited in-repo) | Tax advisor to confirm the figure is still current |
| Books & records retention period vs Spinr's 7-year trip retention | Spinr retains 7 years (`CLAUDE.md` Tax Retention, `data-classification.md`) | No explicit CRA citation of the 6-year minimum it's meant to exceed | INFERRED safe (7 ≥ CRA's general 6-year minimum) | Low priority — conservative posture |
| Corporate invoices meet input-tax-credit documentation requirements | Not investigated this pass (time-boxed) — `routes/corporate_company_bookings.py`/billing PDF not read | Unknown | UNKNOWN | Needs a follow-up pass — see NOT VERIFIED |
| Tax treatment of rider credits/promo and driver incentives | `receipt_pdf.py:163-171` — discount applies to "ride fare (driver earnings) only — never fees or taxes"; exact tax-on-discounted-fare mechanics not traced to a CRA rule this pass | No | ASSUMED / not deeply verified | Tax advisor — flagged as under-investigated, not confirmed wrong |

## STEELMAN (5 bullets)

- **The daily Stripe↔DB reconciliation (`utils/stripe_reconcile.py`) is genuinely mature**: it checks amount mismatches, missing/orphan PaymentIntents, stuck `processing` rides, stuck unprocessed webhook events, orphan pre-auth holds, and payout discrepancies in one nightly pass with Sentry/audit-log write-through — this is well beyond a minimal reconciliation job and reflects real incident-driven hardening (multiple named `_reconcile_*` functions each tracing back to a specific historical gap).
- **The `payment_retry.py` per-attempt idempotency-key suffix (line 627) is a deliberate, correctly-reasoned exception to the naive "never new keys on retry" rule**, not a bug: it operates on an already-created, fixed `payment_intent_id` (itself created under a stable key), so a fresh `confirm()` key per attempt only prevents Stripe from replaying a stale cached error — it cannot cause a double charge because Stripe's own PI-level state (`succeeded` is terminal) governs the outcome, not the idempotency key. The code comment explains this precisely.
- **SR-03's promotion to a CI-blocking gate (2026-08-14) was done by actually running semgrep against the codebase, not by review-and-assume** — the promoting commit's own comment records that a prior "hand-checked, clean" declaration was wrong and the tool found 3 real findings the review missed. That's the right instinct (verify, don't reason) and it caught MONEY-001-adjacent issues before, just not this exact pattern.
- **The `driver_earnings_with_tip()` refactor (fare_service.py:252-300) is a strong example of fixing a class of bug, not an instance** — it exists specifically because 3 independent call sites each mutated `driver_earnings` relative to a stale read, causing a real underpayment incident; the fix makes the function idempotent-by-construction (always recomputed fresh) rather than patching each call site individually.
- **`platform_share` was searched for as an assignment across all of `backend/` and found nowhere** — the 0%-commission invariant (CLAUDE.md's brand-defining number) is not merely undocumented-as-absent, it's genuinely absent from the codebase as a variable that could be set non-zero.

## NOT VERIFIED (time-boxed out of scope this pass)

- `routes/tips.py`, `routes/payouts.py` beyond the GST-BN gate, `routes/corporate_wallet.py`, `utils/surge_engine.py`'s tier-computation internals — not read line-by-line.
- `email_receipt.py` — assumed to mirror `receipt_pdf.py`'s tax-line logic per its own header comment (per MONEY-002), but not independently confirmed to have the same fallback-bundling behavior.
- `allowance_reset.py`'s `auto_approved_this_period` replay-safety claim — cited from `domain-payments.md`, not independently read this pass.
- Corporate invoice input-tax-credit documentation format — not located/read.
- Whether `ledger_projection.py`'s double-entry legs (`financial_event_entries`) are actually enabled in production (`ledger_double_entry_enabled` flag default is off per docs) — not checked against live `app_settings`.
- No live Stripe/Supabase data was queried (Stripe connector explicitly off-limits this pass; no DB access attempted) — every finding above is a static-code read, not a production-data cross-check. The `driver_stripe_ledger` blended-account reconciliation question already tracked as open in ACTION_ITEMS.md was not re-investigated.
- Semgrep was not actually re-run this pass (no execution environment used) — SR-03's file-scope and "0 findings" claim is taken from the rule file's own comments (VERIFIED as documented, not VERIFIED as re-executed).
- **(Orchestrator addition)** The scope items "SURGE_CAP clamp at every fare-calc call site", "corporate/scheduled rides exempt from surge", "payout ≤ collected invariant", and "corporate_wallet_apply_delta callers each carry an idempotency key" were not reported on by the lane and are UNKNOWN from this run — see DEFERRED.md.

VERDICT (lane): FIX BLOCKERS — MONEY-001 (raw-float tip summation) is a concrete, low-effort, high-confidence fix that should land before next release; MONEY-003 (SK PST primary-source gap) is the standing item needing NEEDS FINANCE/LEGAL REVIEW rather than an engineering fix — recommend escalating ACTION_ITEMS.md G9's priority given the live, compounding nature of the exposure either direction. MONEY-002/004/005 are MEDIUM/LOW and can be scheduled normally.
