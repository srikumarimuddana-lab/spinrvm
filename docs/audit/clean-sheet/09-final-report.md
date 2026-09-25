# Clean-sheet audit — final report (Step 7)

*25 September 2026. Orchestrator's report to the founder. Report only: nothing in code, settings or data was changed. Every claim below points to the file that carries its evidence.*

**Bottom line:** do not rebuild Spinr. The ride, fare and surge core is sound. The risk sits at the edges:
- who gets alerted when something goes wrong;
- what data leaves the system;
- which safety checks are switched on;
- who owns each piece.

Most fixes are decisions, settings or small code changes. The plain-language version is [`EXECUTIVE_SUMMARY.md`](EXECUTIVE_SUMMARY.md).

---

## 1. Top 10 findings

This is the "highest-priority rows" table from [`matrices/risk-register.md`](matrices/risk-register.md) §10. Scores are severity × blast radius × likelihood.

| # | Finding | Why it matters | Evidence |
|---|---|---|---|
| 1 | Service-role key rotation unproven (SEC-R10-001) | Full access to every table if the old key still works. Rotation takes about 15 minutes. | `02-findings/security.md` |
| 2 | No timeout on the Twilio SMS client (INT-001) | A slow vendor can stall every login. It is a two-line fix. | `02-findings/integrations.md` |
| 3 | No page fires for a real SOS (TSF-001) | This is the life-safety gap. The runbook describes a system that does not exist. | `02-findings/trust-safety-fraud.md` |
| 4 | Undisclosed Meta per-ride purchase events (STRAT-004) | Breaks the "no ad SDKs" promise. It can be stopped today by clearing a setting. | `02-findings/strategy.md` |
| 5 | LogRocket records iOS sessions unmasked (SEC-R10-003) | Personal data goes to an undisclosed US vendor. It can be paused in the vendor console. | `02-findings/security.md` |
| 6 | The merge gate is advisory (HIST-013/014, QUAL-001, QUAL-004) | Every other safeguard depends on it. A GitHub admin must read what `main` requires. | `00-history.md`, `02-findings/quality.md` |
| 7 | Negative margin per ride, and the revenue model in the documents does not match the code (STRAT-001/002/003/008) | The business loses money on each consumer ride. These are decisions, not code. | `02-findings/strategy.md` |
| 8 | Errors silently swallowed, as a recurring family (HIST-004) | It has already lost 53 of 55 payment-failure events once. A static rule stops the whole class. | `00-history.md` |
| 9 | Background loops: every server runs all 45; thresholds are wrong; one alert channel (REL-001/002/003) | This is a settings split plus a thresholds test. Confirm the webhook is set. | `02-findings/reliability.md` |
| 10 | Database changes applied by hand, with no owner (HIST-006, CARTO-005) | The schema and the code drift apart. | `00-history.md`, `01-inventory/epics.md` |

Just below the top 10 are three groups.
- **Money correctness:**
  - float arithmetic on the fare path and float `rides` money columns (MONEY-006);
  - re-priced rides that keep the booking-time tax (MONEY-007);
  - wallet chargebacks that reverse nothing (MONEY-009);
  - the corporate wind-down double-debit (CORP-001).
- **Rider and driver fairness:**
  - no rider dispute path (RIDERJ-003);
  - an undisclosed acceptance-rate penalty (DRIVER-004);
  - suspended drivers are not told why (DRIVER-001).
- **Accessibility:** text that can't scale and low-contrast colours on money figures (UXA11Y-001/002).

## 2. Top 5 rebuild deltas

These are ranked in the order that survived the hostile review ([`08-hostile-review.md`](08-hostile-review.md) §6). The architecture detail is in [`04-blueprint.md`](04-blueprint.md).

**Before any of them:**
- decide who gets the SOS page;
- prove the key rotation;
- decide on the Meta data flow;
- write the pricing memo;
- answer what the merge gate requires;
- list which database changes are applied in production.

