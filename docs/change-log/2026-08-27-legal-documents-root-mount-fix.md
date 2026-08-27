# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude, at user request ("on the next screen where one might want to learn more on the policies... it shows both the policies are blank in the app can we check and address this issue") |
| Surface(s) | backend, rider-app, driver-app (fix is backend-only; both mobile apps are consumers, unmodified) |
| Domain (Sentry tag) | admin (legal/content surface — closest existing domain tag; not payments/auth/dispatch) |
| PR / commit link | commit following this log |
| Related issue or gap ID | Reported live by the user: Terms of Service and Privacy Policy both render blank in-app when a user taps "learn more" from the consent checkbox |

## 1. Issue / gap identified

Tapping "Terms of Service" or "Privacy Policy" from the consent checkbox (or the
in-app Legal menu) on either rider-app or driver-app shows a placeholder — "No
Terms of Service have been added yet." / "No Privacy Policy has been added yet." —
even though every one of these documents is fully published, with real content, in
production. Queried the production `legal_documents` table directly: all 16 rows
(8 doc types × 2 audiences) have non-empty content, 1.9KB–10.3KB each, published
2026-08-17 through 2026-08-21 per `docs/legal/legal-text-publication-checklist.md`.

## 2. Root cause

`backend/routes/legal_documents.py`'s public `GET /legal-documents` endpoint was
only ever wired into `v1_api_router` in `server.py` (line 356:
`v1_api_router.include_router(legal_documents_router)`), which is exclusively
mounted at `/api/v1` (`app.include_router(v1_api_router, prefix="/api/v1")`). It
was never also mounted at root.

That contradicts the route module's own docstring: *"Public endpoint — no auth, no
/api/v1 prefix (mounted at root)"* — and, more importantly, contradicts every one of
the four mobile call sites, which all fetch `${SpinrConfig.backendUrl}/legal-documents`
directly via raw `fetch()`, with no `/api/v1` prefix:

- `rider-app/app/legal.tsx`
- `driver-app/app/legal.tsx`
- `driver-app/app/become-driver.tsx`
- `driver-app/app/crc-consent.tsx`

(The shared `api` client used elsewhere in both apps — e.g. `api.get('/consent/status')`
— automatically prepends `/api/v1` to every call; these four `legal-documents` fetches
bypass that client and call `fetch()` directly, so they got no such prefix.)

Every one of those four fetches has been hitting a 404 in production. FastAPI's
default 404 JSON body (`{"detail": "Not Found"}`) has no `content` key, so each
call site's `data.content || fallbackText` falls straight through to the "not
published yet" placeholder — masking the fact that real content exists.

This is not a new bug from a recent change — it's a wiring gap that was never
caught, because every existing test for this endpoint
(`backend/tests/test_legal_documents.py`) called the handler function
`get_legal_document(...)` directly, bypassing the FastAPI router/app entirely, so
none of them ever exercised *which URL path* the route actually resolves at.

**Precedent that makes the fix obvious**: `settings_router` (the endpoint this one
replaced, `/settings/legal`) is dual-mounted in `server.py` — once at root, once at
`/api/v1` — with an explicit comment: *"Mounted at root so mobile apps can call
them without an auth token, and also at /api/v1 for parity."* `legal_documents_router`
was clearly meant to follow the same pattern (its own docstring says so) but the
root half of that mount was simply never added.

## 3. Fix / remediation

Added `app.include_router(legal_documents_router)` (no prefix) in `server.py`,
immediately after the analogous `settings_router` dual-mount block, with a comment
explaining the bug and pointing at all four affected mobile call sites. The
existing `/api/v1/legal-documents` mount is left in place (harmless, and now an
intentional alias rather than the only path — see the `_DEPRECATED_ROOT_PREFIXES`
note in section 4).

No frontend changes — all four mobile call sites already fetch the correct
(now-working) path.

Added two regression tests to `backend/tests/test_legal_documents.py` that go
through the real ASGI app (`test_client` fixture, `conftest.py`) instead of calling
the handler directly, so a future regression on either mount actually fails CI:
- `test_legal_documents_reachable_at_documented_root_path` — GET `/legal-documents`
  resolves (this is the one that was broken; confirmed it actually fails without
  the fix by temporarily reverting `server.py` and re-running it — real 404).
- `test_legal_documents_also_reachable_under_api_v1_prefix` — the `/api/v1` alias
  still works too.

## 4. Risk & impact on existing functionality

- **Blast radius: additive only.** This adds a new root mount; it does not remove
  or change the existing `/api/v1/legal-documents` mount, the route handler logic,
  or any DB row. Grepped the whole repo for every consumer of `/legal-documents` —
  the four mobile call sites above (all currently broken, all fixed by this) and
  `docs/legal/legal-text-publication-checklist.md` / `shared/config/legalDocs.ts`
  (docs/config, not code paths). The separate admin CRUD endpoint
  (`/api/admin/legal-documents`, `routes/admin/legal_documents.py`) is a different
  router entirely, unaffected.
- **`DeprecatedRootPathMiddleware`** (`server.py`) flags legacy root-mounted paths
  that have a canonical `/api/v1` equivalent, for migration-tracking purposes
  (`_DEPRECATED_ROOT_PREFIXES` currently lists `/settings` and `/company-info`).
  Deliberately did **not** add `/legal-documents` to that set: unlike `/settings`,
  the docstring documents *root* as the canonical path here (mirroring why
  `/settings/legal`'s root mount itself isn't flagged deprecated) — the `/api/v1`
  alias is the one that's incidental, not the other way around.
