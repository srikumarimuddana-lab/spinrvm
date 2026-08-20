# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Content/UX review (Claude Code, via 5 parallel `/legal-check` runs across the 7 remaining draft legal docs) |
| Surface(s) | docs only — pre-publication legal drafts, not live content |
| Domain (Sentry tag) | payments (fee/referral fixes) + safety (appeal channel) |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | First full-coverage run of `spinr-legal-readiness-reviewer` / `/legal-check` across all remaining drafts |

## 1. Issue / gap identified

Running `/legal-check` across the 7 remaining draft legal docs (all except `insurance-coverage-periods.md`, checked separately the same day) surfaced three categories of issue:

1. **Two resolvable placeholder brackets** in `cancellation-fee-policy.md` that a prior manual pass had searched for in the wrong file and marked "genuinely unverified."
2. **Two factual overclaims** — `promotions-referral-terms.md`'s anti-abuse paragraph named enforcement signals (device/phone/payment-method fingerprinting) that don't exist in code, and `driver-deactivation-appeals-policy.md`'s appeal-channel placeholder offered an email alternative (`driver-appeals@spinr.ca`) that was never built.
3. **One wrong checklist cross-reference** — `background-check-consent.md`'s retention-figure gate implied Privacy Policy Part B §11 already contains the CRC/VSC retention number; it doesn't.

## 2. Root cause

- The cancellation-fee time windows were searched for only in `backend/routes/rides/cancellation.py` in the 2026-08-19 pass; the actual constants live in `backend/services/cancellation_service.py` and `backend/routes/drivers/ride_cancel.py`, one layer down.
- The referral anti-abuse and appeal-channel drafts were written aspirationally, describing what a mature anti-fraud/appeals system might include, without being checked against what the specific enforcement code actually does.
- The retention-figure checklist note was written assuming a cross-referenced section already had the number, without confirming that section actually specifies it for this record type.

## 3. Fix / remediation

**`cancellation-fee-policy.md`**: filled in the post-acceptance grace period (2 minutes / 120s default) and no-show wait (5 minutes / 300s default), both cited to their real config source and phrased as "currently N by default" to reflect admin/service-area configurability, matching how the fee-split paragraph was phrased. The dispute-window bracket stays open — confirmed `routes/disputes.py` has no time-based cutoff at all, not just an unfound constant.

**`promotions-referral-terms.md`**: reworded the anti-abuse paragraph to describe only the guards that actually exist — a rolling 24-hour per-referrer payout cap and a zero-fare-ride exclusion — dropping the unimplemented device/phone/payment-method signals.

**`driver-deactivation-appeals-policy.md`**: resolved the appeal-channel placeholder to "the Appeal screen in the driver app" only, removing the unbuilt email alternative.

**`legal-text-publication-checklist.md`**: updated rows for `cancellation-fee-policy.md`, `promotions-referral-terms.md`, `driver-deactivation-appeals-policy.md`, `non-discrimination-policy.md` (added a newly-closed WAV-availability verification), and `background-check-consent.md` (corrected the retention cross-reference, added the onboarding-gating nuance and the adverse-eligibility-process gap).

`accessibility-statement.md` and `community-guidelines.md` were re-checked and found unchanged — no edits needed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 3 draft markdown files, 1 checklist file, and this change-log entry.** No application code, schema, or live content touched. None of these documents are published to the live `legal_documents` table.
- **No user-facing effect today** — pre-publication drafts, not reachable from any app screen or admin dashboard content yet.
- The corrected numbers (cancellation grace period, no-show wait) are read directly from the same config path the app itself reads at runtime (`cancellation_service.py`, `ride_cancel.py`) — if those defaults or the admin-configurable overrides change later, this document will need a re-check, same as any config-derived legal text.

## 5. User-experience effect

