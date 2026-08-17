# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend (migration + doc only — no code path changed) |
| Domain (Sentry tag) | ai |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | FAQ content audit, finding "High — near-duplicate questions live side by side" (session artifact) |

## 1. Issue / gap identified

Migrations 210 (general driver set) and 212 (Saskatchewan driver set) each independently seeded the same three onboarding-status topics, worded slightly differently: "check my application status," "how long does approval take," and "can support fast-track my approval." A driver browsing or searching the Help Center would see what looks like the same question twice, with two answers that don't quite agree — reading as the app not knowing its own policy.

## 2. Root cause

Migration 212 was written to "complement migration 210" (its own header comment says so) but re-covered several topics 210 already had instead of only adding new ones, with no dedup check against 210's existing question set.

## 3. Fix / remediation

New migration `backend/migrations/326_merge_duplicate_onboarding_faqs.sql` merges each of the 3 pairs:

1. **"Check application status"** — kept migration 210's row (`'How do I check the status of my driver application?'`) for its fuller answer (a "waited a while, contact support" fallback the 212 row lacked), merging in the 212 row's explicit "Spinr Driver app" naming. Migration 212's `'How do I check the status of my application?'` deactivated.
2. **"How long does approval take"** — kept 210's row (`'How long does document review and approval take?'`) unchanged; deactivated 212's near-word-for-word duplicate (`'How long does approval take?'`).
3. **"Can support fast-track approval"** — kept migration 212's row (`'Can support activate or approve my account faster?'`) unchanged, for its more natural real-world question phrasing and slightly more complete answer (also names insurance, not just the CRC); deactivated 210's `'Can you activate or approve my account?'`.

Deactivation is soft (`is_active = false`), matching the convention established by migration 322's SOS consolidation — no hard `DELETE`, full audit trail preserved. The one answer-changing `UPDATE` also clears `embedding`/`embedding_model`, matching the convention migration 325 established. All four `UPDATE`s guard on `(audience, is_active = true, question, OLD answer text)` so a since-hand-edited row is left untouched rather than silently overwritten — cross-checked programmatically against the *current* live text (i.e. accounting for migration 325's AI-voice rewrite, not the raw original 210/212 seed text, since two of these six rows were touched by that rewrite). Independently confirmed by a `spinr-migration-reviewer` pass, which verified all four WHERE-clause match strings thread the post-325 state correctly.

Also updated `docs/driver-faqs-saskatchewan.md` (already kept in sync by the two earlier fixes in this series) to reflect the two migration-212-sourced rows now being deactivated.

## 4. Additional finding — not fixed, flagged for a decision

While scanning for other consumers of these question strings (standard blast-radius check), found that **`backend/routes/support.py` contains a third, entirely separate, hardcoded FAQ system** — a Gemini system prompt (`SYSTEM_PROMPT`) baked into `POST /api/v1/support/chat` (confirmed live and mounted in `backend/server.py:352`). This is not part of the `faqs` table at all, and its content has two problems more serious than duplication:

- It states **"usually 2–3 business days"** for driver approval and **"2–3 business days"** for payouts — exactly the kind of fabricated timeline `docs/driver-faqs-saskatchewan.md` explicitly says the real FAQ content "deliberately avoids."
- It references a **"platform service fee"** / **"platform fee"** deducted from driver earnings — this directly contradicts the core business model stated throughout CLAUDE.md ("**Not** a commission-taking marketplace. Driver keeps 100% of the fare," "0% commission") and repeated in every other FAQ answer touched by this audit ("Drivers keep 100% of the fare. Spinr charges 0% commission on consumer rides").

**Not resolved here** — this is a different content system (a hardcoded Python string sent to a third-party LLM, not a `faqs` table row) and a business-model-accuracy issue, not a duplication issue, so it's out of scope for a migration titled "merge duplicate onboarding FAQs." Flagging directly to the user as a separate, higher-priority item — a driver using this specific chat endpoint could currently be told their earnings are reduced by a platform fee that doesn't exist.

## 5. Risk & impact on existing functionality

