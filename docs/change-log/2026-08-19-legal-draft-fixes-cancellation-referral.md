# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | docs only — pre-publication legal drafts, not live content |
| Domain (Sentry tag) | payments |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Follow-up from Support-section (FAQ/Legal) content audit — readiness check on the 8 unpublished legal drafts |

## 1. Issue / gap identified

Two of the 8 draft legal documents under `docs/legal/` (not yet published to the live `legal_documents` table — none of the 8 are) had placeholder text that was either factually wrong or described the wrong underlying mechanism:

- `cancellation-fee-policy.md` stated *"Spinr does not keep any part of a cancellation fee"* — false. `backend/schemas.py` defines both `cancellation_fee_driver` (default $4.00) and `cancellation_fee_admin` (default $0.50), both admin-configurable, and `routes/rides/cancellation.py` charges and records both on every fee-eligible cancellation.
- `promotions-referral-terms.md` had a placeholder for how long bonus *verification* takes after a referral completes — but no such verification-lag constant exists in the codebase. The real, code-backed number (`REFERRAL_WINDOW_DAYS`/`RIDER_REFERRAL_WINDOW_DAYS` = 30) measures a different thing: the deadline for the referred person to *complete* their qualifying rides, not a post-completion processing delay.

## 2. Root cause

Both documents were written as drafts before (or without cross-referencing) the actual fee-split and referral-window implementation, per each file's own header note that dollar amounts/time windows are placeholders pending a real config check — this is that check.

## 3. Fix / remediation

- `cancellation-fee-policy.md`: rewrote the fee paragraph to state the real $4.00 driver / $0.50 admin split (both configurable via `routes/admin/settings.py`), and made explicit this fee is separate from — and doesn't change — the 0%-commission fare itself. Left the 3 genuinely-unverified time-window brackets (cancellation grace period, no-show wait, dispute window) untouched — searched `routes/rides/cancellation.py` and `services/cancellation_service.py` for a hardcoded constant and found none; not inventing a number.
- `promotions-referral-terms.md`: replaced the wrong-mechanism placeholder with the real 30-day completion-deadline language and the real ride thresholds (rider: 1 ride, driver: 10 rides). Verified against the live database that no service area currently overrides the 30-day/1-ride global defaults (checked all 6 areas with an explicit override set — all match).
- Updated `docs/legal/legal-text-publication-checklist.md`'s rows for both documents to reflect exactly what's now closed vs. still open, per the checklist's own process rule ("update this table in the same PR that closes a gating condition").

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 3 markdown files under `docs/legal/` and one change-log doc.** No application code, schema, or live content touched — none of these 8 documents exist in the `legal_documents` table yet (confirmed earlier this session: only `tos`/`privacy` are published).
- **No user-facing effect today** — these are pre-publication drafts, not reachable from any app screen or admin dashboard content yet.
- The real numbers pulled in (fee split, referral window/thresholds) were verified against actual code and live service-area data, not invented — if those underlying values change later, these two documents will need a re-check (already true of any config-derived legal text, not a new risk this introduces).

## 5. User-experience effect

None — pre-publication content, not live.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/legal/cancellation-fee-policy.md` | Corrected the fee-split paragraph (was factually wrong); documented what's still genuinely unverified | Fix a false claim about who keeps a cancellation fee before it could reach counsel or publication |
| `docs/legal/promotions-referral-terms.md` | Replaced a wrong-mechanism placeholder with the real 30-day/1-10-ride figures; verified against live service-area data | Close the gating condition; state the real mechanism instead of a semantically wrong one |
| `docs/legal/legal-text-publication-checklist.md` | Updated both documents' rows to reflect closed vs. still-open conditions | Keep the canonical tracking table accurate per its own process rule |
| `docs/change-log/2026-08-19-legal-draft-fixes-cancellation-referral.md` | New change-log entry | Standard practice for this repo's live-testing-era content fixes |

## 7. Before / after

```
# Before — cancellation-fee-policy.md
The cancellation fee is [AMOUNT, E.G. A FLAT $X OR A FORMULA] and goes to
the driver, consistent with Spinr's no-commission model — Spinr does not
keep any part of a cancellation fee.

# After
The cancellation fee is a flat amount, currently $4.50 by default (admin-
configurable). $4.00 goes to the driver to compensate the time and travel
already spent reaching you, and $0.50 is a Spinr service portion. This fee
is separate from — and does not change — Spinr's 0% commission on the fare
of a completed ride: drivers still keep 100% of every fare they actually
drive.
```

```
# Before — promotions-referral-terms.md
...the referral bonus is credited only after the qualifying action is
verified, which may take up to [NUMBER, E.G. 7 DAYS].

# After
...the person you referred has 30 days from applying your referral code to
complete the qualifying action (1 ride for a rider referral, 10 rides for a
driver referral) — after 30 days with no qualifying action, the referral
expires unpaid. Once the qualifying action is completed, the bonus is
credited to both accounts.
```

## 8. Rollback plan

`git-revert-safe` — pure markdown content change, no schema, no data, no config, no live surface touched.

## 9. Verification performed

- [x] Grounded the cancellation-fee correction against `backend/schemas.py` (`cancellation_fee_driver`/`cancellation_fee_admin` defaults), `routes/admin/settings.py` (confirmed both are admin-configurable settings, not fixed constants), and `routes/rides/cancellation.py` (confirmed both amounts are actually charged and recorded, not just defined).
- [x] Grounded the referral-terms correction against `backend/routes/drivers/referrals.py` and `backend/routes/users.py` (the real `REFERRAL_WINDOW_DAYS`/`RIDER_REFERRAL_WINDOW_DAYS`/`*_RIDES_REQUIRED` constants and their doc comments, which explicitly describe the deadline-to-complete semantic, not a verification-lag one).
- [x] Queried the live database directly (`service_areas` table) to confirm no area currently overrides these global defaults, rather than assuming from code alone.
- [x] Searched for the still-open cancellation-fee time-window constants (`routes/rides/cancellation.py`, `services/cancellation_service.py`) and confirmed none exist as hardcoded values — left those brackets open rather than guessing, consistent with each draft's own "do not invent numbers" instruction.

**What was NOT verified**: whether `backend/routes/users.py`'s own comment ("the actual wallet crediting is a separate money task... intentionally not done here") is stale — `utils/referral_payout.py`'s module docstring suggests rider referral payout is in fact implemented, which would make that comment outdated, but reconciling that comment is a separate, unrelated code-cleanup task, not something this docs-only fix addresses.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — explicitly confirmed these documents aren't live anywhere yet
- [x] No silent behavior change to an already-shipped flow: pre-publication content only
