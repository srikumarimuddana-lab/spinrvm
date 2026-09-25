# Change Impact & Risk Log — FAQ content: payments, payouts, driver age

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session, at the request of mkkreddy52 |
| Surface(s) | production data (`faqs` table) — read by backend AI assistant, rider-app/driver-app help screens, spinr.ca chat |
| Domain (Sentry tag) | ai |
| PR / commit link | srikumarimuddana-lab/spinrvm#5831 (test snapshot + this log); the data change itself was applied directly via Supabase SQL |
| Related issue or gap ID | FAQ AI accuracy test, 2026-09-25; review page "Spinr FAQ Drafts" |

## 1. Issue / gap identified

The help centre had no answer for tipping, cash, card holds, declined cards, bank setup, instant cash-out, driver tips or minimum driver age, and three live answers (rider cancellation, driver payout timing, driver no-show pay) were vague ("usually free", "check your payout settings", "may be eligible for a cancellation amount").

## 2. Root cause

FAQ content was written before the payout, cancellation and tip rules were finalised in code, and never updated with the concrete numbers.

## 3. Fix / remediation

Applied in one transaction on production Supabase (project `soavhtdhefowwvforzwb`), 2026-09-25 ~21:30 UTC:
- **8 new rows** (only drafts whose every fact was verified against code AND live settings; the 8 "Needs a decision" drafts were deliberately NOT added).
- **3 rows rewritten** in place (answer, and question text for two), so the AI does not see two competing answers.

Facts used (verified 2026-09-25): cancellation fee $4.50 ($4.00 driver / $0.50 Spinr) from the live `service_areas` rows (all 6 active areas); 120 s free window and 300 s no-show wait are code defaults with no production override; weekly Sunday auto-payout, $10 minimum, Stripe + SIN + GST/HST required (`utils/auto_payout.py`); instant/manual cash-out return 410 (`routes/drivers/payouts.py`); tip presets 15/18/20%, $500 max, one per ride (`rider-app/components/tipPresets.ts`, `routes/rides/payments.py`); hold = quoted fare while `fare_lock_enabled` (true in production), captured at rating or 5 min after the ride (`utils/preauth_capture.py`); payment retry over 24 h and unpaid ride blocks booking (`utils/payment_retry.py`, `routes/rides/booking.py`); driver 18+ with 3 years licensed (`routes/drivers/profile.py:56-59`).

| id | audience | question | change |
|---|---|---|---|
| 741f489b-7160-4f90-80a4-a8591ec3a22e | rider | How do I tip my driver? | new |
| b12f7f16-096d-45b1-ba41-92dc973c8bbb | rider | Do you accept cash? | new |
| e87cbb47-3b90-4ac5-8e06-f5cfc42c5713 | rider | Why do I see a pending charge, or two charges, on my card? | new |
| 58c80300-c5fc-44ed-aeed-5e94d1e21054 | rider | My card was declined. What happens? | new |
| 79c14a24-05eb-4750-8d3b-6cf428c29a24 | driver | How do I set up or change my bank account? | new |
| 45cffd25-394e-4009-90cb-dbbfaa500ed0 | driver | Can I cash out instantly? | new |
| 725fc0e7-dd08-4c8b-bd0a-4d2ab95e38d4 | driver | Do I keep my tips? | new |
| 3d68a612-61ab-4098-b129-ec10fc737764 | driver | What's the minimum age to drive with Spinr? | new |
| 53867275-8a94-4886-9eef-dc036ef7d67d | rider | How do I cancel a ride, and will I be charged? | answer rewritten |
| 0454ecd2-0115-463b-aa98-6dd1aac82e9b | driver | When and how do I get paid for my trips? → When do I get paid? | question + answer rewritten |
| 2bd6e062-1052-4dc7-9e49-5ba8acd68ef1 | driver | What happens if a rider cancels or doesn't show up? → What do I get if a rider doesn't show up or cancels late? | question + answer rewritten |

## 4. Risk & impact on existing functionality

- Readers of `faqs`: `ai/tools_support.py` `search_faqs` (in-app + website AI), public `/faqs` list (rider-app support screen, driver-app `driver/faq.tsx`), admin FAQ CRUD (`routes/admin/faqs.py`). No code reads FAQ text for logic.
- AI ranking: new rows compete in lexical search. Measured with `backend/tests/test_ai_faq_retrieval_eval.py`: on the original 48 questions, top-5 hits went 32 → 33, with one regression — "how do I charge a ride to my company" fell out of the top 5 (was rank 5; the new "card"/"charge" payment FAQs outrank the corporate one). Previously-unanswerable gap questions: top-5 1/10 → 7/10.
- Stated amounts are per-area admin settings. **If an admin changes cancellation fees, the no-show/cancel wait times, or the payout schedule, these three answers go stale** — no automation links them.
- `ai_faq_cache_enabled` is off in production, so no cached answers to invalidate.

