# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-05 |
| Author | Claude Code (session-assisted) |
| Surface(s) | admin-dashboard, backend (tests only) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/sgi-driver-export-issue-nkgvgk` (restarted from `main` after #3443 merged) |
| Related issue or gap ID | Follow-up to #3443; operator reported "still metadata, not the image" after that PR merged |

## 1. Issue / gap identified

Two unrelated problems, fixed together because both were blocking:

1. **Document exports still arrived with no scans**, even after #3443. The operator had now hit this three times.
2. **11 tests were failing on `main`** — pre-existing, unrelated to #3443 (confirmed by running the full suite on both and diffing the failure sets), and left unexplained.

## 2. Root cause

**Export default — a silent, undocumented inversion of an approved decision.** PIA R-B (`docs/privacy/2026-07-28-pia-data-transfer-export.md`, marked DONE) states verbatim:

> Both admin-dashboard Export-tab checkboxes **default to checked** (current full-fidelity behavior unchanged for anyone who doesn't touch them) … Success criterion met: … **opt-in-to-exclude, default unchanged**.

Its own implementation change-log (`2026-07-28-data-transfer-export-scope-flags.md`) says the same: *"Two new checkboxes, both defaulting to checked (`true`)"*.

Commit `a56b59b` — subject *"docs(action-items): track ADR-010 metrics-alerting implementation as CR-2026-008 (#3296)"*, an unrelated docs change — rewrote `ExportTab.tsx` and set both to `useState(false)`, adding a comment asserting "Both default OFF (PIPEDA data-minimization)". No Change Impact entry, no PIA amendment, and directly contrary to the approved text. Every export since has silently omitted document files.

#3443 fixed the *silence* (the ZIP now says why files are absent) but preserved the inverted default, because at that point the default looked like a deliberate privacy control. Reading the PIA showed it was not.

**The 11 failing tests** — three independent cases of tests drifting from the code they cover:

| Tests | Cause |
|---|---|
| 8 favorites | `verify_address_matches_coordinate` gained a third return value (`place_id`, B9). Eight mocks still returned a 2-tuple → `ValueError: not enough values to unpack (expected 3, got 2)` at `routes/favorites.py:90`. |
| 2 admin Sentry | Tests invoke the route function directly, so an unpassed `surface` keeps its `Query(...)` **object** rather than the `None` it wraps. A `Query` instance is not `None`, so the `surface is not None` guard fired: `400: Surface 'annotation=Union[str, NoneType] ...' is not configured`. |
| 1 company email OTP | Route gained a non-production bypass (no email provider → fixed code `1234`, `deliver_via_email=False`). The test patches `send_transactional_email` but not `get_app_settings`, so delivery is never attempted and the 502-on-send-failure branch under test is unreachable. |

None of the three is a production defect — `routes/favorites.py` correctly unpacks 3 values; the Sentry route behaves correctly under FastAPI; the OTP bypass is intended. All three are stale tests.

## 3. Fix / remediation

**Export default restored** to what PIA R-B specifies: every document type starts selected, metadata **and** file. Confirmed with the operator before changing (`AskUserQuestion`) — this reverts an unapproved deviation rather than loosening an approved control, and the per-document grid from #3443 makes *excluding* far more precise than the single global toggle R-B originally shipped.

**Tests** fixed at the mock/callsite, not by weakening assertions: 8 mocks return the real 3-tuple; a `_call_list_issues()` helper supplies the route's real defaults with a comment explaining the `Query`-object trap; the OTP test configures an email provider so the branch it targets is reachable.

## 4. Risk & impact on existing functionality

**Blast radius: one component + three test files.** `ExportTab.tsx` has exactly one consumer (the Data Transfer page, embedded once in Records & Compliance). No backend production code changed — `git diff` on `backend/` touches only `tests/`. No schema, migration, background loop, ride/dispatch/payment/auth/safety code.

The one behavioral consequence worth naming: **exports are now larger by default.** An admin who selects 100 drivers and clicks Export without adjusting anything now pulls every document file for all 100, where before they pulled none. That is the PIA-approved behavior and the whole point of the fix, but it means:
- more bytes through the `data-transfer-exports` bucket (7-day retention, existing purge loop handles it unchanged);
- more `driver-documents` storage reads per export;
- the dual-approval gate, if enabled, now sees `doc_file_types` populated rather than `[]` — a different signature, so grants are scoped to the wider request, which is correct.

Rate limiting (10 exports/hour) and `MAX_ENTITIES_PER_EXPORT = 100` are unchanged and still bound the worst case.

## 5. User-experience effect

**Internal admin (super_admin) only.** No rider, driver, or corporate-admin impact; nothing visible mid-session to anyone using the apps.

On the Export tab: all checkboxes now start ticked. An export performed without touching anything returns the documents *and* their files — which is what an operator pulling an SGI compliance package expects. The metadata-only warning from #3443 remains and now fires only when someone deliberately unticks every File box.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx` | `docTypes`/`docFileTypes` initialise to all types; hint text rewritten | Restore the PIA R-B default inverted by `a56b59b` |
| `backend/tests/test_favorites_coverage.py` | 4 mocks → 3-tuple | Match `verify_address_matches_coordinate`'s real signature |
| `backend/tests/test_routes_favorites_coverage.py` | 4 mocks → 3-tuple | Same |
| `backend/tests/test_admin_sentry.py` | `_call_list_issues()` helper; 2 tests use it | Supply real defaults when calling a route function directly |
| `backend/tests/test_auth_remaining_endpoints.py` | Patch `get_app_settings` with a configured provider | Make the 502 branch reachable past the dev bypass |

