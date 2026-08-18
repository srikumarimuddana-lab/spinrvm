# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | rider-app, driver-app (i18n copy + adjacent code comments — no logic/behavior changed) |
| Domain (Sentry tag) | auth |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Flagged as a separate finding while working PR #4188 (FAQ corporate/PIPEDA coverage) |

## 1. Issue / gap identified

Both apps' account-deletion confirmation dialog — the one a rider or driver sees at the exact moment they tap "Delete Account" — states something materially different from, and less generous than, what the backend actually does:

- `rider-app/i18n/en-CA.json` (`privacy.delete_confirm_msg`) / `fr-CA.json`: *"This will schedule your account for deletion after a 30-day grace period as required by PIPEDA."*
- `driver-app/i18n/en.json` (`settings.deleteAccountConfirmMsg`) / `fr.json` / `es.json`: *"...a 30-day recovery window... after that, all your data, earnings history, and ride records are permanently deleted."*

Neither matches `delete_account_pipeda` in `backend/routes/users.py` (the handler both screens' delete buttons actually call, via `DELETE /users/account`): the account is locked **immediately**, not after a 30-day wait, and can be reactivated by signing back in **any time before the real 7-year Saskatchewan Transportation Act retention ceiling** — not 30 days — with ride/earnings records staying fully attributable for that entire window (only GPS coordinates drop earlier, at a separate 3-year ceiling).

## 2. Root cause

The dialog copy in both apps appears to have been written against an earlier or assumed 30-day PIPEDA deletion design and never updated when the real retention behavior (7-year attributable retention per the Saskatchewan Transportation Act, reactivate-anytime via `deletion_scheduled_at`) was implemented in `backend/routes/users.py`. Found and documented (not fixed) while grounding a FAQ answer for the same feature against the real backend in PR #4188; this PR is the follow-up fix.

## 3. Fix / remediation

Rewrote the confirmation-dialog copy in all 5 locale files across both apps to match the real backend behavior — locked immediately, reactivate any time by signing back in, before the 7-year Transportation Act retention period elapses:

- `rider-app/i18n/en-CA.json` / `fr-CA.json` — `privacy.delete_confirm_msg`
- `driver-app/i18n/en.json` / `fr.json` / `es.json` — `settings.deleteAccountConfirmMsg`

`rider-app` has no `es.json`/`zh.json` entry for this key (grepped — the key only exists in `en-CA.json`/`fr-CA.json` for that app), so no change needed there.

Also fixed 5 code comments in both apps that state the same wrong "30-day" model, since they're immediately adjacent to this exact mechanism and would otherwise mislead the next person who touches this code:
- `rider-app/app/reactivate-account.tsx`, `driver-app/app/reactivate-account.tsx` — header docstring
- `rider-app/app/otp.tsx`, `driver-app/app/otp.tsx` — inline comment at the `requires_reactivation` branch
- `driver-app/app/driver/settings.tsx` — inline comment above the `DELETE /users/account` call

No logic changed anywhere — confirmed the two reactivation screens (`reactivate-account.tsx` in both apps) already render the real `deletionScheduledAt` value passed from the backend dynamically (not a hardcoded "30 days"), so those screens had no user-facing bug, only a stale doc-comment.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to display copy + code comments.** No schema, endpoint, or state-machine change. Grepped both apps for `delete_confirm_msg`, `deleteAccountConfirmMsg`, and any other `30-day`/`30 day`/`grace period` string — found and fixed every hit; the only other match (`driver-app/lib/androidAuto/__tests__/carSession.test.ts`) is an unrelated Android Auto location-task grace period, not this feature.
- **Readers of the two i18n keys**: `rider-app/app/privacy-settings.tsx` and `driver-app/app/driver/settings.tsx` — confirmed via grep these are the only 2 call sites (one per app) for these specific keys.
- No admin-dashboard surface touched — the admin dashboard has no mirrored copy of this claim (grepped, no hits).
- No test currently covers either i18n string (consistent with this whole FAQ/i18n-copy series — no test infra exists for locale string content).

## 5. User-experience effect

- **Rider- and driver-facing**, shown only at the moment of tapping "Delete Account" on the confirmation dialog/alert.
- **Not visible mid-session** to anyone not actively going through account deletion.
- This *is* a copy/notification change: the previous text told users a shorter, less accurate story (an automatic 30-day full wipe with a 30-day-only recovery window) than what actually happens (immediate lock, up to 7 years to reactivate, records kept that whole time). The corrected copy is more accurate and, if anything, more reassuring for someone hesitant to delete (a much longer real recovery window) while being honest about the longer real retention period.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/i18n/en-CA.json` | `privacy.delete_confirm_msg` rewritten | Match real backend behavior (immediate lock, 7-year reactivate window) instead of a fabricated 30-day model |
| `rider-app/i18n/fr-CA.json` | Same key, French translation | Same |
| `driver-app/i18n/en.json` | `settings.deleteAccountConfirmMsg` rewritten | Same |
| `driver-app/i18n/fr.json` | Same key, French translation | Same |
| `driver-app/i18n/es.json` | Same key, Spanish translation | Same |
| `rider-app/app/reactivate-account.tsx` | Header docstring comment corrected | Same wrong 30-day model stated adjacent to this exact mechanism |
| `driver-app/app/reactivate-account.tsx` | Header docstring comment corrected | Same |
| `rider-app/app/otp.tsx` | Inline comment corrected | Same |
| `driver-app/app/otp.tsx` | Inline comment corrected | Same |
| `driver-app/app/driver/settings.tsx` | Inline comment corrected | Same |

## 7. Before / after

```
# Before (rider-app/i18n/en-CA.json)
"delete_confirm_msg": "This will schedule your account for deletion after a
  30-day grace period as required by PIPEDA. This cannot be undone."

# After
"delete_confirm_msg": "Your account will be locked right away. You can
  reactivate it any time by signing back in, before ride and trip records
  are permanently deleted after the 7-year retention period Saskatchewan's
  Transportation Act requires."
```

```
# Before (driver-app/i18n/en.json)
"deleteAccountConfirmMsg": "Your account will be scheduled for deletion
  with a 30-day recovery window.\n\nYou can reactivate any time within 30
  days by signing in again; after that, all your data, earnings history,
  and ride records are permanently deleted."

# After
"deleteAccountConfirmMsg": "Your account will be locked right away.\n\nYou
  can reactivate it any time by signing in again, before all your data,
  earnings history, and ride records are permanently deleted after the
  7-year retention period Saskatchewan's Transportation Act requires."
```

(fr/es translations of both keys mirror the same correction; see the file diffs.)

## 8. Rollback plan

`git-revert-safe` — pure copy/comment change, no schema, no config/flag, no data. A plain `git revert` restores the prior (inaccurate) text exactly.

## 9. Verification performed

- [x] All 5 edited JSON files re-parsed with `python3 -c "json.load(...)"` — all valid.
- [x] Blast-radius grep: confirmed the only 2 call sites for these i18n keys (`privacy-settings.tsx`, `driver/settings.tsx`), confirmed no other `30-day`/`grace period` string exists in either app's user-facing surface, confirmed `reactivate-account.tsx` in both apps already renders the real dynamic `deletionScheduledAt` value rather than a hardcoded day count.
- [x] Grounded the corrected copy against the real backend handler (`backend/routes/users.py`'s `delete_account_pipeda` — re-read in this session, not assumed from the earlier PR's summary).
- [x] `npx tsc --noEmit` run in both `rider-app` and `driver-app` after the edit (comment-only changes to `.tsx` files, JSON-only changes elsewhere — no type surface touched, but run to catch any accidental syntax breakage from the edits).
- [ ] No automated test exists for these i18n strings or comments — nothing to run beyond the syntax/type checks above.
- [ ] Not visually confirmed in a running simulator/emulator — this repo has no visual-regression tooling (standing gap, see `ACTION_ITEMS.md`); reasoned about correctness from the exact JSON/string values rather than a screenshot.

**What was NOT verified**: the French and Spanish translations were written by me (not run through a professional translator or the app's existing translation pipeline) — they should be treated as best-effort direct translations of the corrected English, consistent with how this repo's existing i18n content was authored, but not independently reviewed by a native speaker.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed
- [x] This is a copy/notification change — reviewed against the customer-centric tone standard (specific, non-technical, actionable) and, more importantly, corrected for factual accuracy against the real backend behavior
- [x] No silent behavior change to an already-shipped flow: the underlying deletion/reactivation mechanism is unchanged; only the dialog text now correctly describes it
