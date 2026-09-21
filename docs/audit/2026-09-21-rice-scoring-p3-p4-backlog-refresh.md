# RICE Scoring — Open P3/P4 Backlog Refresh (`ACTION_ITEMS.md`)

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (this session) — synthesized from two parallel Explore-agent extraction passes over the current P3 and P4 sections |
| Source | `ACTION_ITEMS.md` P3 (lines 17411–19415) and P4 (lines 19416–28036) sections, as of this session |
| Scope decision | Companion document, not an edit to `ACTION_ITEMS.md` — same rationale as the prior pass: that file is large (28k+ lines) and under active, frequent edit; injecting scores directly risks a needless merge conflict on a shared file for a purely additive, informational pass. |
| Supersedes | `docs/audit/2026-09-14-rice-scoring-p3-p4-backlog.md` (kept, not deleted — see "What changed since 2026-09-14" below) |
| Template used | `docs/templates/RICE_SCORING.md` |

## Why RICE, and what it does not tell you

RICE = (Reach × Impact × Confidence) / Effort. It is a **sequencing
heuristic, not a measurement** — every score below came from one reviewing
pass per item. Treat this as a first-pass ranking to sanity-check, not a
decision handed down.

## Methodology

Two Explore agents scanned the current P3 and P4 sections in parallel
(P3: ~2,000 lines; P4: ~8,600 lines — too large for one pass without
risking a shallow read). Their combined output was 38 open items. I found
**one gap in the automated pass**: `C43` (RLS disabled on 4 production
tables, including `settings` — the item behind `backend/migrations/379_*`,
which this session spent significant time investigating directly) was
missed by the P4 agent entirely, despite being open and previously the
#4-ranked item. I verified it directly against the source file and added
it back manually. This is exactly the kind of miss a single-pass automated
read can make on an 8,600-line section — treat the "closed" exclusions
below as a strong signal, not a guarantee, the same way the source items
themselves ask you to treat their own confidence claims.

Final count: **41 open items** (8 from P3, 33 from P4 including the
manually-added C43).

**Exclusions**: ~180 distinct items across both sections are closed
(`[x]` and/or "CLOSED"/"FIXED"/"RESOLVED" with no material open thread) and
are omitted. One checkbox/text contradiction was found and resolved toward
"open": `N12` is checked `[x]` but its own body explicitly says to treat it
as open until a vendor/process decision is made — scored as open here.

**Duplicate IDs kept as separate rows** (per this file's own established
precedent for duplicate numbering — see `C13`/`C100` in the source file):
`C111` (two distinct items, one closed/one open — only the open one is
below), `C112` (same pattern), `C118` (two distinct open items — a CR
registry and a ride-distance-integrity trigger gap — both below).

**Splits**: none of the 41 items bundled independently-shippable sub-tasks
with materially different scope, with one partial exception — `C119`
(spinr.app phantom-domain cleanup) has 8 named sub-followups (a)–(h) of
varying size; scored as one row for the aggregate remaining work rather
than 8 rows, since they share one root theme and most are small.

## What changed since 2026-09-14

- **New items surfaced** (not in the prior pass, mostly filed in the week
  since): `E14`, `C91` (residual), `C95`, `C106`, `C112` (audit_logs
  trigger — a real, if currently dormant, bug), `C113`, `C117`, `C118`
  (both), `C119`, `C121`, `C122`, `C123`, `C125`, `C126`, `C127`, `C73`.
  That's 16 new open items in roughly a week — this backlog is growing
  faster than it's closing in the P3/P4 tier.
- **Dropped from the prior list** (now closed or no longer found open):
  `C42-C`, `C42-D`, `HM-32`, `A41`, `C108`, `C111` (the settings-column
  variant), `C112` (the FCM-parity variant). Progress, not a regression.
- **Unchanged in substance**: `E1`, `E2`, `E4`, `E7`, `E8`, `E13`, `E6`,
  `C99`, `C103`, `C70`, `C90`, `C49`, `C50`, `A43`, `N18`, `N11c`, `D5`,
  `D6`, `G6`, `G7`, `UX2` — same scope, scored the same as before unless
  noted in "one-line why."

## Sorted by RICE score (descending)

