# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend (migration + doc only — no rider-app/driver-app/admin-dashboard code changed) |
| Domain (Sentry tag) | safety |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | FAQ content audit, finding "Critical — the same safety promise is hand-typed three times" (session artifact) |

## 1. Issue / gap identified

The SOS/911 safety disclaimer in the FAQ library was authored twice, independently: once as a driver-audience row (`backend/migrations/212_seed_saskatchewan_driver_faqs.sql`, question "What safety features does the driver app have?") and once as a rider-audience row (`backend/migrations/230_seed_rider_faqs.sql`, question "What safety features does Spinr have?"). The wording already differs slightly between the two.

## 2. Root cause

The `faqs.audience` column has supported `'both'` since migration 48, but every FAQ seed migration (210, 212, 230) authored rider and driver content as fully separate row sets and never used it — even for content, like the SOS disclaimer, that is identical policy for both apps. Nothing enforced reuse, so the same regulatory-sensitive sentence was typed twice by two different seed passes.

## 3. Fix / remediation

New migration `backend/migrations/322_consolidate_sos_faq.sql`:
- Soft-deactivates (`is_active = false`, not a hard delete) the two audience-specific rows by exact `(question, audience)` match.
- Seeds one consolidated `audience = 'both'` row with a merged answer that keeps the rider-only detail (trip-status sharing) and the driver-only detail (regulatory trip logging) alongside the single canonical SOS/911 sentence.
- No schema change. No code change required: `backend/routes/faqs.py`'s public `GET /faqs` and `backend/ai/tools_support.py`'s `search_faqs` already match `audience = 'both'` for either a rider or driver caller.

