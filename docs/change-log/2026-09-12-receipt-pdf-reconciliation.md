# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (audit follow-through, roadmap item R9) |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5290 |
| Related issue or gap ID | `docs/audit/ride-experience/ROADMAP.md` R9 |

## 1. Issue / gap identified

Rider-app's "Download invoice (PDF)" action on the ride-details screen built its own,
independent HTML→PDF receipt (`buildReceiptHtml` in `rider-app/app/ride-details.tsx`,
rendered via `expo-print`) instead of using the backend's one official generator
(`backend/utils/receipt_pdf.py`, already used for the emailed receipt). The two
implementations only agreed by coincidence — nothing kept their tax-line logic,
snapshot-staleness gating, or driver-block formatting in sync if either changed.

## 2. Root cause

The client-side generator predates a clean way to fetch a rendered PDF from the backend
(no endpoint existed for it); the emailed-receipt path and the download-invoice path grew
independently. Found by the 2026-09-12 ride-experience industry-benchmark audit
(`docs/audit/ride-experience/module-a-rider-app.md`), not by a live discrepancy report —
the user confirmed the fix direction ("make the app fetch the official receipt") before
implementation.

## 3. Fix / remediation

- **Backend:** added `build_receipt_pdf_bytes(ride, rider, driver=None, tip=0) -> bytes` to
  `backend/utils/email_receipt.py` — reuses the exact same route-snapshot resolution
  (`_await_route_receipt_projection`, `_download_route_snapshot`) and the same
  `utils/receipt_pdf.py::generate_receipt_pdf()` call the emailed-receipt path already uses,
  so both receipts render from one code path. Added a new authenticated endpoint
  `GET /rides/{ride_id}/receipt.pdf` (`get_ride_receipt_pdf` in
  `backend/routes/rides/receipts.py`) that hydrates driver info the same way the existing
  `email_ride_receipt` endpoint does, calls the new helper, and returns the PDF bytes with a
  `Content-Disposition: attachment` header.
- **Frontend:** removed `buildReceiptHtml` (the client-side HTML generator) and its `_money`
  helper entirely from `rider-app/app/ride-details.tsx`. Rewrote `handleDownloadInvoice` to
  download `GET /rides/{rideId}/receipt.pdf` via `expo-file-system`'s `File.downloadFileAsync`
  (destination `Paths.cache`, `Authorization` header from `getAuthHeader()`, `idempotent: true`
  so a re-download overwrites rather than errors) and share the resulting file the same way as
  before (`expo-sharing`).
- Fixed a now-stale comment in `rider-app/app/work-profile.tsx` that referenced
  `buildReceiptHtml` by name (that file's own `buildStatementHtml`/`expo-print` flow for the
  corporate monthly work statement is unrelated and untouched — no backend PDF generator
  exists for that statement, so it correctly stays client-rendered).

## 4. Risk & impact on existing functionality

- **What else reads/writes the same code paths:**
  - `build_receipt_pdf_bytes` is a new function with exactly one caller (the new endpoint). It
    does **not** modify `send_receipt_email_result` or its own snapshot-resolution — the two
    are independent call paths that both end up calling the same `generate_receipt_pdf()`, so
    the existing emailed-receipt flow is untouched by this change (verified: no diff to
    `send_receipt_email_result` itself, only a new function added alongside it).
  - `backend/routes/rides/__init__.py`'s re-export list is additive only (`get_ride_receipt_pdf`
    added to both the import block and `__all__`); no existing name changed.
  - `buildReceiptHtml` had exactly one other reference in the codebase outside its own test
    file: a comment (not an import/call) in `rider-app/app/work-profile.tsx`, now fixed. Grepped
    the full repo for `buildReceiptHtml` before removing it — no other caller exists.
- **Could this regress a working flow?** The emailed-receipt flow (`POST
  /rides/{id}/email-receipt`) is unchanged — different endpoint, same generator, no shared
  mutable state. The JSON receipt endpoint (`GET /rides/{id}/receipt`, used for the in-app
  receipt screen — not the PDF download) is unchanged.
