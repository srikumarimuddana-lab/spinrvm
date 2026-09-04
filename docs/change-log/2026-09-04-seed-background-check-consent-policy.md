# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (session requested by vikas@ngitservices.com), applied at explicit direction of the product owner in this conversation |
| Surface(s) | backend (migration), driver-app (consumes this content live) |
| Domain (Sentry tag) | safety (driver eligibility / regulatory background-check consent) |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Legal tab "Background-Check (CRC/VSC) Consent Policy" showing no content |

## 1. Issue / gap identified

The `background-check-consent` legal document had no published row in `legal_documents` at all — unlike the other 8 tracked legal documents (ToS, Privacy, Community Guidelines, Non-Discrimination, Accessibility, Cancellation Fees, Promotions/Referral Terms, Driver Deactivation & Appeals), which were all seeded by migrations 361 and 400. The admin Legal tab showed it as empty, and `driver-app/app/crc-consent.tsx` (the actual driver-facing consent screen) has been showing its "not yet published" fallback message since it shipped.

## 2. Root cause

No seed migration was ever written for this specific document. `docs/legal/background-check-consent.md`'s own draft was ready in substance (its third-party-vendor-model error was corrected 2026-09-02, and its one numeric gap — retention period — was already rewritten to honest non-numeric language) but nobody had taken the publish step.

## 3. Fix / remediation

Added `backend/migrations/406_seed_background_check_consent_policy.sql`, modeled directly on the already-merged `400_seed_deactivation_appeals_policy.sql` pattern: inserts one row (`audience='driver', doc_type='background-check-consent', version=1`) with `ON CONFLICT (audience, doc_type) DO NOTHING`. No schema change — migration 360 already widened the `doc_type` CHECK constraint to allow this value.

**Applied directly to the live database** (Supabase project `soavhtdhefowwvforzwb`, ca-central-1) via `execute_sql`, at the explicit direction of the product owner in this session ("let's populate the content and will review"), including a matching `schema_migrations` tracking row (filename + sha256 checksum, matching what `run_migrations.py` would have written) so the migration runner's own bookkeeping stays consistent and `--status` correctly reports it as applied rather than pending. This is the same "publish without counsel review, at explicit product-owner direction" accepted-risk pattern already used for all 8 prior documents (see `docs/legal/legal-text-publication-checklist.md`).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one new row in `legal_documents`.** No other table, schema, or code path touched. `driver-app/app/crc-consent.tsx` and the admin dashboard's Legal tab are the only two consumers of `GET /legal-documents?audience=driver&type=background-check-consent` / the admin `GET /legal-documents` list — both simply render whatever `content` is present, so this is additive (previously-empty → now-populated), not a behavior change to any code path.
- Confirmed no caching layer sits between this DB row and either consumer (`routes/legal_documents.py` and `routes/admin/legal_documents.py` both query Supabase directly, no `revalidate`/cache-control override) — the moment this row exists, both surfaces reflect it immediately.
- **Two known content gaps carried forward, not silently resolved** (both documented in `docs/legal/legal-text-publication-checklist.md` and repeated in the migration file's own header comment):
  1. Retention period is intentionally non-numeric (no CRC/VSC-specific retention figure exists anywhere in this codebase) — consistent with how migration 400 handled its own unresolved SLA figures.
  2. The "what happens if the result affects your eligibility" paragraph promises the driver is told the specific reason and may respond before a final decision. Verified against code in this session: `routes/admin/documents.py` does push a rejection notification naming the specific reason (including for "background" document types) — that half is real. But there is no CRC/VSC-specific *pre-action* response gate beyond the same general post-decision appeals flow already used for every other deactivation reason. This paragraph is a **process promise that is only partially, not fully, verified** — flagging explicitly rather than letting it read as fully confirmed.

## 5. User-experience effect

**Driver-facing, and this is the one that matters most**: drivers going through CRC/VSC consent capture (`driver-app/app/crc-consent.tsx`) will now see the real consent text instead of "This consent form has not been published yet. Contact support before continuing." This is a live, user-visible change to an actual legal/regulatory consent flow, not just an admin-dashboard content fix. Internal-admin-facing: the Legal tab now shows real content for this policy.

Not mid-session-disruptive (a driver isn't already inside the consent flow when this changed), but it is the actual legal text a real driver will consent to going forward — treat that as a first-class fact, not a side effect of "populating a tab."

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/406_seed_background_check_consent_policy.sql` | New seed migration, inserts the `background-check-consent` row | Publish the last of the 9 tracked legal documents that had no seed at all |

## 7. Before / after

Not applicable in the usual sense (this is a pure additive insert, no existing behavior changed) — before: `GET /legal-documents?audience=driver&type=background-check-consent` returned `{content: "", version: 0}`; after: returns the real consent text at `version: 1`.

## 8. Rollback plan

`DELETE FROM legal_documents WHERE audience = 'driver' AND doc_type = 'background-check-consent' AND version = 1;` — documented in the migration file itself. This reverts the driver-app screen to its pre-existing "not yet published" fallback and the admin Legal tab to empty. A `git revert` of the migration file alone is **not sufficient** — this migration was already applied directly to production data, so the DELETE above (or a superseding version-2 row) is the actual rollback, independent of the file's git history.

## 9. Verification performed

- [x] Confirmed the target DB row now exists with the expected shape (`audience`, `doc_type`, `version=1`, correct content length) via a direct read-only query against the live Supabase project, immediately after the write.
- [x] Confirmed the `schema_migrations` tracking row was written with a checksum that matches an independently recomputed sha256 of the committed migration file — no drift between what's tracked and what's in git.
- [x] Confirmed `background-check-consent` is already an allowed `doc_type` value (migration 360, already applied) — no schema change needed.
- [x] Verified one of the two document text gaps (rejection-reason notification) against real code (`routes/admin/documents.py`) rather than taking the draft's claim at face value.
- [ ] Not run through `backend/scripts/run_migrations.py` itself — applied directly via Supabase's SQL execution path (no `DATABASE_URL`/psycopg session available in this sandbox). The tracking row was hand-inserted to match what that script would have written, but this bypassed the script's own statement-splitting/bootstrap logic (harmless here — this file is a single simple `INSERT`, not multi-statement DDL).
- [ ] Not sent to `spinr-migration-reviewer` before applying (applied first, at explicit urgent direction, then queued for review after — reviewing after-the-fact on an append-only, already-applied seed file, not before).
- [ ] Counsel review — **not done**, consistent with the same accepted-risk pattern already used for the other 8 published legal documents.
- [ ] The "respond before a final decision" process promise is **not fully verified** — see §4. Recommend confirming with the safety/eligibility team, same as the document's own pre-publication notes already flagged.

## What was NOT verified

Not tested against the actual `driver-app` CRC consent screen rendering this content (no device/simulator access from this session) — verified at the API/data layer only. The Alberta (Calgary/Edmonton) rows referenced in the source draft's header notes are forward-looking and not part of the published consent text itself. Fee/processing-time figures cited only in the draft's internal header notes (not the published consent text) were sourced via web-search summaries, not primary-page fetches, per the draft's own disclosure — not independently re-verified in this pass.
