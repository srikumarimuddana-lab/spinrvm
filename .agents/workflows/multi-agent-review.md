---
description: 7-iteration multi-agent review workflow where Manager, Coder, Tester, and Business Analyst review the Spinr codebase and cross-check each other
---

# Multi-Agent Review Workflow

## Trigger
```
/review
```
Single command. All 7 iterations run automatically.

## Agents

| Agent | Role File | What They Review |
|-------|-----------|-----------------|
| **Manager** | `.agents/roles/manager.md` | Project health, priorities, cross-module coordination |
| **Coder** | `.agents/standards/coding-standards.md` | Code quality, security, patterns, DRY |
| **Tester** | `.agents/roles/qa-engineer.md` | Test coverage, test quality, missing tests |
| **Business Analyst** | `.agents/roles/business-analyst.md` | Feature completeness, user flows, business logic |

## 7 Iterations

### Iteration 1 — Individual Reviews
Each agent independently reviews the codebase from their perspective. They read actual source files and produce structured findings.

### Iteration 2 — Cross-Review
Each agent reviews another agent's findings:
- Manager reviews Coder + Tester + BA → reprioritizes
- Coder reviews Tester → assesses test gap severity
- Tester reviews Coder → identifies what needs testing
- BA reviews Manager → assesses business impact

### Iteration 3 — Debate & Challenge
Agents push back on each other:
- Tester defends recommended tests against Coder's pushback
- Coder argues for alternatives to testing (types, linting)
- BA argues for business priority changes
- Manager pushes back on scope creep

### Iteration 4 — Resolution
Manager (orchestrator) resolves all disagreements using priority rules:
1. Safety > velocity
2. User impact > admin panel
3. Revenue path > settings
4. Test coverage > "too simple to test"
5. Pragmatism > perfection

### Iteration 5 — Action Plan Drafts
Each agent writes their specific action items:
- Coder: files to modify, changes to make
- Tester: tests to write, what they verify
- BA: requirements to clarify, specs to write
- Manager: sprint priorities, dependencies

### Iteration 6 — Peer Validation
Each agent validates another's plan:
- Tester validates Coder's fixes (will tests pass?)
- Coder validates Tester's plan (technically feasible?)
- Manager validates BA's requirements (aligned with roadmap?)
- BA validates Manager's priorities (customer impact correct?)

### Iteration 7 — Final Consensus
Unified report produced, saved to `agents/knowledge/reviews/YYYY-MM-DD.md`.

## Continuous Improvement
Each review reads the previous report from `agents/knowledge/reviews/` and tracks:
- Were Critical issues resolved?
- Did test coverage improve?
- Are there recurring patterns?
- Feature completeness score trend

## Output
The final report includes:
- Executive summary with project health color (GREEN/YELLOW/RED)
- Consensus findings sorted by priority
- Resolved debates with reasoning
- Action items per role with complexity estimates
- Sprint priorities with dependencies
- Metrics dashboard
- Next review recommendations