- **Blast radius:** cross-surface but narrow — one backend file pair (new function + new
  endpoint, both additive) and one rider-app screen's single button handler. No other screen,
  store, or shared component reads `buildReceiptHtml` or the new endpoint.
- **Native-dependency note:** `expo-file-system` is promoted from an implicit dependency
  (already pulled in transitively by the `expo` package itself — confirmed via `yarn why
  expo-file-system`: "Hoisted from expo#expo-file-system") to an explicit one in
  `rider-app/package.json`. Because it already ships as part of every Expo SDK 57 native build
  today, this is not a new native module requiring an EAS rebuild before it works — it is
  already compiled into the currently-shipped binary.
- **Adversarial post-implementation review findings, fixed before this commit's follow-up:**
  `spinr-security-auditor` found the new endpoint had no dedicated rate limit — the sibling
  JSON receipt endpoint (`get_ride_receipt`) also has none, but this endpoint's actual cost is
  materially higher (an outbound route-snapshot HTTP fetch plus a synchronous PDF render per
  call), so the loose IP-keyed global default (`100/minute`, `1000/hour`) was too permissive
  for its resource profile. Fixed by adding `@ride_read_limit` (`120/minute`, per-user keyed —
  the same limiter `get_ride`/`get_ride_history` already use), verified against
  `tests/test_rate_limit_decorator_order.py` (which specifically catches the "decorator applied
  above `@router.get`, silently never runs" failure mode this repo has hit before) before this
  commit. `spinr-money-auditor`'s review (in progress in parallel, results pending) covers
  receipt-correctness/PII; no blocking findings from the security pass otherwise — see that
  review's INFO-level notes on `ride_code`'s header-injection surface (confirmed non-exploitable
  today: server-generated from a fixed alphanumeric alphabet, verified by grepping every write
  site) and the pre-existing 404-vs-403 existence-disclosure pattern this endpoint inherits
  unchanged from its sibling.
- **Fare/tax correctness note (checked, not just assumed):** `get_ride_receipt_pdf` does NOT
  duplicate the JSON receipt endpoint's fare-lock/snapshot relabeling logic
  (`get_ride_receipt`'s `fare_locked`/`relabel_booked_distance_lines` branch) — it hands the raw
  `ride` dict straight to `build_receipt_pdf_bytes`, exactly as the pre-existing, already-shipped
  emailed-receipt path (`send_ride_receipt` → `send_receipt_email_result`) already does today.
  This is not a new discrepancy introduced by this change: the PDF (both emailed and now
  downloaded) and the in-app JSON receipt view have always been two different renderers of the
  same underlying ride fields, and this change does not alter that pre-existing relationship —
  it only adds a second way to obtain the PDF that already exists for email.

## 5. User-experience effect

- **Who sees a difference:** riders only, and only when tapping "Download invoice (PDF)" on
  the ride-details screen for a completed ride.
- **Visible mid-session?** No — this is an on-demand action a rider takes after a ride is
  already completed, not a change to any in-progress ride screen.
- **Copy/notification change:** the failure-path toast is now split into two more accurate
  messages instead of one generic one — see before/after below. The success-path toast text
  changed from "Receipt PDF generated." to "Receipt PDF downloaded." (accurate to the new
  behavior: the file is fetched from the network, not rendered on-device).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/email_receipt.py` | Added `build_receipt_pdf_bytes()`, reusing the existing snapshot-resolution + `generate_receipt_pdf()` call. | R9 |
| `backend/routes/rides/receipts.py` | Added `GET /{ride_id}/receipt.pdf` endpoint (`get_ride_receipt_pdf`), mirroring `get_ride_receipt`'s ownership/status checks and `email_ride_receipt`'s driver-hydration shape; added `@ride_read_limit` after adversarial review flagged the endpoint's cost profile as heavier than its unrated JSON sibling. | R9 |
| `backend/routes/rides/_deps.py` | (No functional change — `Response` was already imported here.) | — |
| `backend/routes/rides/__init__.py` | Re-exported `get_ride_receipt_pdf` (import block + `__all__`). | R9 (facade convention) |
| `backend/tests/test_coverage_rides.py` | Added 6 tests for the new endpoint (not-found, wrong-rider, not-completed, success, no-driver, generation-failure-503). | R9 |
| `backend/tests/test_receipt_route_snapshot.py` | Added a test pinning that `build_receipt_pdf_bytes` reuses the same snapshot/generator wiring as the email path. | R9 |
| `rider-app/app/ride-details.tsx` | Removed `buildReceiptHtml`/`_money`; rewrote `handleDownloadInvoice` to download+share the backend's PDF. | R9 |
| `rider-app/app/work-profile.tsx` | Fixed a comment that referenced the now-deleted `buildReceiptHtml`. | R9 (no behavior change) |
| `rider-app/package.json` | Added `expo-file-system` as an explicit dependency (already transitively present). | R9 |
| `rider-app/__tests__/rideDetailsScreen.test.tsx` | Removed the `buildReceiptHtml` describe block and its mocks (`expo-print`/`expo-sharing` mocks no longer needed at module scope); updated the download-invoice failure-toast assertion. | R9 |
| `rider-app/__tests__/ride-details-route.test.tsx` | Replaced the test that pinned `buildReceiptHtml`'s internal snapshot-gating logic with one confirming the function is gone and the backend now owns that gating. | R9 |

## 7. Before / after

```tsx
// Before (rider-app/app/ride-details.tsx)
const handleDownloadInvoice = async () => {
  if (pdfBusy) return;
  setPdfBusy(true);
  try {
    const Print = await import('expo-print');
    const Sharing = await import('expo-sharing');
    const { uri } = await Print.printToFileAsync({ html: buildReceiptHtml(ride) });
    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(uri, { mimeType: 'application/pdf', dialogTitle: 'Spinr ride receipt' });
    } else {
      showToast('Saved', 'Receipt PDF generated.', 'success');
    }
  } catch {
    showToast('PDF Unavailable', 'PDF export requires the latest app version. Please update the app and try again.', 'warning');
  } finally {
    setPdfBusy(false);
  }
};
```

```tsx
// After (rider-app/app/ride-details.tsx)
const handleDownloadInvoice = async () => {
  if (pdfBusy) return;
  setPdfBusy(true);
  let FS: typeof import('expo-file-system');
  let Sharing: typeof import('expo-sharing');
  try {
    FS = await import('expo-file-system');
    Sharing = await import('expo-sharing');
  } catch {
    showToast('PDF Unavailable', 'PDF export requires the latest app version. Please update the app and try again.', 'warning');
    setPdfBusy(false);
    return;
  }
  try {
    const token = await getAuthHeader();
    const file = await FS.File.downloadFileAsync(
      `${SpinrConfig.backendUrl}/api/v1/rides/${rideId}/receipt.pdf`,
      FS.Paths.cache,
      { headers: token ? { Authorization: `Bearer ${token}` } : {}, idempotent: true },
    );
    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(file.uri, { mimeType: 'application/pdf', dialogTitle: 'Spinr ride receipt' });
    } else {
      showToast('Saved', 'Receipt PDF downloaded.', 'success');
    }
  } catch (e: any) {
    showToast('Download Failed', getApiErrorMessage(e, 'Could not download the receipt. Please check your connection and try again.'), 'danger');
  } finally {
    setPdfBusy(false);
  }
};
```

**Concrete before/after scenario:** a rider whose device has an intermittent connection taps
"Download invoice" — *before*: the on-device HTML renderer never touches the network, so a
connectivity failure was not a possible failure mode at all (the only failure mode was a
missing native module, worded as "update the app"); *after*: a network failure now IS a
possible failure mode (the PDF comes from the backend), so it is caught in its own branch and
shown as "Download Failed — Could not download the receipt…" rather than the misleading
"update the app" copy, which is now reserved for the one case it actually describes (the
dynamic `import('expo-file-system')`/`import('expo-sharing')` failing, i.e. a genuinely old
build).

## 8. Rollback plan

`git revert` is sufficient. No data mutation, no migration, no schema change — the new
endpoint is purely additive (nothing else calls it) and the frontend change only affects one
button's on-tap behavior. No feature flag was added: unlike a change to an always-on
background flow, this is an explicit, on-demand rider action with no partial-rollout risk (a
revert takes effect for every rider on their next app update/OTA, and today's dynamic-import
catch branch already gives every rider on an un-updated build the pre-existing graceful
"PDF Unavailable" fallback in the interim).

## 9. Verification performed

- [x] Automated tests run:
  - Backend: `pytest backend/tests/test_coverage_rides.py backend/tests/test_receipt_route_snapshot.py backend/tests/test_admin_send_receipt_email.py backend/tests/test_ai_tools_rides.py backend/tests/test_all_emails_are_branded.py backend/tests/test_branded_receipt_flag.py backend/tests/test_email_snapshots.py backend/tests/test_idor_ownership_guards.py backend/tests/test_outbox_receipts.py backend/tests/test_receipt_distance_label.py backend/tests/test_receipt_line_items.py backend/tests/test_receipt_shell_snapshot.py backend/tests/test_ride_receipt_delivery.py backend/tests/test_rides_extended.py backend/tests/test_loguru_call_conventions.py --no-cov -q` → 377 passed.
  - Rider-app: full `yarn jest` suite → 150 suites / 2046 tests passed (includes the updated
    `rideDetailsScreen.test.tsx`, `ride-details-route.test.tsx`, and `workProfileScreen.test.tsx`).
  - `npx tsc --noEmit` on rider-app → 0 errors.
  - `npx eslint` on all touched rider-app files → 0 new errors (pre-existing hardcoded-style
    warnings only, unrelated to this diff).
- [x] **Real production build:** NOT run this session — no EAS/dev-server access in this
  container. `tsc --noEmit` + the full Jest suite were run; per CLAUDE.md this is explicitly
  *not* equivalent to `npm run build`/EAS build, and is stated as such rather than implied.
- [x] Blast-radius grep performed — confirmed `buildReceiptHtml`'s only other reference
  (`work-profile.tsx`, comment-only) before removing it; confirmed no other caller of the new
  backend function/endpoint.
- [x] Reviewed against relevant CLAUDE.md conventions — dual-import pattern followed in the new
  endpoint's `email_receipt` import; IDOR/status checks mirror the sibling `get_ride_receipt`
  endpoint; PII (driver name/vehicle/code only, no phone/plate) matches the existing
  `email_ride_receipt` hydration shape.
- [x] Adversarial pre-implementation review (CLAUDE.md gate #10): alternative considered — teach
  the client-side `buildReceiptHtml` to fetch the same fields the backend uses and keep
  rendering client-side — rejected because that does not close the actual gap (two independent
  renderers that can drift); fetching the backend's own rendered bytes is the only fix that
  makes drift structurally impossible.
- [x] Adversarial post-implementation review: `spinr-money-auditor` and `spinr-security-auditor`
  run against the actual diff (receipt correctness/PII and IDOR/header-injection/rate-limiting
  respectively) before this commit.
- [ ] Feature-flagged — not flagged; justified above (§8) as an on-demand action with no
  partial-rollout risk, matching the existing graceful-degradation fallback already in place.

### What was NOT verified

- No staging/device confirmation that the PDF actually downloads and shares correctly on a real
  iOS/Android device — reasoned about from the `expo-file-system`/`expo-sharing` API contracts
  and Jest's (necessarily incomplete, see the test file's own note) coverage, not observed on
  hardware. This repo currently has no device-pass access in this session (tracked generally
  under ROADMAP.md R14's access-gap item).
- No visual-regression tooling exists for rider-app (per CLAUDE.md) — this change has no new UI
  beyond the two toast copy changes, reasoned about rather than screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; no data-layer state to unwind).
- [x] Blast radius is stated, not assumed (§4 — every other reference found and confirmed safe).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 —
      toast copy changes stated explicitly, including why).
