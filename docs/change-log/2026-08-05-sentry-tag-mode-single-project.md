# Sentry viewer: support one shared project with tag-based surface filtering

**Date:** 2026-08-05
**Surface:** backend (`routes/admin/sentry.py`), admin-dashboard (type only)

## Issue/gap identified

The Sentry Issues viewer derived an issue's surface from *which Sentry project answered
the request* (`_SURFACE_SETTING` → `SENTRY_PROJECT_*`). Spinr runs **one** Sentry project
(`crimson-smoke-7445`) that all four surfaces report into, so that axis carries no
information. The two usable configurations were both wrong:

- Set all four `SENTRY_PROJECT_*` to the same slug → the fan-out queried the same project
  four times and every issue appeared **4×**, each with a different (wrong) surface label.
- Set only one → the viewer worked, but every issue was labelled with that one surface
  (in practice "backend"), and the surface tabs filtered nothing.

## Root cause

A design assumption, not a bug: the module was written for one-project-per-surface. Every
surface's SDK already sets a `surface` tag at init (`sentry.client/server/edge.config.ts`,
`shared/services/errorReporting.ts:52`), and `surface` was already in `_TAG_ALLOWLIST` —
the information was present, the viewer just never used it.

## Fix/remediation

Added a second mode, selected by config. New setting `SENTRY_PROJECT_ALL`.

- **Project mode** (unchanged): `SENTRY_PROJECT_*` per surface.
- **Tag mode** (new): `SENTRY_PROJECT_ALL` names one project; each leg of the fan-out
  queries it narrowed by `surface:<name>`, so the label is something we *asked for*
  rather than inferred. A fifth `!has:surface` leg surfaces untagged events under
  `unknown` instead of dropping them.

Per-surface slugs win when both are set, so `SENTRY_PROJECT_ALL` is purely additive.

The fan-out was refactored from `(surface, slug)` pairs onto a `_Target(surface, project,
term)` plan, so both modes run through one code path.

## Risk & impact on existing functionality

Blast radius is `routes/admin/sentry.py` plus one optional TS field. Consumers checked:

- **`_surface_projects()`** — read by `_is_configured`, `_sentry_config`, `_resolve_surface`,
  and (via `_targets`) the list path. Grepped for all callers; no others, including outside
  this module. Its project-mode return value is byte-identical to before, so every existing
  caller behaves the same when `SENTRY_PROJECT_ALL` is unset.
- **`_targets` project branch deliberately calls `_surface_projects()`**, not the new
  `_per_surface_projects()`. The two are identical on that branch (tag mode is ruled out
  above it), and it preserves the single seam the existing tests stub. Routing it through
  the new function instead would have silently broken 5 pre-existing tests.
- **`_resolve_surface`** guards the detail and status-change endpoints against the
  org-scoped token reaching other projects. In tag mode the check still runs — it just
  compares against the shared slug instead of a slug→surface map. Verified by test that a
  foreign project still 404s. **This is the security-relevant path in this diff.**
- **Frontend**: response contract unchanged (`surface`, `surfaces[]`, `errors[]`). The page
  already renders `unknown` (`SURFACE_LABEL.unknown`, `surfaceBadgeClass` default), so no
  component change was needed. `mode` was added to `SentryConfig` as optional and is not
  yet read by any component.

Behaviour risks specific to tag mode:

- **Request count rises from 1 to 5** on the "All surfaces" view (4 surfaces + untagged),
  against one project rather than four. Bounded by the existing `60/minute` read limit.
- **Duplicate suppression added.** A Sentry issue groups by error signature, not by tag, so
  one issue can legitimately answer two surface legs. Issues are now deduped by id, first
  leg wins. Legs run in `_SURFACE_SETTING` order so the label is stable across refreshes
  rather than dependent on which HTTP response landed first. This dedupe also applies in
  project mode, where it is a no-op (ids are unique per project).
- **`_update_issue_status` audit rows log `surface: "unknown"` in tag mode**, since the
  mutation path has no event to read the tag from and fetching one would add a request to
  every write. The `project` field in the audit row is unaffected. Accepted.

## User experience effect

Internal-admin only, on `/dashboard/sentry-logs` (super-admin gated). The Surface badge
becomes correct instead of always reading "Backend", and the surface tabs actually filter.
No rider, driver, or corporate-admin surface is touched. Not visible mid-session to anyone
using the apps.

## Files modified

