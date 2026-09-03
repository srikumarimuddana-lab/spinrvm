# Change Impact & Risk Log — publish Driver Deactivation & Appeals Policy

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (this session), at explicit product-owner direction |
| Surface(s) | docs (content) + backend (migration, additive-only) + driver-app (content becomes visible, no code change) |
| Domain (Sentry tag) | safety |
| PR / commit link | branch `publish-driver-deactivation-policy` |
| Related issue or gap ID | Reported by user: driver-app's Legal & Policies screen showed "No Driver Deactivation & Appeals Policy has been added yet." |

## 1. Issue / gap identified

`docs/legal/driver-deactivation-appeals-policy.md` was fully wired end-to-end
in code (`shared/config/legalDocs.ts`, backend `ALLOWED_TYPES` in both
`routes/legal_documents.py` and `routes/admin/legal_documents.py`, the
admin-dashboard editor, and `driver-app/app/policies.tsx`) but was never
inserted into the live `legal_documents` table — the one document
`legal-text-publication-checklist.md`'s own process note #1 explicitly
warned should ship together with `community-guidelines.md` and
`non-discrimination-policy.md` (both published 2026-08-21), but was left
behind.

## 2. Root cause

Migration 361 (2026-08-21) published 6 of the 7 remaining draft docs but
`driver-deactivation-appeals-policy.md` wasn't one of them — it still had
three unresolved `[NUMBER, E.G. ...]` bracketed SLA placeholders with no
backing constant anywhere in `backend/` (safety-hold investigation time,
appeal window, appeal response time), confirmed absent by two independent
sessions (2026-08-20 legal-readiness pass; re-confirmed today). It appears
migration 361 skipped this doc specifically because those brackets were
unresolved, and no follow-up ever closed the gap — it just sat in `Draft`
silently until a real driver hit the empty-content fallback.

## 3. Fix / remediation

Two changes, not one:

1. **Content fix**: the three numeric SLA placeholders were rewritten to
   non-numeric commitments ("Spinr aims to complete a safety investigation
   and resolve a temporary hold as quickly as possible, and will keep you
   updated on status"; "you may appeal at any time"; "will receive a
   response as soon as possible") rather than shipping literal
   `[BRACKETED]` template text to real drivers, or inventing a number with
   no operational backing. This matches `driver-app/app/appeal.tsx`'s own
   existing copy exactly ("We'll review it and get back to you," no
   timeframe) — so app and policy text are now consistent, where before the
   policy would have promised something the app didn't. This is a
   deliberate, disclosed interim state: the underlying gap (no real SLA
   commitment from the safety team) is NOT resolved, only the choice of
   what to publish in its absence.
2. **Publish**: new migration `400_seed_deactivation_appeals_policy.sql`
   inserts the single driver-only row (`audience='driver',
   doc_type='deactivation-appeals'`), following migration 361's exact
   pattern (dollar-quoted content, `ON CONFLICT (audience, doc_type) DO
   NOTHING` for idempotency, `NOTIFY pgrst, 'reload schema'`).

`background-check-consent.md` (the other unpublished driver-only doc found
during this audit) was deliberately **NOT** published in this change — its
gap (a `[BACKGROUND CHECK VENDOR NAME]` placeholder, three occurrences) is a
factual claim about a real third-party data processor, not a policy choice
like an SLA; inventing or genericizing it wasn't judged to be this session's
call to make unilaterally on a PIPEDA-sensitive consent document. Left as
`Draft`, tracked separately.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** One new row in `legal_documents`
  (`audience='driver', doc_type='deactivation-appeals'`), one migration
  file (additive-only, no schema change), one content file edit (the
  source `.md`, not app code), one checklist update. No route, no schema
  change beyond the already-applied migration 360 CHECK constraint, no
  other doc_type touched.
- **Who else reads/writes `legal_documents`**: `routes/legal_documents.py`
  (`GET /legal-documents?audience=&type=`, public read, used by both apps'
  `legal.tsx`), `routes/admin/legal_documents.py` (admin CRUD, unchanged),
  admin-dashboard's `legal-documents.tsx` tab (unchanged, will now show
  this row as populated instead of empty). No other table, no background
  loop, no ride/dispatch/payment path touches this table at all.
- No existing published row is modified — `ON CONFLICT DO NOTHING` means
  even a re-run of this migration is a no-op once applied.
