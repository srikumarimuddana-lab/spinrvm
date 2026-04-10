---
description: 7-iteration strategic review where 4 rideshare industry experts review Spinr against Uber/Lyft/Bolt/Careem/DiDi standards
---

# Multi-Agent Strategic Review Workflow

## Trigger
```
/review
```
One command. 7 iterations. Full project analysis against world-class rideshare standards.

## What This Is
This is NOT a code review of recent commits. This is a **full strategic audit** of the entire Spinr platform compared against Uber, Lyft, Bolt, Careem, and DiDi. The 4 agents are rideshare industry experts who freely debate and form consensus on what Spinr needs.

## Agents

| Agent | Expertise | Reviews |
|-------|-----------|---------|
| **Product Manager** | Worked at Uber/Bolt, knows industry feature tables | Missing features, competitive gaps, roadmap priorities |
| **Senior Developer** | Built rideshare platforms, security expert | Architecture, security vulnerabilities, code quality, scalability |
| **QA Lead** | Tested rideshare apps at scale | Test strategy, untested production risks, quality gaps |
| **Business Analyst** | Mapped every UX flow in Uber/Lyft/Bolt/Careem/DiDi | User journeys, broken flows, edge cases, business logic |

## 7 Iterations

| # | Name | What Happens | Style |
|---|------|-------------|-------|
| 1 | **Full Project Scan** | Each agent scans the ENTIRE project from their expertise | 4 parallel |
| 2 | **Cross-Review** | Each agent reacts to all others' findings | 4 parallel |
| 3 | **Debate & Challenge** | Agents disagree, push back, argue priorities | 4 parallel |
| 4 | **Resolution** | Orchestrator resolves conflicts, forms problem statements | Sequential |
| 5 | **Solution Planning** | Each agent drafts solutions for their domain | 4 parallel |
| 6 | **Peer Validation** | Each agent validates another's plan | 4 parallel |
| 7 | **Final Consensus** | Unified report with scorecard, roadmap, action items | Final |

## Key Principles
- Agents compare against REAL competitor features (Uber, Lyft, Bolt, Careem, DiDi)
- Agents read ACTUAL source code, not just descriptions
- Agents freely disagree — no rubber-stamping
- Every finding becomes a problem statement with: impact, industry context, priority
- Safety and payment integrity always win priority debates
- For Spinr's 0% commission model, driver satisfaction is key

## Output
The final report includes:
- Spinr vs Industry scorecard (X/100 per category)
- Consensus problem statements (CRITICAL → LOW)
- Resolved debates with reasoning
- Phased feature roadmap
- Technical + test action items
- Mini feature specs
- Comparison with previous review (continuous improvement tracking)

## Continuous Improvement
Each `/review` reads previous reports from `agents/knowledge/reviews/` and tracks:
- Score trends over time
- Which problems were resolved
- Which keep recurring
- New gaps discovered
