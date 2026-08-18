# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md C23, Action item 3 |

## 1. Issue / gap identified

Card-network chargebacks (Stripe disputes, `stripe_disputes` table) have zero admin
visibility. C23 Action items 1–2 already capture `evidence_due_by` and alert the
team via Sentry/logs 3 days before it lapses, but an admin still cannot *see* an
open chargeback, its status, or its deadline without a raw SQL query against
Supabase.

## 2. Root cause

The admin dashboard's existing "Dispute Resolution" page only ever covered
rider-raised refund requests (the `disputes` table). Chargebacks arrive
asynchronously via Stripe webhooks into a separate table and were never
surfaced anywhere in the admin UI — an oversight from when B27/C23's webhook
handling was built without a corresponding admin view.

## 3. Fix / remediation

- New read-only backend endpoint `GET /api/admin/disputes/chargebacks` (in
  `routes/admin/support.py`, same `support` module gate as the existing
  `/disputes` endpoints) that lists `stripe_disputes` rows, enriches each with
  the ride's `ride_code` (not full ride details — PIPEDA data minimization),
  and computes `days_remaining` until `evidence_due_by` for rows still needing
  a response.
- New "Chargebacks" tab on the existing `/dashboard/disputes` admin page,
  wrapping the pre-existing "Rider Disputes" content in a `Tabs` component
  (same pattern already used on `/dashboard/compliance`) rather than
  refactoring it, to keep the diff additive.
- New `chargebacks-tab.tsx` component: status-filter buttons, a sortable
  table (ride, reason, amount, status, evidence due date, days remaining,
  filed date), and pagination — mirroring the existing rider-disputes tab's
  fetch/loading/error/empty-state handling exactly.
- This is explicitly **read-only**: chargebacks are still resolved via the
  Stripe Dashboard (per `docs/runbooks/payment-dispute-evidence.md`); no new
  write path, no new mutation of `stripe_disputes`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, additive-only.** No existing endpoint, table
  write, or background loop was modified.
- New backend route is purely additive (`GET`, no side effects) and was
  registered *before* the existing `/disputes/{dispute_id}` path-param route
  — grepped all routes in `routes/admin/support.py` to confirm no other
  literal-path route below `/disputes/{dispute_id}` needed reordering, and
  confirmed via a regression test (`test_registered_before_dispute_id_path_param`)
  that the new route resolves to the `stripe_disputes` table, not the
  dispute-details handler.
- `admin-dashboard/src/lib/api.ts` and `api/safety-disputes.ts` changes are
  additive exports (`getChargebacks`, `Chargeback` type) — grepped both files
  for other consumers of the existing `disputes` exports (`getDisputes`,
  `getDisputeStats`, `getDisputeDetails`, `updateDispute`) to confirm none of
  them were touched; only new symbols were added.
- `page.tsx`: wrapped existing JSX in `<Tabs>` without altering any of its
  internal logic (state, handlers, the resolve-dispute dialog). The rider
  disputes tab's fetch/resolve/dialog behavior is byte-for-byte unchanged;
  only the returned JSX tree gained a `Tabs` wrapper.
- No interaction with ride state machine, wallet/allowance deltas, or any of
  the 16 background loops.

## 5. User-experience effect

- **Internal admin only** — no rider/driver/corporate-admin-facing change.
- Visible to an admin browsing `/dashboard/disputes` at any time; not a
  mid-session change to an in-progress user flow (this is a support/ops
  screen, not a live ride/payment surface).
