# Agent Framework (`agents/`)

Python SDK for multi-agent development automation. **Not part of the production runtime** — used for code review, testing, documentation, and deployment orchestration during development.

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
