# Pillar 8 — Autonomous Swarm Protocol

> How an autonomous engineering swarm operates on this codebase: the
> continuous inspect → brainstorm → debate → implement → adversarially test
> → fix → document loop, bound to Spinr's real machinery. Invoke a cycle
> with `/spinr-swarm`. This protocol is adapted from a generic ride-share
> swarm charter; every place the generic version conflicted with this
> repo's live-tested contracts is reconciled below — **the swarm enforces
> the repo's invariants, it never "corrects" them toward a generic clone.**

## Prime directive

**Understand the system before changing it.** This codebase is in live app
testing with real users. Never assume an existing implementation is wrong
because you would design it differently — this repo contains several
deliberate decisions that look wrong at first glance and are recorded
(the 3.5 s fare-estimate wait, attributable 7-year retention, the
dual-import pattern, unredacted data-transfer export). Check `docs/adr/`,
`docs/audit/*decision*`, and `CLAUDE.md` before concluding anything is a
bug. Prefer small, reversible, flagged, tested changes over rewrites;
a change is complete only after verification, never at "implementation
complete."

## The operating loop

Each cycle runs the Pillar 1 lifecycle with the swarm's adversarial
additions:

```
OBSERVE → UNDERSTAND → MAP DEPENDENCIES (blast radius) → IDENTIFY
→ BRAINSTORM (≥3 options) → DEBATE (specialist review) → RISK ANALYSIS
→ PRIORITIZE (backlog bands) → DESIGN → IMPLEMENT (≤3-file subtasks)
→ TEST → ADVERSARIAL ATTACK → REGRESSION → CRITIC REVIEW → FIX → RETEST
→ VALIDATE → DOCUMENT (Change Impact Log) → MEASURE → LEARN → NEXT
```

## Swarm roster — mapped to real agents

The swarm is not simulated personas; it is the repo's actual reviewer
fleet, dispatched via the `Agent` tool (`/review` routes by diff,
`/full-audit` dispatches everyone):

| Swarm role | Spinr implementation |
|---|---|
| Orchestrator | The main session: decomposes (≤3-file subtasks, `/plan` >5 files), sequences commits, resolves conflicts by evidence |
| System architect | Pillar 3 principles + `docs/adr/`; teardown style in `docs/reviews/` |
| Product strategist | Pillar 2 thesis + "is NOT" guardrail filter + KPI table |
| UX agent | `spinr-design-consistency-reviewer` (states/journeys) + rider/driver journey walks below |
| Accessibility agent | `spinr-accessibility-reviewer` (WCAG 2.1 AA floor; flags reasoned-not-screenshotted) |
| Security agent | `spinr-security-auditor` + `spinr-admin-rbac-reviewer` + `spinr-fraud-auditor` |
| Adversary agent | Adversarial-verify pass on every significant finding/proposal (see below) + `spinr-edge-case-reviewer` |
| QA agent | `spinr-test-coverage-reviewer` + Pillar 4 obligations |
| Performance agent | `spinr-performance-sla-reviewer` vs the P95 SLA table |
| Scale/chaos agent | **Not agent-shaped** — needs real tooling (ACTION_ITEMS E2 load/chaos, E7 restore drills); the swarm reasons about failure modes but never claims to have chaos-tested |
| Data/DB agent | `spinr-migration-reviewer` + repository-layer conventions |
| Dispatch domain agent | `spinr-dispatch-reviewer` + `spinr-insurance-period-auditor` |
| Payments agent | `spinr-money-auditor` + `spinr-corporate-billing-reviewer` + `spinr-surge-auditor` |
| Observability agent | `spinr-observability-reviewer` ("diagnosable at 3 AM without guessing") |
| AI/intelligence agent | `spinr-ai-guardrail-reviewer`; every AI feature needs objective, baseline, eval, failure modes, fallback, monitoring — never AI because AI is available |
| Compliance agent | `spinr-regulatory-compliance-checker` + `spinr-safety-sos-reviewer` (the generic charter lacks this seat; on a SK-regulated product it is mandatory) |

## Reconciliations — where the generic charter is overridden

1. **Ride state machine.** The canonical graph is `CLAUDE.md`'s:
   `scheduled → searching → driver_assigned → driver_accepted →
   driver_arrived → in_progress → completed`, cancellation pre-trip only,
   acceptance raced via the `{'status': 'searching'}` filter. Generic
   states (`REQUESTED`, `RIDER_PICKED_UP`, `PAYMENT_FAILED`, …) do not
   exist here; payment failure is settlement state, not ride state. The
   swarm verifies illegal transitions are rejected — against *this* graph.
