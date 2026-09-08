# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude (session implementing PR #5085's hardening plan) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth (error-response contract; touches every domain that raises `SpinrException`) |
| PR / commit link | branch `claude/pr-5085-5079-hardening-5a2aj7` |
| Related issue or gap ID | F2, validated in PR #5085 (`docs/audit/2026-09-07-pr-5079-validation-and-hardening-plan.md`, "PR-5"); tracked as `ACTION_ITEMS.md` C78 |

## 1. Issue / gap identified

`SpinrException.to_dict()` includes `details` verbatim in the API response, and the existing 4xx/5xx diagnostic sanitizer only covers plain `HTTPException`, never `SpinrException`. Two concrete leak sources: `repositories/_base.py:561`'s `DatabaseError(details={"original": exc_str, "exception_type": exc_name})` (a raw Postgres/Supabase error string, which can echo PII from a constraint violation, plus an internal class name with no client use), and two sites in `dependencies/__init__.py` raising `DatabaseError(details={"original": str(e)})` with a fully raw exception string.

## 2. Root cause

The 4xx/5xx `_should_sanitize_5xx_detail`/`redact_error_detail` sanitizer was built for the `http_exception_handler` path only; `spinr_exception_handler` was never given an equivalent pass over `details`, so anything a `DatabaseError`-raising call site put in `details["original"]` went straight to the client.

## 3. Fix / remediation

`spinr_exception_handler` now redacts `content["error"]["details"]` when it's a dict: drops `exception_type` outright, and passes `original` (only if it's a string) through the existing `_redact_error_detail`. Every other key — `code`, `client_secret`, `unpaid_ride_id`, `next_action`, and anything else a route deliberately sets — passes through byte-for-byte, since `details` is a live client contract (3DS flows, unpaid-ride handling), not something to strip wholesale. Applies uniformly to every status code. `dependencies/__init__.py`'s two raise sites now redact `str(e)` before it ever reaches `DatabaseError.details`, as defense in depth (that exception object also feeds the loguru→Sentry bridge, and CLAUDE.md forbids phone/email in Sentry events).

## 4. Risk & impact on existing functionality

- **Blast radius**: every `SpinrException` response passes through `spinr_exception_handler`, so this is a single-choke-point fix, not a per-call-site patch. **Correction after `spinr-security-auditor` review**: `routes/main.py:64-104` also catches a `DatabaseError` and spreads its raw `.details` into its own `JSONResponse`, fully bypassing this fix — but that router's own docstring says it is never mounted (dead code; the real `/health` in `server.py:229` returns a clean body with no raw details), so there is no currently-reachable bypass. Flagging it here rather than claiming none exists at all, since it would silently reactivate the leak if `routes.main` is ever wired in.
- **`exception_type` removal safety**: grepped `shared/`, `rider-app/`, `driver-app/`, `admin-dashboard/src/`, `backend/tests/` for `exception_type` before dropping it. Found only an unrelated, separate top-level `error.exception_type` field that `general_exception_handler` (a different handler, not touched here) sets and `shared/api/client.ts:1011` reads for a diagnostic breadcrumb (`recordApiError`) — no reader touches the nested `details.exception_type` shape this fix removes.
- **Structured-details contract preserved**: `client_secret`, `unpaid_ride_id`, `code`, `next_action` and any other route-set key pass through unchanged — confirmed via a dedicated test asserting byte-for-byte equality.
- No ride state, money, or wallet path touched — this is a response-serialization change only.
- **Residual gap flagged by review, out of this PR's scope**: `redact_error_detail` has no name or lat/lng pattern (its own module comment acknowledges this), so a Postgres error like `Key (full_name)=(John Smith) already exists` would survive the redaction untouched. Separately, the actual PII source at `repositories/_base.py:561` (`DatabaseError(details={"original": exc_str, ...})`) is itself never redacted — only the two `dependencies/__init__.py` raise sites got the defense-in-depth treatment — so any `logger.error` call elsewhere that logs `e.details["original"]` from a `_base.py`-raised `DatabaseError` (found: `routes/webhooks.py:648,2104`, `routes/drivers/tax_exports.py:312,1017`, `documents.py:436`, `routes/drivers/ride_cancel.py:199-203`) still logs the raw value server-side. This is consistent with CLAUDE.md's deliberate "server-side logging keeps the raw text" design for on-call diagnostics, not a regression from this PR, but it does mean the Sentry-safety motivation for redacting at the source is not fully realized — worth a follow-up ticket to redact at `repositories/_base.py:561` itself rather than only at the two sites this PR touched.
- Reviewed by `spinr-security-auditor` before commit.

