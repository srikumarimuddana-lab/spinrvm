"""Schemathesis pilot: property-based fuzz testing against Spinr's own OpenAPI schema.

Pilot scope — GET-only, see docs/audit/2026-09-08-agentic-tooling-atlas.md for
the full rationale:

- Only GET operations are fuzzed. State-mutating verbs (POST/PUT/PATCH/DELETE)
  are deliberately excluded from this first pass — fuzzing payment, ride-state,
  or auth-mutating endpoints with arbitrary generated payloads against a mocked
  Supabase client would produce noise unrelated to real bugs (the mock doesn't
  enforce the invariants a real Postgres+RLS backend would) and risks masking
  or triggering the wrong kind of failure. Widening to write endpoints is a
  deliberate future step, not an oversight.
- Only ``not_a_server_error`` is checked (any 5xx response), not full response
  schema conformance. Most routes here don't document 401/403/404 in their
  OpenAPI response models, so full conformance checking would fail on those
  undocumented-but-correct auth/not-found responses — noise, not a real
  finding. An unhandled exception surfacing as a 500 on adversarial input is
  the concrete edge-case class this pilot exists to catch.

Runs against the app in-process via ASGI transport — no network egress, and
`patch_external_dependencies` (autouse in conftest.py) already mocks
Supabase/Firebase/SMS for every test in this suite, so no real external
service is touched.

Known noisy-but-harmless side effect: Schemathesis's ASGI transport runs the
app's full lifespan (startup + shutdown, including all 41 background loops
and the AI MCP session manager) around each fuzzed call, rather than once for
the whole run the way `conftest.py`'s `test_client` fixture does. Repeated
lifespan cycles in one process trip `StreamableHTTPSessionManager`'s
run-once guard in `backend/ai/mcp_server.py`, logged as an ERROR
("Failed to start MCP session manager: ... can only be called once per
instance"). This is caught and logged, not raised — it does not fail any
test — but expect to see it in this file's test output. Not addressed in
this pilot; a shared-lifespan runner is a separate improvement.
"""

import pytest
from hypothesis import settings

schemathesis = pytest.importorskip(
    "schemathesis",
    reason="schemathesis is an optional pilot dependency (backend/requirements.in) — "
    "run `pip install -r backend/requirements.txt` to pick it up",
)

from backend.server import app  # noqa: E402 — import after importorskip guard

# /deploy-info excluded: it legitimately returns 503 "deploy-info endpoint not
# configured" whenever deploy metadata isn't present (true for every test/dev
# environment) — a real, intentional response for this environment, not a bug
# `not_a_server_error` should flag. Confirmed by running the pilot locally
# 2026-09-08 before this exclusion was added.
schema = schemathesis.openapi.from_asgi("/openapi.json", app).include(method="GET").exclude(path="/deploy-info")

# Capped well below Schemathesis's 100-example-per-operation default: this is a
# pilot on a suite with dozens of GET operations and a 30s per-test timeout
# (ci.yml), and `deadline=None` avoids Hypothesis flagging a cold FastAPI
# dependency-injection path as "too slow" — noise unrelated to a real bug.
_PILOT_SETTINGS = settings(max_examples=10, deadline=None)


@pytest.mark.slow
@schema.parametrize()
@_PILOT_SETTINGS
def test_get_endpoints_do_not_5xx(case: "schemathesis.Case") -> None:
    """No GET endpoint should return a 5xx for any schema-valid or adversarial input.

    ``allow_redirects=False``: some GET endpoints (e.g. payment/subscription
    checkout-return routes) legitimately 302 to a custom mobile deep-link
    scheme (``spinr-driver://...``), which the underlying `requests` session
    can't follow (`InvalidSchema: No connection adapters were found for
    'spinr-driver://...'`) — confirmed by running the full pilot locally
    2026-09-08. Not a bug; validating the redirect response itself (not
    where it points) is exactly what `not_a_server_error` needs anyway.
    """
    case.call_and_validate(checks=[schemathesis.checks.not_a_server_error], allow_redirects=False)
