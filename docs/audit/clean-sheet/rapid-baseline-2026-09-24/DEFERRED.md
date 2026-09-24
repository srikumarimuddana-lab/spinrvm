# Deferred — what this 60-minute run did not cover, and why

This was a rapid baseline, not the full 20-lane programme in `docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md`. Everything below was skipped, cut short by the 25-minute lane cap, or blocked by the environment. Nothing here was silently dropped.

## 1. Lanes that did not return
None. All eight Wave A lanes returned before the minute-35 cutoff (last one at ~14:31 UTC, run started 14:20). Both Wave B agents returned within their 10-minute box — see `verification.md` and `blueprint-top10.md`.

## 2. Master-prompt lanes not run at all (W1, W3, W4-competitive, W5-operating-model)
- Product Strategist, Rider Journey Owner, Driver Journey Owner, Corporate/B2B Owner, Admin/Ops Owner (W1).
- Quality/Test, Integrations & Vendors, Support/KB/Training, UX & Accessibility as standalone lanes (W3) — only fragments appeared inside A6/A7.
- Competitive Analyst benchmark (W4) — `industry-stack-benchmark.md` was checked by A3 only where the network allowed; every Uber/Lyft/Grab claim remains INFERRED (hosts blocked).
- Operating-Model Designer (W5).
- Cartographer / `traceability.csv` (W0) — A1 drafted the L2/L3 epic list only; no L4/L5/L6 mapping.

## 3. Scope items inside a lane that the time box cut
| Lane | Not reached | Impact |
|---|---|---|
| A1 | Full git history (clone is shallow, 310 commits visible; GitHub API not used); threat-model file contents; Semgrep rule audit; live-schema check | Recurrence families rest on ACTION_ITEMS + change-log text, not commit history |
| A2 | ASVS row-by-row beyond ~24 headline controls; full MASVS-L1; loguru positional-arg / f-string PII sweep; per-screen mobile token storage | Control map is headline-level |
| A3 | Every vendor limit (all vendor doc hosts blocked); real CPU/RSS at 750 connections; actual export sizes; live driver count | Every 10× figure is arithmetic; capacity table cannot be VERIFIED from the repo |
| A4 | 37 of 45 background loops not read for replay safety (only the 5 dispatch-critical ones); rider WS coverage of `driver_arrived → in_progress` to the driver's own socket; `scheduled → searching` WS emit | Loop safety is attested for 5 loops, UNKNOWN for the rest |
| A5 | `routes/tips.py`, `routes/corporate_wallet.py`, `utils/surge_engine.py` internals; `email_receipt.py` fallback mirror; SURGE_CAP clamp at every fare call site; corporate/scheduled surge exemptions; payout ≤ collected invariant; `corporate_wallet_apply_delta` idempotency per caller; corporate invoice ITC format; semgrep not re-executed | Surge and corporate-billing invariants from CLAUDE.md were not independently re-verified this run |
| A6 | Full `logger.warning` sweep (~15 of 97+ sampled); support/Zoho files beyond level grep; PR-label taxonomy; migration-apply timing | BLOCKER pattern "absent in sample", not fleet-wide |
| A7 | Mid-trip destination change / re-pricing UX; stuck-ride sweeper threshold and rider copy; WS reconnect REST resync; admin concurrent-edit beyond `drivers.py`; contrast ratios; bundle size / startup timing; corporate mid-trip suspension | Scenario table has 1 UNKNOWN and 3 PARTIAL rows |
| A8 | PIPEDA retention/deletion windows (`purge_pii_retention()`, migration 296; 7y/3y/30d numbers), data-export endpoint, consent-version-on-signup; insurance-period call-site sweep (Period 2 on `driver_assigned`, append-only check, go_online document expiry); driver–rider collusion; cancellation-fee farming; chargeback velocity; OTP account-takeover path; promo-stacking entry points; driver-app UI leak sweep | Retention and insurance-period claims in CLAUDE.md were NOT re-verified this run |

## 4. Blocked by the environment
- **WebFetch blocked (egress proxy):** supabase.com, docs.stripe.com, docs.fly.io / fly.io, uvicorn.dev; www.uvicorn.org DNS failure. lyft.com, uber.com, grab.com not attempted (pre-declared blocked). All vendor-limit and pgsodium-deprecation claims are INFERRED from search snippets.
- **Government sources:** saskatchewan.ca and canada.ca have been blocked in every prior session per `docs/compliance/2026-08-13-sk-pst-rideshare-determination-needed.md`; not retried. All tax claims are ASSUMED → `ESCALATIONS.md`.
- **Connectors:** Sentry and Stripe MCP failed to connect (proxy 403) and were not depended on, per the plan. Supabase, Railway, Vercel connectors were available but deliberately not exercised (read-only static run; A2 lists `list_extensions` as the one call that would have settled pgsodium status).
- **Visual tooling:** rider-app and driver-app have none; every UI claim in A7 is reasoned about from code, not screenshotted.

## 5. Not done by the orchestrator
- No load test, no dynamic test, no Semgrep run, no `pytest` run — report-only run, by design.
- `EXECUTIVE_SUMMARY.md` severity for OBS-001 uses HIGH (master §4 scale) where the lane wrote CRITICAL (its domain scale); both are recorded in `A6-observability.md`.
- The Verifier sampled 10% of VERIFIED findings; the other 90% carry the lane's own label, unchecked by a second model.

## 6. Recommended next runs
1. Research session 5 (Security, data & infrastructure) — see the report's closing recommendation.
2. A full 42/45-loop replay-safety pass (`spinr-realtime-reliability-reviewer`) to close A4's UNKNOWNs.
3. A PIPEDA retention + insurance-period pass (`spinr-regulatory-compliance-checker` + `spinr-insurance-period-auditor`) to close A8's unreached scope.
