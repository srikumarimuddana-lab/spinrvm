# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md C23, Action items 4 and 5 of 5 (frontend wiring) |

## 1. Issue / gap identified

C23 items 4-5's backend endpoints (`GET /rides/{ride_id}/dispute-pack`,
`POST /disputes/{dispute_id}/submit-evidence`, both merged in PR #4194) had
no admin-dashboard UI — the Chargebacks tab (PR #4165, merged) listed
chargebacks but offered no way to download the evidence pack or submit to
Stripe from the browser.

## 2. Root cause

The backend PR was deliberately scoped backend-only (see its own Change
Impact Log) because the Chargebacks tab it needed to extend (PR #4165) was
still open/draft at the time — the tab component didn't exist on `main`
yet. Both prerequisite PRs have since merged, unblocking this follow-up.

## 3. Fix / remediation

- `admin-dashboard/src/lib/api/safety-disputes.ts`: two new client
  functions —
  - `downloadDisputeEvidencePack(rideId, rideCode)`: fetches the zip as a
    blob with a manual `Authorization` header (binary response can't go
    through the shared `request<T>()` JSON helper — mirrors the existing
    `downloadDriverStatement` pattern in `api/drivers.ts` exactly).
  - `submitDisputeEvidence(disputeId, uncategorizedText?)`: POSTs
    `{confirm: true, uncategorized_text?}` through the normal JSON
    `request<T>()` path.
- `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.tsx`: added
  an Actions column with:
  - A "Download evidence pack" icon button per row (any admin who can see
    the tab — same `require_module("support")` gate as the list itself).
  - A "Submit to Stripe" icon button, **super_admin only**
    (`useAuthStore((s) => s.user?.role === "super_admin")`, same pattern
    already used in `compliance/page.tsx`), shown only for open-status
    rows and only when `evidence_submitted_at` is unset — once submitted,
    the row shows a "Submitted" badge instead of a clickable action.
  - A confirmation `Dialog` for the submit action: leads with "This
    immediately submits evidence to Stripe and cannot be undone.", an
    optional textarea to submit edited cover-letter text (falls back to
    the backend's auto-drafted text if left blank), and inline error
    surfacing (`role="alert"`) that keeps the dialog open on failure so
    the admin can retry without re-entering their edits.
- `spinr-design-consistency-reviewer` review (before this log was
  finalized): no blockers. Two warnings both fixed — the dialog's
  irreversibility warning is now the lead sentence rather than buried
  mid-paragraph, and "already submitted" is now a visible "Submitted"
  badge instead of relying solely on a disabled icon button + hover
  tooltip.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the Chargebacks tab.** No other tab, page,
  or shared component was touched.
- `chargebacks-tab.tsx`'s existing fetch/loading/error/empty-state logic
  for the list itself is unchanged — only new state (`downloadingId`,
  `downloadError`, `submitTarget`, `submitText`, `submitting`,
  `submitError`) and a new Actions column were added.
- `api.ts`/`api/safety-disputes.ts` changes are additive exports; grepped
  for other consumers of the existing `getChargebacks`/`Chargeback`
  exports — none found beyond this tab, so no risk of breaking another
  screen.
- The Submit-to-Stripe action calls a backend endpoint that itself ships
  dark behind `dispute_stripe_evidence_submission_enabled` (default off)
  — this UI change alone cannot cause a live Stripe submission; the
  backend flag is the actual gate. An admin clicking the button before the
  flag is flipped on gets a clean 503 surfaced in the dialog, not a silent
  failure.

## 5. User-experience effect

- **Internal admin (support-module / super_admin) only** — no
  rider/driver/corporate-facing change.
- Visible to an admin browsing the Chargebacks tab; not a mid-session
  change to an in-progress rider/driver flow.
- New copy: the download button's tooltip, the submit confirmation
  dialog's warning text, and the error/success states. Tone matches the
  tab's existing plain, non-technical copy (reviewed by
  `spinr-design-consistency-reviewer`, no tone mismatch found).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api/safety-disputes.ts` | Added `downloadDisputeEvidencePack`, `submitDisputeEvidence`, `SubmitDisputeEvidenceResult` type | API clients for items 4-5's backend endpoints |
| `admin-dashboard/src/lib/api.ts` | Re-exported the two new functions/type | Barrel-file convention |
| `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.tsx` | Added Actions column (download + submit), confirmation dialog | Surface items 4-5 in the UI |
| `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.test.tsx` | New, 7 tests | Cover list rendering, download success/error, super_admin gating, submit success/error, submitted-badge state |

## 7. Before / after

```tsx
# Before: no Actions column
<TableRow>
  <TableCell>{c.ride_code}</TableCell>
  ...
  <SortableHead column="created_at">Filed</SortableHead>
</TableRow>
```

```tsx
# After: Actions column with download + (super_admin-gated) submit
<TableRow>
  <TableCell>{c.ride_code}</TableCell>
  ...
  <TableCell>
    <Button onClick={() => handleDownloadPack(c)} aria-label={...}>
      <Download />
    </Button>
    {isSuperAdmin && OPEN_STATUSES.has(c.status) && (
      c.evidence_submitted_at
        ? <Badge>Submitted</Badge>
        : <Button onClick={() => openSubmitDialog(c)} aria-label={...}><Send /></Button>
    )}
  </TableCell>
</TableRow>
```

## 8. Rollback plan

`git revert` — purely additive UI calling two already-merged, already
flag-gated backend endpoints. No schema, no new backend risk introduced by
this change specifically (the backend's own flag/confirm/require_super_admin
gates, already reviewed and merged in PR #4194, are what actually control
whether a real Stripe call can happen).

## 9. Verification performed

- [x] Automated tests: 7 new tests in `chargebacks-tab.test.tsx`, all
  passing (`vitest run src/app/dashboard/disputes/`)
- [x] Regression check: `vitest run src/__tests__/dashboard/pages.smoke.test.tsx`
  (24 tests) still passes
- [x] `eslint` clean on all touched files (only the two pre-existing
  `react-hooks/set-state-in-effect` warnings on unmodified lines, same as
  before this change)
- [x] **Real production build run**: `npm run build` — succeeded,
  `/dashboard/disputes` compiled with no errors (not just a dev server or
  `tsc --noEmit`)
- [x] `spinr-design-consistency-reviewer` review: no blockers; two
  warnings (dialog copy ordering, submitted-state visibility) both fixed
  before this log was written
- [ ] Manual click-through against a real backend — not performed (no
  staging admin-dashboard/backend pairing available in this session);
  exercised entirely against mocked API responses in tests
- [ ] `spinr-accessibility-reviewer` — not run for this specific diff
  (was run on the original tab in PR #4165); the two new icon buttons
  follow the same `aria-label`+`title` pattern already reviewed there, and
  the dialog's error text uses `role="alert"`, but a dedicated pass on the
  new Dialog/Textarea wasn't performed — flagged as a gap, not silently
  assumed clean

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no new
  backend risk introduced by this piece)
- [x] Blast radius is stated, not assumed: isolated to one tab, additive
  API exports with no other consumers
- [x] No silent behavior change to an already-shipped flow — the existing
  chargebacks list's fetch/loading/error/empty logic is untouched, only
  extended with a new column