- **Rate limiting**: same double-mount tradeoff already accepted for
  `settings_router` (see its comment) — a caller alternating between the root and
  `/api/v1` path gets counted separately per prefix by slowapi. This endpoint is
  public+read-only, same class as `settings_router`, so the existing acceptance
  reasoning applies unchanged.
- **No auth/security change**: the route was already public (no `Depends(...)`
  auth on it) at `/api/v1/legal-documents`; adding a root alias doesn't newly
  expose anything — it's the same handler, same DB read, same response shape.

## 5. User-experience effect

**Rider- and driver-facing, immediately visible.** Anyone tapping "Terms of
Service" or "Privacy Policy" from the login-screen consent checkbox, the inline
otp.tsx consent card, or the general Legal menu now sees the actual published
policy text instead of "No Terms of Service have been added yet." / "No Privacy
Policy have been added yet." Also fixes the same blank-content bug on driver-app's
"become a driver" screen and CRC-consent screen, which fetch a different doc_type
(`background-check-consent`) through the same broken path.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/server.py` | Added `app.include_router(legal_documents_router)` (root, no prefix), mirroring `settings_router`'s existing dual-mount pattern | The route's own docstring and all four mobile call sites assumed this mount existed; it never did |
| `backend/tests/test_legal_documents.py` | Added two `test_client`-based regression tests exercising the real router mount (root + `/api/v1`) instead of calling the handler directly | The existing unit tests could never have caught this class of bug — confirmed the new root-path test fails (404) without the fix |
| `docs/change-log/2026-08-27-legal-documents-root-mount-fix.md` | This log | Bug fix affecting a legal/consent-adjacent, live-tested surface |

## 7. Before / after

```python
# Before — server.py
app.include_router(settings_router)
app.include_router(settings_router, prefix="/api/v1")

# Mount admin routes under /api so the admin dashboard can reach them at /api/admin/...
app.include_router(admin_router, prefix="/api")
```

```python
# After
app.include_router(settings_router)
app.include_router(settings_router, prefix="/api/v1")

# Public per-audience legal-documents endpoint (GET /legal-documents). Same
# dual-mount reasoning as settings_router directly above...
app.include_router(legal_documents_router)

# Mount admin routes under /api so the admin dashboard can reach them at /api/admin/...
app.include_router(admin_router, prefix="/api")
```

## 8. Rollback plan

`git revert` is complete and sufficient — pure additive router mount, no data,
migration, or Stripe/payment component. Reverting simply restores the (broken)
prior behavior; it cannot regress anything that was working, since nothing
previously depended on the root path being absent.

## 9. Verification performed

- [x] Queried production `legal_documents` table directly (Supabase MCP,
      `soavhtdhefowwvforzwb`) — confirmed all 16 rows have real, non-empty content,
      ruling out "the data itself is missing" as the cause.
- [x] Read `server.py`'s actual router-mount code to confirm the mismatch between
      the route's documented mount point and its real one — not inferred from
      symptoms alone.
- [x] `pytest tests/test_legal_documents.py -q` — 8/8 pass (6 pre-existing + 2 new).
- [x] Proved the new root-path test is a real regression guard: temporarily
      reverted `server.py`'s fix (`git stash`) and re-ran it — failed with a real
      404, as expected; restored the fix (`git stash pop`) and re-ran — passes.
- [x] `pytest tests/test_dual_import_parity.py -q` — 3/3 pass (server.py's import
      block itself wasn't touched, only the router-mount call further down; ran
      anyway since `server.py` is import-heavy).
- [x] `ruff check` / `ruff format --check` on both changed files — clean.
- [ ] Not verified against the live production backend directly (outbound HTTPS
      from this sandbox is proxied and this session couldn't reach
      `api-spinr.spinr.ca` to curl the real endpoint before/after) — verified via
      direct DB query (confirms the data side) plus a full-app `TestClient`
      integration test that exercises the exact same FastAPI router-mount code
      path `server.py` uses in production, not a mocked/isolated handler call.

## What was NOT verified

- Did not manually reproduce the blank-policy bug on a running rider-app/driver-app
  build against production before fixing — root-caused directly from `server.py`'s
  router-mount code plus the four frontend fetch call sites, which is a purely
  static, deterministic mismatch (not a race condition or environment-specific
  behavior), so a live repro wasn't necessary to confirm the cause.
- Did not curl the live production URL directly (proxy in this sandbox blocks
  arbitrary outbound hosts) — relied on the `TestClient`-based integration test
  instead, which exercises the identical `app` object and router-mount code that
  `python -m backend.server` serves in production.
- Did not audit whether any other endpoint in the codebase has the same
  documented-root-but-actually-versioned-only mismatch — scoped this fix to the
  specific bug reported (legal documents).

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — additive-only mount, four affected
      consumers named, no other consumer found via repo-wide grep.
- [x] No silent behavior change to an already-shipped, *working* flow — the fixed
      path was never working in the first place; this only adds the missing mount,
      it changes nothing about the `/api/v1` path that was already resolving.