2. **Priorities.** `ACTION_ITEMS.md` bands (P0 launch-gating … P4
   industry-parity) are the ranking; the charter's P5-cosmetic folds into
   P4. New findings land in the existing bands, not a parallel scheme.
3. **Reviewer fleet.** Simulated personas are replaced by the 22 real
   `spinr-*` agents; a swarm cycle that "simulates" a security review
   instead of dispatching `spinr-security-auditor` has not done one.
4. **Change policy & production safety.** The charter's change policy is
   Pillar 1 stage 8 + the pre-merge release gates (blast radius first,
   additive over destructive, flags via `app_settings`, rollback plan
   pre-merge, escalate when unsure). Destructive/consequential production
   actions stop for explicit human approval — matching the charter and
   already law here.
5. **Discovery mode.** "No specific task" → pick open `[ ]` items from
   `ACTION_ITEMS.md` before hunting for new findings; a backlog of 57
   ranked, verified items outranks fresh speculation. New sweeps use
   `/full-audit` + `audit-framework/` dimensions and file findings back
   into the backlog.

## Adopted additions (genuinely new, now house rules)

**Brainstorm protocol.** Significant problems get ≥3 options — A minimal /
B moderate / C strategic — scored 1–10 on user impact, complexity, risk,
performance, security, scalability, maintainability, accessibility, cost,
reversibility; chosen on evidence, in the `docs/audit/*decision-writeups*`
style. Ambiguity between options a user must arbitrate → `AskUserQuestion`.

**Internal debate.** Contested proposals run proposer → architect →
security → performance → UX → adversary → QA → orchestrator decision.
Disagreement resolves by stating assumptions, gathering evidence, and
testing competing hypotheses — never by preference or seniority of phrasing.

**Adversary pass.** Every significant proposal/finding gets a dedicated
attempt to break it: race conditions (two accepts, cancel-vs-accept),
partial failure (payment succeeded / trip update failed), 10×/100× load,
dependency outage (Supabase, Redis, Stripe, Directions), bad GPS, replayed
or malicious clients, force-closed apps, stale caches, out-of-order events.
Findings that survive adversarial verification are real; the rest die
before they cost a cycle.

**Epistemic labels.** Every claim in a swarm report is tagged **KNOWN**
(verified in code/test), **INFERRED** (reasoned, unverified), **TESTED**
(exercised this cycle), **UNTESTED**, or **UNKNOWN** — with confidence
HIGH/MEDIUM/LOW and remaining risks. This extends the Change Impact Log's
"What was NOT verified" into all swarm output. Banned phrases without
evidence: "definitely safe", "no vulnerabilities", "everything fixed",
"production ready".

**Value ranking within a band.**
`value ≈ (user × business × reliability × security impact × confidence) / effort`
— a tie-breaker *inside* an ACTION_ITEMS band, never an override of P0>P1>….

**Self-improvement bounds.** The swarm may improve its own heuristics,
tests, docs, and workflow. It may **not** autonomously weaken security
controls, approval gates, audit mechanisms, hooks, CI gates, credential
systems, or safety mechanisms — and never removes a safety control because
it slows development. (Gate *decay* is handled by filing a `[CR]`, not by
deletion.)

**Journey walks.** Each cycle touching UX walks the full journeys as a
user — rider: open → sign up → locate → destination → select → request →
match → track → ride → pay → rate; driver: sign up → verify → go online →
offer → accept → navigate → pick up → trip → complete → earnings — under
the Pillar 1 mobile-reality assumptions (slow networks, old devices,
interrupted GPS, background restrictions, permission changes).

## Cycle report format

Every swarm cycle reports: **Discovery · Impact · Root cause · Options
(A/B/C + scores) · Adversarial review · Decision · Implementation ·
Validation (commands + counts) · Results · Remaining risks (labeled) ·
Next recommendation.** Behavior-changing cycles additionally file the full
Change Impact Log (`docs/templates/CHANGE_IMPACT_LOG.md`).

## Golden rule

Never optimize blindly; never trust the first solution; never assume tests
are sufficient, users behave, external systems hold, or compiling means
complete. Observe → question → challenge → test → improve → verify. The
objective is not more code — it is a measurably safer, faster, more
reliable, more accessible, more scalable, and substantially better product,
bounded by evidence, permissions, and human approval for consequential
actions.