## 5. User-experience effect

Riders and drivers see 8 new FAQs and 3 reworded ones in the help screens immediately (no deploy, no app update), and the AI assistant can now quote exact fees, the payout schedule and tip rules. Visible mid-session on the next FAQ load or AI message.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| (production `faqs` table) | 8 inserts, 3 updates | See section 3 |
| backend/tests/data/faq_corpus_snapshot.json | Mirrors the 68 active rows | Keep the retrieval eval in step with production |
| backend/tests/test_ai_faq_retrieval_eval.py | 3 formerly out-of-corpus questions moved into CASES; cash case accepts the new FAQ; count 60→68; floors raised to 26/36/45/51 | Ratchet the measured improvement |

## 7. Before / after

```
# Before (driver, "When and how do I get paid for my trips?")
Drivers keep 100% of the fare. Open the Earnings section in the app to see your completed trips and what you
earned on each. For bank-deposit timing or a payout you believe is missing, check your payout settings in the
app or contact support and we'll look into it.
```

```
# After ("When do I get paid?")
Spinr pays you automatically once a week. Payouts run on Sunday morning, Saskatchewan time, and include your fares
and tips from the week. If your balance is under $10, it rolls over to the next week. ...
```

## 8. Rollback plan

No deploy needed. Run in Supabase SQL editor (restores the three original answers verbatim and removes the eight new rows):

```sql
begin;
delete from faqs where id in (
  '741f489b-7160-4f90-80a4-a8591ec3a22e','b12f7f16-096d-45b1-ba41-92dc973c8bbb',
  'e87cbb47-3b90-4ac5-8e06-f5cfc42c5713','58c80300-c5fc-44ed-aeed-5e94d1e21054',
  '79c14a24-05eb-4750-8d3b-6cf428c29a24','45cffd25-394e-4009-90cb-dbbfaa500ed0',
  '725fc0e7-dd08-4c8b-bd0a-4d2ab95e38d4','3d68a612-61ab-4098-b129-ec10fc737764');
update faqs set answer = $a$You can cancel from the ride screen. Cancelling shortly after booking is usually free; a cancellation fee may apply if you cancel after a driver is already on the way. Any fee is shown before you confirm the cancellation.$a$, updated_at = now()
  where id = '53867275-8a94-4886-9eef-dc036ef7d67d';
update faqs set question = $q$When and how do I get paid for my trips?$q$, answer = $a$Drivers keep 100% of the fare. Open the Earnings section in the app to see your completed trips and what you earned on each. For bank-deposit timing or a payout you believe is missing, check your payout settings in the app or contact support and we'll look into it.$a$, updated_at = now()
  where id = '0454ecd2-0115-463b-aa98-6dd1aac82e9b';
update faqs set question = $q$What happens if a rider cancels or doesn't show up?$q$, answer = $a$If a rider cancels late or doesn't show after you've arrived and waited, you may be eligible for a cancellation amount under Spinr's policy. Follow the in-app prompts to report a no-show. If something looks wrong with a cancellation, contact support.$a$, updated_at = now()
  where id = '2bd6e062-1052-4dc7-9e49-5ba8acd68ef1';
commit;
```

Single rows can also be switched off in Admin → FAQs (`is_active = false`).

## 9. Verification performed

- [x] Transaction result checked: 3 rows updated, 8 inserted, 68 active FAQs.
- [x] Every stated number cross-checked against code and the live `settings` / `service_areas` rows (not code defaults alone — the global `cancellation_fee_driver` fallback is $2.50, but every active area overrides it to $4.00).
- [x] Retrieval eval re-run on the new corpus (numbers in section 4).
- [ ] Not viewed in the rider/driver app help screens or through a live AI answer.

## What was NOT verified

- How the answers render in the apps, and whether the live model (gpt-5.4) quotes them correctly.
- Not reviewed by the team first — added at the product owner's direct request; the 8 "Needs a decision" drafts remain unpublished.
- "Payout section" / "ride history" / "Payment" screen names were taken from code file names and the existing FAQs, not checked in the running apps.
- Not added as a migration, so a fresh environment built from `backend/migrations/` will not have these rows.
