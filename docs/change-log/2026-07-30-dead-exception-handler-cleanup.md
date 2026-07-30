# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | User report: Data Transfer export → "Internal Server Error" |

## 1. Issue / gap identified

Investigating a user report ("select all users, check all doc-type boxes, provide a reason, click Export → generic 'Export failed / Internal Server Error'") surfaced a real but separate defect: `core/middleware.py`'s `init_middleware(app)` registered `cors_exception_handler` for the base `Exception` class, but `utils/error_handling.py`'s `register_exception_handlers(app)` — called afterward in `server.py` — registers `general_exception_handler` for the *same* `Exception` class. Starlette's exception-handler registry is a dict keyed by exception type: the second registration silently replaces the first. `cors_exception_handler`'s unhandled-exception branch was therefore dead code — it could never execute — despite reading as the app's last line of defense for unhandled exceptions.

## 2. Root cause

Two independent modules each assumed they owned the catch-all `Exception` registration, and nothing enforced or asserted single ownership. `cors_exception_handler` predates `general_exception_handler`; when the latter was added (with proper `traceback.format_exc()` logging and CORS headers via `_cors_headers_for`), the former was never removed.

## 3. Fix / remediation

Removed the dead `app.add_exception_handler(Exception, cors_exception_handler)` registration and its now-misleading "last line of defense" comment, replacing it with a comment explaining precedence and pointing at `general_exception_handler` (which already provides equivalent traceback logging and CORS headers — confirmed by reading both implementations side by side). `cors_exception_handler` itself is left in place only because `tests/test_p1_cors.py` exercises its header logic directly via a standalone closure, not through the live app.

**This is a dead-code cleanup, not a fix for the reported export bug.** Tracing the synchronous request path in `routes/admin/data_transfer_export.py` (validation → `MAX_ENTITIES_PER_EXPORT=100` cap → dual-approval gate, confirmed off in production → job insert) found no code path that would throw before a `data_transfer_export_jobs` row is written, and a direct production query confirmed zero rows in that table, ever, for anyone — so the export has apparently never completed a job insert. Because `general_exception_handler` already logs a full traceback on any exception raised inside a route/dependency, and a genuinely unhandled exception there would still produce a JSON body (not literal plaintext), the client seeing bare "Internal Server Error" text is more consistent with an exception escaping *outside* FastAPI's exception-handling layer entirely (e.g. from one of the `BaseHTTPMiddleware` subclasses in this file — `FirebaseAppCheckMiddleware`, `RequestIDMiddleware`, `SecurityHeadersMiddleware`, `RelativeRedirectMiddleware`, `DeadlineMiddleware`, `CSRFMiddleware` — none of which is wrapped by the registered exception handlers, since Starlette's built-in `ExceptionMiddleware` sits *inside* the `add_middleware` stack). Static review of each of those middlewares' `dispatch()` did not find an obvious throw site specific to this route. **Pinpointing the exact throw site requires the real production traceback** (Sentry, tagged `domain=admin`, path `/api/admin/data-transfer/export`, around the reported timestamp) — not available from this environment — and is the next step, not resolved by this commit.

## 4. Risk & impact on existing functionality

- **Blast radius: single function, one line removed.** Grepped for `cors_exception_handler` across the backend: only `core/middleware.py` (definition + the removed registration) and `tests/test_p1_cors.py` (an isolated unit test that redefines the handler logic in its own closure and does not import or exercise the live app registration). No other caller.
- Since the removed registration was provably unreachable (confirmed via Starlette's MRO-based handler resolution: `HTTPException` instances always resolve to the more-specific `http_exception_handler`, and everything else resolves to whichever `Exception` handler was registered *last*, which is `general_exception_handler`), removing it changes no observable behavior for any request, including error responses' CORS headers, status codes, or bodies.

## 5. User-experience effect

None. No response body, status code, or header changes for any real request — the removed handler never ran.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | Removed dead `Exception` handler registration; added an explanatory comment on the real precedence | Dead-code cleanup found while investigating the export bug |

## 7. Before / after

```python
# Before
    app.add_exception_handler(Exception, cors_exception_handler)

# After
    # NOT registered here — general_exception_handler (registered later, in
    # server.py) always wins for the base Exception class; see comment above.
```

## 8. Rollback plan

`git revert` — re-adds a registration line that (per §3/§4) has no observable effect either way.

## 9. Verification performed

- [x] `ruff check core/middleware.py` — clean.
- [x] `pytest backend/tests/test_p1_cors.py` — 16/16 passing (this test does not exercise the live app registration, so it could not have caught the dead-code issue in the first place — noted as a standing gap, not fixed here).
- [ ] Full backend suite not run in this pass (scoped to the touched file + its direct test).
- [ ] Not run against a live server / Sentry — no access to production logs from this environment.

## 10. What was NOT verified / deferred

- **The user-reported export failure itself is not yet fixed.** This commit removes a piece of misleading dead code discovered during the investigation; it does not identify or fix the actual throw site. See §3 for why the real traceback (from Sentry or server logs) is needed to proceed, and the explanation given to the user in-chat for the reasoning.
- `tests/test_p1_cors.py` testing a standalone reimplementation instead of the registered handler is a real test-coverage gap (it would never have caught this dead-registration bug), flagged here but not fixed — out of scope for this change.
