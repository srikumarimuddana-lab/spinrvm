# Research: how to run Spinr's SDLC through AI agents, end to end

**Question asked:** what's the smart, validated way to have AI agents carry a piece of
work through the whole lifecycle — ideation, requirements, plan, design, architecture,
development, QA, security, release, change management, operations — instead of a human
doing each handoff by hand? What does the wider ecosystem already use, and is any of it
legit enough to build on?

**Answer, up front:** don't adopt an external multi-agent framework. Use the
orchestration engine already wired into this environment (Claude Code's `Workflow`
tool), because it already has the two things every external option is missing for
Spinr specifically — real tool access to this repo, and awareness of the CLAUDE.md
gates. Full reasoning below.

## What exists out there (checked August 2026)

| Project | What it is | Legitimacy signal | Why it's not the right fit here |
|---|---|---|---|
| **LangGraph** (LangChain) | Graph/state-machine engine for agent workflows — nodes can loop, not just flow forward | ~33.9k GitHub stars, 34.5M monthly downloads, reached GA (1.0) Oct 2025 | Real, well-run project — but it's a library you wire an LLM client into yourself. We'd be rebuilding tool access, repo permissions, and the CLAUDE.md gate logic from scratch on top of it. |
| **CrewAI** | "Role + goal" agents grouped into a crew that delegates between members | ~52.8k stars, 5.2M monthly downloads | Good model for *describing* roles (we borrow this idea below) but it's a coordination pattern, not something that already knows how to open a PR against this repo or run `pytest`. |
| **AutoGen** (Microsoft) | Multi-agent conversation framework | ~58.7k stars — but **merged into Microsoft Agent Framework in Oct 2025 and is now in maintenance mode** (bug fixes only) | Picking a framework the vendor has already put in maintenance mode is a bad bet for a system meant to run indefinitely. |
| **MetaGPT** | Simulates a whole software company (PM, architect, engineer roles) from one requirement line | Real, active, well-cited research project | Built to generate a *new* small app from scratch in one shot. Doesn't understand an existing 5-surface repo with its own conventions, migrations, and live users — it would generate code, not integrate with ours. |
| **ChatDev** | "Virtual software company," agents role-play CEO/CTO/programmer/tester through fixed phases | Real, active (ChatDev 2.0 shipped 2026), OpenBMB-backed | Same shape as MetaGPT: great as a research artifact demonstrating the idea we're being asked for, not something we should point at a production codebase with live paying users. |
| **OpenHands** (formerly OpenDevin) | Full coding agent platform that actually executes engineering work | ~70k stars, funded (Series A), contributors from AMD/Apple/Google/Amazon/Netflix/NVIDIA, 72% SWE-bench Verified with Claude Sonnet 4.5 | The most legitimate of the "autonomous engineer" projects — but it's a *separate platform* we'd have to run, authenticate, and re-teach every one of Spinr's conventions to. We already have an equivalent (Claude Code) running inside this repo today. |
| **SWE-agent** (Princeton) | Structured "agent-computer interface" for resolving one GitHub issue at a time | Research-grade, credible (Princeton NLP), narrow single-issue scope | Solves one narrower problem (fix one issue) than what's being asked (run the whole lifecycle, with a team of roles, repeatedly). |

The common failure mode across every one of these: they are all **single-session
tools** — good at "take one instruction, produce one output, stop." What Spinr is
actually asking for is a **repeatable pipeline with named roles, a paper trail, and
release gates that must not be skipped** — closer to a CI/CD pipeline than a chatbot.

## The graph/state-machine idea is right — just build it on what's already here

LangGraph's core insight — model the work as a graph of stages, where a stage can hand
off, branch, or loop back instead of marching strictly forward — is the correct mental
model for an SDLC pipeline (a security finding *should* be able to send work back to
the development stage, not just fail forward). Claude Code's `Workflow` tool already
gives us that: `pipeline()`/`parallel()` compose stages, and a stage can be re-entered
by looping in plain JavaScript. We don't need a second graph engine to get graph
semantics.

