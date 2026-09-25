# Risk register — every finding, de-duplicated and scored

**Lane:** R20 Synthesizer · **Wave:** W5 · **Date:** 2026-09-25 · **Mode:** report-only (this file changes no code, config or data).

## §0 How to read this register

- **One row per risk, not per finding.** Findings that describe the same defect in different lanes are merged into one row. The "Merged IDs" column lists every finding id folded into that row. The first id listed is the canonical one (strongest evidence). §9 is an index from every finding id to its row, so nothing is dropped.
- **Sources de-duplicated:** the 15 lane reports in `02-findings/`, `03-benchmark.md` (BENCH-001..010), `00-history.md` (HIST-001..017, decay signals §3), `01-inventory/epics.md` (CARTO-001..005), and the rapid baseline `rapid-baseline-2026-09-24/A2`–`A8` (SEC-A2-*, A3-*, rapid DISPATCH-*, MONEY-001..005, OBS-*, CONCURRENCY-001, RELIABILITY-002, A11Y-003, INFO-*, SAFETY-*).
- **Score = S × B × L** (severity × blast radius × likelihood, 1–5 each, max 125; `ground-rules.md` §6). Where a lane gave numbers, the lane's numbers are kept unchanged. Where a lane gave only words, R20 assigned numbers using CRITICAL 5 / HIGH 4 / MEDIUM 3 / LOW 2 / RECOMMENDATION 1; those scores carry a **†**. Where two lanes scored the same risk differently, the row keeps the score backed by the stronger evidence (a direct code read beats a cross-reference) and the note says so.
- **Evidence label** is the one the source card carried, after the corrections in `07-reverification.md` §4. Following that file's guidance, an absence claim that drives a HIGH score and was not re-grepped by a second agent is treated as INFERRED, but the finding itself is not downgraded.
- **Refuted core claims so far (4), all the same kind — an absence or "everywhere" claim broader than the search behind it:** QUAL-003 (a test exists), TSF-010/BENCH-001 (the pickup code is a trip PIN), a traceability-matrix row calling driver navigation weak (it is built: `driver-app/lib/navigation/launchNavigation.ts`, used at `ActiveRidePanel.tsx:376,521`, with tests), and the blueprint's claim that every money writer passes through `repositories/_base.py`. Partial errors add INT-003, SKB-007 and COMP-018 (`07-reverification.md`). Treat any remaining unverified absence claim in this register as INFERRED.
- **Status:** `Open` · `Open–stuck` (the reason is in the note) · `In progress` · `Built, dark` (the fix exists behind a flag that is off by schema default; the live value is UNKNOWN unless stated) · `Decision needed` (see `05-escalations.md`) · `Owner-managed` · `Accepted` (a recorded decision to live with it) · `Closed` · `Pass` · `Withdrawn`.
- **Owner role** is the role that should own the fix. It is *not* a claim that someone holds that role today. `matrices/ownership.md` shows who actually owns each surface; the honest answer is usually "unowned".
- **Roadmap** points at `ROADMAP.md` item ids (N = Now, X = Next, L = Later, E = decision in `05-escalations.md`).

**Owner-role legend:** FOUNDER (founder/product owner) · REPO-ADMIN (GitHub org/repo admin) · CI (CI/CD owner) · SRE (infra: Fly, Railway, Cloudflare, Redis, alerting) · DB-OWNER (migration owner, currently unassigned) · BE-PAY (backend payments, ledger, corporate billing) · BE-DISP (backend dispatch, realtime, insurance periods) · BE-AUTH (backend auth and security) · BE-PLAT (backend platform: repositories, logging, loops) · MOBILE (rider-app and driver-app) · ADMIN (admin dashboard and admin routes) · T&S (trust, safety and fraud operations) · SUPPORT (support lead) · PRIVACY (privacy officer) · COUNSEL · ACCOUNTANT · FINANCE · COMPLIANCE.

**Totals:** 142 rows, de-duplicated from 198 distinct finding-card ids (plus the decay-signal and ACTION_ITEMS ids they cite). Row severity: 6 CRITICAL, 41 HIGH, 5 MEDIUM-HIGH, 55 MEDIUM, 5 LOW-MEDIUM, 19 LOW, 4 RECOMMENDATION, 4 PASS/keep, 1 UNKNOWN, 2 WITHDRAWN (QUAL-003; TSF-010/BENCH-001). §10 lists the highest-priority rows; §11 is the id index.