## 5. User-experience effect

No user-visible UX change for a well-behaved client — `details["original"]` was never meant to be shown verbatim to a rider/driver; it exists for a client to branch on structured fields (`code`, `client_secret`). A client that was (incorrectly) rendering `details.original` raw to a user would now see `[redacted]` substrings instead of leaked diagnostic text — this is the fix's whole point, not a regression.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/error_handling.py` | `spinr_exception_handler` redacts `details.exception_type` (dropped) and `details.original` (redacted) before returning the response | Close the leak at the single handler choke point |
| `backend/dependencies/__init__.py` | Both `DatabaseError(details={"original": str(e)})` raise sites now redact `str(e)` first | Defense in depth; also protects the loguru→Sentry bridge |
| `backend/tests/test_error_response_sanitisation.py` | Added `TestSpinrExceptionDetailsRedaction` (4 tests) | Pin the redaction contract end-to-end via `TestClient` |
| `backend/tests/test_dependencies_auth_gaps.py` | Extended one existing test + added one new test with PII-bearing exception messages | Pin the defense-in-depth redaction at the raise sites |

## 7. Before / after

```python
# Before
content = exc.to_dict()
if isinstance(content.get("error"), dict):
    content["error"]["request_id"] = request_id
```

```python
# After
content = exc.to_dict()
if isinstance(content.get("error"), dict):
    content["error"]["request_id"] = request_id
    details = content["error"].get("details")
    if isinstance(details, dict):
        redacted_details = {k: v for k, v in details.items() if k != "exception_type"}
        original = redacted_details.get("original")
        if isinstance(original, str):
            redacted_details["original"] = _redact_error_detail(original)
        content["error"]["details"] = redacted_details or None
```

## 8. Rollback plan

`git revert` — pure response-serialization change, no schema/data/flag coupling. Reverting restores the previous (leaking) behavior exactly.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_error_response_sanitisation.py tests/test_dependencies_auth_gaps.py tests/test_create_ride_guard_clauses.py -q` — 57 passed, 0 failed. Full `pytest -m unit -q` — 3492 passed, 1 skipped, 0 failed.
- [x] `ruff check`/`ruff format --check` on all four changed files — clean (one pre-existing, unrelated unused-import warning in test_dependencies_auth_gaps.py, not introduced by this change, left untouched per CLAUDE.md's surgical-changes rule).
- [x] Blast-radius grep performed: confirmed `spinr_exception_handler` is the sole path for `SpinrException` responses; confirmed `exception_type` has zero client readers of the nested `details.exception_type` shape being dropped (see §4).
- [x] `spinr-security-auditor` review pass before commit.
- [ ] Not run against a live client (mobile/admin) — verified at the FastAPI `TestClient` layer only, not an actual rider/driver/admin app render.

## What was NOT verified

Not tested against a real 3DS payment flow or a real admin-dashboard error toast — the "structured details pass through unchanged" guarantee is proven at the response-body level via `TestClient`, not by observing an actual client render the response. No visual-regression tooling exists for this response shape (it isn't a UI surface). `redact_error_detail`'s coverage was not independently re-verified beyond reading its existing docstring/patterns — per the security review it has no name or lat/lng pattern, so a full name or coordinate pair embedded in a Postgres error string would not be caught by this fix.
