# Change Impact & Risk Log — dev tier moves to a FastAPI Cloud trial

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-26 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend (packaging only), CI/CD workflows, docs |
| Domain (Sentry tag) | — (infra/build; no ride, payment, auth, or corporate logic touched) |
| PR / commit link | branch `claude/test-dev-canary-setup-5h07cp` |
| Related issue or gap ID | `ACTION_ITEMS.md` E1a; ADR-011 addendum 2026-08-26 |

## 1. Issue / gap identified

The dev tier was planned for Fly.io. A decision was taken to trial **FastAPI
Cloud** instead, with a view to eventually migrating staging, canary, and
production if it proves capable. FastAPI Cloud's deploy path requires a
`pyproject.toml` and discovers the app from `main.py`/`app.py` — the repo had
neither.

## 2. Root cause

Not a defect. This is a deliberate platform evaluation. The repo's packaging
has always been `requirements.txt` + Dockerfile, which suits Fly and Railway
and does not suit a PaaS that builds from Python project metadata.

## 3. Fix / remediation

Additive support for a second deploy path, alongside — not replacing — the Fly
one:

- `backend/pyproject.toml` mirroring `requirements.txt`'s 149 pins.
- `backend/scripts/sync_pyproject_deps.py` (`--write` / `--check`).
- A `pyproject-sync` job in `pip-compile-check.yml`.
- `backend/main.py`, a re-export shim for app discovery.
- `.github/workflows/deploy-backend-dev-fastapicloud.yml`, manual dispatch.

## 4. Risk & impact on existing functionality

**Blast radius: isolated. No production code path changes.**

Grepped every consumer of the backend entry point and dependency files:

| Consumer | Reads | Affected? |
|---|---|---|
| `backend/Dockerfile` | `requirements.txt`, `server:app` | **No** — untouched |
| `railway.json` | `server:app` via uvicorn | **No** — untouched |
| `backend/fly.toml` / `fly.staging.toml` / `fly.canary.toml` / `fly.dev.toml` | Dockerfile | **No** — untouched |
| `pip-compile-check.yml` `check` job | `requirements.in` → `requirements.txt` | **No** — job unchanged; only path filters and a new sibling job added |
| `ci.yml` `backend-test` | `requirements.txt` | **No** |

`backend/main.py` is a new file importing `server.py`; nothing in the repo
imports `main` except the FastAPI Cloud deploy. `pyproject.toml` is new and read
by nothing in the existing build.

**Real risk introduced:** a dependency-list divergence between
`pyproject.toml` and `requirements.txt` would make the dev tier run different
package versions than production while appearing healthy. Mitigated by the
`pyproject-sync` CI job plus a second check inside the deploy workflow itself.
Verified to fail correctly on injected drift.

**Standing risk, not mitigated by this change:** FastAPI Cloud may scale the app
to zero when idle, which would silently stop the 18 `core/lifespan.py`
background loops. On the dev tier this is an observation to make, not an
outage. It is recorded as blocking gate 1 in the runbook and in E1a, and is the
reason no tier beyond dev is being moved.

## 5. User-experience effect

**Nobody.** No user-facing behaviour changes on any surface. Nothing is visible
mid-session to a rider or driver. No copy or notification changes. The dev tier
serves no real users by definition, and no infrastructure has been provisioned.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/pyproject.toml` | New | Required by the FastAPI Cloud deploy path; mirrors requirements.txt |
| `backend/scripts/sync_pyproject_deps.py` | New | Generates and verifies that mirror |
| `backend/main.py` | New | Re-export shim for FastAPI app discovery |
| `.github/workflows/deploy-backend-dev-fastapicloud.yml` | New | The dev deploy |
| `.github/workflows/pip-compile-check.yml` | Added `pyproject-sync` job; extended path filters and header comment | Fails CI on mirror drift |
| `docs/runbooks/fastapi-cloud-dev.md` | New | Setup plus the three go/no-go gates |
| `docs/adr/011-environment-topology.md` | Dated addendum; status line amended | Records the amendment without editing prior decisions |
| `ACTION_ITEMS.md` | E1a updated | Backlog reflects the trial and the gates |

## 7. Before / after

No behaviour-changing diff to existing code — every backend change is a new
file. The one edit to an existing file is additive:

```yaml
# Before — pip-compile-check.yml
jobs:
  check:            # requirements.txt vs requirements.in
