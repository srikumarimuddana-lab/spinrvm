# Change Impact & Risk Log: company portal confirmations use the in-app dialog

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard app, company portal (`/company-portal/[id]/…`), used by corporate customers |
| Domain (Sentry tag) | corporate (confirmation UI only) |
| PR / commit link | UX program W2.1b — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Plan W2.1; uses the `useConfirm()` hook from W2.1 |

## 1. Issue / gap identified

Two company-portal actions confirmed with the browser's `window.confirm()` pop-up:
- **Bookings:** cancel a booking. This cancels a pre-trip ride and texts the customer.
- **Sections:** archive a section.

## 2. Root cause

Written before the shared in-app confirmation existed. W2.1 kept these two out of the staff-dashboard PR because this surface faces customers.

## 3. Fix / remediation

Both use `useConfirm()`, with the same wording and control flow:
- **Booking cancel:** the buttons read **"Keep booking"** and **"Cancel booking"** (red), rather than "Cancel" / "OK", so there aren't two different "Cancel"s side by side.
- **Section archive:** the confirm button is "Archive section", in the default style, since archiving is reversible.

## 4. Risk & impact on existing functionality

- **Blast radius:** two company-portal pages; no staff pages and no API changes.
  - Booking cancel still calls `cancelCompanyBooking` only after an explicit confirm; the ride state machine and backend are untouched.
  - The company portal has no visual-regression baseline.
- **Keyboard:** focus starts on "Keep booking" or Cancel, so Enter no longer confirms, unlike `window.confirm()`.
- **Busy state:** the booking row's spinner only starts after confirm, as before (`setCancelling` follows the confirm).

## 5. User-experience effect

- **Company admins and members** (corporate customers, live-tested): cancelling a booking or archiving a section shows an in-app dialog in the portal's style instead of a browser pop-up.
- **Wording:** the explanation text is unchanged; the booking dialog's buttons now say "Keep booking" / "Cancel booking".
- **Mid-session:** applies on the next page load after deploy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/company-portal/[id]/bookings/page.tsx` | Booking cancel uses `useConfirm()` with Keep/Cancel booking labels | In-app, unambiguous confirmation |
| `admin-dashboard/src/app/company-portal/[id]/bookings/page.test.tsx` | New, 2 tests | Keep booking never cancels; Cancel booking cancels once |
| `admin-dashboard/src/app/company-portal/[id]/sections/page.tsx` | Section archive uses `useConfirm()` | In-app confirmation |

## 7. Before / after

```tsx
// Before
if (!window.confirm("Cancel this booking? The customer will be notified by text.")) return;
```

```tsx
// After
if (!(await confirm({
    title: "Cancel this booking?",
    description: "The customer will be notified by text.",
    confirmLabel: "Cancel booking",
    cancelLabel: "Keep booking",
    destructive: true,
}))) return;
```

## 8. Rollback plan

**No feature flag:** it's confirmation UI with the same wording and flow, and no backend or data change. **Rollback:** revert the PR; the app redeploys through Vercel.

## 9. Verification performed

- [x] **New tests:** 2 in `bookings/page.test.tsx`. Both fail on the old page.
- [x] **Checks:** typecheck and lint pass. The full admin suite and production build were run on the stacked branch (see W2.1's log).
- [x] **Reviews:** the security and accessibility reviews covered these two sites with W2.1, with no blockers. The accessibility nit (a reversible archive shouldn't be red) is applied.

## 10. What was NOT verified

- **Section archive** has no page test; it relies on the hook's tests.
- **No company-user session was used,** and nothing was screenshotted; the portal has no visual baseline.
- **No screen-reader pass.**