**What Claude Code already provides that every external framework is missing for us
specifically:**
- Real file read/write, git, and GitHub access to *this* repository, already scoped and
  permissioned.
- 25 existing domain-specialist subagents (`spinr-security-auditor`,
  `spinr-migration-reviewer`, `spinr-money-auditor`, etc.) already tuned to Spinr's
  rules — reusable directly as pipeline stages instead of rewritten from scratch.
- The CLAUDE.md gates (Change Impact Log, blast-radius check, feature-flag rule, test
  coverage minimums) are already how *every* piece of work in this repo is judged. An
  external framework has no notion of any of this and would need it all rebuilt.
- No new hosted service, API key, or vendor dependency to operate and secure.

**What we are borrowing from the research, not inventing ourselves:**
- **CrewAI's role framing** (a named role with a goal, not a generic "agent #3") — used
  for the role `.md` files in `agents/roles/`.
- **ChatDev/MetaGPT's phase structure** (ideation → requirements → design → build → QA
  → release) — used as the pipeline's phase list in `PIPELINE_DESIGN.md`.
- **LangGraph's cyclic-graph principle** (a stage can send work backward, not just
  forward) — used for the "findings send work back to Development" loop in the
  pipeline design.
- **Anthropic's own published multi-agent guidance** (hierarchical delegation with
  capability narrowing; a subagent gets only the tools its stage needs, never
  everything the orchestrator has) — applied directly, since we're building on
  Anthropic's own SDK.

## Decision

Build the pipeline as a Claude Code `Workflow` script that lives in this repo,
reuses the existing `spinr-*` subagents as stage workers, and is gated by the same
CLAUDE.md rules as human-written PRs. Treat the legacy `agents/*.py` scaffold (see
below) as superseded rather than extended.

## A finding worth flagging: the existing `agents/` folder doesn't actually run

Before building anything new, this framework's existing Python code
(`agents/orchestrator.py`, `agents/backend_agent.py`, etc.) was read end to end. It
**never calls a language model.** `OrchestratorAgent._decompose_task` and
`_find_best_agent` are rule-based Python — a fixed lookup table mapping task-type
strings to agent-type strings — and every "agent" class is a message-passing shell
around that table. It's a well-organized simulation of what a multi-agent system's
plumbing looks like, not a working one. `agents/CLAUDE.md` already says as much
("not part of the production runtime"), but it doesn't say the deeper thing: as
written, running it end to end produces task objects moving between Python classes
and no actual engineering work. That's why this plan doesn't extend it — extending
dead plumbing would mean debugging a simulation instead of doing the work asked for.
The role definitions and phase structure it encodes (the table in `agents/CLAUDE.md`,
the hierarchy levels) are still useful as a rough draft and are referenced, not
discarded, in `PIPELINE_DESIGN.md`.

## Sources

- [LangGraph — LangChain resource comparison](https://www.langchain.com/resources/ai-agent-frameworks)
- [Best open source AI agent frameworks 2026 — Firecrawl](https://www.firecrawl.dev/blog/best-open-source-agent-frameworks)
- [AutoGen → Microsoft Agent Framework merger, maintenance mode](https://www.ayautomate.com/blog/best-multi-agent-frameworks)
- [MetaGPT — GitHub (FoundationAgents)](https://github.com/FoundationAgents/MetaGPT)
- [ChatDev 2.0 — GitHub (OpenBMB)](https://github.com/openbmb/ChatDev)
- [OpenHands — GitHub, funding, SWE-bench score](https://github.com/OpenHands/openhands)
- [OpenHands vs SWE-Agent comparison, 2026](https://localaimaster.com/blog/openhands-vs-swe-agent)
- [Anthropic — multi-agent orchestration best practices discussion](https://github.com/anthropics/anthropic-sdk-python/discussions/1313)
- [Anthropic — effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Anthropic — how we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Graph-based agent workflow orchestration, 2026 landscape — Zylos Research](https://zylos.ai/research/2026-04-14-graph-based-agent-workflow-orchestration-production/)