```

```yaml
# After — pip-compile-check.yml
jobs:
  check:            # unchanged
  pyproject-sync:   # new: pyproject.toml vs requirements.txt
```

## 8. Rollback plan

- **The trial:** stop running the workflow. Nothing routes to FastAPI Cloud and
  nothing depends on it. The Fly dev path is configured and one workflow run
  away. No redeploy of anything is required.
- **The repo additions:** `pyproject.toml`, `main.py`, and the sync script are
  inert to production and can be left in place indefinitely, or removed
  together in one commit. Removing them requires also dropping the
  `pyproject-sync` job and its path filters.
- No database migration, no live data touched, so no data-level remediation
  exists to plan for.

## 9. Verification performed

- [x] **Dependency mirror verified in both directions.** `--check` passes in
      sync (149 pins); with an injected version change it exits 1 and prints a
      readable two-way diff. Restored afterwards.
- [x] **`pyproject.toml` parses** (`tomllib`), 149 dependencies, first/last
      entries spot-checked against `requirements.txt`.
- [x] **`main.py` resolves the real app** — `Spinr API 1.0.0`, 31 routes, via
      both `main:app` (from `backend/`) and `backend.main:app` (from the repo
      root), exercising both halves of the dual-import pattern.
- [x] **Deploy contract verified against the real package**, not guessed:
      installed `fastapi-cloud-cli` 0.23.0 from PyPI and read its own generated
      CI workflow for the `FASTAPI_CLOUD_TOKEN` / `FASTAPI_CLOUD_APP_ID`
      contract, plus `deploy`, `env set`, `tokens`, and `apps` flags.
- [x] **Invocation corrected from the vendor template after inspection** —
      `fastapi-cloud-cli` ships no console script, so `uvx --from
      fastapi-cloud-cli fastapi deploy` cannot work; used the verified
      `python -m fastapi_cloud_cli deploy` instead.
- [x] **Lint/format** — `ruff check` and `ruff format --check` clean on both
      new Python files.
- [x] **All workflow YAML parses**, including the amended
      `pip-compile-check.yml` (2 jobs).
- [x] **No fabricated action pins** — the two SHAs used are copied verbatim
      from existing repo workflows.

## 10. What was NOT verified

- **Nothing has been deployed.** No FastAPI Cloud account, app, or secret
  exists. The workflow has never run. It is validated as YAML and reasoned
  through, not executed.
- **The three gates are unanswered** — background-loop survival under idle,
  WebSocket longevity, and serving region. They require observation on a
  running dev tier and are the explicit blockers on any further migration.
- **Vendor documentation could not be read.** `fastapicloud.com` and
  `fastapi.tiangolo.com` are blocked by this organization's network egress
  policy. Product claims (public beta, roadmap gaps, pricing) come from
  search-result summaries, not the pages themselves. The *technical* contract
  was verified against the CLI package instead, which is stronger evidence, but
  the product claims should be confirmed with the vendor.
- **The backend test suite was not re-run** for this batch. The changes add
  files rather than alter imports on any tested path; the prior batch's run
  (3050 unit tests passing, one pre-existing unrelated failure) still stands.
- **`uv` was not exercised** — the final workflow does not use it.
- **The pip-compile/Python-version discrepancy was not addressed**:
  `requirements.txt` was compiled under Python 3.11 while the Dockerfile pins
  3.12.13. Pre-existing, out of scope, noted in `pyproject.toml`'s comments.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (stop running one workflow)
- [x] Blast radius is stated, not assumed (every entry-point and dependency
      consumer enumerated in §4)
- [x] No silent behaviour change to an already-shipped flow — every backend
      change is a new file, unread by the production build path
