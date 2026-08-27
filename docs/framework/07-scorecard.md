# Pillar 7 — Scorecard & Roadmap

> Where Spinr actually stands against this framework, scored from repo
> evidence on 2026-08-27, and the shortest path to "better than the
> incumbents" on each axis. Complements `docs/PRODUCTION_READINESS.md`
> (launch verdict) — this scorecard grades the *engineering system*, not
> just launch readiness. Re-score when the cited items close; the grades
> are claims about evidence, so they change only when the evidence does.

## Maturity scorecard

Scale: 1 = ad hoc · 2 = documented · 3 = enforced · 4 = enforced + measured
· 5 = enforced, measured, and exercised under failure.

| Pillar | Score | Evidence for | Held back by |
|---|---|---|---|
| 1. Delivery lifecycle | **4** | Gates, hooks, Change Impact Log, 751 change-log entries, 150+ closed backlog items | Automated PR review down (C7/C9); sprint context stale |
| 2. Product & requirements | **4** | "Is NOT" guardrails enforced in code; domain contracts; KPI table | Roadmap doc (.planning/ROADMAP.md) frozen at 2026-05; growth docs not yet wired to backlog cadence |
| 3. Architecture & platform | **4** | ADRs; state-machine, money, trust invariants all code-enforced; replay-safe loops | ADR-001 ID collision (two ADRs share the number); standby drift (C5) |
| 4. Quality engineering | **3.5** | Four green suites measured 2026-08-27 (backend 12,905 passed at 93.38% coverage; 3,405 frontend tests passed); coverage ratchets; 22-agent review fleet | Admin-dashboard coverage ~19%; zero active visual regression (B38); Maestro inert (B25); test-env green is decorative (CR-2026-008) |
| 5. Security, compliance, safety | **4** | Layered enforcement hook→CI→RLS→agents; threat models; honest incident write-up; retention/deletion decisions recorded | DAST inert; export dual-approval open (B11); MFA comms (C4); token-theft tripwire (C2) |
| 6. Operations & deployment | **3** | DNS-cutover design; 53 runbooks; metrics/alerting ADR-010; flag-without-redeploy | **Never-exercised fail-over (C1) + paused, drifting standby (C5)** — the design is a 5, the exercised reality a 2; 25 open operational P2s |

## How this compares to Uber/Lyft engineering — honestly

Incumbents win on scale-hardened infrastructure, dedicated SRE/on-call
depth, mature experimentation platforms, and real-device farms. Spinr
cannot and should not compete there at launch scale. Spinr's system is
ahead on four things that scale *badly* in big organizations:

1. **Traceability** — problem → gate → test → change-log → backlog is one
   unbroken chain in one repo; incumbents spread it across teams and tools.
2. **Honesty artifacts** — "What was NOT verified", the inert-gate ledger,
   and self-flagging stale docs are structural anti-overclaim devices most
   process stacks lack entirely.
3. **Regulation as code** — insurance periods, retention floors, tax lines,
   and surge caps are invariants with dedicated auditors, not a compliance
   department's spreadsheet.
4. **Reviewer density** — 22 domain auditors on every risky diff is a
   review bench no team of this size otherwise has.

The gap to close is singular: **exercised operational resilience**. Every
score of 3 above traces to things designed but never fired (fail-over
drill, DAST, Maestro, visual baselines, alert rules). World-class is not
more design — it is pulling these triggers.

## Roadmap: shortest path to world-class

**Now (launch-gating, from the open P0/P1 band):**
close the A40 fleet-audit blockers and A34 cutover decisions; land the
A28/A29 audit follow-ups; B11 export dual-approval; B25/B38/B39 to give the
frontends real E2E, visual, and schema-validation floors.

**Next (operational, the 25 P2s — human-with-dashboard work):**
run the C1 fail-over drill and un-pause Railway (C5); set the C2 tripwire;
finish the C3 env sweep and C4 MFA comms; restore one automated PR reviewer
(C9 or re-key C7) so the review fleet has an always-on backstop.

**Then (compounding advantages, P3/P4 band):**
coverage ratchets to their ceilings (B37); admin-dashboard coverage out of
the teens; visual baselines seeded and enforced; iOS Maestro lane; the
industry-parity P4 items in priority order.

**Standing rule:** this scorecard is re-graded after each band closes, and
a grade only moves on evidence (a merged PR, a drill log, a green formerly-
inert gate). Aspirations live in the roadmap; the scorecard is what is.
