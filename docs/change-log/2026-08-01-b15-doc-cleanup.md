# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | backend (docs only — `.claude/context/`, `ACTION_ITEMS.md`) |
| Domain (Sentry tag) | safety |
| PR / commit link | (see `claude/b15-doc-cleanup` branch) |
| Related issue or gap ID | ACTION_ITEMS.md B15 |

## 1. Issue / gap identified

Two inaccuracies in `.claude/context/domain-safety.md` found during the original B15 trace, plus a stale tracking status in `ACTION_ITEMS.md`: (a) the doc claimed a DB-failure-on-SOS fallback of "direct Twilio + PagerDuty call with best-effort data" that does not exist anywhere in the codebase; (b) the doc stated the SOS hold duration is "3s" when the actual constant (`SOS_HOLD_MS` in `shared/components/SOSButton.tsx`) is 1200ms; (c) `ACTION_ITEMS.md`'s B15 entry was still marked fully open even though its DB-insert-fallback/try-except sub-finding shipped in merged PR #2931.

## 2. Root cause

(a)/(b): the doc described intended or previously-planned behavior that was either never built or changed without a doc update — not confirmed which; not investigated further here since it's out of scope (documentation-accuracy work, not a design retrospective). (c): the prior session that merged PR #2931 didn't loop back to update the tracking doc after the fix shipped.

## 3. Fix / remediation

**No code or behavior change.** Pure documentation correction:
- `.claude/context/domain-safety.md`: replaced the PagerDuty fallback claim with what actually fires today on a DB failure — admin dashboard WS broadcast, safety distribution-list email, `logger.critical()` line — and noted the absence of real on-call paging as a known gap (not resolved here, left as an open product/infra decision). Corrected hold duration 3s → 1.2s in two places. Did **not** touch the separate rideless/standalone-SOS-path finding — that's a real product decision, left alone per instruction.
- `ACTION_ITEMS.md` B15: updated status text to reflect that the try/except fix (PR #2931) and these doc corrections are done, while keeping the checkbox `[ ]` because the non-DB-dependent-fallback decision, the real-paging decision, and the rideless-SOS-path decision are all still open. B16 (the entry immediately after) was not touched.

## 4. Risk & impact on existing functionality

- **Blast radius: none.** No code files changed, no endpoint, table, background loop, or money/wallet path touched. Changes are confined to two markdown files (`.claude/context/domain-safety.md`, `ACTION_ITEMS.md`) plus this new change-log file.
- Grepped the whole backend for "pagerduty" (case-insensitive) to confirm the doc's claim was false before rewriting it — only match is an unrelated comment in `backend/utils/refresh_tokens.py` about refresh-token-reuse alerting, not SOS.
- Grepped for `SOS_HOLD_MS` to confirm the real value (1200 in `shared/components/SOSButton.tsx`) before writing it into the doc.
- No other consumer of these two markdown files was identified as needing a corresponding change — they are read by future Claude Code sessions (context import) and by ACTION_ITEMS.md's own "how to use this file" process, not by any running system.

## 5. User-experience effect

None. Nobody rider/driver/corporate-admin/internal-admin facing — these are internal engineering/AI-context documents, not consumed by any app surface or user-visible flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.claude/context/domain-safety.md` | Corrected the PagerDuty-fallback claim to the real notification channels (WS broadcast + email + `logger.critical()`), noted absence of real paging as a known gap; corrected SOS hold duration 3s → 1.2s in two places; added a note that the try/except DB-failure handling exists (PR #2931) but a non-DB-dependent fallback for sustained outages does not | Make doc match actual code behavior, per B15's original trace |
| `ACTION_ITEMS.md` | B15 section only: marked the DB-insert-fallback/try-except sub-finding and the two doc corrections as done (referencing PR #2931), kept checkbox `[ ]` and clarified which sub-items remain open (non-DB fallback decision, real-paging decision, rideless-SOS-path decision) and why (product/infra decisions, not engineering tasks) | Close the stale-status gap without overclaiming resolution of unresolved product decisions |
| `docs/change-log/2026-08-01-b15-doc-cleanup.md` | New file (this log) | Required by CLAUDE.md for any change touching the safety domain, even a docs-only one |

## 7. Before / after

Not applicable in the code sense (no behavior-changing diff — this is a documentation correction, not code). For reference, the doc text changed from:

```
- **Never silently drop an SOS** on DB failure — fall back to direct Twilio + PagerDuty call with best-effort data.
```

to:

```
- **Never silently drop an SOS** on DB failure. `trigger_emergency` (`backend/routes/rides/safety.py`) wraps the
  `safety_incidents` insert in a try/except (mirrors `backend/routes/safety.py`'s `POST /safety/report`, PR #2931)
  and returns a clean 503 instead of a 500 so the client's retry logic can recover. There is no non-DB-dependent
  fallback path today ... Building that fallback is an open decision, tracked in `ACTION_ITEMS.md` B15.
```

and the hold duration line changed from `3 s` to `1.2 s` (`SOS_HOLD_MS = 1200`).

## 8. Rollback plan

`git revert` is fully sufficient — this touches only markdown documentation with no runtime effect, no data migration, no `app_settings` flag, and no state written anywhere. There is nothing "live" to roll back; reverting the commit restores the prior doc text exactly.

## 9. Verification performed

- [ ] Automated tests run — not applicable, no code changed.
- [ ] Manual repro steps followed in staging — not applicable, no code changed.
- [x] Blast-radius grep performed — grepped the whole backend for "pagerduty" (case-insensitive; only unrelated match in `refresh_tokens.py`) and for `SOS_HOLD_MS` (confirmed `1200` in `shared/components/SOSButton.tsx`) before editing the doc, to avoid propagating a second unverified number.
- [x] Reviewed against relevant CLAUDE.md convention(s) — "Do not silently swallow errors" (confirmed the actual try/except from PR #2931 is present in `backend/routes/rides/safety.py` before describing it as fixed in both docs).
- [ ] Feature-flagged — not applicable, no user-visible or code behavior change.
- **Was a production build run?** No — not applicable, this PR touches no `admin-dashboard`/`rider-app`/`driver-app`/backend code, only markdown.

**What was NOT verified:** whether real on-call paging (PagerDuty/Opsgenie) *should* be built, and whether a rideless/standalone SOS path *should* exist, are both left as open product/infra decisions per the task instructions — no attempt was made to resolve either, only to document the current (unresolved) state accurately.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data implications).
- [x] Blast radius is stated, not assumed (grep results listed above; docs-only change, no code/runtime path touched).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — section 5 explicitly states no user-facing effect exists because there is no behavior change at all.