- No new copy/notification sent to any end user.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/support.py` | Added `GET /disputes/chargebacks` (registered above the `{dispute_id}` path-param routes) | Surface `stripe_disputes` rows with `ride_code` + `days_remaining` enrichment |
| `backend/tests/test_admin_chargebacks_route.py` | New test file, 10 tests | Cover authz, route-ordering, status filter, DB-error surfacing (503, not swallowed), ride-code enrichment, days-remaining math, malformed-timestamp handling |
| `admin-dashboard/src/lib/api/safety-disputes.ts` | Added `Chargeback` interface + `getChargebacks()` | New API client for the endpoint above |
| `admin-dashboard/src/lib/api.ts` | Re-exported `getChargebacks` / `Chargeback` | Keep the existing barrel-file re-export convention |
| `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.tsx` | New component | Renders the chargebacks table/filters/pagination |
| `admin-dashboard/src/app/dashboard/disputes/page.tsx` | Wrapped existing content in `Tabs`; added a "Chargebacks" tab mounting the new component | Surface the new tab without touching existing rider-disputes logic |

## 7. Before / after

```tsx
# Before (page.tsx, abbreviated)
return (
  <div className="space-y-6">
    {/* Header */}
    ...
    {/* Stats */}
    <div className="grid grid-cols-4 gap-4">...</div>
    {/* Disputes Table */}
    <Card>...</Card>
    {/* Resolve Dialog */}
    <Dialog>...</Dialog>
  </div>
);
```

```tsx
# After (page.tsx, abbreviated)
return (
  <div className="space-y-6">
    {/* Header */}
    ...
    <Tabs defaultValue="rider">
      <TabsList>
        <TabsTrigger value="rider">Rider Disputes</TabsTrigger>
        <TabsTrigger value="chargebacks">Chargebacks</TabsTrigger>
      </TabsList>
      <TabsContent value="rider" className="space-y-6">
        {/* Stats */}
        <div className="grid grid-cols-4 gap-4">...</div>
        {/* Disputes Table */}
        <Card>...</Card>
        {/* Resolve Dialog */}
        <Dialog>...</Dialog>
      </TabsContent>
      <TabsContent value="chargebacks">
        <ChargebacksTab />
      </TabsContent>
    </Tabs>
  </div>
);
```

No behavior change to the existing "Rider Disputes" content — this is a pure
wrap, not a rewrite.

## 8. Rollback plan

Purely additive UI + a new read-only GET endpoint with no writes and no
background-loop interaction. Rollback is a plain `git revert` of this commit
— there is no live data (no Stripe charge, no wallet delta, no ride state)
touched by this change, so no data-level remediation is needed.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_admin_chargebacks_route.py -q` — 10/10 passed
- [x] `ruff check` / `ruff format --check` on `routes/admin/support.py` and the new test file — clean
- [x] **Real production build run**: `npm run build` in `admin-dashboard/` — succeeded, `/dashboard/disputes` compiled with no errors (not just `tsc --noEmit` or dev server)
- [ ] Manual repro in staging — not performed (no staging Supabase access in this session; the endpoint was exercised only against mocked `db_supabase.get_rows` in tests)
- [x] Blast-radius grep performed: searched `admin-dashboard/src/lib/api.ts` and `api/safety-disputes.ts` for other consumers of the existing dispute exports; searched `routes/admin/support.py` for route registration order
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA data minimization (only `ride_code` joined, not full ride/rider PII), FastAPI route-ordering convention, do-not-swallow-errors (DB errors on both `stripe_disputes` and `rides` lookups raise 503, not silently return `[]`)
- [x] `spinr-admin-rbac-reviewer` review: SAFE TO MERGE, no blockers. One WARNING noted for future follow-up (not actioned here): the endpoint inherits the router-level `require_module("support")` gate — same as its sibling `/disputes` and `/disputes/stats` endpoints — rather than `require_super_admin`, which this repo reserves for full Stripe-ledger pulls (`stripe_payout_sync`, `stripe_connect_ledger`). Judged acceptable as-is because this endpoint is read-only, returns no card data/PII beyond `ride_code`, and cannot act on a dispute (resolution stays in the Stripe Dashboard) — it's closer in shape to the existing rider-disputes endpoints than to a full ledger pull. Flagged in ACTION_ITEMS.md follow-up note if the sensitivity tier should be revisited.
- [ ] Feature-flagged — not flagged; this is a net-new, additive, internal-admin-only read view with no risk to any live-tested rider/driver/corporate flow, so a flag was judged unnecessary (consistent with "isolated, low-risk" carve-out in the CLAUDE.md gate policy)
- [x] `spinr-design-consistency-reviewer` review: found one real BLOCKER — the fetch failure path silently rendered the identical "No chargebacks found" empty-state as a true zero-chargeback result, with no error surfaced and nothing logged. Fixed: added an `error` state, a visible retry banner above the table on fetch failure, and a `console.error` per CLAUDE.md's do-not-silently-swallow-errors convention. Two WARNINGS noted as pre-existing patterns already present on the sibling rider-disputes tab (not newly introduced, out of scope to fix here): no filter pill for the Stripe `warning_*` early-fraud-warning statuses, and the same empty-catch gap existing on the rider-disputes tab itself (unfixed, since the plan was to keep that tab's logic untouched).
- [x] `spinr-accessibility-reviewer` review: no hard blockers. Fixed the two cheap, in-scope items: added `aria-pressed` to the status-filter buttons, and `role="status"`/`aria-live="polite"`/visually-hidden loading text on the spinner. Two items explicitly NOT verified (stated per CLAUDE.md gate #6 rather than assumed passing): (1) contrast ratio of `text-amber-600` (days-remaining "due soon" color) on white — reasoned to be borderline-to-failing AA for normal-size text but not measured with a real contrast tool, no automated visual-regression tooling exists in this repo; (2) no live-region announcement on table content changing after a filter click or Refresh — judged lower priority than the loading-state fix and left as a follow-up, since it also matches the pre-existing pattern on the sibling rider-disputes tab.
- [x] `spinr-admin-rbac-reviewer` review: SAFE TO MERGE, no blockers, route ordering confirmed correct (chargebacks route not shadowed by `/disputes/{dispute_id}`). One WARNING for future consideration, not actioned: this endpoint inherits the general `require_module("support")` gate (same as its `/disputes` siblings) rather than `require_super_admin`, which this repo reserves for full Stripe-ledger pulls. Judged acceptable since this endpoint is read-only and returns no PAN/PII beyond `ride_code`.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no live-data touch)
- [x] Blast radius is stated, not assumed: isolated, additive-only, no existing route/table/loop modified
- [x] No silent behavior change to an already-shipped flow — the existing "Rider Disputes" tab's logic and JSX are unchanged, only wrapped