| Rank | Item | Reach | Impact | Confidence | Effort | Score | One-line why |
|---|---|---|---|---|---|---|---|
| 1 | **A43** — PR #5048 bypassed the CLAUDE.md escalation gate | 6 | 3 | 80% | 0.5 | **28.80** | Unchanged from last pass — still just a branch-protection setting away from fixed; still the highest-leverage item on the list. |
| 2 | **C73** — `main`'s merge doesn't require checks to pass | 6 | 2 | 80% | 0.5 | **19.20** | Same root cause and same remediation action as A43 — fixing A43's branch-protection setting very likely closes this too. Listed separately only because the source file tracks it separately. |
| 3 | **C103** — Device/ops verification access gap (consolidates C70/C90/C91-residual + 2 more) | 7 | 2 | 80% | 1 | **11.20** | One resourcing decision unblocks 5 other listed items at once; C97's own #1 sub-item is now resolved, narrowing what's left. |
| 4 | **E1** — Staging environment | 8 | 3 | 80% | 2 | **9.60** | Foundational; blocks E2 and E6 directly. Still 3 human-only manual steps, no code. |
| 4 | **E2** — Marketplace load/simulation testing | 6 | 2 | 80% | 1 | **9.60** | Harness fully built, never executed; also the prerequisite for C50's Go/No-Go. |
| 4 | **E13** — Leading-indicator ops monitoring | 6 | 2 | 80% | 1 | **9.60** | Workflows shipped; every date is still a TBD placeholder — a real production outage already happened in this exact gap class. |
| 4 | **C43** — RLS disabled on 4 production tables (incl. `settings`) | 3 | 2 | 80% | 0.5 | **9.60** | Migration written, reviewed, ready — but **deliberately parked by explicit user instruction this session** ("its not done yet iam holding back the last step so park it"). High score, zero actionability until that deferral lifts. |
| 8 | **E7** — Backup-restore drill | 5 | 2 | 80% | 1 | **8.00** | Verification tooling fully built and reviewed; remaining work is pure execution. |
| 9 | **E4** — Synthetic monitoring + SLO alerting | 7 | 2 | 80% | 1.5 | **7.47** | Spec documented; needs a vendor pick + wiring. |
| 10 | **C90** — Phone-screen CarMarker fixes unverified on real device | 7 | 1 | 50% | 0.5 | **7.00** | Code shipped and test-verified; rolls up into C103's access blocker. |
| 11 | **C123** — Unreachable admin-RLS pattern on 7 more tables (2 safety-critical) | 4 | 1 | 80% | 0.5 | **6.40** | Same fix pattern already proven (migration 430); deliberately held for sign-off given safety-table sensitivity, not technical difficulty. |
| 11 | **E8** — CODEOWNERS + review routing | 4 | 1 | 80% | 0.5 | **6.40** | File/logic built; only real team-slug assignment + a branch-protection toggle remain. |
| 13 | **C99** — No Fly/Railway CLI access; Firebase MCP can't authenticate | 3 | 1 | 80% | 0.5 | **4.80** | Largely subsumed by C103 now; kept as a standalone item since it was never explicitly closed. |
| 14 | **C121** — Fly deploys an unsigned source rebuild, not the signed/scanned GHCR image | 8 | 2 | 50% | 2 | **4.00** | Highest blast-radius item in this batch (every production backend deploy) — but 3 concrete unresolved gaps (auth, race condition, provenance) keep confidence at 50%. |
| 15 | **C125** — Production missing 8 more pending, unreviewed migrations | 5 | 1 | 50% | 1 | **2.50** | Same class of work this session already did for a different batch (407–423) in one sitting — proven achievable, just needs a session with `DATABASE_URL`. |
| 16 | **N12** — No per-email-client rendering verification | 8 | 0.5 | 80% | 1 | **3.20** | Checkbox says closed, body says open — corrected to open here. Touches every outbound email; effort is low for the interim manual-checklist path. |
| 16 | **C91** (residual) — `dashboard-monitoring` visual-regression baseline needs re-capture | 2 | 1 | 80% | 0.5 | **3.20** | Code fix already shipped; blocked only on a human running `update-visual-baselines.yml` (no Actions-dispatch access in-session). |
| 16 | **C94** — Code scanning never enabled repo-wide; SARIF uploads permanently fail | 4 | 0.5 | 80% | 0.5 | **3.20** | Lowered from the prior pass's Impact=1 — now mitigated with `continue-on-error` so it no longer blocks merges; underlying gap is real but no longer urgent. |
| 16 | **C119** — `spinr.app` phantom-domain cleanup, 8 named sub-followups remaining | 4 | 1 | 80% | 1 | **3.20** | One sub-item (consent-copy inconsistency across 3 screens) is PIPEDA-adjacent and worth pulling forward even if the rest waits. |
| 20 | **C112** (dup) — `audit_logs` migration-57 trigger defeats a flag-gated retention DELETE exception | 3 | 1 | 80% | 1 | **2.40** | Currently a silent no-op, not a crash — but a naive future fix (just re-enabling the flag) without also fixing the trigger conflict would turn this into a crash that rolls back the *entire* daily retention purge. Needs its own migration, not a quick patch. |
| 20 | **C118** (dup) — Open Change Requests registry, incl. 2 small PRs (#5419/#5422) pending review | 3 | 0.5 | 80% | 0.5 | **2.40** | Most of this item is gated on other blockers (Grafana vendor account, Stripe access, C21 audit); the two drafted PRs are the one immediately actionable slice. |
| 20 | **C106** — Turn-by-turn shipped on Legacy Directions API, not Routes API | 3 | 0.5 | 80% | 0.5 | **2.40** | Explicitly a deliberate NO-GO for now (feature is dark-launched, $0 cost) — this is a tripwire to re-check before ever flipping the flag on, not active work. |
| 23 | **C70** — Android Auto hardware re-validation overdue | 2 | 2 | 50% | 1 | **2.00** | Same safety/UX bug class already fixed twice, unverified after 5 more changes; rolls up into C103. |
| 24 | **C118** (dup) — `ride_distance_integrity_events`/`recomputes` lack DB-enforced immutability | 2 | 1 | 80% | 1 | **1.60** | Two regulatory audit-trail tables claim immutability in their own comments with no trigger enforcing it — no live exploit path today, but a real gap vs. sibling tables that do enforce it. |
| 24 | **E14** — Stray `origin/staging` branch (8,000+ commits, diverged) | 2 | 0.5 | 80% | 0.5 | **1.60** | Pure git hygiene; needs one human decision (reconcile vs. retire), do-not-touch until decided. |
| 24 | **C95** — `label-run-maestro.yml` fix applied but never confirmed by a green run | 2 | 0.5 | 80% | 0.5 | **1.60** | Low effort — just needs one clean CI run to observe; verification was blocked by an unrelated outage at the time. |
| 27 | **C117** — Claude Code marketplace plugins show 0 enabled across this session type's entire history | 3 | 0.5 | 50% | 0.5 | **1.50** | Every plugin-enablement PR this cloud-session type has shipped may be inert here; needs a non-cloud session to confirm scope (environment-specific vs. platform bug). |
| 28 | **G6** — Driver retention program | 6 | 2 | 50% | 5 | **1.20** | Draft 6-month plan exists (`docs/growth/driver-retention-strategy.md`), deliberately deferred until there's a real post-launch driver cohort to measure against. |
| 29 | **G7** — Corporate/B2B GTM plan not actioned | 4 | 2 | 50% | 5 | **0.80** | Real evidence of displaceable local demand (Captain Taxi); phased 90-day plan exists (`docs/CORPORATE_B2B_GTM.md`), unexecuted — business/sales work, not engineering. |
| 29 | **N18** — No light-on-dark Spinr logo variant | 2 | 0.25 | 80% | 0.5 | **0.80** | Cosmetic; asset candidate already exists, pending design sign-off. |
| 29 | **C113** — Debug FCM endpoint has a misleading "parity" comment, no PII exclusion | 2 | 0.25 | 80% | 0.5 | **0.80** | No live leak (hardcoded literal value) — fix the comment and add the exclusion next time the file is touched. |
| 29 | **C49** — RLS DB-role-level test coverage (~42 of ~70 tables covered) | 3 | 1 | 80% | 3 | **0.80** | Methodology proven and incrementally repeatable across 3+ rounds; meaningful remaining scope (~28 tables), no full inventory yet. |
| 33 | **UX2** — Shared spacing/type-scale tokens not enforced on ~15 inline-JSX style props | 3 | 0.25 | 80% | 1 | **0.60** | Highest-value part (StyleSheet blocks) already shipped repo-wide; this is the long tail, blocked on a go/no-go call rather than effort. |
| 33 | **C126** — `backend-test` CI timeout bumped 20→30min as a stopgap | 3 | 0.5 | 80% | 2 | **0.60** | Stopgap holds fine today; structural fix (pytest-xdist or splitting the RLS suite) is real engineering work with 4 downstream `needs:` dependents to update. |
| 35 | **C122** — `locationIntegrity.test.ts` flaked red in CI twice, never reproduced locally in 16 attempts | 2 | 0.5 | 50% | 1 | **0.50** | Code traced as provably deterministic; leading hypothesis is Jest/coverage-instrumentation, needs CI-access experimentation to isolate. |
| 36 | **C111** (dup) — `emergency_contacts` has no admin/super_admin RLS override policy | 1 | 0.25 | 80% | 0.5 | **0.40** | No live impact — the real SOS pipeline reads via service-role, bypassing RLS entirely. |
| 36 | **C50** — PostgREST → direct-pool (Supavisor) migration for the dispatch claim path | 8 | 0.5 | 50% | 5 | **0.40** | Highest blast-radius item on the whole list by its own text, but gated on a load test (needs E1/E2) and an unattributed Fly secret needing explanation — deliberately not greenlit. |
| 36 | **C127** — `deploy-fly-signed-image.yml`'s wait-budget margin shrunk after C126's timeout bump | 1 | 0.25 | 80% | 0.5 | **0.40** | Dormant risk — the workflow is manual/opt-in only, not yet in the automatic production path. |
| 39 | **N11c** — Delete legacy receipt/invoice email shell + flag | 3 | 0.25 | 50% | 1 | **0.38** | The receipt path has 24 clean days in prod (safe to delete), but the *invoice* path has had **zero** sends of any status — deletion is blocked pending diagnosis of a possibly-separate, unfiled bug (is the invoice-send path silently broken?). |
| 40 | **D6** — Read-only root filesystem | 2 | 1 | 50% | 3 | **0.33** | Meaningful hardening, fully blocked on an undated host migration off Railway. |
| 41 | **D5** — In-app VoIP/masked-PSTN rider↔driver calling | 5 | 1 | 50% | 8 | **0.31** | Genuinely unscoped; building anything here means explicitly reopening a 2026-06 privacy decision (chat-only), not just a scoping pass. |

## Read before acting on this ranking

- **#1/#2 (A43/C73) likely collapse into one fix.** Before treating these as
  two separate units of work, confirm with whoever has repo-admin access
  that fixing A43's branch-protection setting also resolves C73 — they may
  be the same GitHub Settings checkbox.
- **#4 (C43) is high-scoring but not actionable.** This is a business
  decision already made this session, not a technical blocker — don't
  schedule engineering time against it until the deferral is explicitly
  lifted.
- **#3 (C103) and its 5 dependents (C70, C90, C91, plus 2 more) are really
  one resourcing decision**, not 6 separate pieces of engineering work —
  the blocker is device/ops access, not code.
- **#14 (C121) has the widest blast radius on the entire list** (every
  production backend deploy) but Confidence is capped at 50% because 3
  concrete gaps are unresolved — this is the item most likely to move
  significantly once someone actually attempts the fix.
- **N11c's blocker may be hiding a real, unfiled bug** — zero
  `subscription_invoice` sends in 24 days could mean the invoice-send path
  itself is silently broken, not just unused. Worth a quick diagnosis pass
  independent of whether the cleanup task itself gets prioritized.
- **G6/G7's Effort is business/GTM work, not engineering** — not directly
  comparable to the technical items around them.
- Reach for backend/infra-only items remains an ordinal judgment call
  ("how much of the platform does this touch"), not a measured count.

## Recommendation

The near-zero-effort governance cluster is the clear next slate: **A43 +
C73** (very possibly one settings change), **E8** (CODEOWNERS), and **C95**
(just needs a clean CI observation) are all ≤0.5 effort with real,
proven-costly failure modes behind them. In parallel, **E1** (staging) is
worth prioritizing purely because it's the single biggest unblocker on this
whole list — it gates E2, E6, and indirectly C50's Go/No-Go. **C103**
deserves a scoping conversation with whoever owns ops/device access before
committing engineering time, since it unblocks 5 other listed items at
once. **C43** stays parked per your explicit instruction — do not schedule
it despite the score.
