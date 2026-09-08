# Agentic tooling landscape — pilot decisions (2026-09-08)

Full survey (37 public Claude Code plugin/hook/agent repos + agentic-automation
tools, scored for value/risk against Spinr's actual surfaces) was published as
an Artifact rather than checked into the repo, since it's a living reference
rather than a decision that needs versioning: `Agentic Tooling Atlas` (see the
session that produced it for the link, or ask for it to be regenerated — it
is not a repo file). This doc exists only to record the two items promoted
out of that survey into an active pilot, so the decision and its guardrails
don't live solely in chat history.

## Piloting: Schemathesis (property-based API fuzz testing)

- **What**: `backend/tests/test_schemathesis_fuzz.py`, scoped to **GET-only**
  operations on the FastAPI app's own OpenAPI schema via
  `schemathesis.openapi.from_asgi()`. State-mutating verbs (POST/PUT/PATCH/
  DELETE) are deliberately excluded from this first pass — fuzzing payment,
  ride-state, or auth-mutating endpoints with arbitrary generated payloads
  against a mocked Supabase client would produce noise unrelated to real
  bugs (the mock doesn't enforce the same invariants a real Postgres+RLS
  backend would) and risks masking or triggering the wrong kind of failure.
  Widening to write endpoints is a deliberate future step, not an oversight —
  it needs its own review of which routes are safe to fuzz destructively.
- **Why**: FastAPI auto-generates the OpenAPI schema already used by
  `backend/tests/conftest.py`'s `test_client` fixture — near-zero setup, and
  the `patch_external_dependencies` autouse fixture already mocks Supabase/
  Firebase/SMS for every test, so fuzzing runs safely in-process with no
  network egress and no real external side effects.
- **Marked `@pytest.mark.slow`, and explicitly `--ignore`d from CI's main
  `pytest` step (`ci.yml`)** — not just from the fast local loop. The GET
  surface turned out to be 532 operations after excluding `/deploy-info`
  (see below); at up to 10 fuzzed examples each, that's real added CI
  runtime for every future PR, which isn't something a first pilot should
  impose without a separate, explicit decision to wire it in as its own
  step. Run it on demand: `pytest -m slow backend/tests/test_schemathesis_fuzz.py`.
- **Dependency**: `schemathesis>=4.26.0` added to `backend/requirements.in`.
  `requirements.txt`/`requirements-locked.txt` were regenerated via
  `pip-compile` in the same PR — see that PR's Tier 4 Verification for
  exactly what was (and wasn't) checked.
- **Findings from running the pilot locally (2026-09-08)**: collection found
  533 GET operations (the routers mount several paths under multiple
  prefixes — bare, `/api/...`, and `/api/v1/...` — for backward
  compatibility, hence the count exceeding the number of logically distinct
  endpoints). Two false positives found and fixed, both environment/tooling
  artifacts rather than app bugs:
  1. `GET /deploy-info` legitimately returns 503 "deploy-info endpoint not
     configured" whenever deploy metadata isn't present, true for every
     test/dev environment — excluded from the schema
     (`.exclude(path="/deploy-info")`), leaving 532.
  2. A payment/subscription checkout-return endpoint legitimately 302s to a
     custom mobile deep-link scheme (`spinr-driver://...`), which the
     underlying `requests` session can't follow
     (`InvalidSchema: No connection adapters were found for
     'spinr-driver://...'`) — fixed generally via `allow_redirects=False`
     on the call, rather than excluding that one endpoint, since validating
     the redirect response itself is exactly what `not_a_server_error`
     needs anyway.

  **Full run after both fixes: 532 passed, 0 failed, ~4.5 minutes.**
  No genuine application bug surfaced in this first pass — the value here is
  the harness itself now existing and running clean, not a bug count.
- **Known noisy side effect**: Schemathesis's ASGI transport re-runs the
  app's full lifespan (startup+shutdown, all 41 background loops, the AI
  MCP session manager) around each fuzzed call rather than once for the
  whole run. Repeated cycles in one process trip
  `StreamableHTTPSessionManager`'s run-once guard in
  `backend/ai/mcp_server.py`, logged as an ERROR ("can only be called once
  per instance"). Confirmed caught and logged, not raised — it doesn't fail
  any test, just pollutes logs. Not addressed in this pilot; documented in
  the test file's own docstring so it isn't mistaken for a real bug later.

## Piloting: Sentry MCP (pull-only)

- **What**: no new code — `.mcp.json`'s existing `sentry` entry (unscoped
  base URL, already declared before this pilot) is approved for pull-only
  use: reading issues/traces/breadcrumbs to speed up debugging against
  Sentry events this repo already tags by `domain`/`surface`/`ride_id`/
  `driver_id`/`rider_id`.
- **Guardrail**: Sentry's Seer (or any Sentry-suggested fix) is never
  applied or merged without the same human review and Change Impact Log
  gate as any other change — see CLAUDE.md's "PR review handling (Codex
  auto-review)" section, which this pilot is bound by identically.
- **Still blocked on**: (1) a human completing the interactive OAuth login
  (`claude mcp add --transport http sentry https://mcp.sentry.dev`, or the
  claude.ai connector settings equivalent) — no Claude Code session can do
  this on the user's behalf, interactive or not; (2) the org/project slug,
  needed to narrow the URL from its current unscoped base per
  `.claude/context/connector-scoping.md`'s Sentry row. Both remain open;
  this pilot approval only sets the *usage* guardrail ahead of time so
  there's no ambiguity once access exists.

## Not part of this pilot

Everything else in the Atlas (Supabase MCP, Semgrep MCP, Chromatic, document
OCR vendors, Novu, CodeRabbit, etc.) stays at "shortlisted" status — no
action taken. Promoting another item follows the same pattern: a pilot
guardrail here (or its own audit doc, for anything code-shaped) plus an
explicit ask before anything touches `.mcp.json`, `requirements.in`, or a
live-tested code path.