## §1 Engineering system, gates and recurrence families

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-01 | The merge gate is advisory in practice: `main`'s required-checks list has never been read by anyone with access, and PRs have merged before checks finished (the 47-second merge that shipped B42, 53 of 55 lost `payment_failed` events). Every other gate inherits this. | HIST-014, HIST-013, decay §3 #1–#2, C21, C73, A43, C13 | HIGH | 4×5×4 = 80 | VERIFIED (merge timing) / UNKNOWN (branch-protection contents) | REPO-ADMIN | Open–stuck 44 days: needs GitHub Settings access no session has | N4 |
| RR-02 | Web E2E tests (admin, rider-web, driver-web) cannot fail a PR, and native mobile E2E (Maestro) never runs, so a broken booking or checkout flow is not caught before release. | QUAL-004, decay §3 #6, #10, B25, C76 | HIGH | 4×5×4 = 80 | VERIFIED | CI | Open | N4, X18 |
| RR-03 | The two required "summary" CI jobs report red on superseded runs, teaching the team to ignore red checks. | QUAL-001 | MEDIUM | 3×4×4 = 48 | VERIFIED | CI | Open | N4 |
| RR-04 | Automated PR review is partial and mis-documented: Claude review is off by a cost decision; Codex resumed on 2026-09-16 after 48 silent days, but CLAUDE.md and C9 still say it is silent. (Re-verified 2026-09-25 by GitHub search: Codex commented on 8 PRs created since 2026-08-01, the first #5480 on 2026-09-16; #5748 carried a full review with 8 findings acted on, per `docs/change-log/2026-09-24-pr5748-codex-review-fixes.md`. Whether each of the 8 was a full review was not checked.) | decay §3 #3–#4, C7, C9, rapid-baseline finding 2 (corrected by W0) | MEDIUM | 3×4×3 = 36 † | VERIFIED | REPO-ADMIN + FOUNDER (cost) | Decision needed | E-F10, N16 |
| RR-05 | "Never swallow errors" is prose only. The same warn-and-continue anti-pattern has been fixed at least five times and is open again (C135: 7 of 8 unclaim sites ignore the result). | HIST-004, C135, B42 (closed), OBS-005 | CRITICAL (family) | 5×5×4 = 100 | VERIFIED | BE-PLAT | Open (C135 partially mitigated 2026-09-23) | X20 |
| RR-06 | Docs are snapshots, not derived. Loop count drifted five times; the "admin JWT fully trusted" sentence is false in three files; the pre-commit money hook is described as blocking but only warns; `domain-dispatch.md` documents a rider event nothing emits; two admin docstrings contradict migrations. Agents read these docs first. | HIST-005, SEC-A2-008, MONEY-004, drift §4 rows 1–14, DRIFT-003, the stale `acceptance_rate` docstring (07-reverification DRIVER-004 note), fraud-auditor velocity-cap row | MEDIUM (aggregate HIGH) | 3×5×5 = 75 | VERIFIED | BE-PLAT (docs) — unowned | Open | N16, X20 |
| RR-07 | The backlog tracker has nine duplicated item ids and stale statuses, which breaks the "grep before filing" de-duplication rule. | HIST-007 | MEDIUM | 2×5×5 = 50 | VERIFIED | REPO-ADMIN | Open | X20 |
| RR-08 | Production schema is applied by hand: 116 files were never recorded as applied, 8+ are pending (C125, plus 450), and live state has drifted from the repo in both directions (RLS was enabled live on `settings` without the repo recording it). No named migration owner. | HIST-006, CARTO-005, SEC-R10-002, C125, G2, C44 | HIGH | 4×5×4 = 80 | VERIFIED / VERIFIED-LIVE (SEC-R10-002) | DB-OWNER (unassigned) | Open–stuck: no `DATABASE_URL` in any session; no owner | X9, E-F9 |
| RR-09 | Duplication by default: fixes land in one copy and not its sibling (CarMarker ×5 plus an unregistered third copy on the admin track page; notification inbox ×2; referral; auth files; rider WS client never sends `last_seq`; a dead second API client). The parity guard only diffs props, and the pre-commit check only warns. `shared/` has no logic owner. | HIST-002, HIST-015, CARTO-002, INFO-004, RIDERJ-006 (b), RIDERJ-007, RELIABILITY-002 | HIGH | 4×4×4 = 64 | VERIFIED | MOBILE + REPO-ADMIN | Open | X16 |
| RR-10 | PII reaches each new outbound channel before a scrubber does (logs, FCM, AI, Zoho, exception text, git). Redaction is per sink; three copies of the FCM exclusion set exist. | HIST-012 | HIGH | 4×4×4 = 64 | VERIFIED | BE-PLAT + PRIVACY | Open | X15 |
| RR-11 | Check-then-act on shared state is the natural code shape (read, decide, update); seven fixes so far, no shared compare-and-swap helper. | HIST-011 (specific instances: RR-96, RR-113, RR-50) | HIGH | 4×4×3 = 48 | VERIFIED | BE-PLAT | Open | X20 |
| RR-12 | About 290 ERROR logs in dispatch and driver code reach Sentry with no domain or ride/driver tag, and no CI check stops new ones. (The sibling loguru-convention family is closing thanks to a static test — the template to reuse.) | OBS-001, OBS-006, HIST-008 (closing), ACTION_ITEMS R4 | HIGH (lane: CRITICAL) | 4×4×4 = 64 † | VERIFIED | BE-PLAT | Open | N18 |
| RR-13 | Row-Level Security is dormant by design (no real Supabase user sessions exist), so a wrong policy produces no error. | HIST-009, C108 | HIGH (dormant) | 4×5×2 = 40 | VERIFIED | BE-AUTH + DB-OWNER | Accepted (C108); revisit if Supabase user JWTs are ever issued | X9 (apply 450) |
| RR-14 | Insurance-period transitions were written independently at each state change and two implementations disagreed; the v2 claim RPC fixes this but is flag-gated, so v1 and v2 writers coexist. | HIST-010 | CRITICAL | 5×4×3 = 60 | VERIFIED | BE-DISP + COMPLIANCE | In progress (v2 dark) | X13 |
| RR-15 | Timezone is handled per call site; scheduled-ride time is an optional request field and the rider picker uses the device zone. | HIST-016, RIDERJ-005 | MEDIUM | 3×3×3 = 27 | VERIFIED | BE-DISP + MOBILE | Open | L15 |
| RR-16 | Four short-code (OTP) paths with lockout logic copied three times, one plaintext by design, dev-bypass logic forked between two files. | HIST-017, SEC-R10-016, SEC-A2-007 | MEDIUM | 3×3×3 = 27 | VERIFIED | BE-AUTH | Open | X8 |
| RR-17 | "Legacy import" is a JSON marker filtered by hand at 59 call sites; it once showed real drivers $0.00. Still live: a driver's lifetime total and trip list use different inclusion rules. | HIST-003, DRIVER-002, RECURRENCE-003 | CRITICAL (historic) / MEDIUM (open part) | 5×4×3 = 60 | VERIFIED | BE-PAY | Partly closed; DRIVER-002 open | X3, L14 |

## §2 Security, privacy and data egress

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-18 | The database master (service-role) key that sat in public git history for ~3.5 months cannot be shown to have been rotated — two repo documents disagree — and nothing could show whether it was used. | SEC-R10-001, drift §4 row 16, decay §3 #8, threat-model rank 1 | CRITICAL | 5×5×3 = 75 | VERIFIED (contradiction) / UNKNOWN (rotation fact) | FOUNDER + SRE | Decision needed (today) | N1, E-S1 |
| RR-19 | LogRocket session replay is on by default in production iOS builds of both apps with no masking, records OTP, address and map screens, and is not in the DPA register. | SEC-R10-003, threat-model N-12 / DI-4 | HIGH | 4×4×4 = 64 | VERIFIED (code) / INFERRED (vendor capture defaults) | MOBILE + PRIVACY | Open | N2, E-S2 |
| RR-20 | Driver push payloads carry precise pickup/drop-off coordinates and the rider's rating: the admin-assign push is unfiltered, and the fix for normal dispatch pushes exists but ships dark. The promised tracking item for the admin path was never filed. | SKB-001, SKB-002 | HIGH | 4×2×4 = 32 | VERIFIED (code) / UNKNOWN (live flag value) | BE-DISP + PRIVACY | Built, dark (dispatch) / Open (admin path) | N13 |
| RR-21 | One shared HS256 secret signs and verifies every token class, so anyone holding it can mint an admin token. | SEC-A2-001, threat-model S-4 | HIGH | 4×5×2 = 40 | VERIFIED | BE-AUTH | Open | X8 |
| RR-22 | The OTP pepper falls back to the JWT secret; whether it is set in production is unknown. | SEC-R10-005 | MEDIUM | 3×3×2 = 18 | VERIFIED (code) / UNKNOWN (env) | SRE | Open | N6 |
| RR-23 | Driver SIN and emergency-contact encryption depend on `pgsodium`, which Supabase marks as pending deprecation; the key-rotation runbook has never been executed. | SEC-A2-002, SEC-R10-010 | HIGH | 4×4×2 = 32 | VERIFIED-LIVE (extension versions) / INFERRED (vendor timeline) | BE-AUTH + DB-OWNER | Open | X8, L4 |
| RR-24 | Admin single-session logout depends on a Redis denylist that fails open. | SEC-R10-007, SEC-A2-003, threat-model N-10 | MEDIUM | 3×3×2 = 18 | VERIFIED | BE-AUTH | Open | X8 |
| RR-25 | The refresh-token reuse grace window lets an attacker who replays a stolen token first stay signed in; the victim's retry is treated as the benign one. | SEC-R10-004, threat-model S-5 | MEDIUM | 3×4×2 = 24 | VERIFIED (code) / INFERRED (client behaviour) | BE-AUTH | Open | X8 |
| RR-26 | Three privileged paths sit outside MFA and outside staff records: the env super-admin, the break-glass token, and app-review accounts with a static OTP that never expires. | SEC-R10-006, threat-model N-1 / N-2 / AR-2, C4 | MEDIUM | 4×4×2 = 32 | VERIFIED | BE-AUTH + FOUNDER | Open | X8 |
| RR-27 | Per-IP OTP throttles trust a spoofable client-IP header because the Cloudflare-only origin lock is not enforced; the real bound is 5 guesses per phone per day on a 4-digit code, and the same lockout can deny service to a real user for 24 h. | SEC-R10-009, C131, threat-model N-8 / N-9 / S-1 | MEDIUM | 3×4×2 = 24 | VERIFIED (code) / INFERRED (exploit rate) | SRE + BE-AUTH | Open–stuck: needs an infra (Fly/Cloudflare) change nobody owns | X8 |
| RR-28 | 55 of 102 id-scoped routes check ownership by "fetch then compare" with no lint or test that the compare exists (no IDOR found today). | SEC-R10-011 | RECOMMENDATION | 2×4×2 = 16 | VERIFIED | BE-AUTH | Open | X20 |
| RR-29 | Dev-agent control plane: `git push origin main` is pre-approved for agents; four auditor agents have Bash; the account-level Supabase connector holds production write (time-boxed to 2026-10-31); Vercel scope drifted back to three projects; the agent action log has no production caller. The deny list does block `rm -rf`, `git reset --hard`, key/pem writes and `--force`. | SEC-R10-012, threat-model A-9 | MEDIUM | 4×4×2 = 32 | VERIFIED (repo) / live row count not re-verified | REPO-ADMIN | Open | N5 |
| RR-30 | All four threat models are v1.0 from 2026-04-24; at least nine rows are wrong in one direction or the other. | SEC-R10-013, SEC-A2-004 (RS-4 contradiction) | LOW | 2×3×3 = 18 | VERIFIED | BE-AUTH (security) | Open | N16 |
| RR-31 | `docs/data-classification.md` is wrong about what is encrypted, about retention, and omits five sub-processors; CLAUDE.md claims a driver home-address column that does not exist. | SEC-R10-014 | LOW (MEDIUM if a DSAR or OPC inquiry relies on it) | 3×3×2 = 18 | VERIFIED | PRIVACY | Open | N16 |
| RR-32 | App attestation is unconfirmed in production and exempts 15 path prefixes (including both OTP endpoints); GPS anti-spoofing relies on a client-reported flag. Threat models over-credit it. | SEC-A2-005, SEC-R10-015, C3, threat-model S-3 / DS-3 / RS-2, T-4 / DT-1 | MEDIUM | 3×4×2 = 24 | INFERRED / UNKNOWN (console state) | SRE (Firebase console) | Open–stuck: console-only toggle, never recorded | N6, L2 |
| RR-33 | No SBOM, no build-provenance attestation, a CI hash check that silently falls back, and two divergent Dockerfiles. | SEC-R10-017, threat-model SC-5 / SC-2 | LOW | 2×4×2 = 16 | VERIFIED | CI | Open | L6 |
| RR-34 | Mobile hardening: no screen-capture prevention on OTP/payment/earnings, no root/jailbreak signal, and the no-pinning choice has no ADR. | SEC-R10-018, SEC-A2-004 | LOW | 2×3×2 = 12 | VERIFIED (absence) | MOBILE + BE-AUTH | Open | X8 (ADR), L5 |
| RR-35 | Request-ID middleware trusts an unverified JWT for `user_id` log correlation. | SEC-A2-006 | LOW | 2×2×2 = 8 | VERIFIED | BE-PLAT | Open | L-backlog |
| RR-36 | Migration 450 (revoke client EXECUTE on 16 SECURITY DEFINER functions) is not live; 16 functions have a mutable `search_path`; `pgaudit` off; `http` extension on. Dormant because no client JWTs exist. | SEC-R10-008 | MEDIUM | 3×4×1 = 12 | VERIFIED-LIVE 2026-09-24 | DB-OWNER | Open | X9 |
| RR-37 | The custom gitleaks rule for driver-export PII spans line breaks and matches non-email strings; three confirmed false positives, each costing a manual investigation. | QUAL-002 | LOW-MEDIUM | 2×3×5 = 30 | VERIFIED | CI | Open | X18 |
| RR-38 | Meta ad SDK plus server-side per-ride `Purchase` conversions send ride value, promo code and a hashed identity to Meta — against the "no ad SDKs / no behavioural retargeting" guardrail and undisclosed in the privacy policy and sub-processor list. | STRAT-004 | HIGH (benchmark lane rates it CRITICAL as a guardrail breach) | 4×5×5 = 100 | VERIFIED (code) / UNKNOWN (whether the token is set live) | FOUNDER + PRIVACY + COUNSEL | Decision needed | N3, E-S3 |
| RR-39 | Vendor register says the primary host is Railway (US) with a Render (US) fallback and omits Fly.io (Canada, the intended primary). A Render blueprint (`render.yaml`, US region) is committed at the repo root; whether a Render service is live is unverified. The DPA register also lacks a Fly.io row. | INT-003 (corrected per 07-reverification §4.1) | HIGH | 4×3×3 = 36 | VERIFIED (Fly/Railway rows) / INFERRED (Render live status) | PRIVACY + SRE | Open; data-residency question escalated | N16, E-S5 |
| RR-40 | The e-mail fallback vendor (Resend) has no documented data-residency region, while the primary (SES) is pinned to `ca-central-1`. | INT-007 | MEDIUM | 3×2×3 = 18 | VERIFIED (SES pin) / UNKNOWN (Resend region) | PRIVACY | Decision needed | N16, E-S5 |
| RR-41 | WebSocket authentication path. | SEC-R10-019 | PASS | — | VERIFIED | — | Pass | — |

## §3 Money, tax and corporate billing

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-42 | Float arithmetic on money is still recurring outside the lint gate: the canonical fare-estimate total (persisted for corporate guest rides), two driver tip totals, and referral bonuses. The pre-commit hook only warns; the CI rule has 7 exclusions and cannot see column types. **A repository-level guard alone would not see the writers that matter:** `rides` is written directly at `repositories/ride_repo.py:260,276` and `payouts` at `services/stripe_payout_sync_service.py:457`, and a single-line grep finds ~71 direct table writes and ~90 RPC calls outside `repositories/_base.py` (08-hostile-review §1.7). | HIST-001, MONEY-006, MONEY-001, MONEY-008, MONEY-005 | HIGH | 4×4×3 = 48 | VERIFIED | BE-PAY | Open (class not closed; 5 new instances this wave) | N19, X5 |
| RR-43 | Completion-time re-pricing changes the fare but keeps the booking-time GST/PST, so a receipt's tax line can stop matching its subtotal. Dormant if `fare_lock_enabled` is still on in production (ADR-016 records it on as of 2026-09-11). | MONEY-007 | HIGH | 4×3×4 = 48 | VERIFIED (code) / UNKNOWN (live flag) | BE-PAY | Open | X5 |
| RR-44 | A chargeback on a wallet or corporate top-up leaves the credited balance spendable and unrecorded (only an admin toast and a warning log). | MONEY-009, OBS-005 | HIGH | 4×3×3 = 36 | VERIFIED (path) / INFERRED (exploitability) | BE-PAY | Open | X5 |
| RR-45 | "Payout ≤ collected" is deliberately not an invariant (drivers are paid for uncollected rides), but its cost is unmeasured and has no per-driver cap. | MONEY-010 | MEDIUM | 3×4×3 = 36 | VERIFIED | FINANCE + BE-PAY | Open | X5 |
| RR-46 | Receipts and corporate statements carry no GST/HST registration number; corporate customers may be unable to claim input-tax credits. Whose number belongs there depends on the supplier-of-record answer. | MONEY-011, CORP-005, COMP-015 (receipt part) | MEDIUM | 3×3×3 = 27 | VERIFIED (absence) / ASSUMED (requirement) | ACCOUNTANT → BE-PAY | Open–stuck on the tax answer | N9, X6 |
| RR-47 | T4A slip fills Box 020 and Box 048 with the same amount for GST registrants. | MONEY-012 | MEDIUM | 3×3×3 = 27 | VERIFIED (code) / ASSUMED (box semantics) | BE-PAY + ACCOUNTANT | Open; must be fixed before 2027-02-28 run | X6 |
| RR-48 | Daily reconciliations compare mismatched populations (time windows, top-ups, amounts), so the alert is either always red or never trusted. | MONEY-013 | MEDIUM | 3×3×4 = 36 | INFERRED | BE-PAY | Open | X5 |
| RR-49 | The receipt PDF fallback bundles GST and PST into one "Tax" line. | MONEY-002, COMP-006 | MEDIUM | 3×3×2 = 18 | VERIFIED | BE-PAY | Open | X5 |
| RR-50 | Corporate wind-down: the ledger debit has no idempotency key and the close guard has a check-then-act race, so a double close can debit a terminal account twice. A key built from the wallet id alone is too coarse if a partial refund loop is re-run with a different refund set; derive it from the sorted Stripe refund ids and make the close conditional on current status (08-hostile-review §1.4). | CORP-001 | HIGH | 4×2×2 = 16 † | VERIFIED | BE-PAY | Open | N20 |
| RR-51 | The corporate allowance cap guard was silently dropped by a later migration once (fixed); nothing diffs a new function body against the last applied one, so it can recur. The static contract test pins migration 297, not the latest body (319). Regression test path: `backend/tests/rls/money/test_money_rpc_races.py`. | CORP-002 | RECOMMENDATION (safe today; was CRITICAL while live) | 1×3×2 = 6 † | VERIFIED | DB-OWNER + BE-PAY | Closed (instance) / Open (systemic check) | X9 |
| RR-52 | `update_corporate_wallet_config` is a generic column patch; only the caller's schema prevents a direct balance write. | CORP-003 | LOW | 2×4×1 = 8 † | VERIFIED | BE-PAY | Open | N20 |
| RR-53 | Money-moving admin actions (wallet credit/debit up to $10,000 each, dispute refunds up to the full fare) need no second approver and have no cumulative cap; the `support` and `finance` presets can reach them (a 10-per-minute rate limit exists). With two admins, requiring a second approver on *every* amount would block refunds whenever one is away; the threshold must sit above routine support refunds, with an interim per-admin daily cap and alert first (08-hostile-review §1.8). | ADMIN-OPS-001 | HIGH | 3×3×2 = 18 | VERIFIED | FOUNDER (threshold) → ADMIN | Decision needed | X7, E-F7 |
| RR-54 | Driver payouts are set to manual on Stripe and only one payout has ever run; the decision was never filed. | STRAT-012 | MEDIUM | 3×2×5 = 30 | VERIFIED (in doc) | FINANCE + FOUNDER | Decision needed | N15, E-F5 |
| RR-55 | Rider and corporate wallets are prepaid balances the platform holds ("zero float" is false) and no report totals them. | STRAT-011 | LOW / MEDIUM (finance treatment) | 2×3×4 = 24 | VERIFIED (mechanism) / ASSUMED (accounting treatment) | FINANCE + ACCOUNTANT | Open | X19, E-T9 |
| RR-56 | SK PST on ride-share fares has no primary-source citation after four verbal determinations; production charges GST only. If PST applies, under-collection compounds with every ride. | COMP-003, MONEY-003, G9 | HIGH | 4×5×3 = 60 | ASSUMED (config VERIFIED) | ACCOUNTANT | Open–stuck 34 days (government sites blocked in every session) | N9, E-T1 |
| RR-57 | The platform's own tax posture is undetermined (Part XX.1 reporting, supplier of record, whose GST number, T4A vs platform statement) while the code hard-blocks every driver payout on the driver holding a GST number. | COMP-015, rapid CRA table rows 1–5 | HIGH | 4×4×3 = 48 | ASSUMED | ACCOUNTANT | Decision needed | N9, E-T2–E-T6 |

## §4 Business model, pricing and KPIs

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-58 | Per-ride contribution margin is negative today (booking fee is $0, so each ride costs Spinr processing and API fees) and no report measures it. | STRAT-002 | HIGH | 4×5×5 = 100 | INFERRED (economics) / VERIFIED (no fee, no margin metric) | FOUNDER + FINANCE | Decision needed | N14, X19, E-F1 |
| RR-59 | The revenue model in the ToS, PRD and CLAUDE.md (premium rider features, partner referrals) is not the one in the code (driver pass, booking fee, corporate SaaS). | STRAT-001 | HIGH | 4×4×5 = 80 | VERIFIED | FOUNDER + COUNSEL | Decision needed | X19, E-F1 |
| RR-60 | Live pricing config has no minimum fare, no per-minute rate and identical rates across vehicle classes; a drafted fix waits on a pricing decision. | STRAT-008 | MEDIUM (HIGH for driver retention) | 3×4×4 = 48 | VERIFIED (as recorded from a DB read) / INFERRED (comparison) | FOUNDER | Open–stuck on a decision | N14, E-F2 |
| RR-61 | "100% of your fare goes to your driver" is true only because the booking fee is off; turning the fee on without changing five copy sites would make the claim misleading. | STRAT-003 | MEDIUM | 3×3×4 = 36 | VERIFIED | FOUNDER + MOBILE | Open (must precede any booking fee) | X19 |
| RR-62 | `platform_fee_percent` is an admin-writable percentage-commission setting with no reader — one line from contradicting the 0% model. | STRAT-005 | LOW (latent MEDIUM) | 2×3×3 = 18 | VERIFIED | BE-PAY | Open | X19 |
| RR-63 | The Maps daily budget breaker is a $5/day cliff with no early warning, not sized to launch volume, and returns 503 on the booking path when tripped (it fails open on a Redis error). | STRAT-006, A3-004 | MEDIUM | 3×4×3 = 36 | INFERRED (per-ride call count) / VERIFIED (default, behaviour) | SRE + BE-DISP | Open | X17 |
| RR-64 | Production service areas include two Riyadh entries; the repo contradicts itself on whether they are test data or a market. | STRAT-007 | LOW (strategic UNKNOWN) | 2×2×5 = 20 | VERIFIED (contradiction) / UNKNOWN (intent) | FOUNDER | Decision needed | N15, E-F6 |
| RR-65 | Three CLAUDE.md KPIs (rolling driver retention, safety incident rate, support first response) have no query, and no business KPI (margin, MRR, wallet float) exists. | STRAT-009 | MEDIUM | 3×3×5 = 45 | VERIFIED | FOUNDER + BE-PLAT | Open | X19 |
| RR-66 | Corporate SaaS — the declared profit engine — cannot be bought: dark, unpriced, no self-serve, no MRR metric. | STRAT-010 | MEDIUM | 3×3×5 = 45 | VERIFIED | FOUNDER | Decision needed | X19, E-F3 |
| RR-67 | Three of eight published performance SLAs emit no metric, none of the eight has a live alert, the metrics backend was never provisioned, and there are no outside-in synthetic checks. | OBS-002, C11, CR-2026-008, decay §3 #15, #17, reliability.md §6 | HIGH | 4×4×4 = 64 † | VERIFIED | SRE | Open–stuck: needs a vendor account decision (C11) | X10 |
| RR-68 | The admin surface (60 route files, 63 pages) has an eight-bullet PRD footprint; nobody outside the authors can say what "correct" is for most admin pages. | CARTO-001 | MEDIUM | 3×4×4 = 48 † | VERIFIED | FOUNDER | Decision needed (PRD addendum or declare internal scope) | L-backlog |

## §5 Regulatory, legal and compliance

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-69 | The licence-class rule may be inverted: code and CLAUDE.md require Class 5; a provincial regulation snippet says Class 4. Nobody in the repo has cited the source. | COMP-001 | HIGH | 4×5×3 = 60 | UNKNOWN (code VERIFIED; requirement from snippets) | FOUNDER → SGI / COUNSEL | Decision needed | N10, X1, E-R1 |
| RR-70 | Every Saskatchewan eligibility rule beyond document expiry (age, vehicle age, experience, licence class) is behind a flag that defaults off, and the experience check has no data for the existing fleet. The v2 availability path does not contain the eligibility block at all. Onboarding copy promises what production does not enforce. | COMP-002, DRIVER-003, BENCH-005 | HIGH | 4×4×4 = 64 | VERIFIED (code) / UNKNOWN (live flag) | COMPLIANCE + BE-DISP | Built, dark | N11, X1 |
| RR-71 | No evidence of municipal TNC licences (Regina, Saskatoon) or accrual of the Saskatoon per-trip fee. | COMP-004, G2 (bylaw) | HIGH | 4×4×3 = 48 | INFERRED (requirement) / VERIFIED (absence in code) | FOUNDER | Decision needed | N10, X14, E-R3 |
| RR-72 | SGI monthly kilometre reporting appears mandatory; Spinr has only an on-demand report with no schedule or filing record. Quarterly and annual SGI reports are unbuilt. | COMP-005, COMP-017 | HIGH | 4×3×4 = 48 | INFERRED (obligation) / VERIFIED (code) | COMPLIANCE | Open | N10, X14, E-R4 |
| RR-73 | Company-level insurance (CGL, tech E&O, cyber) is not confirmed to exist. | G4 (ACTION_ITEMS, open since 2026-08-21), compliance.md H6 | HIGH | 4×5×2 = 40 † | ASSUMED | FOUNDER → broker | Open | N10, E-R12 |
| RR-74 | Retention drifts in both directions: driver tombstones and safety incidents are kept indefinitely, and three doc-vs-SQL retention numbers disagree. | COMP-007 | MEDIUM | 3×4×3 = 36 | VERIFIED | PRIVACY + DB-OWNER | Open | X14, E-R8 |
| RR-75 | Consent has three holes: legacy-imported users, corporate self-serve signup (no consent stamp), and a dark re-consent mechanism. | COMP-008, CORP-004 | MEDIUM | 3×4×3 = 36 | VERIFIED | PRIVACY + BE-AUTH | Built, dark (re-consent) / Open | X14 |
| RR-76 | The independent-contractor agreement is a draft with no signature flow; nine live legal texts have had no counsel review. | COMP-009 | MEDIUM | 3×4×3 = 36 | VERIFIED | COUNSEL | Decision needed | X14, E-R7 |
| RR-77 | WAV: the documented "estimated wait for the next WAV + standard alternative" is not built; the rider sees a greyed toggle when no WAV driver is available. (The benchmark rates this HIGH as "not a working feature"; the compliance lane's code read supports the narrower LOW gap, so the lane's score is kept.) | COMP-010, BENCH-008, A11Y-003 | LOW | 2×2×3 = 12 | VERIFIED | FOUNDER + MOBILE | Decision needed (build or correct the doc) | L8 |
| RR-78 | WCAG 2.1 AA is a target, not a tested state on mobile; the accessibility inbox cited 13 times is not provisioned; the SMS fallback for ride-state pushes is promised but unbuilt; `docs/ACCESSIBILITY.md` cites Ontario's AODA (the Saskatchewan basis is ASSUMED). | COMP-011, UXA11Y-005 | MEDIUM | 3×3×3 = 27 | VERIFIED / ASSUMED (legal basis) | FOUNDER + MOBILE | Open | N16, X22, E-R9 |
| RR-79 | Audio recording is not built (correct); if it ever is, the framing is PIPEDA-governed notice to both parties. | COMP-012 | RECOMMENDATION | — | VERIFIED / INFERRED (law) | COUNSEL | No action | — |
| RR-80 | The 2026-09-12 driver-PII incident. | COMP-013 | CRITICAL (as a compliance exposure) | 5×4×4 = 80 | VERIFIED (record) | FOUNDER | **Owner-managed, closed by owner statement 2026-09-24, not independently verified** | — |
| RR-81 | A driver who is "online but unreachable" (dead phone, crash) keeps an open Period 1 row; coverage reading favours the driver, but the audit trail cannot tell "waiting" from "phone dead". | COMP-014, SAFETY-003 | LOW | 2×3×3 = 18 | VERIFIED (design) / ASSUMED (SGI reading) | COMPLIANCE → SGI | Decision needed | N10, X14, E-R5 |
| RR-82 | `audit-framework/regulatory-matrix.md` over-scopes (federal accessibility acts) and contradicts code (driver age 21 vs 18; audit-log 3 vs 7 years); the anti-money-laundering row is unexamined. | COMP-016 | LOW | 2×2×3 = 12 | INFERRED / VERIFIED | COMPLIANCE | Open | N16, E-R10 |
| RR-83 | Compliance checks are not labelled as a gate: the regulatory checker agent never runs in CI and the PR checklist boxes are advisory. The tax, WAV, CRC, insurance-period, DSAR and purge tests already block PRs through the full pytest step. Re-scored on that narrower gap. | COMP-018 (re-scored per 07-reverification §4.3) | LOW | 2×3×2 = 12 † | VERIFIED | COMPLIANCE + CI | Open | X14 |

## §6 Dispatch, realtime, reliability and capacity

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-84 | Every web server also runs all 44 background loops (45 registry entries with the watchdog); the role split exists but is unset in the real Fly and Railway configs, so adding servers adds database load. | A3-001, REL-003 | HIGH | 4×4×5 = 80 | VERIFIED | SRE | Open (canary config exists, never adopted) | N12, E-F8 |
| RR-85 | Loop-watchdog staleness thresholds are wrong in both directions for about 30 of 44 loops (safety and dispatch loops alert hours late; long-interval loops look falsely stale), and the annual T4A loop can never be flagged. | REL-001 | HIGH | 4×4×4 = 64 | VERIFIED | BE-PLAT + SRE | Open | N17 |
| RR-86 | All loop alerting goes to one webhook whose production setting is unknown, with no fallback; the SOS paging webhook is also unset. MTTD/MTTR for loop, Redis and deploy failures is therefore unknown. | REL-002, decay §3 #16, reliability.md §7 | HIGH | 4×5×3 = 60 | VERIFIED (mechanism) / UNKNOWN (env) | SRE | Open | N6 |
| RR-87 | Neither cross-provider failover nor backup restore (PITR) has ever been exercised, and the restore runbook flags its own core assumption as likely false. | REL-004, C1, E7 | HIGH | 4×5×3 = 60 | VERIFIED | SRE + DB-OWNER | Open | X10, E-F8 |
| RR-88 | There is no backend deploy-rollback runbook (the admin dashboard has one); `/health` is liveness-only, so a behaviourally wrong deploy passes. | reliability.md §8 | MEDIUM | 3×4×2 = 24 † | VERIFIED | SRE | Open | X10 |
| RR-89 | No staging environment is confirmed to exist, yet most "ship dark, verify in staging" plans and the DAST job assume one. | decay §3 #23, 00-history §9 Q10 | MEDIUM | 3×5×3 = 45 † | INFERRED / UNKNOWN | SRE + FOUNDER | Decision needed | N6, E-F8 |
| RR-90 | Vendor plan tiers (Supabase compute, Redis, Sentry quota, Vercel, EAS) are recorded nowhere; the capacity runbook describes the fleet replaced on 2026-09-16; Railway runs 4 workers vs Fly 2. | A3-006, A3-005 | MEDIUM | 2×4×3 = 24 | VERIFIED (absence) | SRE + FOUNDER | Open–stuck: needs dashboard access | N16, X10 |
| RR-91 | During a WS Redis outage, cross-replica delivery is dropped by design with no metric distinguishing "local" from "lost". | REL-005 | MEDIUM | 3×4×2 = 24 | VERIFIED (mechanism; `socket_manager.py:371`) | BE-DISP | Open | X10 |
| RR-92 | Each driver GPS ping costs about 5–6 Redis round-trips; Redis operations are the first thing to break at 10× drivers. No load test has run. | A3-002 | HIGH | 4×4×3 = 48 | INFERRED (arithmetic) | BE-DISP + SRE | Open | L12 |
| RR-93 | No response compression at the origin (admin exports up to 10k rows as raw JSON); WS compression never asserted or measured. | A3-003, A3-007 | MEDIUM | 2×3×4 = 24 | VERIFIED (absence) / INFERRED | BE-PLAT | Open | L12 |
| RR-94 | The rider gets no event while a driver is deciding on the offer, although the dispatch doc promises one — a product decision, not a stranding bug. Sending the status through the existing broadcast helper would render the rider app's existing "Driver confirming" label and then "Driver Unavailable" on timeout — a visible UX change, so it is a product choice between emitting and correcting the doc (08-hostile-review §1.3). | DISPATCH-001 (W2 and rapid) | MEDIUM | 2×3×3 = 18 | VERIFIED | FOUNDER → BE-DISP | Decision needed | X13, E-F11 |
| RR-95 | Declined-driver exclusion is Redis-first; on the default (legacy) path a stale row can abort the whole offer batch, risking the 2 s dispatch SLA exactly when Redis is degraded. The flagged-off path already behaves correctly. | DISPATCH-002 (W2 deepened; rapid) | MEDIUM-HIGH | 2×3×2 = 12 | VERIFIED | BE-DISP | Open | X13 |
| RR-96 | Releasing a driver's availability is read-then-write, so a narrow race can leave `is_available` true while `is_online` is false (breaks a CLAUDE.md invariant). The atomic replacement exists but is dark. | DISPATCH-003 (W2) | LOW-MEDIUM | 2×2×1 = 4 | VERIFIED | BE-DISP | Built, dark | X13 |
| RR-97 | Positive controls: offline-mid-offer and offline-mid-accept races closed by one helper each; every `rides.status` write is guarded; all 44 loops surveyed for replay safety (closes the rapid baseline's loop-survey gap). | DISPATCH-004 (W2 and rapid), rapid DISPATCH-003 (scope gap, now closed by reliability.md §3), INFO-005 | PASS | — | VERIFIED | — | Pass | — |

## §7 Integrations and vendors

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-98 | The Twilio client has no HTTP timeout: a slow (not down) Twilio can hang OTP requests and exhaust the shared thread pool. | INT-001 | CRITICAL | 5×4×3 = 60 | VERIFIED | BE-AUTH | Open (same-day fix) | N8 |
| RR-99 | Zoho ticket-creation failures, including the SOS-linked ticket, are downgraded to a warning and dropped: no retry, no metric, no Sentry. | INT-002, OBS-004, SKB-008 | HIGH | 4×4×3 = 48 | VERIFIED | T&S + BE-PLAT | Open | X11 |
| RR-100 | Maps proxy routes (autocomplete, details, geocode, legacy directions) have a cost breaker but no failure-rate breaker; during a Google outage every keystroke waits for the full timeout. | INT-004 | MEDIUM | 3×3×3 = 27 | VERIFIED | BE-DISP | Open | X17 |
| RR-101 | LLM provider adapters have no explicit request timeout; SDK defaults (~10 min class) apply to a user-facing chat. | INT-005 | MEDIUM | 3×2×3 = 18 | VERIFIED (absence) | BE-PLAT | Open | X17 |
| RR-102 | `CloudinaryService` is a wired, pinned dependency with zero call sites and no vendor record. | INT-006 | LOW | 1×2×2 = 4 | VERIFIED | BE-PLAT | Decision needed (delete or document) | X17 |

## §8a Rider, driver and admin journeys

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-103 | A rider has no self-serve dispute or refund request; drivers and admins do, and the backend already accepts rider callers. | RIDERJ-003, BENCH-002 | HIGH | 3×3×3 = 27 | VERIFIED (absence re-grepped) | MOBILE | Open | X2 |
| RR-104 | If the driver's phone dies mid-trip, the ride stays `in_progress`: the rider cannot book again, sees only "location delayed", and recovery depends on an admin noticing a Sentry alert. No runbook sets a response time. | RIDERJ-002, DRIVER-005, BENCH-009 | HIGH | 3×3×2 = 18 | VERIFIED | MOBILE + SUPPORT | Open | X2, X11 |
| RR-105 | Mid-trip stop add/remove is built and race-safe server-side but unreachable from either app. | RIDERJ-001, BENCH-003 | MEDIUM-HIGH | 2×2×3 = 12 | VERIFIED | FOUNDER → MOBILE | Decision needed (build or descope) | L11, E-F12 |
| RR-106 | The vehicle-type cards (the main booking choice) have no accessibility label, role or selected state. | RIDERJ-004 | MEDIUM-HIGH | 2×3×3 = 18 | VERIFIED | MOBILE | Open | X2 |
| RR-107 | A suspended or banned driver is never shown the reason, although it is stored and even attached to their appeal record. | DRIVER-001, BENCH-007 | MEDIUM (benchmark: HIGH) | 2×3×2 = 12 | VERIFIED | MOBILE + COUNSEL | Open | X3 |
| RR-108 | Declining an offer lowers a driver's future offer priority (explicit decline at `ride_flow.py:798`, streak reset at `:804`; the timeout path at `matching.py:2074` also lowers it; applies on the default ETA-ranking branch). This is disclosed nowhere in the app or FAQ. | DRIVER-004 (call sites corrected per 07-reverification §4.5), SKB-006 | MEDIUM | 2×3×3 = 18 | VERIFIED | FOUNDER + COUNSEL → SUPPORT | Decision needed (disclose, recommended) | X3, E-R7 |
| RR-109 | Driver instant cash-out exists in the backend but has no driver-app screen. | BENCH-004 | MEDIUM-HIGH | 3×4×3 = 36 † | VERIFIED | MOBILE + BE-PAY | Open | X3 |
| RR-110 | No battery-aware or low-power behaviour found in the driver location pipeline (coverage gap, not a confirmed defect). | DRIVER-006 | LOW-MEDIUM | not scored | INFERRED | MOBILE | Verify first | L17 |
| RR-111 | `PUT /admin/staff/{id}` enforces super-admin in the handler body instead of the dependency its siblings use. | ADMIN-OPS-002 | MEDIUM | 2×3×2 = 12 | VERIFIED | ADMIN | Open | X7 |
| RR-112 | Two audit-log write paths; about eight routers insert directly and never set `request_id`, breaking log correlation. | ADMIN-OPS-003 | MEDIUM | 2×3×1 = 6 | VERIFIED | ADMIN | Open | X7 |
| RR-113 | No admin editor has a staleness check: two admins editing surge, fare config, feature flags, promotions or a driver record at once — the second save silently wins. | ADMIN-OPS-004, CONCURRENCY-001 | MEDIUM | 2×3×2 = 12 | VERIFIED | ADMIN | Open | X7 |
| RR-114 | Maps & Routing (which gates WAV dispatch by service area) has no owning review agent. | CARTO-003 | MEDIUM | 3×3×3 = 27 † | VERIFIED | REPO-ADMIN | Open | X16 |
| RR-115 | The traceability CSV's `gap` column is keyword-collision noise (4 of 4 spot checks were real code); do not cite it as missing features. | CARTO-004 | LOW (method) | 2×2×3 = 12 † | VERIFIED | R20 note | Accepted (caveat applied in this register) | — |

## §8b Trust, safety, fraud and support

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-116 | The only SOS on-call runbook describes a table and an SMS paging flow that do not exist; the real paging webhook is unconfigured in every environment, so no automated page fires for a real SOS today. The runbook also links a trauma-support guide that does not exist. | TSF-001, SKB-003 | HIGH | 4×4×5 = 80 † | VERIFIED | T&S + FOUNDER | Decision needed (who is paged, with what tool) | N7, E-F4 |
| RR-117 | No fraud detection signal for driver–rider collusion, cancellation-fee farming, or repeat chargebacks; the prior-dispute count is computed but only printed inside an evidence PDF. (BENCH-006 restates these three and is not independent corroboration.) | TSF-002, TSF-003, TSF-004, SAFETY-005 (superseded), BENCH-006 (derivative) | MEDIUM-HIGH | 3×4×3 = 36 † | VERIFIED (absence re-grepped for TSF-004) | T&S + ADMIN | Open | X12 |
| RR-118 | No damage or cleaning-fee claim flow: no fee schedule, no photo evidence, no appeal window. | TSF-008 | HIGH | 4×3×3 = 36 † | VERIFIED | FOUNDER → T&S + BE-PAY | Decision needed | X12 (design), L7 |
| RR-119 | ~~No trip PIN.~~ **Withdrawn as filed:** a 4-digit pickup code already gates trip start (generated at `backend/dependencies/__init__.py:78-80`, shown to the rider on the arriving, arrived and status screens, entered by the driver in `ActiveRidePanel.tsx`, checked with a lockout at `routes/drivers/ride_flow.py:1164-1210`). **Residual gaps only:** (a) a ride with a missing pickup code returns 409 "contact support to start it" and that support path is unchecked; (b) the rider-verifies-driver direction relies on photo and plate. | TSF-010, BENCH-001 (both refuted; 08-hostile-review §1.1) | WITHDRAWN (residual LOW) | 2×3×2 = 12 † (residual) | VERIFIED (code read by orchestrator and hostile review) | T&S + SUPPORT | Withdrawn; residual checks open | X4 |
| RR-120 | Law-enforcement data requests have a written policy but no export tool and no record of what was disclosed. | TSF-009 | MEDIUM | 3×2×2 = 12 † | VERIFIED | T&S + PRIVACY | Open | X11 |
| RR-121 | No SIM-swap-specific account-takeover signal. | TSF-005 | LOW-MEDIUM | 2×3×2 = 12 † | VERIFIED | BE-AUTH | Open | L9 |
| RR-122 | Promo stacking and free-ride double-reward paths not fully traced for corporate and scheduled entry points. | TSF-006 | LOW | 2×3×2 = 12 † | INFERRED | T&S | Verify | X12 |
| RR-123 | The SOS incident row does not record the ride's status at trigger time (SOS is correctly never blocked by ride state). | TSF-007 | LOW | 2×2×3 = 12 † | VERIFIED | T&S | Open | X11 |
| RR-124 | Whether an admin's full-PII view of an SOS incident is itself audit-logged is unverified. | TSF-011 | UNKNOWN | — | UNKNOWN | T&S + PRIVACY | Verify | X11 |
| RR-125 | No non-database fallback for SOS during a sustained DB outage (accepted decision B15a, 2026-08-01). | SAFETY-001 | MEDIUM | — | VERIFIED | T&S | Accepted | — |
| RR-126 | Emergency-contact relationship field and OTP-at-add are not built. | SAFETY-002 | LOW | — | VERIFIED | T&S | Open | L-backlog |
| RR-127 | No device signal at signup, so one device can create many accounts (promo/referral farming). Needs a privacy impact assessment first. | SAFETY-004 | MEDIUM | 3×3×3 = 27 † | VERIFIED | FOUNDER + PRIVACY | Decision needed | L9, E-S6 |
| RR-128 | Positive: core SOS is unflagged; flagged variants fail to the safer default. | TSF-012 | PASS | — | VERIFIED | — | Pass | — |
| RR-129 | No customer-facing outage or incident communications exist (no status page, no pre-approved templates). | SKB-004, BENCH-010 (part) | MEDIUM | 3×3×3 = 27 | VERIFIED | SUPPORT + COUNSEL | Open | X11, E-F13 |
| RR-130 | FAQ and AI-assistant answers have no language dimension although the apps ship English, French and Spanish UI. | SKB-005 | MEDIUM | 3×3×2 = 18 | VERIFIED | FOUNDER + SUPPORT | Decision needed | L10 |
| RR-131 | The no-show fee push notification states the charge with no dispute path. (The e-mail already carries a contest line, so the rider has a written route; severity lowered per 07-reverification §4.2.) | SKB-007, BENCH-010 (part) | LOW | 2×3×2 = 12 † | VERIFIED (push only) | SUPPORT + MOBILE | Open | X2 |

## §8c Quality and accessibility

| Row | Risk (plain language) | Merged IDs | Sev | S×B×L | Label | Owner role | Status | Roadmap |
|---|---|---|---|---|---|---|---|---|
| RR-132 | Coverage floors sit below the CLAUDE.md targets (e.g. payments 85% floor vs 90% target) and nothing says so. | QUAL-005 | MEDIUM | 3×3×3 = 27 | VERIFIED | CI | Open | X18 |
| RR-133 | 72.9% of backend tests (12,671 of 17,392) carry no tier marker, so `-m unit` and "not slow" do not mean what CLAUDE.md says. | QUAL-006 (counts refreshed per 07-reverification §4.8) | MEDIUM | 3×3×3 = 27 | VERIFIED (`pytest --collect-only`, re-run) | CI | Open | X18 |
| RR-134 | Two async test runners coexist; CLAUDE.md's marker guidance does not match what actually runs. | QUAL-007 | LOW-MEDIUM | 2×3×3 = 18 | VERIFIED (config) / INFERRED (risk) | CI | Open | X18 |
| RR-135 | QUAL-003 (corporate surge-exemption test missing). | QUAL-003 | — | — | — | — | **Withdrawn** — a test exists; never cite it as a gap | — |
| RR-136 | `print()` present in backend code (hard CLAUDE.md rule, low blast radius). | OBS-003 | MEDIUM | 3×1×3 = 9 † | VERIFIED | BE-PLAT | Open | X18 |
| RR-137 | `allowFontScaling={false}` is used 76+ times with no bounded fallback, concentrated on fare totals and driver earnings, so OS text-size settings do not reach the most important numbers. | UXA11Y-001 | HIGH | 3×3×3 = 27 | VERIFIED | MOBILE | Open | X22 |
| RR-138 | Brand/semantic colour tokens used as text fail WCAG AA contrast in computed (not rendered) pairings; the AA-safe `primaryDark` is used at 22 of ~680 sites. | UXA11Y-002 (counts refreshed: 657/22) | HIGH | 3×2×3 = 18 | VERIFIED (arithmetic, not measured on device) | MOBILE (shared theme) | Open | X22 |
| RR-139 | Destination search shows the same screen for a network failure and for "no results". | UXA11Y-003 | MEDIUM | 2×2×3 = 12 | VERIFIED | MOBILE | Open | X2 |
| RR-140 | The live car marker ignores the OS reduce-motion setting (whether this motion is "essential" is a product call). | UXA11Y-004 | MEDIUM | 2×2×2 = 8 | VERIFIED | FOUNDER + MOBILE | Decision needed | X22 |
| RR-141 | The admin a11y ratchet comment and ACTION_ITEMS E11 understate progress (4 violations remain, not 64). | UXA11Y-006 | RECOMMENDATION (positive) | — | VERIFIED | ADMIN | Open (comment fix) | N16 |
| RR-142 | Positive steelman items the history and lanes want kept: Decimal fare core, surge cap at every call site, OTP hashing and lockout, refresh-token rotation with theft detection, idempotent booking, replay-safe loops, SOS never auto-dials. | rapid EXECUTIVE_SUMMARY "keep", 00-history §6, 03-benchmark §4 | PASS | — | VERIFIED | — | Keep | — |

## §10 Highest-priority rows

Score is one input, not the order of work. Tie-breakers from the master prompt §4 apply: CRITICAL rows and anything touching safety, money or personal data go first even at a lower score (safe over fast), and a cheap fix with a large score reduction goes before an expensive one.

| Rank | Row | Score | Why it is first |
|---|---|---|---|
| 1 | RR-18 service-role key rotation unproven | 75 (CRITICAL) | Full read/write to every table if the old key still works; rotation is ~15 minutes and costs nothing if it was already done. |
| 2 | RR-98 Twilio has no timeout | 60 (CRITICAL) | A slow vendor can stop every login; two-line fix. |
| 3 | RR-116 no page fires for a real SOS | 80 | Life-safety; the fix is a decision plus a setting plus a runbook rewrite. |
| 4 | RR-38 Meta per-ride purchase events | 100 | Breaches a stated product guardrail and is undisclosed; can be stopped today by clearing a setting. |
| 5 | RR-19 LogRocket unmasked replay | 64 | Personal data leaving to an undisclosed US vendor on every iOS session; can be paused today in the vendor console. |
| 6 | RR-01 / RR-02 / RR-03 merge gate | 80 / 80 / 48 | Every other gate depends on it; needs one person with GitHub admin access. |
| 7 | RR-58 / RR-59 / RR-60 negative margin, revenue model, pricing | 100 / 80 / 48 | The business loses money per ride and the documents describe a different model; decisions, not code. |
| 8 | RR-05 silent-swallow family | 100 | Already lost 53 of 55 payment-failure events once; a static rule stops the whole class. |
| 9 | RR-84 / RR-85 / RR-86 loops and alerting | 80 / 64 / 60 | Config-only split; thresholds test; confirm the one alert channel is set. |
| 10 | RR-08 migrations applied by hand | 80 | Schema and code drift apart with no owner; name an owner, then automate. |

Owner-managed and excluded from the ranking: RR-80 (COMP-013, closed by owner statement 2026-09-24, not independently verified).

## §11 Finding-id index (every id → register row)

| Finding id | Row(s) |
|---|---|
| A11Y-003 | RR-77 |
| A3-001 | RR-84 |
| A3-002 | RR-92 |
| A3-003 | RR-93 |
| A3-004 | RR-63 |
| A3-005 | RR-90 |
| A3-006 | RR-90 |
| A3-007 | RR-93 |
| ADMIN-OPS-001 | RR-53 |
| ADMIN-OPS-002 | RR-111 |
| ADMIN-OPS-003 | RR-112 |
| ADMIN-OPS-004 | RR-113 |
| BENCH-001 | RR-119 |
| BENCH-002 | RR-103 |
| BENCH-003 | RR-105 |
| BENCH-004 | RR-109 |
| BENCH-005 | RR-70 |
| BENCH-006 | RR-117 |
| BENCH-007 | RR-107 |
| BENCH-008 | RR-77 |
| BENCH-009 | RR-104 |
| BENCH-010 | RR-129, RR-131 |
| CARTO-001 | RR-68 |
| CARTO-002 | RR-09 |
| CARTO-003 | RR-114 |
| CARTO-004 | RR-115 |
| CARTO-005 | RR-08 |
| COMP-001 | RR-69 |
| COMP-002 | RR-70 |
| COMP-003 | RR-56 |
| COMP-004 | RR-71 |
| COMP-005 | RR-72 |
| COMP-006 | RR-49 |
| COMP-007 | RR-74 |
| COMP-008 | RR-75 |
| COMP-009 | RR-76 |
| COMP-010 | RR-77 |
| COMP-011 | RR-78 |
| COMP-012 | RR-79 |
| COMP-013 | RR-80 |
| COMP-014 | RR-81 |
| COMP-015 | RR-46, RR-57 |
| COMP-016 | RR-82 |
| COMP-017 | RR-72 |
| COMP-018 | RR-83 |
| CONCURRENCY-001 | RR-113 |
| CORP-001 | RR-50 |
| CORP-002 | RR-51 |
| CORP-003 | RR-52 |
| CORP-004 | RR-75 |
| CORP-005 | RR-46 |
| DISPATCH-001 | RR-94 |
| DISPATCH-002 | RR-95 |
| DISPATCH-003 | RR-96, RR-97 |
| DISPATCH-004 | RR-97 |
| DRIFT-003 | RR-06 |
| DRIVER-001 | RR-107 |
| DRIVER-002 | RR-17 |
| DRIVER-003 | RR-70 |
| DRIVER-004 | RR-06, RR-108 |
| DRIVER-005 | RR-104 |
| DRIVER-006 | RR-110 |
| HIST-001 | RR-42 |
| HIST-002 | RR-09 |
| HIST-003 | RR-17 |
| HIST-004 | RR-05 |
| HIST-005 | RR-06 |
| HIST-006 | RR-08 |
| HIST-007 | RR-07 |
| HIST-008 | RR-12 |
| HIST-009 | RR-13 |
| HIST-010 | RR-14 |
| HIST-011 | RR-11 |
| HIST-012 | RR-10 |
| HIST-013 | RR-01 |
| HIST-014 | RR-01 |
| HIST-015 | RR-09 |
| HIST-016 | RR-15 |
| HIST-017 | RR-16 |
| INFO-004 | RR-09 |
| INFO-005 | RR-97 |
| INT-001 | RR-98 |
| INT-002 | RR-99 |
| INT-003 | RR-39 |
| INT-004 | RR-100 |
| INT-005 | RR-101 |
| INT-006 | RR-102 |
| INT-007 | RR-40 |
| MONEY-001 | RR-42 |
| MONEY-002 | RR-49 |
| MONEY-003 | RR-56 |
| MONEY-004 | RR-06 |
| MONEY-005 | RR-42 |
| MONEY-006 | RR-42 |
| MONEY-007 | RR-43 |
| MONEY-008 | RR-42 |
| MONEY-009 | RR-44 |
| MONEY-010 | RR-45 |
| MONEY-011 | RR-46 |
| MONEY-012 | RR-47 |
| MONEY-013 | RR-48 |
| OBS-001 | RR-12 |
| OBS-002 | RR-67 |
| OBS-003 | RR-136 |
| OBS-004 | RR-99 |
| OBS-005 | RR-05, RR-44 |
| OBS-006 | RR-12 |
| QUAL-001 | RR-03 |
| QUAL-002 | RR-37 |
| QUAL-003 | RR-135 |
| QUAL-004 | RR-02 |
| QUAL-005 | RR-132 |
| QUAL-006 | RR-133 |
| QUAL-007 | RR-134 |
| RECURRENCE-003 | RR-17 |
| REL-001 | RR-85 |
| REL-002 | RR-86 |
| REL-003 | RR-84 |
| REL-004 | RR-87 |
| REL-005 | RR-91 |
| RELIABILITY-002 | RR-09 |
| RIDERJ-001 | RR-105 |
| RIDERJ-002 | RR-104 |
| RIDERJ-003 | RR-103 |
| RIDERJ-004 | RR-106 |
| RIDERJ-005 | RR-15 |
| RIDERJ-006 | RR-09 |
| RIDERJ-007 | RR-09 |
| SAFETY-001 | RR-125 |
| SAFETY-002 | RR-126 |
| SAFETY-003 | RR-81 |
| SAFETY-004 | RR-127 |
| SAFETY-005 | RR-117 |
| SEC-A2-001 | RR-21 |
| SEC-A2-002 | RR-23 |
| SEC-A2-003 | RR-24 |
| SEC-A2-004 | RR-30, RR-34 |
| SEC-A2-005 | RR-32 |
| SEC-A2-006 | RR-35 |
| SEC-A2-007 | RR-16 |
| SEC-A2-008 | RR-06 |
| SEC-R10-001 | RR-18 |
| SEC-R10-002 | RR-08 |
| SEC-R10-003 | RR-19 |
| SEC-R10-004 | RR-25 |
| SEC-R10-005 | RR-22 |
| SEC-R10-006 | RR-26 |
| SEC-R10-007 | RR-24 |
| SEC-R10-008 | RR-36 |
| SEC-R10-009 | RR-27 |
| SEC-R10-010 | RR-23 |
| SEC-R10-011 | RR-28 |
| SEC-R10-012 | RR-29 |
| SEC-R10-013 | RR-30 |
| SEC-R10-014 | RR-31 |
| SEC-R10-015 | RR-32 |
| SEC-R10-016 | RR-16 |
| SEC-R10-017 | RR-33 |
| SEC-R10-018 | RR-34 |
| SEC-R10-019 | RR-41 |
| SKB-001 | RR-20 |
| SKB-002 | RR-20 |
| SKB-003 | RR-116 |
| SKB-004 | RR-129 |
| SKB-005 | RR-130 |
| SKB-006 | RR-108 |
| SKB-007 | RR-131 |
| SKB-008 | RR-99 |
| STRAT-001 | RR-59 |
| STRAT-002 | RR-58 |
| STRAT-003 | RR-61 |
| STRAT-004 | RR-38 |
| STRAT-005 | RR-62 |
| STRAT-006 | RR-63 |
| STRAT-007 | RR-64 |
| STRAT-008 | RR-60 |
| STRAT-009 | RR-65 |
| STRAT-010 | RR-66 |
| STRAT-011 | RR-55 |
| STRAT-012 | RR-54 |
| TSF-001 | RR-116 |
| TSF-002 | RR-117 |
| TSF-003 | RR-117 |
| TSF-004 | RR-117 |
| TSF-005 | RR-121 |
| TSF-006 | RR-122 |
| TSF-007 | RR-123 |
| TSF-008 | RR-118 |
| TSF-009 | RR-120 |
| TSF-010 | RR-119 |
| TSF-011 | RR-124 |
| TSF-012 | RR-128 |
| UXA11Y-001 | RR-137 |
| UXA11Y-002 | RR-138 |
| UXA11Y-003 | RR-139 |
| UXA11Y-004 | RR-140 |
| UXA11Y-005 | RR-78 |
| UXA11Y-006 | RR-141 |

*End of register. Written by R20 (W5), 2026-09-25. No code, config or data was changed.*