None — pre-publication content, not live.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/legal/cancellation-fee-policy.md` | Filled in grace-period and no-show-wait brackets from real config; corrected pre-publication notes | Close 2 of 3 remaining unverified brackets |
| `docs/legal/promotions-referral-terms.md` | Reworded anti-abuse paragraph to match actual enforcement | Remove a factual overclaim before it could reach counsel |
| `docs/legal/driver-deactivation-appeals-policy.md` | Resolved appeal-channel placeholder to in-app only | Remove an unbuilt email alternative from a live promise |
| `docs/legal/legal-text-publication-checklist.md` | Updated 5 rows: 3 with new closures, 1 with a corrected cross-reference, 1 with new open items surfaced | Keep the canonical tracking table accurate per its own process rule |
| `docs/change-log/2026-08-20-legal-draft-fixes-fee-brackets-referral-abuse-appeal-channel.md` | New change-log entry | Standard practice for this repo's live-testing-era content fixes |

## 7. Before / after

```
# Before — cancellation-fee-policy.md
...cancel more than [NUMBER, E.G. 2 MINUTES] after acceptance...
...within [NUMBER, E.G. 5 MINUTES] of arrival...

# After
...cancel more than 2 minutes after acceptance (currently 120 seconds by
default, admin-configurable per service area)...
...within 5 minutes of arrival (currently 300 seconds by default,
admin-configurable per service area)...
```

```
# Before — promotions-referral-terms.md
Spinr monitors referral activity for abuse, including unusual referral
velocity from a single device, phone number, or payment method.

# After
Spinr monitors referral activity for abuse, including an unusually high
rate of referral bonuses paid to the same account in a short period, and
does not count a ride toward a referral's qualifying activity if the ride
itself was free.
```

```
# Before — driver-deactivation-appeals-policy.md
...you may appeal within [NUMBER, E.G. 30 DAYS] by [DESCRIBE APPEAL
CHANNEL — E.G. "using the Appeal button in the driver app" OR "emailing
driver-appeals@spinr.ca"].

# After
...you may appeal within [NUMBER, E.G. 30 DAYS] using the Appeal screen in
the driver app.
```

## 8. Rollback plan

`git-revert-safe` — pure markdown content change, no schema, no data, no config, no live surface touched.

## 9. Verification performed

- All findings came from 5 parallel runs of `spinr-legal-readiness-reviewer` (the new agent, first exercised for real on `insurance-coverage-periods.md` earlier the same day) — each run grounded its claims in specific file:line citations, re-verified below.
- `cancellation_service.py:58` (`free_cancel_window_seconds`, default 120) and `ride_cancel.py:324` (`noshow_wait_seconds`, default 300) — confirmed both are read from `app_settings` with a per-service-area override path, matching the fee-split's existing configurability pattern.
- `routes/disputes.py` read in full — confirmed no time-based filing cutoff exists, not just an unfound constant.
- `referral_payout.py:107-189` — confirmed the real per-referrer 24h payout-velocity cap (default 5, migration 336) and the `grand_total > 0` qualification guard; grepped `backend/` for device-fingerprint/phone-clustering/payment-method-clustering referral checks — none found.
- `appeal.tsx` — confirmed no `driver-appeals@spinr.ca` reference anywhere in `driver-app/` or `backend/`.
- `routes/rides/estimates.py` — confirmed `wav_available` is computed and returned pre-booking, matching `non-discrimination-policy.md`'s claim.
- `data-classification.md` and `privacy-policy.md` Part B §11 — confirmed neither specifies a CRC/VSC-specific retention period; the "7 years" figures there apply to different record types.
- `accessibility-statement.md` and `community-guidelines.md` — re-checked and confirmed unchanged, no drift since their prior closures.

**What was NOT verified**: whether the live database currently has any service-area override on `free_cancel_window_seconds` or `noshow_wait_seconds` that would make the "currently N by default" framing misleading for a specific area — the agents doing this pass had no DB access, per their design; a DB-connected session should spot-check this before actual publication, same caveat as the referral-window figures. Also not verified: the dispute-window figure, background-check retention figure, and background-check adverse-process confirmation remain fully open and were correctly left that way, not guessed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — isolated to docs/legal/ and the checklist
- [x] No silent behavior change to an already-shipped flow: pre-publication content only
