# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend (migration + doc only — no code path changed) |
| Domain (Sentry tag) | ai |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | FAQ content audit, finding "High — the FAQ was written for a chatbot, then reused as a static help screen" (session artifact) |

## 1. Issue / gap identified

17 driver-audience FAQ answers (seeded by migrations 210 and 212) were written in first-person AI-assistant voice — "Ask me and I'll read your current application status for you," "I can tell you whether your CRC on file is valid or expired." That phrasing makes sense inside the AI support assistant's chat bubble (there genuinely is a "me" there who can look something up), but the exact same stored text is also served verbatim to the driver app's static Help Center accordion screen, where there is no "me" and the screen can't act on "ask me here" — it reads as a broken promise on that surface.

## 2. Root cause

The 51-row FAQ table serves two different consumption modes — a static tap-to-expand help screen (`driver-app/app/driver/faq.tsx`, `shared/components/SupportScreen.tsx`) and the conversational AI assistant's `search_faqs` tool (`backend/ai/tools_support.py`) — from the same `answer` text, with no per-surface variant. The content was authored with the chatbot as the primary/only audience in mind.

## 3. Fix / remediation

New migration `backend/migrations/325_faq_answers_drop_ai_voice.sql`: rewrites the `answer` text on the 17 affected rows, replacing each "ask me and I'll check/tell/show you X" clause with a direct pointer to the in-app screen that shows X (Account / Onboarding, the documents section, Earnings). Every other clause/fact in each answer is preserved verbatim — this is a voice edit only, not a policy or content change. One row ("How do I contact support?") keeps a reference to the assistant, but rewritten in third person to match the house style already used in the rider FAQ set ("ask our in-app assistant, which will hand you off for anything it can't resolve" — this phrasing already existed verbatim in migration 230's rider row before this change).

The rider-audience set (migration 230) needed no changes — grepped for the same pattern and its one offending row ("What safety features does Spinr have?") was already deactivated and consolidated into a shared `audience='both'` FAQ by migration 322 (an earlier fix from this same audit).

Also updated `docs/driver-faqs-saskatchewan.md` (the static mirror of migration 212's content, already kept in sync by two earlier fixes in this series) so it doesn't go stale relative to the DB.

**Caught by a `spinr-migration-reviewer` second-opinion pass before merge** (per CLAUDE.md's standing note that Codex review has been silent since 2026-07-30): the first draft of this migration changed `answer` on all 17 rows but didn't clear `embedding`/`embedding_model`, unlike the established convention in `backend/routes/admin/faqs.py` ("editing the question/answer invalidates any stored semantic embedding — clear it so search re-embeds from the new text"). Embeddings are only lazily recomputed when `embedding IS NULL` (`209_faqs_add_embeddings.sql`), not on every answer edit, so the gap would have left these 17 rows semantically searchable only by their old, now-incorrect wording once `ai_faq_semantic_enabled` is turned on — currently latent (that flag defaults `false`) but a real correctness gap. Fixed before merge: every `UPDATE` now also sets `embedding = NULL, embedding_model = NULL`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 17 rows' `answer` column in the `faqs` table.** No schema change, no other table touched.
- **Readers of this content**: `backend/routes/admin/faqs.py` (admin CRUD, unaffected — reads/writes by id), the public `/faqs` handler (`backend/features.py::get_faqs`, confirmed the actually-live one per the prior sort_order fix's finding), and `backend/ai/tools_support.py::search_faqs` (the AI assistant) — all three simply serve whatever text is in the row; none has logic keyed to the specific wording being replaced.
- **Overwrite-safety guard**: each `UPDATE`'s `WHERE` clause matches on `(audience, question, OLD answer text)`, not just `question` — so if an admin has since hand-edited one of these 17 rows through the admin dashboard, that edit is left alone rather than silently clobbered. Verified programmatically (see Verification) that every OLD-answer string in the migration is byte-for-byte identical to what's actually in migrations 210/212, so the match will succeed against an untouched row and safely no-op against an edited one.
- **Idempotent**: re-running the migration after it's applied is a no-op (the WHERE clause no longer matches once the answer has already been rewritten).
- No interaction with the ride state machine, wallet/payment deltas, RLS policies, or any of the 16 background loops.

## 5. User-experience effect

- **Driver-facing only** (all 17 affected rows are `audience='driver'`). Riders see no change.
- The driver Help Center screen's answers read as intended for that screen — no more references to "asking" a static page. The AI assistant's live behavior (looking up a driver's actual status via `get_driver_application_status`/`get_document_status`/`get_driver_earnings_summary`, per the note already in `docs/driver-faqs-saskatchewan.md`) is unaffected — those tools are separate from `search_faqs` and don't read this stored text.
- **Not visible mid-session** to a driver already viewing the FAQ screen (it refetches on screen load, not live-pushed).
- No new claims introduced — every regulatory/policy fact (CRC requirements, SGI insurance, tax handling, etc.) in each answer is unchanged verbatim; only the AI-voice clause was rewritten.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/325_faq_answers_drop_ai_voice.sql` | New migration: rewrites 17 driver-audience FAQ answers to remove first-person AI-chatbot voice | Static Help Center copy shouldn't promise something only a chat surface can do |
| `docs/driver-faqs-saskatchewan.md` | Same 11 answers (the subset from migration 212) updated to match | Keep the static doc mirror from going stale relative to the DB, per the pattern established by the two earlier fixes in this audit series |

## 7. Before / after

```
# Before (migration 210)
'How do I check the status of my driver application?'
  → 'Open the driver app and go to your Account / Onboarding section to see
     your current status. You can also ask me here and I''ll read your
     current application status for you. Review starts once all your
     required documents are uploaded and readable. If it has been a while
     with no update, I can hand you to support.'
```

```
# After
'How do I check the status of my driver application?'
  → 'Open the driver app and go to your Account / Onboarding section to see
     your current status. Review starts once all your required documents
     are uploaded and readable. If it has been a while with no update,
     contact support.'
```

```
# Before (migration 212)
'Where do I see my earnings?'
  → 'Open the Earnings section of the Spinr Driver app to see your
     completed trips and per-trip earnings. You can also ask me for a
     summary of your recent trips and earnings.'
```

```
# After — redundant trailing sentence dropped entirely, no info lost
'Where do I see my earnings?'
  → 'Open the Earnings section of the Spinr Driver app to see your
     completed trips and per-trip earnings.'
```

Full list of all 17 before/after pairs is in the migration file itself (each `UPDATE`'s `WHERE ... AND answer = '<old text>'` clause is the "before"; the `SET answer = '<new text>'` is the "after").

## 8. Rollback plan

`git-revert-safe`, and also directly reversible via SQL without a code revert: because the migration matches on the exact OLD answer text, the original wording for all 17 rows is preserved in full inside the migration file (as the `WHERE` match value) — a rollback migration would simply swap each `SET`/`WHERE` pair. No Stripe charges, wallet deltas, or ride state involved.

## 9. Verification performed

- [x] **Programmatic cross-check**: wrote a script to parse the actual `(question, answer, category)` tuples out of `210_seed_driver_faqs.sql` and `212_seed_saskatchewan_driver_faqs.sql`, and diffed every one of the 17 `UPDATE`'s `WHERE ... AND answer = '<old text>'` strings against them — confirmed all 17 match byte-for-byte (after unescaping `''` → `'`), so the migration's guard clause will actually fire against the real seeded rows rather than silently no-op due to a transcription typo.
- [x] Checked for remaining first-person AI voice (`grep -iE "\bi\b|ask me"`) in the new `SET answer =` text across all 17 rows — none found.
- [x] Confirmed no test file or other source hardcodes any of the old/new answer strings (grepped the full repo).
- [x] Structural sanity check: 17 `UPDATE` statements, 17 terminating semicolons, balanced single-quote count (142, even).
- [x] Manual second-opinion review via the `spinr-migration-reviewer` subagent against `backend/migrations/CLAUDE.md`'s conventions (append-only, forward-compatible, naming/numbering, rollback soundness) — per CLAUDE.md's standing note that Codex review has been silent since 2026-07-30.
- [ ] Not run against a real/throwaway Supabase schema in this session — this is a content-only `UPDATE` against an existing table/column with no schema risk, but the actual row-match behavior (as opposed to the string-level cross-check above) was not confirmed against a live database.

**What was NOT verified**: whether any of these 17 rows have already been hand-edited by an admin in the live/staging database since being seeded — if so, this migration's guard correctly no-ops on that specific row rather than overwriting it, but that means the AI-voice fix simply won't apply to that row either, silently. No way to check this without DB access in this session; worth a quick `SELECT question, answer FROM faqs WHERE audience='driver' AND question IN (...)` spot-check post-deploy to confirm all 17 rows picked up the new wording.
