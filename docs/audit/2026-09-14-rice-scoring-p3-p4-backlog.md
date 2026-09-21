# RICE Scoring — Open P3/P4 Backlog (`ACTION_ITEMS.md`)

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (background agent pass) |
| Source | `ACTION_ITEMS.md` P3 (line 17165) and P4 (line 19149) sections |
| Scope decision | This is a **companion document, not an edit to `ACTION_ITEMS.md`**. That file is large (26k+ lines) and under active, frequent edit by other work in flight this session and elsewhere — editing it directly to inject scores risked a needless merge conflict on a shared file for a purely additive, informational pass. If a maintainer wants these scores folded into `ACTION_ITEMS.md` itself, that should be a separate, deliberate edit reviewed on its own. |

## Why RICE, and what it does not tell you

RICE = (Reach × Impact × Confidence) / Effort. It is a *sequencing* heuristic, not a measurement — every number below was inferred by one reviewing pass reading each item's own written description once. It did not consult analytics, support-ticket volume, telemetry, or the item's actual owner. Treat this as a first-pass ranking to sanity-check, not a decision.

## Methodology correction vs. the original ask

The request assumed ~50–60 open P3/P4 items. The actual count is **32**. Most P3/P4 bullets found on inspection were `- [x]` (closed, with a long resolution writeup), and several `- [ ]` bullets are explicitly self-labeled by the file as superseded/historical audit trail (e.g., an original investigation kept unchecked on purpose after a later entry closed it) — those were excluded to avoid double-counting resolved work. Two P3 items (C42's sub-bullets) were split into independently-scoreable sub-items (C42-C, C42-D) since they are separable tasks under one heading.

## Sorted by RICE score (descending)

| Rank | Item | Reach | Impact | Confidence | Effort | Score | One-line why |
|---|---|---|---|---|---|---|---|
| 1 | **A43** — PR #5048 bypassed the CLAUDE.md escalation gate (34-file security/money batch merged 47s after opening) | 6 | 3 | 80% | 0.5 | **28.80** | Near-zero-effort fix (one branch-protection setting) against a proven-costly failure mode — already caused two same-day incidents. |
| 2 | **C97** — Driver-app push notifications "not visible" (two compounding root causes, neither fully fixed) | 8 | 2 | 50% | 0.5 | **16.00** | Touches nearly every active driver; confidence capped at 50% because the item's own fork (infra vs. client cause) is unresolved pending ops access. |
| 3 | **C103** — Device/ops verification access gap (root cause behind 5 other open items) | 7 | 2 | 80% | 1 | **11.20** | One resourcing decision unblocks C70, C90, C91's last step, and two C97 sub-items at once. |
| 4 | **E1** — Staging environment (deploys go `main` → production directly today) | 8 | 3 | 80% | 2 | **9.60** | Foundational — blocks E2, E4 (partially), E6, E7, and C50's load-test gate; scaffolding already built, 3 concrete human actions remain. |
| 4 | **E2** — Marketplace load/simulation testing (harness built, execution blocked on E1) | 6 | 2 | 80% | 1 | **9.60** | Directly validates whether the P0–P2 query-optimization work already meets the dispatch/fare SLAs. |
| 4 | **E13** — Leading-indicator ops monitoring (certs, domain, secret rotation, capacity) | 6 | 2 | 80% | 1 | **9.60** | This exact gap class already caused a real production outage (expired cert on `api-spinr.spinr.ca`); 4 workflows already merged, remaining work is data entry. |
| 4 | **C43** — RLS disabled on 4 production tables (incl. `settings`, holding Stripe/Twilio/Maps keys) | 3 | 2 | 80% | 0.5 | **9.60** | Migration written, reviewed, and tested; only a deliberate `--apply` + regression pass remains — deferred for business, not technical, reasons. |
| 8 | **E7** — Backup-restore drill ("a backup is only real after a restore") | 5 | 2 | 80% | 1 | **8.00** | Verification tool fully built, tested (21 tests), reviewed by `spinr-money-auditor`; remaining work is pure execution. |
| 9 | **E4** — Synthetic monitoring + SLO alerting (outages currently discovered by users) | 7 | 2 | 80% | 1.5 | **7.47** | Probe spec and thresholds already documented; needs a vendor pick + wiring. |
| 10 | **C90** — Phone-screen vehicle-icon fixes unverified on a real device | 7 | 1 | 50% | 0.5 | **7.00** | On-map marker visible on nearly every active ride; risk is visual-quality, not correctness, and code already shipped. |
| 11 | **E8** — CODEOWNERS + review routing (owner handles still placeholders) | 4 | 1 | 80% | 0.5 | **6.40** | File and routing logic already built; only real team-slug assignment + a branch-protection toggle remain. |
| 11 | **C42-D** — Confirm the dominant DB-rejection reason in production | 4 | 1 | 80% | 0.5 | **6.40** | One named metrics command informs a capacity-threshold tuning decision. |
| 11 | **C94** — `codeql-action/upload-sarif` fails on every PR (Code Scanning disabled at repo level) | 4 | 1 | 80% | 0.5 | **6.40** | Root cause fully diagnosed; fix is a single repo-admin settings toggle (possibly gated on an Advanced Security purchase). |
| 14 | **C99** — No Fly/Railway CLI access; Firebase MCP can't authenticate from this environment | 3 | 1 | 80% | 0.5 | **4.80** | Blocks C97's #1 recommendation; fix is a credential/connector grant, no code. |
| 15 | **C48** — Location-write gate: read shadow measurement, decide the flip, delete scaffolding | 7 | 1 | 80% | 1.5 | **3.73** | Touches every online driver's GPS write path (~85–120 writes/sec fleet-wide); fully detailed runbook exists with flip/rollback criteria. |
| 16 | **C70** — Android Auto hardware re-validation overdue (5 JS-level changes since last device pass) | 2 | 2 | 50% | 1 | **2.00** | The exact bug class already fixed twice (icon pointing wrong way while driving) is unverified after 5 subsequent changes — real safety/UX risk if regressed. |
| 17 | **E6** — Pre-launch DAST + third-party pentest | 5 | 2 | 50% | 3 | **1.67** | Standard high-value control for a payments+PII platform, but genuinely gated on a procurement/budget cycle outside engineering's control. |
| 18 | **C42-C** — Drop the legacy `X-Deadline-Ms` header path | 2 | 0.5 | 80% | 0.5 | **1.60** | Already defanged by a clamp; trigger metric named, just needs to read 0 before deleting. |
| 19 | **G6** — Driver retention program (no post-launch plan beyond onboarding) | 6 | 2 | 50% | 5 | **1.20** | Real headroom per external research (income predictability > commission, which Spinr already fixed); draft plan exists but unstarted, and effort is a 6-month program, not a code change. |
| 19 | **HM-32** — iOS demand heatmap: Skia raster-gradient renderer, awaiting device verification | 3 | 0.5 | 80% | 1 | **1.20** | Code fully implemented and unit-tested; needs one EAS build + device check, no more code. |
| 21 | **A41** — Legacy-migration data-quality audit (5-agent sweep) | 5 | 2 | 50% | 5 | **1.00** | Several regulatory-blocker-class sub-findings (consent basis, insurance-period reconstruction); large multi-threaded epic with a brand-new export not yet touched. |
| 22 | **N18** — No light-on-dark Spinr logo variant | 2 | 0.25 | 80% | 0.5 | **0.80** | Cosmetic; affects only branded-email rendering in dark-mode clients. Asset already drafted, pending design sign-off. |
| 22 | **G7** — Corporate/B2B GTM plan not yet actioned (Captain Taxi has a displaceable in-market product) | 4 | 2 | 50% | 5 | **0.80** | Real evidence of local displaceable demand; a phased 90-day plan, not a code task, so effort doesn't compare cleanly to engineering items. |
| 22 | **C49** — RLS DB-role-level test coverage (partial: ~32 of ~64 policy-bearing tables) | 3 | 1 | 80% | 3 | **0.80** | Methodology proven and incrementally repeatable (3 rounds landed cleanly); meaningful remaining scope with no full inventory yet. |
| 22 | **C108** — `auth.users` empty in production; `auth.uid()`-based RLS policies unreachable via anon key | 2 | 0.25 | 80% | 0.5 | **0.80** | Confirmed zero live production impact (service-role key bypasses RLS on every real request); fix is doc-wording only. |
| 26 | **UX2** — Shared spacing/type-scale constants — inline-JSX-prop sweep remaining | 3 | 0.25 | 80% | 1 | **0.60** | Highest-value part (StyleSheet blocks) already shipped; ~15 files of inline styling remain. |
| 27 | **C25** — Accepted-risk record: image-size HIGH advisories, no upstream patch exists | 1 | 0.25 | 100% | 0.5 | **0.50** | Build-time/devDependency only, confirmed never shipped in the runtime bundle; this is a standing risk-acceptance record, not an actionable task. |
| 28 | **N11c** — Delete the pre-retrofit receipt/invoice shell + its flag | 1 | 0.25 | 80% | 0.5 | **0.40** | Trivial deletion, but blocked on its own precondition (weeks of clean branded sends) not yet met — 0 sends recorded so far. |
| 28 | **C50** — PostgREST → direct pool (Supavisor) for the dispatch claim path | 8 | 0.5 | 50% | 5 | **0.40** | Highest blast-radius item on this list by its own text, but its own stated likely outcome is **No-Go** (existing optimizations may already meet SLA); real answer is gated behind a load test that needs E1 (staging) first. |
| 28 | **C111** — `emergency_contacts` has no admin/super_admin RLS override policy | 1 | 0.25 | 80% | 0.5 | **0.40** | No current code path affected — the real SOS pipeline reads via service-role, bypassing RLS entirely. Not urgent; a documented future decision if a feature ever needs it. |
| 31 | **D6** — Read-only root filesystem (blocked on host migration off Railway) | 2 | 1 | 50% | 3 | **0.33** | Meaningful security hardening, but fully blocked on an undated host migration with unclear timing. |
| 32 | **D5** — In-app VoIP/masked-PSTN rider↔driver calling | 5 | 1 | 50% | 8 | **0.31** | Genuinely unscoped per its own text; would also reopen a 2026-06 deliberate chat-only privacy decision. |

## Read before acting on this ranking

- **#1 (A43)'s rank is driven almost entirely by near-zero stated effort** (a repo-admin checkbox). Worth confirming that's actually as trivial as the item claims before treating it as the top pick.
- **#2/#3 (C97, C103) have Confidence capped at 50–80%** because their own text says the real root cause or user-facing scope is unconfirmed pending ops/device access this environment doesn't have. Their true score could move substantially once that access exists — this is exactly the kind of item RICE is bad at scoring from a single read.
- **Reach numbers for backend/infra-only items** (C42-C/D, C48, C50, D6) are an ordinal judgment call ("how much of the platform does this touch"), not a measured user count.
- **G6/G7's Effort is business/GTM work, not engineering**, so their scores aren't directly comparable to the technical items around them on the same table.
- Excluded: roughly a dozen `- [ ]` bullets `ACTION_ITEMS.md` itself marks superseded/historical/kept-for-the-record. If any of those readings turn out to be live rather than historical, they'd need to be added back in and scored.

## Recommendation

Given the strategic focus this session settled on (Saskatoon marketplace liquidity → cohort retention instrumentation → backlog prioritization), the top-4 cluster (A43, C97, C103, E1/E2/E13/C43 tied) is a reasonable next-sprint slate: A43 and the E1/E13/C43 group are each near-zero engineering effort relative to their score and mostly unblock *other* work (E1 unblocks E2/E6/E7/C50; C103 unblocks 5 other items). C97 is the one genuinely uncertain high-score item — worth a scoping conversation with whoever owns ops/Firebase access before committing engineering time, since its own confidence is only 50%.