## 7. Before / after

```tsx
// Before — a56b59b, contradicting PIA R-B with no change-log entry
const [docTypes, setDocTypes] = useState<Set<string>>(new Set(["background_check"]));
const [docFileTypes, setDocFileTypes] = useState<Set<string>>(new Set());

// After — PIA R-B: "default to checked ... opt-in-to-exclude"
const [docTypes, setDocTypes] = useState<Set<string>>(new Set(DOC_TYPE_OPTIONS));
const [docFileTypes, setDocFileTypes] = useState<Set<string>>(new Set(DOC_TYPE_OPTIONS));
```

```python
# Before — 2-tuple mock vs a 3-tuple function: ValueError at favorites.py:90
patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None)))
# After
patch("routes.favorites.verify_address_matches_coordinate", AsyncMock(return_value=(True, None, None)))
```

## 8. Rollback plan

`git revert` is a complete rollback. No migration, no live-data mutation, no persisted state — the only artifacts are on-demand ZIPs, and previously generated ones are untouched. The two halves revert independently: reverting the `ExportTab.tsx` hunk restores the narrow default without touching the test fixes.

If the wider default proves too heavy in practice, the narrower middle option (Background Check file only) is a one-line change to the two `useState` initialisers — no backend involvement, since the backend already accepts any per-type subset.

## 9. Verification performed

- [x] **Automated tests run.** Full backend suite: **9880 passed, 0 failed** (was 11 failed / 9869 passed on `main` before this change). Every previously-failing test now passes; no test was skipped, deleted, or had its assertions weakened to achieve that.
- [x] **Real production build run** — `cd admin-dashboard && npm run build` exits 0. Not a dev server, not `tsc --noEmit` alone. `npx eslint ExportTab.tsx` clean. Vitest: 160 passed / 20 files.
- [x] **Blast-radius grep performed** — confirmed `ExportTab.tsx` has a single importer; confirmed `backend/` production code is untouched (`git diff --stat backend/` shows only `tests/`).
- [x] **Root cause traced to a specific commit** via `git log -S` rather than assumed, and cross-checked against both the PIA and its implementation change-log before changing a compliance-relevant default.
- [x] **Operator confirmation obtained** before changing the default (`AskUserQuestion`), per CLAUDE.md gate 9 — the change touches PIPEDA posture and had two defensible readings.

## What was NOT verified

- **Not tested against live Supabase.** No export was run end-to-end against a real `driver-documents` bucket. If the operator's exports still arrive without files after this deploys, the cause is *not* the default and the ZIP's `file_export_status` column (from #3443) will name it — `unavailable_fetch_failed` or `unavailable_no_storage_key` rather than `excluded_by_request`.
- **Deploy state not confirmed.** #3443 merged at 03:11 UTC; whether Vercel (admin) and Fly (backend) had served the new build at the time of the operator's last attempt was not checked. Their report may predate the deploy entirely, in which case they were still driving the old single-toggle UI.
- **No frontend test asserts the new default.** `ExportTab.tsx` still has no test file; the initialiser change is covered by the production build and inspection only. Standing gap, same as #3443.
- **The GPS default was deliberately left OFF.** PIA R-B says *both* checkboxes default checked, so `includeRideGps = false` is the same class of deviation as the document-files one. It was not changed here because the operator was asked about document files specifically, and exact pickup/dropoff coordinates are a distinct and more sensitive data category. **This remains an open, unreconciled discrepancy between the PIA and the code** — it needs its own decision.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — the default change is user-visible, operator-confirmed, and documented in §5
