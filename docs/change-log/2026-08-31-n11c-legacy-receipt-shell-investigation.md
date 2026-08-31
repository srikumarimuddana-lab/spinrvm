# N11c — Legacy receipt/invoice shell deletion: investigation (no code change)

**Date:** 2026-08-31
**Outcome:** Investigated only. **Nothing deleted.** The precondition
ACTION_ITEMS.md N11c sets for deletion ("once the branded version has been
seen in real inboxes") is only partially confirmed, and not confirmed at all
for one of the two shells this item covers.

## Issue/gap identified

`backend/utils/email_receipt.py` carries a legacy, pre-retrofit receipt HTML
shell (`_LEGACY_BRAND_RED`, `_LEGACY_HEADER`, `_LEGACY_FOOTER`, and the
branch at line ~460 that selects them) behind
`app_settings.branded_receipt_enabled` (default `true`, checked at line
~548). `backend/utils/subscription_invoice.py` (~line 68) and
`backend/utils/subscription_invoice_pdf.py` (~line 238) gate the Spinr Pass
invoice shell on the same flag. N11c proposes deleting both legacy branches,
the flag check, and any admin toggle, once the branded path has proven
itself live. Nobody had checked that precondition against real data before
this pass — it was carried in ACTION_ITEMS.md as an open assumption.

## What was checked

Read-only Supabase MCP access to production (`soavhtdhefowwvforzwb`,
`ca-central-1`) was available this session. Every query below was a `SELECT`
— no writes were made to production data.

1. **`settings` table** (the `app_settings` row, `id='app_settings'`):
   `branded_receipt_enabled = true`, `updated_at = 2026-08-21 14:47:35 UTC`.
   `updated_at` is whole-row (any column write bumps it), so it is not
   evidence about this column specifically — see next point.

2. **`schema_migrations`**: `288_settings_branded_receipt_enabled.sql`
   applied `2026-08-18 21:55:59 UTC`, adding the column with default `true`.

3. **`audit_logs`** (`action='settings_updated'` rows, `changed_keys` in
   `details`): searched every settings-change audit row for
   `branded_receipt_enabled` in `changed_keys`. It never appears — from
   before the column existed through the most recent settings change
   (2026-08-21, `legacy_consent_notice_enabled`, unrelated). The flag has
   been `true` since creation and has never been toggled off in production.

4. **`email_send_log`** (`email_type`, `status`, `created_at`) — the
   strongest evidence available, because it reflects actual sends, not just
   flag state:
   - `email_type='receipt'`: 10 rows with `status='sent'` at or after
     2026-08-18 21:55:59 (the flag's creation timestamp), from
     2026-08-18 23:01:56 to 2026-08-28 22:52:30. Zero `receipt` failures in
     that window. This is real evidence the branded receipt shell has
     rendered correctly in real inboxes — but the window is ~10 days, not
     the "several weeks" N11c's own wording treats as the bar.
   - `email_type='subscription_invoice'` (the Spinr Pass invoice, gated by
     the same flag): **zero rows of any status** at or after 2026-08-18.
     The last successful send was 2026-07-15 20:59 UTC — over a month
     before the flag existed — and the last row of any kind was a failure
     run ending 2026-07-29 00:16 UTC, three weeks before migration 288
     applied. No branded invoice has been confirmed sent since the flag
     came into being.

## Why this doesn't clear the bar

N11c's precondition is "the branded version has been seen in real inboxes."
For the receipt: partially true, recently, with a short window. For the
invoice: not demonstrated at all — the branded invoice path may never have
actually run in production since the flag was created, or its sends may
simply not be happening for an unrelated reason (see Open questions). Either
way, deleting `_LEGACY_*` and the flag now would remove the invoice's only
proven-working fallback based on zero real-world evidence that its branded
replacement works end-to-end in production.

CLAUDE.md's pre-merge release gates ("Escalate, don't silently ship, when in
doubt") apply directly here: this is a payments-adjacent, live-tested-app
surface (`subscription_invoice` sends real money-related documents to
paying corporate customers), and the evidence is genuinely insufficient to
proceed. Per the task's own instructions, force-completing the deletion on a
guess is the wrong outcome — leaving the code untouched and documenting the
gap is the correct one.

## Open questions for a future pass

- Is `subscription_invoice` simply low-volume (few corporate subscription
  renewals in this window), or is something broken in that send path since
  around 2026-07-29? The gap (zero sends of *any* status for a month) is
  itself worth a look, independent of N11c.
- Once `email_send_log` shows several consecutive weeks of clean
  `subscription_invoice` sends with `branded_receipt_enabled=true` and no
  rollback, re-run this same query set. If both shells clear the bar, the
  actual deletion is a small, well-scoped change mirroring N8's removal of
  `utils/receipt_email.py`: delete `_LEGACY_*` constants and the
  conditional branches in `email_receipt.py`, `subscription_invoice.py`,
  `subscription_invoice_pdf.py`; drop the `branded_receipt_enabled` flag
  check (always take the branded path); remove the admin Settings toggle
  (`routes/admin/settings.py` / admin-dashboard Company Info tab, added in
  the original retrofit per `docs/change-log/2026-08-08-receipt-invoice-branding-retrofit.md`);
  and decide deliberately what happens to the legacy shape pinned in
  `backend/tests/test_receipt_shell_snapshot.py` and the legacy golden file
  in `backend/tests/snapshots/email/` from N12 — removing rather than
  leaving a snapshot with nothing to compare against.

## Files touched this pass

| File | Change | Why |
|---|---|---|
| `ACTION_ITEMS.md` | N11c entry updated with investigation findings | Record what was checked so a future pass doesn't repeat the same queries |
| `docs/change-log/2026-08-31-n11c-legacy-receipt-shell-investigation.md` | New (this file) | Document the negative result per task instructions |

No application code, tests, or migrations were touched. No `pytest`/`ruff`
run needed — nothing in `backend/` changed.

## Rollback plan

N/A — no production or code change was made.

## Verification performed

Four read-only `SELECT` queries against production Supabase via MCP
(`list_projects`, then `execute_sql` against `soavhtdhefowwvforzwb`),
cross-checked against the migration filename and the flag-consumer line
numbers in the three affected backend files. No writes.

## What was NOT verified

- Whether `subscription_invoice` sends are simply rare (expected, given
  low corporate-subscription-renewal volume) versus broken — not
  investigated further here; flagged as an open question above rather than
  guessed at.
- Whether any receipts/invoices were sent through a path that bypasses
  `email_send_log` (e.g., a retry or manual resend not logged there) — not
  checked; taken at face value as the system of record for this pass.
- Whether Railway (the standby deploy target, currently drifting per
  ACTION_ITEMS.md C5) has ever actually served a request that read
  `branded_receipt_enabled` — only the shared Supabase `settings`/log data
  was checked, which is deploy-target-agnostic by design, so this doesn't
  change the finding either way.