- The rewritten SLA language is a genuine content decision with real
  stakes (per the doc's own cited AALDEF findings on appeals-without-SLAs)
  — flagged explicitly in the doc, the checklist, and here, not buried.

## 5. User-experience effect

**Driver-facing, visible immediately on next app open (not mid-session —
policy pages are fetched on navigation, not cached/pushed).** Before: a
driver opening Legal & Policies → Driver Deactivation & Appeals Policy saw
"No Driver Deactivation & Appeals Policy has been added yet." After: the
real policy text, with non-numeric (not numeric) time commitments. No other
screen, flow, or existing behavior changes — this is the first time this
content has ever been shown to any driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/legal/driver-deactivation-appeals-policy.md` | 3 numeric SLA brackets → non-numeric commitments; publication date filled in; header/pre-publication notes updated to record the resolution and flag the still-open real-SLA follow-up | Make the draft publishable without inventing numbers or shipping raw brackets |
| `docs/legal/legal-text-publication-checklist.md` | `driver-deactivation-appeals-policy.md` row updated to Published (with open gap); process note #1 updated to record the staggered-publish incident and its resolution | Keep the checklist accurate — it's the source of truth other sessions read |
| `backend/migrations/400_seed_deactivation_appeals_policy.sql` | New migration, inserts the one driver-only row | Actually publish the content live |

## 7. Before / after

```
# Before (docs/legal/driver-deactivation-appeals-policy.md)
Spinr aims to complete a safety investigation and resolve a temporary hold
within [NUMBER, E.G. 5 BUSINESS DAYS], and will tell you if an
investigation is taking longer...
```

```
# After
Spinr aims to complete a safety investigation and resolve a temporary hold
as quickly as possible, and will keep you updated on status — including
telling you if an investigation is taking longer...
```

## 8. Rollback plan

`DELETE FROM legal_documents WHERE audience = 'driver' AND doc_type =
'deactivation-appeals' AND version = 1;` (stated in the migration's own
comment header). No data was overwritten (fresh insert via `ON CONFLICT DO
NOTHING`), so this is a complete, safe rollback — reverts the app to
showing the "not added yet" fallback again, same as before this change.
The `.md` source-file edits are a plain `git revert` if the wording
decision itself needs to be undone independently of the DB row.

## 9. Verification performed

- [x] `backend/tests/test_legal_documents.py` — 8/8 passed (unchanged
  code path; `ALLOWED_TYPES` set assertion confirms `deactivation-appeals`
  was already a valid type before this change, as expected).
- [x] Migration SQL sanity-checked: dollar-quote delimiter balance,
  filename ordering (400, next after existing 399), `ON CONFLICT` clause
  present, matches migration 361's exact structural pattern.
- [x] Cross-referenced `driver-app/app/appeal.tsx`'s actual shipped copy
  to confirm the new non-numeric policy language doesn't contradict it —
  they now say the same thing (no timeframe, "we'll get back to you").
- [x] Reviewed against CLAUDE.md conventions: additive-only migration
  (new row, no existing row touched), no ride-state-machine/money/RLS
  surface, safety-domain content change with explicit product-owner
  sign-off obtained before publishing (see conversation record).
- [ ] **Migration not actually run against a live database** in this
  session — no Supabase/DATABASE_URL access here. Whoever runs
  `run_migrations.py` next (per this repo's normal deploy process) applies
  it; SQL syntax was verified by inspection and pattern-matching against
  361, not by execution.
- [ ] **No real production build** run (`npm run build` for
  admin-dashboard, or an app export) — this change touches zero app code,
  only a docs `.md` file and a migration SQL file, so no frontend build
  step is applicable.
- [ ] Not verified: on-device/visual — driver-app's `legal.tsx` renders
  content as-fetched plain text (confirmed by reading the component, not
  screenshotted); no visual regression tooling exists for this surface
  (standing gap, per CLAUDE.md).

## 10. What was NOT verified

- **Counsel review was not done**, matching the accepted-risk pattern
  already recorded for every other published-anyway doc since 2026-08-17.
- **The underlying "no real SLA" gap is not resolved**, only the choice of
  what to show a driver in its absence. A safety-team decision on real
  numeric SLAs (safety-hold investigation time, appeal window, appeal
  response time) is still an open follow-up, now doubly relevant since the
  live policy text is what drivers will actually read and rely on.
- **`background-check-consent.md` remains unpublished** — deliberately out
  of scope for this change; its vendor-name gap needs a real answer from
  product/safety, not a content workaround.
- Did not independently re-verify the "different reviewer" appeal-handling
  claim against current `backend/routes/admin/driver_appeals.py` beyond
  reading the doc's own pre-publication note #3 (already resolved by a
  prior session, 2026-08-31, #4738) — took that finding as given rather
  than re-deriving it.