| file path | what changed | why |
|---|---|---|
| `backend/core/config.py` | Added `SENTRY_PROJECT_ALL`; corrected the token-type comment | New setting; the old comment recommended an Organization Auth Token, which cannot grant `event:read`/`event:write` |
| `backend/routes/admin/sentry.py` | `_per_surface_projects` / `_shared_project` / `_tag_mode` / `_Target` / `_targets`; `_list_issues` on the target plan + dedupe; `_resolve_surface` tag-mode branch; `_get_issue` reads surface from the event tag; `mode` in `/config` | The change itself |
| `backend/tests/test_admin_sentry.py` | +11 tag-mode tests, `_tag_mode_settings` context manager | Coverage for both modes |
| `backend/.env.example` | Documented both modes | Operator-facing |
| `admin-dashboard/src/lib/api/sentry.ts` | Optional `mode` on `SentryConfig` | Documents the new field |

## Before/after

```python
# before — surface implied by which project answered; one project = one label for everything
projects = _surface_projects()
results = await asyncio.gather(*(
    _fetch_project_issues(client, surf, slug, query=full_query, ...)
    for surf, slug in ordered))

# after — surface asked for explicitly; one project, five narrowed legs
targets = _targets(surface)   # [(backend, all, "surface:backend"), … , (unknown, all, "!has:surface")]
results = await asyncio.gather(*(
    _fetch_project_issues(client, t.surface, t.project,
                          query=f"{base_query} {t.term}" if t.term else base_query, ...)
    for t in targets))
```

## Rollback plan

No deploy needed. Unset `SENTRY_PROJECT_ALL` and set `SENTRY_PROJECT_BACKEND` to the same
slug: the code falls back to project mode, which is the exact pre-change behaviour. The
mode is chosen per-request from settings, with no cached state and no migration — nothing
is written to a database, no money or ride state is touched. Unsetting `SENTRY_API_TOKEN`
disables the whole viewer (renders the setup panel), also without a redeploy. Reverting the
commit is safe at any point.

## Verification performed

- `pytest tests/test_admin_sentry.py` → **47 passed** (36 pre-existing + 11 new). The 36
  pre-existing tests pass unmodified, which is the regression check for project mode.
- The 11 new tests were run in isolation (`-k "tag_mode or reports_mode"`) and confirmed
  **11 passed, 36 deselected** — so they genuinely execute rather than being collected away.
  Run separately because of the known bulk-run anyio/asyncio interaction in this repo.
- `ruff check` clean on all three backend files; `ruff format` applied and tests re-run
  after formatting (47 passed again).
- Covered by the new tests: query composition per leg (exact strings asserted, including
  the `!has:surface` leg), all legs hitting the one project, per-surface and `unknown`
  target selection, 400 for an unknown surface, dedupe with stable first-leg labelling,
  surface resolved from the event tag, an unrecognised tag value rejected back to
  `unknown`, foreign-project 404 in tag mode, and `mode` in `/config`.
- Blast radius established by grep for every `_surface_projects` caller.

## What was NOT verified

- **Never run against live Sentry.** All tests mock `_fetch_project_issues` /
  `_sentry_request`, so both search terms are asserted only as *strings we send*.
  Checked against Sentry's docs rather than the API: `tagname:value` is documented for
  custom tags, `has:` is documented ("Returns results with the defined tag or field"), and
  `!` is documented as the negation operator. The **combination** `!has:surface` is the
  standard idiom but is not documented explicitly, so the `unknown` leg is the one term to
  confirm after deploying. If Sentry rejects it, that leg 502s and is reported in `errors`
  / `partial` — it degrades visibly rather than silently returning an empty bucket.
- **No frontend build or visual check** for this change. `page.tsx` was read, not run; the
  claim that `unknown` renders correctly is from reading `SURFACE_LABEL`/`surfaceBadgeClass`,
  not from seeing it. No visual regression tooling exists for this surface (standing gap).
- **`/dashboard/sentry-logs` was not exercised end to end** — no browser, no real token.
- **Cross-surface duplicate issues were tested with fixtures only.** Whether Sentry actually
  groups an identical error signature across two surfaces into one issue is untested against
  real data; the dedupe is defensive.
- The full backend suite was not run — only `test_admin_sentry.py`.
- `SENTRY_PROJECT_ALL` is not yet set in any deployed environment, so tag mode has never
  executed outside tests.