1. **A money write guard that covers the writers that matter.** First, list every place that writes money. Rides (`repositories/ride_repo.py:260,276`) and payouts (`services/stripe_payout_sync_service.py:457`) write directly today, so they go behind the guard first. It ships with the cheap live money fixes (CORP-001, MONEY-006/-008, MONEY-001).
2. **One way to apply database changes, and one job per server.** Promote the existing worker split from `backend/fly.worker-canary.toml` to the real config.
3. **Small shared building blocks, each with an automatic test.** Examples: the SMS timeout, stripping precise locations from pushes (SKB-001), loop thresholds, the audit request id, and an API drift check.
4. **Fraud and money controls before new ways to move money.** An admin refund cap and an instant-payout velocity cap come first. The rider dispute button and the driver cash-out screen come after.
5. **Shared mobile building blocks kept in step by a CI check and contract tests.** This includes shared Text, Money and Button pieces, which fix the font-scaling and contrast gaps once for both apps.

A ride-status history log is added only where code is already being touched. It is the sixth item, not a rewrite.

**Rejected on purpose:**
- the ledger as the only money writer, for now;
- idempotency keys on all 373 endpoints;
- a paid feature-flag vendor;
- generated API clients, for now;
- switching token signing to ES256 now;
- event-sourcing ride state.

## 3. Escalations that need your decision

The full list, with options and who can answer each, is in [`05-escalations.md`](05-escalations.md). Grouped by who decides:
- **You (founder):** key rotation; SOS paging target; the Meta and LogRocket flows; pricing and revenue model; owners for each area; the money-approval threshold; whether to disclose the dispatch ranking to drivers (DRIVER-004).
- **An accountant:**
  - does Saskatchewan PST apply to fares? This has been open since August, and a yes means a growing shortfall;
  - the supplier of record and the GST number on receipts;
  - platform-operator reporting;
  - the T4A boxes and threshold;
  - the small-supplier rule that blocks first payouts.
