# Agent Framework (`agents/`)

Python SDK for multi-agent development automation. **Not part of the production runtime** — used for code review, testing, documentation, and deployment orchestration during development.

## Start here: the real, running pipeline

The files below (`orchestrator.py`, `registry.py`, etc.) are a **legacy scaffold** —
read `RESEARCH.md`'s "finding" section before using them: they never call a language
model, so running them produces Python objects moving between classes, not actual
engineering work. They're kept for their role/hierarchy structure, which the newer
design below still draws on.

The actual working pipeline, as of 2026-08-20, is:

- **`RESEARCH.md`** — why an external framework (LangGraph/CrewAI/AutoGen/MetaGPT/
  ChatDev/OpenHands) wasn't adopted, and what was adopted instead
- **`PIPELINE_DESIGN.md`** — the 10-stage ideation→operations graph, who owns each
  stage, and the three-document paper trail (`progress-report.md`, `decisions.md`,
  `challenges-and-issues.md`) every run produces
- **`GUARDRAILS.md`** — the standing can't-do list (hard stops the pipeline is
  physically unable to do; soft stops that need a human's go-ahead)
- **`roles/*.md`** — one plain-language file per department (matches the org chart in
  the "rideshare team roles" session): what it decides, what it needs, what it can
  never do. Each department also has an `agents/roles/<department>/*.md`
  subdirectory of individual role docs (e.g. `product-design-engineering/
  backend-engineer.md`) — 41 in total, one per role on the org chart — each linking
  back to its department doc for pipeline-stage ownership and the shared can't-do
  list, adding only what's specific to that individual role. The pipeline script
  reads department docs, not individual ones — individual docs are for humans (and
  future finer-grained pipeline work) browsing "what does this specific role do."
- **`pipeline.workflow.js`** — the actual Claude Code `Workflow` script that runs the
  pipeline, reusing the existing `spinr-*` review subagents as stage workers
- **`runs/<date>-<slug>/`** — one folder per completed pipeline run

## Legacy scaffold (pre-2026-08-20)

| Module | Class | Role |
|--------|-------|------|
| `base_agent.py` | `BaseAgent` | Abstract base: task queue, message bus, knowledge entries |
| `orchestrator.py` | `OrchestratorAgent` | Top-level coordinator: decomposes tasks, assigns to specialists |
| `registry.py` | `AgentRegistry` | Single entry-point: initialise all agents, submit tasks |
| `code_reviewer.py` | `CodeReviewerAgent` | Static analysis and best-practice checks |
| `tester.py` | `TestingAgent` | Test generation and coverage analysis |
| `security_agent.py` | `SecurityAgent` | Vulnerability scanning |
| `backend_agent.py` | `BackendAgent` | FastAPI / Supabase domain specialist |
| `frontend_agent.py` | `FrontendAgent` | React Native / Expo domain specialist |
| `deployer.py` | `DeploymentAgent` | CI/CD and Railway/EAS deployment tasks |
| `documenter.py` | `DocumentationAgent` | Doc generation and CLAUDE.md maintenance |
| `knowledge_base.py` | `KnowledgeBaseAgent` | Shared knowledge store for all agents |
| `cli.py` | — | CLI entry-point (`python -m agents.cli`) |
