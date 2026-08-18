# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md C23, Action items 4 and 5 (of 5) |

## 1. Issue / gap identified

Once a chargeback exists, assembling a response is entirely manual: 4–6
endpoints and 3 SQL queries by hand (per the existing runbook), and there is
no way to submit evidence to Stripe from the admin dashboard at all —
support has to use the Stripe Dashboard directly.

## 2. Root cause

C23 items 1–3 (evidence-deadline capture, T-3-days alert, read-only
Chargebacks tab) gave visibility into that a chargeback exists and when it's
due, but never built the response tooling itself — that was explicitly
scoped as items 4–5, deferred as higher-effort/higher-risk in the original
finding.

## 3. Fix / remediation

- **Item 4** — new `GET /api/admin/rides/{ride_id}/dispute-pack`: zips
  together an invoice-summary PDF, the existing route-map PNG, a
  ride-timeline + account-history PDF, a GPS-trail CSV, and a draft cover
  letter. Read-only download for a human to review/edit.
- **Item 5** — new `POST /api/admin/disputes/{dispute_id}/submit-evidence`:
  calls `stripe.Dispute.modify(evidence=...)` to actually submit evidence
  to Stripe. This is the highest-risk piece of C23 — a real, effectively
  irreversible external write (evidence can be updated but not
  un-submitted before the dispute's `evidence_due_by`) — so it:
  - Ships dark behind a new `dispute_stripe_evidence_submission_enabled`
    app_settings flag, default off (same "ship dark, flip on after
    staging verification" pattern as the existing
    `corporate_subscription_billing_enabled` flag).
  - Requires an explicit `confirm: true` on every request — the flag
    alone never makes a single call submit.
  - Is gated by `require_super_admin` at both the router mount and the
    handler dependency (same posture as `stripe_payout_sync` /
    `stripe_connect_ledger` / `tax_id_import`), stricter than item 4's
    general "support" module gate.
  - Takes an atomic claim on `evidence_submitted_at IS NULL` **before**
    calling Stripe (not after — see review-fix note below), rolling the
    claim back if the Stripe call fails so a genuine retry isn't
    permanently blocked.
- New shared modules: `utils/dispute_evidence_pack.py` (ride timeline,
  GPS-trail rows, account-history summary, draft cover letter — reused by
  both endpoints so they can't drift on PIPEDA filtering) and
  `utils/dispute_evidence_pdf.py` (renders the PDF pages via the existing
  `report_branding.py` toolkit).
- Factored the existing `route-map.png` endpoint's Google Static Maps logic
  into a shared `_fetch_route_map_png_bytes()` helper in `rides.py` so both
  the original endpoint and the new pack reuse it instead of duplicating
  the PIPEDA GPS-phase filter. The original endpoint's behavior and its 44
  pre-existing tests are unchanged.

**Review-driven fixes (both real, both fixed before this log was written,
not left as follow-ups):**
- `spinr-security-auditor` found item 4's zip-download endpoint was
  initially mounted under `rides_router` and inherited
  `require_module("rides")` — over-granting to any ops/dispatch admin with
  no dispute-handling authorization, and under-granting to support-only
  admins (the actual intended users who see the Chargebacks tab, C23 item
  3). Fixed by moving it into its own router
  (`routes/admin/dispute_pack_download.py`) mounted with
  `require_module("support")`.
- `spinr-security-auditor` also found the ride-timeline builder labeled
  every offered-but-not-assigned driver with their **full name**
  (`offer.driver_name`), contradicting this module's own stated
  driver-code-only PIPEDA policy. Fixed: offer events in the timeline now
  carry no driver identifier at all.
- `spinr-money-auditor` found the idempotency claim in item 5 was taken
  **after** the live Stripe call, not before — two concurrent/retried
  requests could both pass the plain-read "already submitted?" guard and
  both reach Stripe. Fixed: the claim is now atomic and taken before the
  Stripe call, with rollback on Stripe failure.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated for item 4, contained-but-external for item 5.**
  - Item 4 is a new GET endpoint plus a pure-refactor of the existing
    route-map.png logic (verified behavior-identical by the money-auditor
    review and by the 44 pre-existing route-map tests all still passing
    unmodified).
  - Item 5 is a new POST endpoint with a real external side effect
    (Stripe), but ships dark (flag off) and requires two independent
    deliberate human actions (an admin flipping the flag in Settings, and
    a super_admin passing `confirm: true` on a specific request) before it
    can fire for real in production.
- Grepped `routes/admin/rides.py` and `routes/admin/__init__.py` for other
  consumers/importers of `_fetch_route_map_png_bytes` and the moved
  endpoint — none found; this is the only caller besides the original
  `route-map.png` handler.
- No interaction with the ride state machine, wallet/allowance deltas, or
  any of the 16 background loops.
- The new `stripe_disputes.evidence_submitted_at` claim column already
  existed (migration 326, C23 item 1) — no new migration needed for either
  item.

## 5. User-experience effect

- **Internal admin only** (support-module / super_admin), no
  rider/driver/corporate-facing change.
- Not visible mid-session to any end user — this is a support/ops tool for
  chargeback response, not a live ride/payment surface.
- Frontend wiring (a "Download evidence pack" button and a "Submit to
  Stripe" confirmation flow on the Chargebacks tab from PR #4165) is
  **not** included in this PR — PR #4165 (C23 item 3) is still open/draft
  at the time of this PR, and the tab component it would extend doesn't
  exist on `main` yet. This PR is backend-only; the UI wiring is a planned
  follow-up once #4165 merges.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/dispute_evidence_pack.py` | New | Shared PIPEDA-filtered evidence assembly (timeline, GPS CSV rows, account-history summary, cover letter) |
| `backend/utils/dispute_evidence_pdf.py` | New | Renders the invoice-summary and timeline/account-history PDF pages |
| `backend/routes/admin/dispute_pack_download.py` | New | `GET /rides/{ride_id}/dispute-pack` zip download, `require_module("support")` |
| `backend/routes/admin/dispute_evidence_submission.py` | New | `POST /disputes/{dispute_id}/submit-evidence`, ships dark, `require_super_admin`, atomic pre-Stripe-call claim |
| `backend/routes/admin/rides.py` | Factored `_fetch_route_map_png_bytes()` out of the existing route-map.png handler; removed unused imports | Shared by both the original endpoint and the new pack download without duplicating logic |
| `backend/routes/admin/settings.py` | Added `dispute_stripe_evidence_submission_enabled` flag | Ship-dark gate for item 5 |
| `backend/routes/admin/__init__.py` | Mounted the two new routers | `dispute_pack_download_router` → `require_module("support")`; `dispute_evidence_submission_router` → `require_super_admin` |
| `backend/tests/test_dispute_evidence_pack.py` | New | 13 tests for the assembly module |
| `backend/tests/test_dispute_evidence_pdf.py` | New | 6 tests for PDF rendering |
| `backend/tests/test_admin_dispute_pack_download.py` | New (renamed from an interim filename) | 6 tests for the zip-download endpoint |
| `backend/tests/test_admin_dispute_evidence_submission.py` | New | 15 tests for the submission endpoint, including 3 for the claim-ordering fix |

## 7. Before / after

```python
# Before: idempotency claim taken AFTER the Stripe call
stripe.Dispute.modify(stripe_dispute_id, evidence={...}, api_key=stripe_secret)
claimed = await db_supabase.update_one(
    "stripe_disputes", {"id": dispute_id, "evidence_submitted_at": None}, {"$set": {...}}
)
```

```python
# After: atomic claim taken BEFORE the Stripe call, released on failure
claimed = await db_supabase.update_one(
    "stripe_disputes", {"id": dispute_id, "evidence_submitted_at": None}, {"$set": {...}}
)
if claimed is None:
    raise HTTPException(status_code=409, detail="Evidence submission already in progress or completed")
try:
    stripe.Dispute.modify(stripe_dispute_id, evidence={...}, api_key=stripe_secret)
except Exception as exc:
    await _release_claim(dispute_id)  # never reached Stripe -- don't permanently block a retry
    raise HTTPException(status_code=502, ...) from exc
```

```python
# Before: offer events labeled with the driver's full name
_add(f"offer_sent (driver {driver_label})", offer.get("offered_at"))
```

```python
# After: no driver identifier on offer events at all
_add(f"offer_{i}_sent", offer.get("offered_at"))
```

## 8. Rollback plan

- **Item 4** (read-only download): `git revert` — no writes, no live data
  touched.
- **Item 5** (Stripe submission): the ship-dark flag
  (`dispute_stripe_evidence_submission_enabled`) is the rollback — flip it
  off in Settings to immediately disable the endpoint for all requests,
  no deploy needed. A `git revert` is also safe on top of that since the
  endpoint made no schema change (reuses the existing
  `evidence_submitted_at` column from migration 326). If a real evidence
  submission has already gone to Stripe before a rollback, that submission
  itself is not revertible via code — same as any other completed Stripe
  API call — but the endpoint's own effect (the claim) can be cleared with
  a direct `UPDATE stripe_disputes SET evidence_submitted_at = NULL WHERE
  id = '<id>'` if a support agent needs to resubmit through the Stripe
  Dashboard instead.

## 9. Verification performed

- [x] Automated tests: 86 new/updated tests across the 4 new/modified test
  files, all passing (`pytest tests/test_dispute_evidence_pack.py
  tests/test_dispute_evidence_pdf.py tests/test_admin_dispute_pack_download.py
  tests/test_admin_dispute_evidence_submission.py -q`)
- [x] Regression check: `tests/test_admin_rides_read_endpoints_coverage.py`
  (44 tests, including the 6 pre-existing route-map.png tests) and
  `tests/test_admin_module_list_parity.py` (8 tests) still pass unmodified
  — confirms the route-map refactor and the new router mounts didn't break
  anything existing
- [x] Broader regression sweep: `pytest -k "dispute or admin_rides or
  admin_module_list or admin_settings"` — 381 passed, 1 skipped (pre-existing,
  unrelated)
- [x] `ruff check` / `ruff format --check` clean on every touched file
- [x] `spinr-money-auditor` review: found and fixed the claim-ordering race
  (see Section 3); verified Decimal-only money math in both new utils
  files, error surfacing (not swallowed), and confirmed the route-map
  refactor is behavior-identical to the original
- [x] `spinr-security-auditor` review: found and fixed the RBAC gate
  mismatch and the driver-name PIPEDA leak (see Section 3); verified no
  CSV/zip injection surface, no header-injection surface on the
  Content-Disposition filename, and correct double-gating (mount +
  handler) on the submission endpoint
- [ ] Manual repro against real Supabase/Stripe test mode — not performed
  (no staging Supabase or Stripe test-mode credentials available in this
  session; exercised entirely against mocked `db_supabase` and mocked
  `stripe.Dispute.modify` in tests). **This endpoint's flag should stay
  off in production until someone with staging access runs it against a
  real Stripe test-mode dispute at least once**, per this repo's own
  ship-dark convention.
- [ ] Frontend wiring not built/tested in this PR (see Section 5) —
  deferred until PR #4165 merges

## 10. Sign-off

- [x] Rollback plan is concrete and testable: item 4 is a plain revert;
  item 5's flag-off is an immediate, no-deploy kill switch
- [x] Blast radius is stated, not assumed: item 4 isolated/read-only, item
  5 contained by flag + confirm + super_admin but genuinely external once
  flipped on
- [x] No silent behavior change to an already-shipped flow — the existing
  `route-map.png` endpoint's behavior is unchanged (refactor only,
  confirmed by its unmodified pre-existing test suite)
