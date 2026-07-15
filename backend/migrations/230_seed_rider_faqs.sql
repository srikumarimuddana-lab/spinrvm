-- 230_seed_rider_faqs.sql
--
-- Seeds rider-audience FAQ content (booking, fares/surge, payments/refunds,
-- wallet, promos, safety, accessibility, receipts, support). The driver app
-- had a seeded FAQ set (210/212) but the rider app had none — so riders only
-- ever saw 'both'/driver rows. These are audience='rider', global (no service
-- area); tag any province/area-specific rider FAQs via the admin dashboard.
--
-- Idempotent: insert-if-not-exists by (question, audience). faqs.id is TEXT with
-- no DB default, so it is generated with gen_random_uuid()::text.
-- Forward-compatible, no schema change, no locks.
--
-- Rollback: (manual) DELETE FROM faqs WHERE audience = 'rider'
--   AND question IN (<the questions below>);

INSERT INTO faqs (id, question, answer, category, audience, is_active, created_at)
SELECT gen_random_uuid()::text, v.question, v.answer, v.category, 'rider', true, now()
FROM (
    VALUES
    ('How do I book a ride?',
     'Open the Spinr app, enter your pickup and destination, choose a vehicle option, and confirm. You''ll see the fare and the nearest driver''s ETA before you book — nothing is charged until a driver accepts.',
     'rides'),
    ('Can I schedule a ride for later?',
     'Yes. When booking, choose Schedule and pick your date and time. We''ll dispatch a driver ahead of your pickup so they arrive on time.',
     'rides'),
    ('How is my fare calculated?',
     'Your fare is a base fare plus per-kilometre and per-minute charges, any booking or airport fees, and taxes (GST, and PST where it applies) — shown as separate line items on your receipt. The full price is shown before you book; there are no hidden fees.',
     'pricing'),
    ('What is surge pricing and when does it apply?',
     'When demand is high, a surge multiplier can raise the fare. It''s capped at 2.5x, and the exact price is always shown to you before you confirm — surge is never added after you book.',
     'pricing'),
    ('Why was my fare higher than usual?',
     'A higher fare usually reflects a longer distance or time, an airport or area fee, or surge pricing during busy periods. Open the ride''s receipt to see the exact line items; if something looks wrong, contact support.',
     'pricing'),
    ('What payment methods can I use?',
     'You can pay with a saved card or your in-app Spinr wallet. Set or change your default payment method in the app under Payment. Your card details are handled securely by our payment processor.',
     'payments'),
    ('How do I top up my Spinr wallet?',
     'Go to Wallet in the app and choose Add funds. Your balance can then be used to pay for rides. Top-ups and ride charges show in your wallet history.',
     'wallet'),
    ('How do I use a promo code?',
     'Enter the code under Promos or at checkout; an eligible discount is applied to your next qualifying ride. Each promo has its own conditions (minimum fare, first-ride-only, expiry), which are shown with the code.',
     'promotions'),
    ('How do I cancel a ride, and will I be charged?',
     'You can cancel from the ride screen. Cancelling shortly after booking is usually free; a cancellation fee may apply if you cancel after a driver is already on the way. Any fee is shown before you confirm the cancellation.',
     'rides'),
    ('How do refunds work?',
     'If you''re charged incorrectly or a trip has an issue, contact support from the ride''s receipt. Approved refunds go back to your original payment method or your Spinr wallet.',
     'payments'),
    ('What safety features does Spinr have?',
     'Every trip is tracked, you can share your trip status, and an in-app SOS notifies your emergency contacts and our safety team and offers one-tap 911. SOS never auto-dials and is not a replacement for calling 911 — if anyone is in danger, call 911 first.',
     'safety'),
    ('Can I request a wheelchair-accessible vehicle or ride with a service animal?',
     'Wheelchair-accessible vehicle (WAV) rides can be requested where a WAV driver is online in your area. Service animals are always welcome and drivers cannot refuse them.',
     'accessibility'),
    ('How do I get a receipt for my trip?',
     'Open the trip in your ride history to view and share its receipt, with the fare broken down into base, distance, time, fees, surge, tip and taxes (GST/PST).',
     'rides'),
    ('How do I contact support or report a lost item?',
     'Open Support in the app to reach our team or report a lost item from the relevant trip. You can also ask the in-app assistant, which will hand you off for anything it can''t resolve.',
     'account')
) AS v(question, answer, category)
WHERE NOT EXISTS (
    SELECT 1 FROM faqs f WHERE f.question = v.question AND f.audience = 'rider'
);
