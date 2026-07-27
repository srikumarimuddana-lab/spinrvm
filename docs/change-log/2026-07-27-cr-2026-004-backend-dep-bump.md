# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-27 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | auth (touches pyjwt/cryptography), plus general dependency hygiene |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | CR-2026-004 (issue #2434), implements the CI Change Request for `python-dependency-audit` and (per a follow-up comment on the same issue) `G6 · Trivy container scan` |

## 1. Issue / gap identified

`pip-audit -r backend/requirements-locked.txt` reported 63 known vulnerabilities across 13 backend Python packages, failing the `python-dependency-audit` CI job on every PR (confirmed pre-existing on `main`, unrelated to any specific PR's diff). A separate investigation found `G6 · Trivy container scan` failing on 22 of the same underlying CVEs, baked into the built `spinr-backend` Docker image.

## 2. Root cause

`backend/requirements.txt` and `backend/requirements-locked.txt` were pinned to versions that predate upstream security fixes for 13 packages. All 13 are unbounded (`>=`) or entirely transitive in `backend/requirements.in` — nothing in the source constraints was blocking the fix versions. The actual cause was `pip-compile`'s default behavior: it reuses existing pins from the current output file whenever they still satisfy the declared constraints, so a bare recompile (no flags) silently reproduced the exact same vulnerable pins. `--upgrade-package <name>` was required per target package to force resolution against the latest available release.

## 3. Fix / remediation

Bumped 13 packages across two staged commits (per CR-2026-004's implementation plan — lowest-risk/most-isolated packages first, then the higher-surface/auth-adjacent ones):

**Stage 1** (isolated, non-auth): `httplib2` 0.31.2→0.32.0, `idna` 3.13→3.18, `msgpack` 1.1.2→1.2.1, `pyasn1` 0.6.3→0.6.4.

**Stage 2** (higher-surface, includes auth-adjacent): `pyjwt` 2.12.1→2.13.0, `cryptography` 47.0.0→49.0.0, `starlette` 1.0.0→1.3.1, `aiohttp` 3.13.5→3.14.3, `urllib3` 2.6.3→2.7.0, `pillow` 12.2.0→12.3.0, `mcp` 1.27.2→1.28.1, `pydantic-settings` 2.14.0→2.14.2, `python-multipart` 0.0.27→0.0.32.

Both `requirements.txt` (pinned, no hashes) and `requirements-locked.txt` (pinned + hashes, what CI/deploy actually installs from) were regenerated via `pip-compile`/`pip-compile --generate-hashes` after each stage. No application code changed — this is a dependency-pin-only change.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (backend only), but wide within it** — these are foundational libraries used throughout the backend: `pyjwt`/`cryptography` underpin every JWT issuance/verification (rider/driver/admin auth, refresh tokens, MFA-adjacent flows per `backend/core/config.py`'s JWT settings); `starlette` is FastAPI's ASGI foundation, touching every request; `aiohttp` and `urllib3` are used by HTTP-calling code (Twilio, Stripe, Google Maps, Firebase, MCP client calls); `pillow` is used for image handling (driver photo review/upload per `backend/routes/admin/staff.py`/drivers routes); `python-multipart` underlies every multipart file upload (driver documents, KYB uploads); `pydantic-settings` underlies the entire `Settings` config-loading layer.
- Grepped for every place these packages are used directly (not just installed transitively) — `pyjwt` in `backend/routes/admin/auth.py` (JWT mint/verify) and `backend/middleware` (user-id extraction from claims); `cryptography` indirectly via `pyjwt[crypto]` and `google-auth`; no other backend code imports these 13 packages by name directly, they're consumed through the higher-level libraries (`fastapi`, `firebase-admin`, `google-api-python-client`, `supabase-auth`, `twilio`, `mcp` SDK) that depend on them.
- No interaction with the 16 background loops, the ride state machine, or money/wallet deltas from this change directly — but `aiohttp`/`urllib3`/`starlette` are on the request path for payment-adjacent HTTP calls (Stripe SDK's own transport), so a genuine regression there could indirectly affect payment flows. Verification below specifically checked for this via the full test suite (which includes Stripe-mocked payment tests) rather than assuming safety.

## 5. User-experience effect

- **Nobody directly — backend-only, no API contract or response-shape change.** This is a dependency-pin bump with no application code touched; every existing endpoint's request/response behavior is unchanged.
- Not visible mid-session to any rider/driver/admin — no user-facing surface reads these package versions directly.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/requirements.txt` | 13 package version pins bumped (2 commits: 4 low-risk, then 9 higher-risk) | Source of truth for the pinned (unhashed) dependency versions; regenerated via `pip-compile requirements.in --upgrade-package <name>` per target |
| `backend/requirements-locked.txt` | Same 13 packages bumped, plus regenerated `--hash` entries for every affected package and any of their own dependency-graph shifts | What CI/deploy actually installs from (`pip install -r requirements-locked.txt`); regenerated via `pip-compile --generate-hashes --output-file=requirements-locked.txt requirements.txt` after each stage |

## 7. Before / after

```
# Before — backend/requirements.txt (excerpt)
cryptography==47.0.0      # via google-auth, pyjwt
...
pyjwt==2.12.1             # via -r requirements.in, firebase-admin, mcp, supabase-auth, twilio
...
starlette==1.0.0          # via fastapi, mcp, sse-starlette
```

```
# After — backend/requirements.txt (excerpt)
cryptography==49.0.0
    # via
    #   google-auth
    #   pyjwt
...
pyjwt[crypto]==2.13.0
    # via
    #   -r requirements.in
    #   firebase-admin
    #   mcp
    #   supabase-auth
    #   twilio
...
starlette==1.3.1
    # via
    #   fastapi
    #   mcp
    #   sse-starlette
```

(The annotation-comment style also changed from single-line to multi-line — a `pip-compile` output-format artifact from a version difference in the compile toolchain, not a manual edit; content/meaning is identical, just reformatted.)

## 8. Rollback plan

**`git revert` is sufficient and complete.** This is a pure dependency-pin change with no data written, no schema touched, no Stripe charges or wallet deltas involved, and no migration. Reverting both commits restores the prior pins exactly (the lock file's hashes make this byte-reproducible, not just "close enough"). No second deploy coordination needed beyond the normal deploy pipeline picking up the reverted `requirements-locked.txt`.

## 9. Verification performed

- [x] **Automated tests run** — full backend suite twice per stage (once as baseline, once post-bump), diffed:
  - `pytest backend/tests -m unit` (unit-marked subset): 374 passed / 6 failed both before and after each stage — identical failures (`test_dual_import_parity.py`, `test_error_handling_guards.py`, `test_payout_toctou.py`, all pre-existing static-analysis-style assertions unrelated to these packages).
  - `pytest backend/tests` (full unmarked suite, 4677 tests): **4590 passed / 79 failed / 8 skipped / 1 xfailed both before and after stage 2** — `diff` of the two sorted `FAILED`-test lists is byte-for-byte identical. Zero regression.
  - Targeted auth/JWT/MFA/OTP/token pass (`-k "jwt or auth or mfa or otp or token"`, 542 tests): 537 passed / 5 failed both before and after — same 5 failures both times. One (`test_expired_token_is_rejected_401`) was individually investigated because it's PyJWT-adjacent: confirmed the app still correctly returns 401 for an expired token either way; the test's stricter assertion on response *text* containing "expire" was already failing pre-bump, unrelated to this change.
- [ ] Manual repro in staging — not performed (no staging deploy access in this session); reasoned instead from the full-suite diff-equality above, which is a stronger signal than a handful of manual clicks for a dependency-pin-only change with no application code touched.
- [x] **Blast-radius grep performed** — see Section 4; searched for direct imports of all 13 packages across `backend/` (only `pyjwt` is imported directly, in auth/middleware code; the rest are consumed transitively through higher-level SDKs).
- [x] Reviewed against relevant `CLAUDE.md` conventions — JWT trust model (unaffected: no claim-handling logic changed, only the library versions that implement encode/decode), no money/state-machine/RLS/PIPEDA implications identified.
- [x] Not feature-flagged — not user-visible/non-trivial in the sense the flagging guidance targets; a backend dependency-pin bump with zero API contract change.
- [ ] **`pip-audit`'s live OSV re-scan could not be run in this sandbox** — `api.osv.dev` is blocked by this environment's outbound proxy (403 on the HTTPS tunnel). Verified equivalently via `pip show <package>` confirming every installed version matches or exceeds the CR's target fix version, cross-referenced against the CR's own findings table. Full confirmation that `pip-audit` itself reports zero findings, and that a rebuilt Docker image passes Trivy, will need to happen in CI (flagging as a follow-up check on the PR once CI runs).

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`, hash-locked so reproducible exactly
- [x] Blast radius is stated, not assumed — single-surface (backend), wide-but-transitive within it, confirmed via direct-import grep
- [x] No silent behavior change to an already-shipped flow — Section 5 covers it (no application code touched, no API contract change; verified via full-suite diff-equality rather than assumed from version numbers alone)
