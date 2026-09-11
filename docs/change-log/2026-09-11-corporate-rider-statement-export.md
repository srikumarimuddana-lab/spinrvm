# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (agent session) |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | corporate |
| PR / commit link | (this PR) |
| Related issue or gap ID | none — new capability identified via codebase research, not a tracked backlog item |

## 1. Issue / gap identified

An individual corporate rider had no way to export their own monthly
ride/spend statement for personal expense reporting. Only the company
admin's company-wide statement endpoints existed
(`routes/corporate_company.py`'s `billing_summary`/`billing_statement`/
`billing_statement_pdf`, all gated by `require_company_admin`).
`routes/corporate_rider.py` (the individual member-facing file) exposed
only `/balance` and `/rides` — an in-app list, no export.

## 2. Root cause

Not a bug — a genuine, previously-unbuilt capability gap. The company-wide
statement machinery was built for the company admin's own billing
reconciliation use case and was never adapted to a single rider's own
expense-reporting need, even though the underlying per-member query
(`list_company_ride_payment_sources(..., member_id=...)`) already existed
to support the admin side's per-member breakdown.

## 3. Fix / remediation

Added two new endpoints to `routes/corporate_rider.py`:

```
GET /rider/work-profile/{company_id}/statement/{month}       (JSON)
GET /rider/work-profile/{company_id}/statement/{month}/pdf    (PDF)
```

`member_id` is derived strictly server-side via the existing
`_ensure_member(current_user, company_id)` lookup — never a request
parameter — so there is no way for a rider to request another rider's
statement. The implementation reuses `corporate_company.py`'s
`_month_bounds`/`_attach_ride_tax`/`_aggregate_rows` helpers and the
existing `generate_corporate_statement_pdf` renderer rather than
duplicating them; only the underlying query's `member_id` filter changes
from company-wide to rider-scoped.

Added a "Download This Month's Statement" row to rider-app's Work Profile
screen, which fetches the JSON endpoint and renders it to PDF client-side
via the same `expo-print`/`expo-sharing` pattern `ride-details.tsx`
already uses for its own receipt download.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (corporate rider portal), additive.** No
  existing endpoint, table write path, or background loop is modified —
  both new endpoints are pure `GET`s.
- **Who else reads/writes `list_company_ride_payment_sources` and the
  helpers this reuses**: `routes/corporate_company.py`'s own
  company-admin statement endpoints (unchanged — this PR does not modify
  that file, only imports two of its module-level helper functions) and
  `routes/corporate_accounts.py`'s admin PDF download path (same
  helpers, also unmodified). Grepped for every other caller of
  `list_company_ride_payment_sources`, `_month_bounds`, `_attach_ride_tax`,
  and `_aggregate_rows` — no other call site is affected, since this PR
  only adds new call sites, changing nothing about the functions
  themselves.
- **The authorization boundary is the load-bearing risk here, and it's
  directly tested**: `test_statement_scopes_strictly_to_own_member_id`
  seeds two members' rows in the mocked DB (one with a $999 spend) and
  asserts the caller's own statement contains only their own $10 line
  item and total — the other member's spend never appears or gets summed
  in, even though both belong to the same company and month.
- **No money movement**: this is read-only reporting over already-settled
  `ride_payment_sources` rows. It never calls
  `corporate_wallet_apply_delta` or writes any wallet/allowance/billing
  state, so it cannot itself cause a double-charge, a missed charge, or a
  balance drift.

## 5. User-experience effect

**Rider-facing.** A corporate rider now sees a new "Download This Month's
Statement" row on the Work Profile screen. Not visible mid-session in any
disruptive way — it's an additive row a rider can ignore entirely; no
existing screen, flow, or notification changes. No admin- or
driver-facing change at all.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_rider.py` | Added the two new statement endpoints + helper functions. | The new capability. |
| `backend/tests/test_corporate_rider_routes.py` | Added 7 tests (own-statement success, cross-member isolation, 403 non-member, empty-month, malformed-month 422, PDF variant + audit log). | Coverage, especially the authorization boundary. |
| `rider-app/app/work-profile.tsx` | Added the "Download This Month's Statement" row + `buildStatementHtml` + `handleDownloadStatement`. | UI entry point. |

## 7. Before / after

Pure additive change — no existing behavior-changing diff to show (no
existing endpoint or function body was altered, only new ones added and
two existing helper functions imported unchanged).

## 8. Rollback plan

`git-revert-safe` — this PR adds two new endpoints and one new UI row;
nothing here has been applied to live data (no migration, no wallet
delta, no ride-state write). A plain revert removes the capability with
no further remediation needed.

## 9. Verification performed

- [x] **Automated tests run**: `cd backend && python3 -m pytest tests/test_corporate_rider_routes.py --no-cov -q` → **29 passed** (22 pre-existing + 7 new).
- [x] `ruff check`/`ruff format --check` clean on both changed backend files.
- [x] `cd rider-app && npx tsc --noEmit` — clean.
- [x] `npx eslint app/work-profile.tsx` — same warning count as before the change (verified via `git stash` diff), 0 new errors.
- [x] Blast-radius grep performed — every other caller of
  `list_company_ride_payment_sources`, `_month_bounds`, `_attach_ride_tax`,
  `_aggregate_rows` (see §4).
- [ ] Manual repro steps followed in staging — not run; no staging Supabase environment reachable from this sandbox.
- [ ] Feature-flagged — not flagged; judged unnecessary for a new, purely additive, read-only GET endpoint with no existing-flow behavior change, per CLAUDE.md's flagging rule being aimed at user-visible/non-trivial *behavior changes*, not net-new opt-in reporting features. Reconsider if reviewers disagree.

## 10. What was NOT verified

- **Not run against a real Supabase project** — mocked-Supabase unit tests only.
- **Not run in a mobile simulator/device** — only `tsc`/`eslint` on the rider-app change; the actual PDF generation/share flow was not exercised end-to-end on a device.
- **No visual regression tooling exists for rider-app** — the new UI row was reasoned about (matches the existing "Request More Funds" row's exact style), not screenshotted, per CLAUDE.md's disclosure requirement for this surface.
