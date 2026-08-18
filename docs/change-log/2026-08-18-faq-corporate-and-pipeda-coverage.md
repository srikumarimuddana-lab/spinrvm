# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend (migration only — no code path changed) |
| Domain (Sentry tag) | ai |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | FAQ content audit, finding "Medium — coverage gaps against what CLAUDE.md itself commits to" (session artifact) |

## 1. Issue / gap identified

None of the 51 rows seeded across this FAQ series covered two shipped, self-serve features: corporate/Work Mode billing (rider-app only) and the PIPEDA data-export/account-deletion rights CLAUDE.md's Compliance section commits to. A rider on a company plan, or anyone exercising their access/deletion right, had no FAQ entry to find and had to reach a human for something that's otherwise entirely self-serve in-app.

## 2. Root cause

The original three seed migrations (210, 212, 230) covered onboarding/documents/payments/safety topics but were authored before, or without reference to, the corporate-billing and PIPEDA data-rights features reaching the apps.

## 3. Fix / remediation

New migration `backend/migrations/330_seed_corporate_and_data_rights_faqs.sql` adds 4 rows:

1. **"How does billing work on a corporate account?"** (`audience='rider'`) — explains Work Mode / company-account billing.
2. **"Why was my ride blocked by company policy?"** (`audience='rider'`) — explains the pre-booking policy check.
3. **"How do I request a copy of my data?"** (`audience='both'`) — points to each app's actual self-serve export screen.
4. **"How do I delete my account?"** (`audience='both'`) — points to each app's self-serve delete flow.

Every claim was grounded against the actual running code before being written, not guessed:
- Corporate content: `rider-app/app/work-profile.tsx` (screen title, "Work Mode"/"Personal Mode" toggle), `rider-app/app/ride-options.tsx` (the policy-block confirm sheet and "Company account" payment-option label), and `.claude/context/domain-payments.md` ("Surge never applies to corporate-paid rides"). Confirmed driver-app has no corporate/Work Mode equivalent — drivers are never paid via a corporate account — so both rows are `audience='rider'`, not `'both'`.
- Data-rights content: `backend/routes/users.py`'s real `POST /data-export` (30-day PIPEDA s.9 SLA, self-fulfilled by email, usually much faster) and `DELETE /account` (locks the account immediately, allows reactivation by signing back in any time before the real 7-year SK Transportation Act retention ceiling, then a hard delete — records stay fully attributable, not anonymized, for that window). Both apps have an equivalent self-serve settings screen (`rider-app/app/privacy-settings.tsx`, `driver-app/app/driver/settings.tsx`), confirmed via source, so both rows are `audience='both'`.

