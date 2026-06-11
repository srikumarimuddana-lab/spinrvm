"""AI assistant package (rider/driver AI mode).

Modules:
- pii: PII scrubbing applied to every user message before it reaches any
  third-party LLM provider and before persistence (PIPEDA).
- tools: read-only + booking-proposal tool registry (single source of truth
  for the in-process chat loop and the /mcp mount).
- providers: swappable LLM provider adapters (anthropic/openai/gemini/openrouter).
- orchestrator: provider-agnostic chat turn loop emitting SSE frames.
- conversations: ai_conversations / ai_messages persistence.
- mcp_server: optional streamable-HTTP MCP mount (ships dark).

Configuration lives in the app_settings row (see AppSettings ai_* fields);
the feature is disabled unless ai_assistant_enabled is set by an admin.
"""