- **Blast radius: isolated to 3 rows' `is_active`/`answer` in the `faqs` table.** No schema change, no other table touched.
- **Readers of this content**: same three as migration 325's — `backend/routes/admin/faqs.py` (admin CRUD), `backend/features.py::get_faqs` (the actually-live public handler, per the earlier sort_order fix's finding), and `backend/ai/tools_support.py::search_faqs`. All three simply serve whatever rows are `is_active = true`; deactivating a row just means fewer results, no special-cased logic broken.
- **Grepped the full repo for the 3 retired question strings** and found one unrelated hit worth noting (see section 4) plus the migration/doc files themselves — no test or other code hardcodes any of these three questions.
- No interaction with the ride state machine, wallet/payment deltas, RLS policies, or any of the 16 background loops.

## 6. User-experience effect

- **Driver-facing only** (all 6 rows are `audience='driver'`). Riders see no change.
- A driver browsing the Help Center or asking the AI assistant now sees one answer per onboarding-status topic instead of two slightly different ones.
- **Not visible mid-session** to a driver already viewing the FAQ screen (refetches on screen load, not live-pushed).
- No new claims introduced — the "kept" answer for each pair was already live content; only the redundant sibling is removed from view.

## 7. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/326_merge_duplicate_onboarding_faqs.sql` | New migration: deactivates 3 near-duplicate driver-onboarding FAQ rows, merges a small phrase into 1 kept row | Remove side-by-side duplicate/conflicting FAQ content |
| `docs/driver-faqs-saskatchewan.md` | Removed the two now-deactivated migration-212 entries; updated entry count; added a note pointing to migration 326 | Keep the static doc mirror from going stale relative to the DB |

## 8. Rollback plan

`git-revert-safe` — no schema change, no hard delete. Reactivating the 3 rows and reverting the 1 merged answer is a straight SQL swap, given in full in the migration's header comment (including an old-text guard on the rollback's answer-revert, so a post-326 hand-edit wouldn't be clobbered by the rollback either).

## 9. Verification performed

- [x] **Programmatic cross-check**: verified all four `UPDATE`s' `WHERE ... AND answer = '<old text>'` values match the *current* live content byte-for-byte — correctly threading migration 325's rewritten text for the two rows it touched, and the original 212 seed text for the one row 325 didn't touch (confirmed it wasn't among 325's 17 rewritten rows).
- [x] Structural sanity check: 4 real `UPDATE` statements (excluding the 2 that appear only in header-comment prose), 4 terminating semicolons, balanced single-quote count in actual SQL lines (26, even) — comment-line English contractions ("don't", "you'll") were excluded from this check since they aren't SQL string delimiters.
- [x] Blast-radius grep: confirmed no test or other source hardcodes any of the 3 retired question strings; surfaced the unrelated `routes/support.py` finding (section 4) in the process.
- [x] Second-opinion review via `spinr-migration-reviewer` (per CLAUDE.md's standing note that Codex review has been silent since 2026-07-30) — no blockers; applied its two minor nitpicks (add `is_active = true` guard to the merge UPDATE for consistency; add an old-text guard to the rollback comment's answer-revert) before this push.
- [ ] Not run against a real/throwaway Supabase schema in this session — content-only `UPDATE`s against an existing table/columns with no schema risk, but actual row-match behavior not confirmed live.

**What was NOT verified**: whether any of these 6 rows have been hand-edited by an admin since seeding (if so, this migration's guards correctly no-op on that row, meaning the merge/dedup simply won't apply there — silently, by design). Worth a `SELECT question, is_active FROM faqs WHERE audience='driver' AND question IN (...)` spot-check post-deploy. Also not verified: whether the `backend/routes/support.py` finding in section 4 has any other inaccuracies beyond the two called out — only spot-checked, not read in full.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow: this reduces visible duplicate content, doesn't remove a unique topic
- [x] Found-but-out-of-scope issue (section 4 — the `routes/support.py` hardcoded prompt with a fabricated SLA and a contradicted commission claim) surfaced explicitly rather than silently fixed or silently ignored
