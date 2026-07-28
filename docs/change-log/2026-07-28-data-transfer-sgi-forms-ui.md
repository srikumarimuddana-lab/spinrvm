# Change Impact & Risk Log — Data Transfer module: SGI Forms tab UI (Phase 5.3)

## Issue/gap identified
Phase 5.1's SGI form-generation route had no UI — an admin could not
actually trigger a D00032/D00033 generation through the dashboard.

## Root cause
Deliberate phasing — backend first, UI second.

## Fix/remediation
- Modified `admin-dashboard/src/lib/api.ts`: added `generateSgiForm()`. This
  one is NOT built on the generic `request<T>()` helper (which always calls
  `res.json()`) because the response is a raw PDF binary — it follows the
  existing `fetchKybDocumentBlob` pattern (manual `fetch` + auth header +
  `.blob()`) already established in this file, adding the `X-CSRF-Token`
  header since (unlike that GET-only precedent) this is a POST.
- New `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx`:
  form-type picker (D00032/D00033), Add/Remove/Change action picker,
  generate-and-download button. Filters the shared selection to
  `entity_type === "driver"` only (SGI forms report drivers/vehicles, not
  riders — a rider selection carried over from Search & Select is silently
  excluded rather than erroring, with a visible count so the admin isn't
  confused about why their selection count looks smaller here). Enforces
  the same row caps as the backend (`FORM_ROW_LIMITS` mirrors
  `sgi_form_filler.py`'s `MAX_DRIVER_ROWS`/`MAX_VEHICLE_ROWS`) client-side
  so the admin gets an immediate, specific error instead of a generic 422
  after a round-trip.
- Modified `admin-dashboard/src/app/dashboard/data-transfer/page.tsx`: wires
  `SgiFormsTab` into the SGI Compliance Forms tab (replacing the
  placeholder) — this is also the last placeholder tab, so all four tabs
  (Search & Select, Export, Import, SGI Compliance Forms) are now fully
  wired end-to-end.

## Risk & impact on existing functionality
Blast radius: `generateSgiForm` is a new, additive export in `api.ts` with
one consumer (`SgiFormsTab.tsx`, new in this same commit). No existing
export's signature or behavior changed. `page.tsx`'s edit is the same
one-import + one-tab-content-swap shape as the prior two tab-wiring
commits.

## User experience effect
None for existing users — reachable only via the still-unlinked
`/dashboard/data-transfer` URL (Phase 6.1, next and final subtask, adds
nav).

## Files modified
| File | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api.ts` | +`generateSgiForm()` (blob response, not JSON) | Typed client for the SGI generate route |
| `admin-dashboard/src/app/dashboard/data-transfer/SgiFormsTab.tsx` | New: form/action picker + generate UI | SGI Compliance Forms tab body |
| `admin-dashboard/src/app/dashboard/data-transfer/page.tsx` | Wires `SgiFormsTab` into the last placeholder tab | Replace placeholder — module UI now complete |

## Before/after snippet
N/A — purely additive; no existing behavior-changing diff.

## Rollback plan
Delete `SgiFormsTab.tsx`, revert the additive `generateSgiForm` block in
`api.ts`, revert `page.tsx`'s SGI Forms tab to the placeholder. No other
code imports any of these yet (grep-confirmed).

## Verification performed
- `npx tsc --noEmit -p tsconfig.json` across the whole project — zero errors
  attributable to any file this subtask touched.
- Cross-checked `FORM_ROW_LIMITS` (10/16) against the actual
  `MAX_DRIVER_ROWS`/`MAX_VEHICLE_ROWS` constants in `sgi_form_filler.py`
  (not guessed) so the client-side and server-side caps can't drift apart.
- Confirmed `fetchKybDocumentBlob`'s blob-fetch pattern (the precedent this
  follows) exists and is structured the way I modeled `generateSgiForm`
  after, rather than inventing a new binary-response convention.

## What was NOT verified
- Not run in a browser — the actual PDF download (blob URL creation,
  filename, browser download prompt) is untested against a live backend.
- The rider-selection-filtering UX (drivers-only counted, riders silently
  excluded with a visible count) was reasoned through but not visually
  confirmed to read clearly to a real admin.
- This completes the module's full UI (all four tabs functional); an actual
  end-to-end click-through — search → select → export/import/generate — has
  still not been performed in this session, since no dev server was started.
  This should happen before the module ships to real admins; flagging as
  outstanding per CLAUDE.md's UI-verification guidance.
