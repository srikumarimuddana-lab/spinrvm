# Technical debt register — prioritised by impact × probability × cost of delay

**Lane:** Tier B matrix (2 of 2) · **Wave:** post-W5 · **Date:** 2026-09-25 · **Mode:** report-only. No code, config or data changed.

## §0 Method

- **Sources**: `matrices/risk-register.md` (142 de-duplicated rows, RR-01..142 — the primary source;
  this file reframes a subset of those rows through a different lens rather than re-deriving them),
  `00-history.md` §3 "Decay signals" (38 signals, cited) and §4 "Doc-vs-code drift", and
  `ACTION_ITEMS.md`'s open (`- [ ]`) items (102 top-level open checkboxes; a further sample of
  nested action items was grepped for candidates not already folded into a risk-register row).
- **De-duplication rule**: every row states its **Dedup** status — either `= RR-nn` (this row is a
  reframing of that risk-register row, same underlying defect, no new finding) or `NEW` (not
  individually represented as its own risk-register row, sourced from `00-history.md` §3/§4 or
  `ACTION_ITEMS.md` directly). §4 lists every `NEW` row together so the count is checkable at a
  glance. This file adds **no new severity scoring** — S×B×L values are risk-register's, cited
  verbatim where a row reframes one.
- **Cost of delay** is qualitative, each with a one-clause reason (compounds per-ride, compounds
  per-PR, compounds per-day-public, fixed one-time cost that doesn't grow, etc.) — not a dollar
  figure, per the task's instruction.
- **Effort** is S (hours to ~1 day, one file/config change) / M (days, a few files, needs a test) /
  L (weeks, multi-file/multi-track, needs a migration or a staged rollout) — read from the
  originating lane's own "Blast radius"/"Rollout" language where available, otherwise estimated
  from the fix description.
- **Owner role** uses `matrices/risk-register.md`'s own owner-role legend (cited) for consistency.
- **Finding IDs** cites every underlying finding-card id this row folds in, plus the risk-register
  row it reframes (if any).
- Evidence labels follow the rest of this audit: VERIFIED / cited-VERIFIED / INFERRED / ASSUMED /
  UNKNOWN.

## §1 Tier 1 — cost of delay compounds daily or is a single missed window

| Item | Cost of delay | Effort | Owner role | Finding IDs | Dedup |
|---|---|---|---|---|---|
| Confirm/execute rotation of the leaked `SUPABASE_SERVICE_ROLE_KEY` and the downstream `app_settings` secrets it can read (Stripe, Twilio, Maps) | **Compounds daily, unbounded** — if the old key still works, every day of delay is another day of full read/write exposure on every table; if already rotated, the cost of delay is only "a gate stays advisory for no reason" | S (rotation itself is ~15 minutes per `docs/runbooks/secret-rotation.md`, cited) | FOUNDER + SRE | RR-18, `matrices/incident.md` row 1 | = RR-18 |
| Confirm SOS paging: set `sos_paging_webhook_url` (or equivalent) so a real emergency press actually reaches a human | **Compounds per-incident, safety-critical** — every day this stays unset is a day a real SOS press pages nobody automatically; the existing runbook describes a system that does not exist | S (a setting + a runbook rewrite; `05-escalations.md` E-F4 recommends an interim SMS-bridge this week, a full paging service within a month) | FOUNDER + T&S | RR-116, RR-86 | = RR-116 |
| Stop the undisclosed Meta per-ride `Purchase` conversion event stream | **Compounds per-ride** — every paid ride today adds one more undisclosed data-flow event; the guardrail breach (CLAUDE.md "never add third-party ad SDKs") also compounds reputationally the longer it's undiscovered by a rider/regulator | S (clear the Meta token in `app_settings` — no deploy, immediately reversible) | FOUNDER + PRIVACY + COUNSEL | RR-38, STRAT-004 | = RR-38 |
| Pause LogRocket unmasked session replay on iOS | **Compounds per-session** — every iOS session today sends OTP entry, saved addresses and live map screens to an undisclosed US vendor | S (pause in the LogRocket console — no code change, no deploy) | MOBILE + PRIVACY | RR-19 | = RR-19 |
| Add an HTTP timeout to the Twilio client | **Compounds per slow-vendor-minute** — with no timeout, a *slow* (not down) Twilio response can hang every OTP request and exhaust the shared thread pool, taking down login/signup platform-wide, not just OTP | S (two-line fix per the citing card) | BE-AUTH | RR-98 | = RR-98 |
| Fix the merge-gate bypass (`main`'s required-checks list unread by anyone with access; PR #5048 merged 47s after opening via an apparent repo-admin bypass) | **Fixed cost that compounds only in exposure, not in effort** — every day the gate stays advisory is another day a PR like #5048 (which shipped B42, 53/55 lost payment-failure events) can merge unreviewed | S to name and record the gap; the actual branch-protection fix needs a human with GitHub Settings access this audit has never had | REPO-ADMIN | RR-01, RR-02, RR-03, `matrices/incident.md` row 3 | = RR-01 |
| Get a written primary-source answer on SK PST applicability to ride-share fares | **Compounds per-ride, retroactively** — if PST applies, every ride charged GST-only since launch is an under-collection that compounds and cannot be fixed by a later config flip; four verbal determinations exist and none cite a primary source (11/11 government-site fetches blocked again this session, §0 of `compliance.md`) | S once the answer exists (`pst_enabled` is a config flip, no deploy) — the actual bottleneck is a 10-minute human read, stuck 34+ days | ACCOUNTANT | RR-56, COMP-003 | = RR-56 |
| Get a written primary-source answer on the correct SK driver licence class (Class 4 vs Class 5) | **Compounds per-online-driver** — the eligibility recheck is flag-gated off today (nil live effect), but every day the class question stays unanswered is a day closer to someone switching the flag on the wrong rule | S (a regulation read; code change is a one-line accepted-set flip once answered) | FOUNDER → SGI/COUNSEL | RR-69, COMP-001 | = RR-69 |

## §2 Tier 2 — cost of delay compounds per-release or per-PR

| Item | Cost of delay | Effort | Owner role | Finding IDs | Dedup |
|---|---|---|---|---|---|
| Web E2E (admin/rider/driver Playwright) is `continue-on-error: true` on every PR; native mobile E2E (Maestro) opt-in only | Compounds per-PR merged without a real functional-regression gate; the compensating "blocking on `main`" control gates nothing downstream (no deploy or alert consumes a red `main`) | M (flip 3 jobs to blocking once flake rate is re-verified, or wire a "main is red" consumer) | CI | RR-02, QUAL-004 | = RR-02 |
| "Never swallow errors" recurs as a live class (C135: 7 of 8 unclaim sites still ignore the result) | Compounds per new call site added under time pressure — this is the same class that produced the B42 payment-failure loss once already | M (a static lint rule following the loguru-convention precedent, `test_loguru_call_conventions.py`, would close the class mechanically rather than per-instance) | BE-PLAT | RR-05 | = RR-05 |
| Docs are snapshots, not derived (loop count drifted 5×, "admin JWT fully trusted" false in 3 files, a rider WS event promised but never emitted, "Codex silent" stale) | Compounds per doc read by a human or an AI agent that trusts a number without re-deriving it — this audit itself corrects several of these (see `matrices/docs-coverage.md` §2) | L (a CI check that derives CLAUDE.md's own numeric claims from code, e.g. loop count from `LOOP_CATALOG`, is the only fix that doesn't just drift again) | BE-PLAT (docs) — currently unowned | RR-06, HIST-005 | = RR-06 |
| Duplication-by-default: CarMarker ×5 (+ a 3rd unregistered copy), 2 notification inboxes, 2 auth files, a rider WS client never sending `last_seq` | Compounds per sibling fix that lands in one copy and not the other — `00-history.md`'s own framing: "CLAUDE.md's 'surgical change' rule is producing this" | L (per-fork contract tests + a CI parity check per `08-hostile-review.md` §5 item 16's recommendation) | MOBILE + REPO-ADMIN | RR-09, HIST-002, HIST-015 | = RR-09 |
| Coverage floors sit below CLAUDE.md's literal stated minimums by design ("measured − 5, rounded down") with no ratchet-up mechanism | Compounds slowly — every module whose real coverage improved past its original floor stays floored at the old, lower number indefinitely unless someone manually re-tightens it | S (an informational, non-blocking script that flags `measured > target + 5` — QUAL-005's own recommendation) | CI | RR-132, QUAL-005 | = RR-132 |
| 73-76% of the backend suite carries no `unit`/`integration`/`slow` tier marker; `pytest -m unit` and `-m "not slow"` do not mean what CLAUDE.md's own testing-conventions text says | Compounds per new unmarked test file added — CLAUDE.md's "fast local loop" promise degrades further every sprint | M (a `pytest_collection_modifyitems` hook + a static lint check, per QUAL-006's own recommendation) | CI | RR-133, QUAL-006 | = RR-133 |
| Semgrep SARIF / CodeQL upload steps `continue-on-error: true`, partially fixed 2026-09-08, a deeper repo-setting blocker remains | Compounds per week findings don't reach GitHub's Security tab — a real static-analysis finding can sit unseen | M (needs the repo-setting fix `00-history.md` §3 signal #9 flags as still open) | REPO-ADMIN + CI | none — `00-history.md` §3 signal #9 | **NEW** |
| `render.yaml` (a committed Render blueprint, US region) and the vendor/subprocessor/DPA registers disagree on the primary host (Railway US vs the intended Fly.io Canada primary); Render's live status is unconfirmed | Compounds per day the register could be handed to an auditor or a corporate customer's security questionnaire and be visibly wrong | S once the Render console fact is confirmed (delete stale rows or add the missing Fly.io row); the console check itself needs SRE access this audit doesn't have | PRIVACY + SRE | RR-39, INT-003 (corrected) | = RR-39 |
| Insurance-period v1/v2 writer coexistence (v2 RPC fixes the once-diverging dual-implementation bug, but is flag-gated, so both still run) | Compounds per go-live day of the flag staying off — a regulatory-audit-trail correctness bug with a known fix sitting dark | M (flip the flag after the v2 RPC covers every Period 2/3 opening site, per `08-hostile-review.md` §5 item 12's correction) | BE-DISP + COMPLIANCE | RR-14, HIST-010 | = RR-14 |
| All 44 background loops (45 registry entries with the watchdog) run on every web replica; `SPINR_PROCESS_ROLE` split exists in code but is unset in the real Fly/Railway configs | Compounds per server added for unrelated scaling reasons — each new web replica adds DB/Redis load from loops that shouldn't run there at all | M (config-only split once a canary config is adopted — the code path already exists) | SRE | RR-84, A3-001, REL-003 | = RR-84 |
| Loop-watchdog staleness thresholds wrong in both directions for ~30 of 44 loops; the annual T4A job can never be flagged stale at all | Compounds silently — a hung safety/dispatch loop is invisible for up to 2h; a healthy long-interval loop cries wolf most of every day, training the team to ignore the channel | M (a test tying every `LOOP_CATALOG` entry to a threshold, plus per-loop threshold fixes — REL-001's own recommendation) | BE-PLAT + SRE | RR-85, REL-001 | = RR-85 |
| Monitoring/alerting workflow cluster runs on unset or placeholder data: `ALERT_WEBHOOK_URL` (loop/capacity alerts), `secret-rotation-monitor.yml` and `renewal-calendar-monitor.yml` (both on TBD placeholder dates), `supabase-capacity-monitor.yml` (no-ops without `SUPABASE_ACCESS_TOKEN`/`SUPABASE_PROJECT_REF`), `billing-usage-monitor.yml` (Stripe/Twilio keys possibly unset) | Compounds per day this cluster looks "wired" (green scheduled runs) while actually checking nothing — the single most misleading category of decay signal in `00-history.md` §3, since a green scheduled workflow reads as "monitored" to anyone who doesn't open it | M (fill the real values once, per-vendor — mostly a data-entry task, not an engineering one; each is independently small) | SRE + FOUNDER | RR-86, RR-90, `00-history.md` §3 signals #16, #18-21 | = RR-86 (loop-alert half); signals #18-21 (placeholder-data half) are **NEW**, not individually in risk-register |
| Synthetic (outside-in) monitoring is a spec-only YAML file with nothing behind it (`monitoring/synthetic-checks.yaml` states plainly: "nothing in this repo talks to Checkly / UptimeRobot / Grafana Synthetic Monitoring / PagerDuty") | Compounds per outage that would have been caught from outside — the 2026-09-02 cert outage on `api-spinr.spinr.ca` had, per its own incident commit body, "nothing watching it" | M (provision one vendor and wire the existing spec) | SRE | RR-67 | = RR-67 (folded — same underlying "no live alert" gap) |
| Grafana/metrics-aggregation stack (ADR-010, `metrics-agent/` config) merged but never deployed | Compounds per day the 3 CLAUDE.md-published SLA metrics with no emitter, and the 8 KPI targets with no live measurement, stay unmeasured | L (needs a vendor-account decision, C11/CR-2026-008) | SRE | RR-67 | = RR-67 |
| 11 workflows are `workflow_dispatch`-only (manual), including `apply-supabase-schema.yml` and `update-visual-baselines.yml` — no agent session can run them | Compounds per fix that needs one of these and stalls on human availability instead | S to document (name the 11, per `00-history.md` §3 signal #30); the underlying fix (Actions-dispatch access for agent sessions) is a policy decision, not engineering effort | REPO-ADMIN | RR-08 (schema-apply half) | Schema-apply half = RR-08; the general "11 manual-only workflows" framing is **NEW** |

## §3 Tier 3 — cost of delay accumulates slowly or is bounded/one-time

| Item | Cost of delay | Effort | Owner role | Finding IDs | Dedup |
|---|---|---|---|---|---|
| Production schema applied by hand; 116 untracked files, 8+ pending, no named migration owner | Compounds per migration merged without a live-state reconciliation — schema and repo drift further apart each time | L (name an owner first — S; automate apply after CI evidence — L) | DB-OWNER (unassigned) | RR-08 | = RR-08 |
| `ACTION_ITEMS.md` has 9 duplicated numeric ids, breaking its own "grep before filing" de-dup convention | Compounds per new item filed against a colliding id — the exact failure mode this audit's own §0 method notes had to work around | S (renumber the 9, or move to a generator that can't collide) | REPO-ADMIN | RR-07, HIST-007 | = RR-07 |
| Non-atomic check-then-act on shared state; 7 fixes so far, no shared compare-and-swap helper | Compounds per new "read, decide, update" code path written under time pressure — the same shape keeps recurring because there's no idiomatic alternative to reach for | M (a shared CAS helper in `repositories/_base.py`, already proposed by `08-hostile-review.md`'s card-18 discussion) | BE-PLAT | RR-11, HIST-011 | = RR-11 |
| ~290 ERROR logs in dispatch/driver code reach Sentry with no domain or ride/driver tag | Compounds per untagged error logged — makes Sentry triage slower every incident, not just this one | M (a static test following the loguru-convention precedent) | BE-PLAT | RR-12 | = RR-12 |
| Four OTP systems hardened separately (lockout logic copied 3×, one plaintext by design, dev-bypass forked across 2 files) | Bounded — the systems exist and work; the cost is future-fix drag (a lockout fix applied to one copy and not the others) | M (consolidate to one shared OTP module) | BE-AUTH | RR-16, HIST-017 | = RR-16 |
| Timezone/DST handled per call site; scheduled-ride time is an optional field, rider picker uses device zone | Bounded, low-frequency (DST transitions ×2/year, per `00-history.md`'s "DST ×5" recurrence count) but each recurrence is a real scheduled-ride bug | M (a single service-area-timezone source of truth) | BE-DISP + MOBILE | RR-15, HIST-016 | = RR-15 |
| `CloudinaryService` — a wired, pinned dependency with zero call sites and no vendor record | Effectively zero ongoing cost; a one-time cleanup or documentation task | S | BE-PLAT | RR-102 | = RR-102 |
| `platform_fee_percent` — an admin-writable percentage-commission field with no reader, one accidental wiring away from contradicting the 0%-commission model | Low today; the cost of delay is entirely in the "one line from a regression" risk, not accumulation | S (remove the field or the writer, per `08-hostile-review.md` §6's endorsement of card 7's removal) | BE-PAY | RR-62, STRAT-005 | = RR-62 |
| Mobile lint debt accumulated under the SDK 57 upgrade (round 1 dated 2026-08-12, per `ACTION_ITEMS.md`) | Bounded, one-time — a version-upgrade side effect, not a recurring pattern | M | MOBILE | none dedicated in risk-register | **NEW** — sourced from `ACTION_ITEMS.md:15624` |
| LogRocket major-version split across rider-app (`@logrocket/react-native`) tracked as its own open cleanup item | Bounded, one-time dependency-alignment task | S | MOBILE | RR-19 (shares the LogRocket surface, different concern — version alignment, not the masking/disclosure gap) | Adjacent to RR-19 but not the same defect — **NEW** |
| Settings-read-failure metric-name convergence (ADR-011 decision item #2 specified one naming scheme; a later PR used a different one) — R1-R6 cluster in `ACTION_ITEMS.md:17870-17904` | Bounded — a naming-consistency debt, not a live defect; delay cost is "a dashboard query written against the wrong name silently returns nothing" | M (the R1-R6 items themselves: settle the `grand_total = 0` ambiguity, extract one shared kill-switch read helper, converge metric names, sweep `.bind(domain=...)`, add alerts for 4 new counters, update a stale runbook line) | BE-PLAT + OBS | RR-06 (doc-drift family, loosely) | **NEW** — the specific R1-R6 cluster is not individually in risk-register |
| No light-on-dark variant of the Spinr logo (`N18` in `ACTION_ITEMS.md`) | Bounded, cosmetic — no functional or safety cost | S | MOBILE (brand) | none | **NEW** — pure housekeeping, included for completeness since it is a real open item |
| Dead pre-retrofit receipt/invoice shell and its flag (`N11c` in `ACTION_ITEMS.md`) | Bounded — dead code sitting behind an off flag; cost of delay is only reviewer confusion ("is this still used?") each time someone reads near it | S (delete, per CLAUDE.md's "notice unrelated dead code → mention it, don't delete it (unless asked)" — this item is the "asked" case, since it's already flagged for removal) | BE-PLAT or MOBILE (surface not confirmed this pass) | none | **NEW** |
| `.kilo/` directory — dormant AI-tool config, flagged "archive candidate" by two prior audits, label only corrected 2026-09-14 | Bounded — zero functional cost; the debt is purely "a third prior audit will re-flag this unless someone deletes it or commits to using it" | S | REPO-ADMIN | none | **NEW** — sourced from `00-history.md` §3 signal #34 / CLAUDE.md's own Claude-Adjacent Directories table |
| Nightly duplicate-migration-prefix sweep was red for 6 consecutive nights before anyone acted (resolved 2026-09-08) | Resolved, zero ongoing cost — included as a **pattern** worth registering: a scheduled check with no escalation path can stay red for a week unnoticed | S (an escalation rule — page or issue-file after N consecutive red nights — would close the *pattern*, not just this instance) | CI | none | **NEW** — the instance is closed; the "no escalation path for a red nightly" pattern itself is not captured elsewhere |

## §4 New items — not previously represented as their own risk-register.md row

Listed together so the count is checkable without re-reading §1-§3 line by line. 8 new items,
sourced from `00-history.md` §3 (5 items) and `ACTION_ITEMS.md` directly (3 items):

1. Semgrep SARIF/CodeQL upload steps advisory + a deeper repo-setting blocker (`00-history.md` §3 signal #9)
2. Placeholder-data monitoring cluster (`secret-rotation-monitor.yml`, `renewal-calendar-monitor.yml`, `supabase-capacity-monitor.yml`, `billing-usage-monitor.yml`) — signals #18-21
3. "11 manual-only workflows, no agent-dispatch access" as its own operational-debt framing (signal #30) — distinct from the schema-apply instance already in RR-08
4. Mobile lint debt from the SDK 57 upgrade (`ACTION_ITEMS.md:15624`)
5. LogRocket major-version split cleanup item, version-alignment not disclosure (`ACTION_ITEMS.md:16364`)
6. Settings-read-failure metric-name convergence, the R1-R6 cluster (`ACTION_ITEMS.md:17870-17904`)
7. No light-on-dark logo variant (`ACTION_ITEMS.md` N18)
8. Dead pre-retrofit receipt/invoice shell + flag (`ACTION_ITEMS.md` N11c)
9. `.kilo/` dormant directory, archive-or-commit decision never made (`00-history.md` §3 signal #34)
10. "No escalation path for a red scheduled check" as a named pattern (generalized from the 6-night duplicate-prefix sweep instance, signal #28, itself resolved)

(10 listed; §1-§3 introduce them at points 8-10 slightly reordered by tier — count is 10, not 8,
corrected here after drafting §1-§3 in tier order rather than source order.)

## §5 What could not be checked this pass

- **A semantic read of `ACTION_ITEMS.md`'s remaining ~90 open top-level items and hundreds of
  nested sub-items** — only a keyword sample was reviewed (§0); some fraction of the 102 open
  checkboxes likely duplicate a risk-register row not caught by this pass's spot-check.
- **Whether any of the 38 `00-history.md` §3 decay signals not listed in §4 as NEW are genuinely
  already folded into a risk-register row versus simply omitted** — this file traced the ones with
  a clear risk-register citation in the signal's own "Existing id" column and treated everything
  else as NEW; a signal whose "Existing id" column cites an `ACTION_ITEMS.md` id not itself mapped
  into `matrices/risk-register.md` §11's index could be a silent third case (neither RR-row nor
  flagged NEW here) — not exhaustively cross-checked against all 142 risk-register rows one by one.
- **Live production values for every flag/config named in §1-§3** (`ALERT_WEBHOOK_URL`,
  `SPINR_PROCESS_ROLE`, `pst_enabled`, the placeholder-data monitors' real vendor tokens) — no
  database or vendor-console session was available this pass.
- **Actual effort estimates were not validated against a real implementation attempt** for any row
  — the S/M/L sizes are read from the originating finding cards' own "Rollout"/"Blast radius"
  language or estimated from the fix description, not independently re-derived by attempting the
  fix.