**Caught by a `spinr-migration-reviewer` pass before merge** (per CLAUDE.md's standing note that Codex review has been silent since 2026-07-30): the first draft's data-export answer said the driver app's button is labelled "Export Data" — it's actually "Email My Data" (`driver-app/i18n/en.json`'s `settings.downloadData`) — and claimed a specific "usually within 24 hours" turnaround with no code backing it (`POST /data-export` fires a fire-and-forget background job with an explicit no-guarantee fallback when no email is on file, per `backend/routes/users.py`). Both fixed before this push: the button name corrected, the fabricated hour count replaced with "as soon as it's ready" — the same guidance-only, no-fabricated-SLA rule migration 210's own header comment states and that this whole FAQ series has enforced throughout.

## 4. Additional finding — not fixed, flagged for a decision

While grounding the account-deletion FAQ answer against the real backend behavior, found that **the in-app confirmation dialog copy for account deletion, in both apps, states something different from — and less generous than — what the backend actually does**:

- `rider-app/i18n/en-CA.json` (`privacy.delete_confirm_msg`): *"This will schedule your account for deletion after a 30-day grace period as required by PIPEDA."*
- `driver-app/i18n/en.json` (`settings.deleteAccountConfirmMsg`): *"Your account will be scheduled for deletion with a 30-day recovery window... after that, all your data, earnings history, and ride records are permanently deleted."*

Neither matches `delete_account_pipeda` in `backend/routes/users.py`, which the same screens actually call (`DELETE /users/account`): the account is locked **immediately**, and the rider/driver can reactivate by signing in **any time before the real 7-year retention ceiling** — not 30 days — and ride/earnings records stay fully attributable for that entire window (only GPS coordinates drop earlier, at a separate 3-year ceiling), not deleted at 30 days as the driver-app copy claims.

**Not resolved here** — this is a UI copy bug in the account-deletion *confirmation dialog* in both apps (an `i18n` string mismatch against real backend behavior), not FAQ content, and out of scope for a content-only FAQ migration. The FAQ answer added by this migration states the accurate (backend) behavior rather than repeating the confirm-dialog's wrong "30 days" claim, so the two surfaces now disagree with each other until the dialog copy is fixed — flagging that directly to the user as a separate, worth-fixing item. This is arguably more consequential than a stale FAQ answer: it's shown to every rider/driver at the exact moment they're deciding whether to permanently delete their account, and currently either understates how long their data is retained (driver-app: implies a 30-day full wipe when it's really 7 years) or overstates urgency to reactivate (both apps: implies a 30-day-only window when it's really 7 years).

## 5. Risk & impact on existing functionality

- **Blast radius: isolated to 4 new rows in the `faqs` table.** No schema change, no existing row touched, no other table touched.
- **Readers of this content**: same three as every other fix in this series — `backend/routes/admin/faqs.py` (admin CRUD), `backend/features.py::get_faqs` (the actually-live public handler), and `backend/ai/tools_support.py::search_faqs`. All three simply serve whatever rows are `is_active = true`; four new rows just means four more possible results, nothing else changes.
- Confirmed no near-duplicate of these 4 questions exists in the other seed migrations (210/212/230) — genuinely new coverage, not another instance of the near-duplicate problem fixed by migration 327.
- No interaction with the ride state machine, wallet/payment deltas, RLS policies, or any of the 16 background loops.

## 6. User-experience effect

- **Rows 1–2 (corporate)**: rider-facing only. Riders not using Work Mode see no change.
- **Rows 3–4 (data rights)**: both rider- and driver-facing.
- **Not visible mid-session** to anyone already viewing the FAQ screen (refetches on screen load, not live-pushed).
- No existing content changed — this is purely additive.

## 7. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/330_seed_corporate_and_data_rights_faqs.sql` | New migration: adds 4 FAQ rows (2 corporate/Work Mode, 2 PIPEDA data-rights) | Fill coverage gaps against features CLAUDE.md and the shipped apps already have |

## 8. Rollback plan

`git-revert-safe` — pure `INSERT`, no schema change. The header comment includes the exact `DELETE ... WHERE question IN (...)` to remove all 4 rows if needed.

## 9. Verification performed

- [x] **Programmatic structural check**: single `INSERT` statement, balanced single-quote count in actual SQL lines (32, even).
- [x] **Grounded every factual claim against source** before writing it: `rider-app/app/work-profile.tsx`, `rider-app/app/ride-options.tsx`, `.claude/context/domain-payments.md`, `backend/routes/users.py`'s `data-export`/`account` deletion handlers, `rider-app/app/privacy-settings.tsx`, `driver-app/app/driver/settings.tsx`.
- [x] Checked for near-duplicate questions against the existing 51+ rows — none found.
- [x] Second-opinion review via `spinr-migration-reviewer` (per CLAUDE.md's standing note that Codex review has been silent since 2026-07-30), including independent verification of the audience assignment and the accuracy of every FAQ claim against the cited source files, not just trusting the migration's own comments.
- [ ] Not run against a real/throwaway Supabase schema in this session — pure content `INSERT`, no schema risk, but row-insert behavior not confirmed live.

**What was NOT verified**: whether the corporate/Work Mode UI copy has any other discrepancy against the backend beyond what's already documented in `domain-corporate.md`/`domain-payments.md` — only spot-checked the specific claims this FAQ answer makes. Also not verified: real-world rendering of the two new corporate FAQ entries under `driver-app`'s FAQ screen filter (they're `audience='rider'`, so they should never appear there — reasoned from the same audience-filter logic every other fix in this series has relied on, not re-tested end-to-end).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow: purely additive content
- [x] Found-but-out-of-scope issue (the account-deletion confirm-dialog i18n mismatch, section 4) surfaced explicitly rather than silently fixed or silently ignored