- **SGI, cities, counsel and your broker:**
  - Class 4 or Class 5 licence (COMP-001);
  - municipal ride-share licences and fees;
  - the SGI monthly kilometre report;
  - the Period 1 reading for an unreachable driver;
  - the accessibility legal basis (the docs cite Ontario's AODA);
  - company insurance.
- **Data residency:** a `render.yaml` in a US region is committed at the repo root. Whether a live Render service exists is unknown.
- **Recorded, not re-opened:** the 2026-09-12 driver-data incident is owner-managed, closed on your statement of 24 September, and not independently checked.

## 4. What was not verified

- **Live state.** No production data, flag values, secrets or vendor consoles were read. Unknowns include:
  - whether the key was rotated;
  - whether the alert webhook is set;
  - the backup tier;
  - whether Render is live;
  - which migrations are applied;
  - what branch protection requires.
- **Execution.** No test suite run, no load test and no Semgrep run. The only execution was a test-collection count.
- **The rider and driver apps.** They have no screenshot tooling, so every app claim is reasoned from code.
- **Tax, legal and regulatory.** Government sites were unreachable, so every such claim is labelled assumed.
- **Competitors.** Uber and Lyft claims come from their public help pages. The local taxi comparison is assumed.
- **Absence claims.** Four core claims were refuted, all "this does not exist" claims broader than the search behind them:
  - QUAL-003: a missing test;
  - TSF-010 / BENCH-001: a missing trip PIN (a pickup code exists);
  - the navigation matrix row (the driver map handoff exists);
  - the blueprint's claim that every money writer goes through one layer.

**Error rates:**

| Sample | Result |
|---|---|
| Independent re-check, 16 cards | 0 refuted, 3 partly wrong |
| All checks combined | 4 refuted core claims |

Treat any remaining "X does not exist" as likely, not certain. See [`07-reverification.md`](07-reverification.md).

## 5. Three next items that can run in parallel without touching the same files

From [`ROADMAP.md`](ROADMAP.md) Now:
1. **Add a timeout to the Twilio client (N8).** Touches `backend/sms_service.py` (client built at line 42) and its test. Recommended first: it is the smallest change that removes a login-wide outage.
2. **Fix the float money writes (N19).** Touches the money files named in MONEY-006/-008 and the Semgrep allowlist.
3. **Fix the loop-watchdog thresholds and the T4A heartbeat (N17).** Touches `backend/utils/loop_monitor.py` and `backend/utils/t4a_annual_job.py`.

Each needs its own branch, test and Change Impact Log entry under CLAUDE.md. The console-only items in parallel with these are key rotation, the LogRocket pause, the Meta setting and the branch-protection read. They touch no files.

## 6. If Uber or Lyft rebuilt from scratch tomorrow, what would a Spinr-native platform do differently?

- **Obvious** (table stakes they would ship on day one; Spinr lacks or half-ships them):
  - a real SOS paging and triage service;
  - a rider "dispute this ride" path;
  - instant driver cash-out with a velocity cap;
  - basic fraud signals for collusion, cancellation-fee farming and chargeback velocity;
  - receipts that carry the tax registration number.

  A pickup verification code already exists; only the rider-checks-driver direction is missing.
- **Meaningful** (where Spinr can beat them because it is small, local and honest):
  - **The fare.** 0% commission, with any booking fee shown as its own honest line; a hard 2.5× surge cap shown before booking.
  - **Compliance.** An append-only insurance-period trail and Saskatchewan eligibility checks built in as code, switched on, not left dark.
  - **Corporate.** Allowances that fit local employers.
- **Difficult** (right, but hard on a live product):
  - one path for every ride-status change across about 13 write sites;
  - a typed money boundary over float columns while money is moving;
  - one shared mobile core for two apps that already drift;
  - schema changes applied one way.

  Each needs a dry run, a feature flag and a real rollback, not a rewrite.
- **Novel** (not standard practice at the incumbents):
  - **Tell drivers how ranking works.** Disclose to drivers that declining offers lowers their priority, instead of hiding it.
  - **Publish a fairness audit.** Publish a neighbourhood fairness audit of dispatch, ETA and price before expanding past Saskatchewan (`03-benchmark.md` §6).
  - **Turn repeat bugs into tests.** Turn every recurring bug family into an automatic test, the operating model for an AI-assisted team.
  - **Design for rural winters.** Design trip completion for rural winter connectivity loss.
- **Should not build** (things Spinr should refuse to copy):
  - unbounded or hidden surge;
  - commission on consumer rides;
  - ad SDKs and behavioural retargeting (the Meta events already drift this way);
  - hidden acceptance-rate penalties or other control-of-work pressure;
  - "every endpoint" idempotency;
  - a paid flag vendor;
  - generated clients or ES256 before the basics hold;
  - event-sourced ride state;
  - timestamp migration prefixes;
  - three more reviewer agents instead of extending the existing ones.

## 7. Which research session to run first

**Session 3: Trust, safety & fraud** (`docs/audit/clean-sheet-prompt/research-sessions.md` §4). It goes first for three reasons:
- **It is the weakest feature.** Fraud detection is absent across 16 of 18 dimensions in [`matrices/feature-completeness.md`](matrices/feature-completeness.md).
- **It holds up the money work.** The roadmap puts the rider dispute button and the cash-out screen behind fraud controls.
- **It is where the life-safety gap sits.** How dispatchers triage SOS is in its scope.

Session 2 (pricing, payments and tax) matters as much. But its hardest questions need an accountant's answer, not research, and that is already escalated.

## 8. Scope accounting: what was done and what was not

**Done:**
- W0: history and inventory.
- W1 to W3: 15 lane reports.
- W4: benchmark and blueprint.
- W5: executive summary, roadmap, escalations, operating model, risk register and ownership.
- Tier A matrices: threat model, data classification, operational readiness, feature completeness and edge cases.
- Step 6, both halves: the 16-card independent re-check and the hostile review.
- This report.

**Not done:**
- **Tier B matrices:** integration, API inventory, event/WebSocket inventory, agent permissions, dependency graph, test coverage, docs coverage, compliance, incident and tech-debt register.
- **Traceability:** reconciling the 769 orphan rows in `traceability.csv`.
- **Live checks:** any check against production.
- **The five research sessions.**
- **Blueprint corrections:** if its hand-back is still pending, its corrections changelog is incomplete; the roadmap and this report already use the corrected order.
