# Change Impact & Risk Log — B39 step 37: admin-dashboard support-ticket forms (create-ticket subject, reply, internal note) → zod

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx`
  and `.../tickets/[id]/page.tsx` had three ad hoc inline validation
  gates with no dedicated test coverage: the "New ticket" dialog's
  `create()` (subject required, silent no-op — duplicated exactly in
  the "Create ticket" button's `disabled` prop), and the ticket-detail
  page's `sendReply()` (sanitized reply content required, silent no-op)
  and `sendNote()` (internal note required, silent no-op — duplicated
  exactly in the "Add note" button's `disabled` prop). This area (the
  Zoho Desk-backed support-ticket screens) was not part of B39's
  original 21-candidate broader sweep (2026-08-31) — found while doing
  this item's ADR "freshness check" (`docs/adr/013-...md`), a targeted
  2-3-form spot-check of the admin-dashboard surface per this item's
  own instructions, not a full re-sweep.
- **Root cause:** no schema-validation library was adopted on this
  surface at the time these screens were built; each screen
  re-implements its own validation inline, same pattern B39 has been
  closing form-by-form since 2026-08-22.
- **Fix/remediation:** extracted the three checks into two new
  colocated files. `admin-dashboard/src/lib/createTicketFormSchema.ts`
  (`isTicketSubjectValid`) replaces `create()`'s `if
  (!form.subject.trim()) return;` and the "Create ticket" button's
  exact-duplicate `disabled` check — both call sites now share the one
  predicate, same discipline as B39 step 25's exact-duplicate
  consolidation. `admin-dashboard/src/lib/ticketReplyFormSchema.ts`
  (`isReplyContentValid`, `isNoteContentValid`) replaces `sendReply()`'s
  `if (!toText(html).trim()) return;` and `sendNote()`'s `if
  (!note.trim()) return;`. `isNoteContentValid` is also wired into the
  "Add note" button's exact-duplicate `disabled` check (consolidated,
  same as the subject check above). The "Send reply" button's own
  `disabled` prop was deliberately **left untouched**: it checks
  `!toText(reply).trim()` against the *unsanitized* draft, while the
  actual guard inside `sendReply()` checks the DOMPurify-sanitized
  version — a pre-existing, non-identical duplicate, not introduced by
  this change. Folding it into `isReplyContentValid` would silently
  change which value gates the button, a validation-rule change this
  item's own risk warning forbids bundling into a pure extraction.
  Pure extraction throughout — byte-for-byte identical accept/reject
  behavior on every touched guard; no behavior change.
- **Risk & impact on existing functionality:** both new files are
  colocated under `src/lib/` and used only by their respective pages.
  Blast-radius grep for `form.subject.trim()`, `toText(html).trim()`,
  and `note.trim()` across `admin-dashboard/src` found: the payload
  line `subject: form.subject.trim()` in `tickets/page.tsx` (untouched,
  same "leave the trimmed-payload construction alone" discipline prior
  steps used); two unrelated `note.trim() || undefined` optional-field
  constructions in `company-portal/[id]/allowance-requests/page.tsx`
  and `corporate-accounts/[id]/members/page.tsx` (different shape — an
  optional field, not a required-field gate; confirmed unrelated); and
  one **new, real duplicate** found by this grep and deliberately left
  unmigrated — `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx`
  line 106 has its own separate `if (!form.subject.trim()) { toast(...);
  return; }` gating a different ticket-creation form on a different
  page (`dashboard/support`, not `dashboard/support-tickets`) — flagged
  as a follow-up B39 candidate in `ACTION_ITEMS.md`, not migrated here,
  consistent with this item's own "migrate one form at a time" scope
  and this pass's explicit "spot-check, not a re-sweep" instruction.
- **User experience effect:** none, internal-admin-only. Same fields,
  same order, same (silent, no-toast) accept/reject behavior on all
  three migrated guards.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/createTicketFormSchema.ts` | New file: `isTicketSubjectValid` | Extract validation logic |
  | `admin-dashboard/src/lib/ticketReplyFormSchema.ts` | New file: `isReplyContentValid`, `isNoteContentValid` | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/createTicketFormSchema.test.ts` | New file: 6 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/lib/__tests__/ticketReplyFormSchema.test.ts` | New file: 9 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx` | `create()`'s guard and the "Create ticket" button's `disabled` check both now call `isTicketSubjectValid`; added import | Pure extraction + exact-duplicate consolidation |
  | `admin-dashboard/src/app/dashboard/support-tickets/tickets/[id]/page.tsx` | `sendReply()`'s guard now calls `isReplyContentValid(toText(html))`; `sendNote()`'s guard and the "Add note" button's `disabled` check both now call `isNoteContentValid`; added import | Pure extraction + exact-duplicate consolidation |

- **Before/after snippet:**

  ```tsx
  // before (tickets/page.tsx)
  const create = async () => {
      if (!form.subject.trim()) return;
      ...
  };
  <Button onClick={create} disabled={creating || !form.subject.trim()}>

  // after
  const create = async () => {
      if (!isTicketSubjectValid(form.subject)) return;
      ...
  };
  <Button onClick={create} disabled={creating || !isTicketSubjectValid(form.subject)}>
  ```

  ```tsx
  // before (tickets/[id]/page.tsx)
  const sendReply = async () => {
      const html = DOMPurify.sanitize(reply).trim();
      if (!toText(html).trim()) return;
      ...
  };
  const sendNote = async () => {
      if (!note.trim()) return;
      ...
  };
  <Button variant="outline" onClick={sendNote} disabled={sending || !note.trim()}>

  // after
  const sendReply = async () => {
      const html = DOMPurify.sanitize(reply).trim();
      if (!isReplyContentValid(toText(html))) return;
      ...
  };
  const sendNote = async () => {
      if (!isNoteContentValid(note)) return;
      ...
  };
  <Button variant="outline" onClick={sendNote} disabled={sending || !isNoteContentValid(note)}>
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 15/15 new tests pass (6 +
  9, `npx vitest run src/lib/__tests__/createTicketFormSchema.test.ts
  src/lib/__tests__/ticketReplyFormSchema.test.ts`); full
  admin-dashboard suite (`npm run test:coverage`, the exact CI
  invocation) 61/61 suites, 577/577 tests passing, 0 regressions; `npx
  tsc --noEmit` clean (repo-wide); `npx eslint` on touched files: 0
  errors, 1 pre-existing `react-hooks/set-state-in-effect` warning on
  an unrelated line of `tickets/[id]/page.tsx` (confirmed via `git
  diff` that the warned line, an unrelated `useEffect`, is not part of
  this change); **real production build** (`npm run build`) completed
  successfully, exit code 0, full route manifest generated including
  `/dashboard/support-tickets/tickets` and
  `/dashboard/support-tickets/tickets/[id]`, not just `tsc`/dev server,
  per CLAUDE.md's explicit requirement.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase or live Zoho Desk
  connection — verified via unit tests, `tsc`, `eslint`, and a
  production build only, per this repo's stated no-active-visual-
  regression-tooling gap (CLAUDE.md pre-merge gate #6) for the
  `support-tickets` pages (not one of the 6 seeded
  visual-regression pages per B38); this is a visually-invisible,
  logic-only change so it was reasoned about, not screenshotted. The
  `support/_tabs/tickets.tsx` duplicate found during the blast-radius
  grep was deliberately not migrated in this pass — see "Risk & impact"
  above.
