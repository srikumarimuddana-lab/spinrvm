-- 490_seed_payment_payout_faqs.sql
--
-- Eight FAQs added and three rewritten directly in production on 2026-09-25
-- (docs/change-log/2026-09-25-faq-content-payments-payouts.md). This file makes
-- that content reproducible: staging, disaster-recovery and fresh databases
-- built from backend/migrations/ get the same help centre the AI assistant
-- and backend/tests/test_ai_faq_retrieval_eval.py assume.
--
-- IDEMPOTENT, same pattern as 365/366/367: inserts are skipped when a row with
-- the same question + audience already exists (true in production, where the
-- rows were added by hand), and the rewrites match on either the old or new
-- question text, so a second run changes nothing.
--
-- SOURCES — every figure below was checked against code AND the live
-- production settings on 2026-09-25/26, not code defaults alone:
--   * tip presets 15/18/20%, custom amount, one tip per ride, $500 max, tipping
--     only on the ride-complete screen (no ride-history tip entry point) —
--     rider-app/components/tipPresets.ts, rider-app/app/ride-completed.tsx,
--     backend/routes/rides/payments.py
--   * no cash; card / wallet / company account only
--   * card hold = quoted fare at booking (fare_lock_enabled is true in
--     production); scheduled rides are authorised at dispatch, not booking —
--     backend/routes/rides/booking.py, backend/utils/scheduled_rides.py
--   * capture at rating or 5 min after the ride — backend/utils/preauth_capture.py
--   * declined card at booking → no ride; at scheduled dispatch → ride still
--     goes ahead (block_on_decline=False); failed payments retried 3 times,
--     5 minutes apart (NOT over 24 h); unpaid ride blocks booking —
--     backend/utils/payment_retry.py
--   * weekly Sunday payout, $10 minimum rollover, Stripe + SIN + GST/HST
--     required; no instant or manual cash-out — backend/utils/auto_payout.py,
--     backend/routes/drivers/payouts.py
--   * cancellation / no-show: free until 2 min after accept; then $4.50,
--     $4.00 to the driver; no-show after a 5-minute wait — values from every
--     active production service_areas row (the global settings fallback is
--     lower, $2.50 driver share) and backend/services/cancellation_service.py
--   * drivers 18+ with 3 years licensed — backend/routes/drivers/profile.py
--
-- DELIBERATELY NOT CLAIMED: bank-deposit timing ("2-3 business days" appears
-- in the driver app but no backend setting backs it), and the $5,000 manual
-- review hold on large payouts.
--
-- If an admin later changes the per-area cancellation fees or the payout
-- schedule, the three rewritten answers go stale — no automation links them.
--
-- Rollback: docs/change-log/2026-09-25-faq-content-payments-payouts.md
-- section 8 has the full compensating SQL, including the verbatim prior text
-- of the three rewritten answers (so no backup restore is needed). In short:
--   DELETE FROM faqs WHERE (question, audience) IN (
--     ('How do I tip my driver?','rider'), ('Do you accept cash?','rider'),
--     ('Why do I see a pending charge, or two charges, on my card?','rider'),
--     ('My card was declined. What happens?','rider'),
--     ('How do I set up or change my bank account?','driver'),
--     ('Can I cash out instantly?','driver'), ('Do I keep my tips?','driver'),
--     ($q$What's the minimum age to drive with Spinr?$q$,'driver'));
--   then the three UPDATEs back to the section-8 text.

UPDATE faqs
SET answer = $a$You can cancel from the ride screen. It's free before a driver accepts your ride and for 2 minutes after they accept. After that, or once your driver has arrived at pickup, a $4.50 cancellation fee applies, and $4.00 of it goes to your driver.$a$,
    updated_at = now()
WHERE question = 'How do I cancel a ride, and will I be charged?'
  AND audience = 'rider'
  AND is_active = true;

UPDATE faqs
SET question = 'When do I get paid?',
    answer = $a$Spinr pays you automatically once a week. Payouts run on Sunday morning, Saskatchewan time, and include your fares and tips from the week. If your balance is under $10, it rolls over to the next week. Before your first payout you need to finish your payout (Stripe) setup and have your SIN and GST/HST number on file. How long the deposit takes to reach your account depends on your bank.$a$,
    updated_at = now()
WHERE question IN ('When and how do I get paid for my trips?', 'When do I get paid?')
  AND audience = 'driver'
  AND is_active = true;

UPDATE faqs
SET question = $q$What do I get if a rider doesn't show up or cancels late?$q$,
    answer = $a$If you've arrived at pickup and waited 5 minutes, you can mark the rider as a no-show in the app, and you receive $4.00. You also receive $4.00 when a rider cancels more than 2 minutes after you accept, or after you've arrived. If a cancellation payment looks wrong, contact support.$a$,
    updated_at = now()
WHERE question IN ($q$What happens if a rider cancels or doesn't show up?$q$,
                   $q$What do I get if a rider doesn't show up or cancels late?$q$)
  AND audience = 'driver'
  AND is_active = true;

INSERT INTO faqs (id, question, answer, category, audience, is_active, created_at)
SELECT gen_random_uuid()::text, v.question, v.answer, v.category, v.audience, true, now()
FROM (
    VALUES
    ($q$How do I tip my driver?$q$,
     $a$After your ride, choose a tip on the ride-complete screen: 15%, 18% or 20% of the fare, or enter your own amount. Your driver keeps 100% of every tip. You can leave one tip per ride, up to $500.$a$,
     'payments', 'rider'),
    ($q$Do you accept cash?$q$,
     $a$No. Rides are paid in the app with a saved card, your Spinr wallet, or a company account if your employer has set one up.$a$,
     'payments', 'rider'),
    ($q$Why do I see a pending charge, or two charges, on my card?$q$,
     $a$When you book, we place a temporary hold on your card for the quoted fare. For a scheduled ride, the hold is placed shortly before pickup, when we start finding your driver, instead of at booking. The charge is finalized when you rate the trip, or automatically 5 minutes after the ride ends. If you cancel without a fee, the hold is released. If you add a tip after the ride has been paid, the tip shows as a separate charge. How long a released hold stays on your statement depends on your bank.$a$,
     'payments', 'rider'),
    ($q$My card was declined. What happens?$q$,
     $a$If your card is declined when you book, the ride isn't created. Update your card under Payment and book again. A scheduled ride is different: your card is checked shortly before pickup, and if it's declined then, the ride still goes ahead and the fare is charged after the trip. If a payment fails after a ride, we try the charge again automatically a few times shortly afterwards. While a ride is unpaid you can't book a new one, so update your payment method under Payment to clear it.$a$,
     'payments', 'rider'),
    ($q$How do I set up or change my bank account?$q$,
     $a$Open the payout section of the Spinr Driver app and complete the Stripe payout setup. That's where you add or change your bank account. Your SIN has to be on file before you can start.$a$,
     'payments', 'driver'),
    ($q$Can I cash out instantly?$q$,
     $a$No. Instant cash-out isn't available. All earnings are paid in the weekly automatic payout every Sunday.$a$,
     'payments', 'driver'),
    ($q$Do I keep my tips?$q$,
     $a$Yes, 100% of every tip. Tips are added to your earnings and paid in your weekly payout, and you get a notification when a rider tips you.$a$,
     'payments', 'driver'),
    ($q$What's the minimum age to drive with Spinr?$q$,
     $a$You must be at least 18, and you need at least 3 years of licensed driving experience.$a$,
     'onboarding', 'driver')
) AS v(question, answer, category, audience)
WHERE NOT EXISTS (
    SELECT 1 FROM faqs f
    WHERE f.question = v.question AND f.audience = v.audience
);
