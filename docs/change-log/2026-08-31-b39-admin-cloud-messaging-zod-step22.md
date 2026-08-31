# Change Impact & Risk Log — B39 step 22: admin-dashboard cloud-messaging broadcast/suppression validation → zod

- **Issue/gap identified:** `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx`
  had two ad hoc validation blocks with no dedicated test coverage: the
  broadcast-compose form's `handleSend` (title/description required,
  recipients required for a "particular" audience, a schedule time
  required when scheduling, at least one delivery channel selected) and
  the marketing-suppression form's `handleAddSuppression` (a target
  email/phone required) — the sixth admin-dashboard candidate from the
  2026-08-31 broader B39 sweep (`ACTION_ITEMS.md` B39, PIPEDA-adjacent).
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted both blocks into new
  `admin-dashboard/src/lib/cloudMessagingFormSchema.ts` —
  `getBroadcastFormError` (+ `isBroadcastTitleDescriptionValid`,
  `isParticularAudience`, `isRecipientsSelected`, `isScheduleTimeValid`,
  `isDeliveryChannelSelected`) for the broadcast form, and
  `getSuppressionFormError` (+ `isSuppressionTargetValid`, a
  documentation zod schema) for the suppression form. `handleSend` and
  `handleAddSuppression` now each call their respective `get*Error(...)`
  function once instead of their original sequential `if` blocks. Pure
  extraction — byte-for-byte identical accept/reject behavior and
  identical error copy; no validation-rule change. `isParticular` is now
  computed via the exported `isParticularAudience(...)` helper (used both
  inside the schema and at the call site for payload construction)
  instead of being redefined inline — same value, same logic, single
  source of truth.
- **Risk & impact on existing functionality:** both new functions are
  colocated in `src/lib/cloudMessagingFormSchema.ts` and used only by
  `cloud-messaging/page.tsx`'s two handlers. Blast-radius grep for the
  four distinctive error strings found zero other call sites in `src/`.
  The Send button's `disabled` prop (`sending` only) and the Suppress
  button (no `disabled` guard beyond the click handler itself) do not
  duplicate any of the extracted checks — nothing to leave alone here.
- **User experience effect:** none. Same fields, same order, same error
  copy on both forms.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `admin-dashboard/src/lib/cloudMessagingFormSchema.ts` | New file: predicate functions, zod schema, `getBroadcastFormError` + `getSuppressionFormError` | Extract validation logic |
  | `admin-dashboard/src/lib/__tests__/cloudMessagingFormSchema.test.ts` | New file: 18 accept/reject tests | Close the coverage gap this item names |
  | `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx` | `handleSend`'s and `handleAddSuppression`'s inline `if` blocks each replaced with one `get*Error(...)` call; `isParticular` now sourced from the shared `isParticularAudience(...)` helper; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before (handleSend)
  if (!form.title.trim() || !form.description.trim()) { toast({ title: "Missing fields", description: "Please fill in title and description.", variant: "destructive" }); return; }
  const isParticular = form.audience === "particular_customer" || form.audience === "particular_driver";
  if (isParticular && form.particular_ids.length === 0) { toast({ title: "No recipients selected", description: "Please select at least one user/driver.", variant: "destructive" }); return; }
  if (form.is_scheduled && !form.scheduled_at) { toast({ title: "Missing schedule time", description: "Please select a date and time.", variant: "destructive" }); return; }
  const channels: string[] = [];
  if (form.send_push) channels.push("push");
  if (form.send_email) channels.push("email");
  if (form.send_sms) channels.push("sms");
  if (channels.length === 0) { toast({ title: "No delivery channel", description: "Please select at least one delivery channel.", variant: "destructive" }); return; }

  // after
  const formError = getBroadcastFormError({
      title: form.title, description: form.description, audience: form.audience,
      particularIds: form.particular_ids, isScheduled: form.is_scheduled, scheduledAt: form.scheduled_at,
      sendPush: form.send_push, sendEmail: form.send_email, sendSms: form.send_sms,
  });
  if (formError) { toast({ ...formError, variant: "destructive" }); return; }
  const isParticular = isParticularAudience(form.audience);
  const channels: string[] = [];
  if (form.send_push) channels.push("push");
  if (form.send_email) channels.push("email");
  if (form.send_sms) channels.push("sms");
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 18/18 new tests pass
  (`npx vitest run src/lib/__tests__/cloudMessagingFormSchema.test.ts`);
  full admin-dashboard suite (`npx vitest run`) 50/50 suites, 515/515
  tests passing, 0 regressions; `npx tsc --noEmit` clean (repo-wide);
  `npx eslint` on touched files: 0 errors, 5 pre-existing
  `react-hooks/set-state-in-effect` warnings on unrelated lines in the
  same file, unchanged by this diff; **real production build**
  (`npm run build`) completed successfully. Blast-radius grep confirmed
  no other file in `src/` uses either form's distinctive error copy.
- **What was NOT verified:** no manual click-through in a running admin
  dashboard against a live/staging Supabase — verified via unit tests,
  `tsc`, `eslint`, and a production build only, per this repo's stated
  no-active-visual-regression-tooling gap (CLAUDE.md pre-merge gate #6);
  this is a visually-invisible, logic-only change so it was reasoned
  about, not screenshotted.