Also updated `docs/driver-faqs-saskatchewan.md` (a static mirror of migration 212's content) to note the entry was deactivated and consolidated, so the doc doesn't silently go stale relative to the DB.

## 4. Risk & impact on existing functionality

- **Readers of the `faqs` table**: `backend/routes/admin/faqs.py` (admin CRUD — unaffected, reads/writes by id), `backend/routes/faqs.py` (public `GET /faqs`, filters `is_active = true` — the two old rows simply stop appearing, the new one appears in their place), `backend/ai/tools_support.py::search_faqs` (same `is_active` filter, same audience-matching logic already used for the existing 51 rows).
- **Blast radius: isolated to this one Q&A topic.** Grepped the full repo for the exact question strings ("What safety features does the driver app have?", "What safety features does Spinr have?") and for `audience.*both` / `'both'` in the FAQ test files — no test or non-FAQ code depends on either exact string or on there being zero `audience='both'` rows.
- Does not touch the ride state machine, wallet/payment deltas, or any of the 16 background loops.
- Soft-deactivation (not hard delete) means the old rows and their original wording remain recoverable in the DB for audit/rollback, consistent with the domain-safety append-preferred convention.

## 5. User-experience effect

- **Rider and driver facing.** Both `rider-app/app/support.tsx` and `driver-app/app/driver/faq.tsx` fetch `/faqs` dynamically — the next time either app loads its Help Center, the old per-audience safety entry is replaced by the consolidated one. No app release needed.
- **Visible mid-session?** Only if a rider/driver has the Help Center/FAQ screen open at the moment the migration runs (it refetches on each screen load, not live-pushed) — not a mid-ride or mid-trip visible change, and not blocking; a driver who already loaded the FAQ screen before the migration keeps seeing the old cached list until they reopen it.
- **Copy change**: yes — the SOS sentence itself is unchanged verbatim ("SOS never auto-dials and is not a replacement for calling 911 — if anyone is in danger, call 911 first."); only the surrounding sentence and the question's audience scope changed. No new claims introduced.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/322_consolidate_sos_faq.sql` | New migration: deactivates the two audience-specific SOS FAQ rows, inserts one consolidated `audience='both'` row | Single source of truth for the compliance-sensitive SOS/911 disclaimer instead of two independently-editable copies |
| `docs/driver-faqs-saskatchewan.md` | Removed the now-deactivated driver-only SOS entry from the listed 33; added a note pointing to migration 322 and the shared entry; updated the entry-count note | Keep the static doc mirror from silently going stale relative to the DB |

## 7. Before / after

```
# Before — two independent rows, two audiences, two API-visible entries
migration 212 (audience='driver'): "What safety features does the driver app have?"
  → "Spinr includes an in-app SOS that notifies your emergency contacts and our
     safety team and offers one-tap 911. It does not auto-dial and is not a
     replacement for calling 911 — if anyone is in danger, call 911 first.
     Trips are logged for safety and regulatory purposes."

migration 230 (audience='rider'): "What safety features does Spinr have?"
  → "Every trip is tracked, you can share your trip status, and an in-app SOS
     notifies your emergency contacts and our safety team and offers one-tap
     911. SOS never auto-dials and is not a replacement for calling 911 — if
     anyone is in danger, call 911 first."
```

```
# After — one row, audience='both', served to both apps by the existing filter
"What safety features does Spinr have?"
  → "Every trip is tracked, and riders can share their trip status with
     someone they trust. If you ever need help, in-app SOS notifies your
     emergency contacts and our safety team and offers one-tap 911. SOS never
     auto-dials and is not a replacement for calling 911 — if anyone is in
     danger, call 911 first. Trips are logged for safety and regulatory
     purposes."
```

## 8. Rollback plan

No feature flag needed — this is data-only and reversible by SQL, documented in the migration's own header comment:

```sql
UPDATE faqs SET is_active = true, updated_at = now()
  WHERE audience IN ('driver', 'rider')
  AND question IN ('What safety features does the driver app have?',
                    'What safety features does Spinr have?');
DELETE FROM faqs WHERE audience = 'both'
  AND question = 'What safety features does Spinr have?';
```
Running it restores exactly the prior visible state (both apps show their original independent entry again). No Stripe charges, wallet deltas, or ride state involved, so a straight data revert is a complete rollback — not just a partial mitigation.

## 9. Verification performed

- [x] Blast-radius grep performed: searched the full repo for the two exact question strings (only appear in the two seed migrations and `docs/driver-faqs-saskatchewan.md`, both now handled) and for FAQ test files referencing `audience`/`'both'` (`test_admin_faqs_crud.py`, `test_faqs_coverage.py`, `test_routes_faqs_coverage.py` — no hardcoded dependency on either string or on zero `'both'` rows).
- [x] Reviewed against relevant CLAUDE.md conventions: migration append-only rule (new file, no edit to 212/230), additive-over-destructive (soft `is_active` toggle, not `DELETE`), PIPEDA/safety content accuracy preserved verbatim on the compliance-critical sentence.
- [ ] Automated tests run — not run in this pass; no existing test asserts on these two question strings or on FAQ row counts, so none were expected to fail, but the migration was not executed against a real or throwaway Supabase schema in this session (no DATABASE_URL available here).
- [ ] Manual repro / staging check — not performed; would require running `python -m backend.scripts.run_migrations` against a real or staging Supabase instance and then hitting `GET /faqs?audience=driver` and `GET /faqs?audience=rider` to confirm the merged row appears for both and the old rows are gone.
- [ ] `npm run build` — not applicable; no frontend files changed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (SQL given above, no data-loss since deactivated rows are soft-deleted, not dropped)
- [x] Blast radius is stated: isolated to the `faqs` table's SOS-related rows and the one static doc mirroring them; no other domain touched
- [x] No silent behavior change: the SOS/911 disclaimer sentence itself is verbatim-preserved; the only behavior change (two entries becoming one, shown to both apps) is documented above in Section 5

**What was NOT verified**: this migration was authored but not executed against a live or staging Supabase database in this session (no `DATABASE_URL` configured here) — the idempotent `WHERE NOT EXISTS` / `WHERE is_active = true` guards were reasoned through against the schema and the other seed migrations' proven pattern, not confirmed by an actual run. Whoever applies this migration should run `python -m backend.scripts.run_migrations --dry-run` first and spot-check `GET /faqs?audience=driver` and `GET /faqs?audience=rider` afterward.
