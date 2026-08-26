-- 366_seed_retention_and_passenger_insurance_faqs.sql
--
-- Two questions the product answers internally but has never published, both
-- identified in desktop_website/docs/faq-parity.md:
--
--   1. How long trip data is kept. /account-deletion states the seven-year
--      retention, but no FAQ did — and it is the obvious follow-up to "how do
--      I delete my account", which IS an FAQ. Answering it wrongly, or not at
--      all, is a PIPEDA problem.
--   2. Whether a passenger is insured. Insurance was answered only from the
--      driver's seat ("How does insurance coverage work while I'm driving?").
--      A rider asking who covers them mid-trip got nothing.
--
-- Every figure below is taken from the retention function as it currently
-- stands (335_purge_pii_retention_step_a_planned_route_polyline.sql):
--   c_ride_keep_age      7 years   — trip records
--   c_gps_anon_age       3 years   — pickup/dropoff GPS
--   c_profile_scrub_age  30 days   — profile PII after a deletion request
--   c_chat_age           90 days   — in-app messages
--   c_loc_history_age    90 days   — driver location history
-- If those constants change, these answers change with them.
--
-- The insurance answer deliberately states NO coverage limits: no dollar
-- figure appears anywhere in this codebase, and inventing one in a published
-- answer would be worse than sending the reader to support. It describes the
-- period model from CLAUDE.md instead — commercial cover applies from driver
-- assignment through the end of the trip, which is the whole time a passenger
-- is in the car.
--
-- audience: the retention answer applies to riders and drivers alike ('both');
-- the insurance one is written for riders, since drivers already have theirs.
--
-- Idempotent: insert-if-not-exists by (question, audience), matching the
-- pattern of 210/230/330. faqs.id is TEXT with no default.
-- Rollback: DELETE FROM faqs WHERE question IN (the two below);

INSERT INTO faqs (id, question, answer, category, audience, is_active, created_at)
SELECT gen_random_uuid()::text, v.question, v.answer, v.category, v.audience, true, now()
FROM (
    VALUES
    ('How long do you keep my trip data?',
     'Trip records are kept for seven years. Saskatchewan transportation rules and tax law require it, so a deletion request cannot shorten that window — it is the one thing we cannot erase on request. After seven years the records are deleted outright.

Some things go sooner. The GPS points for your pickup and dropoff are removed at three years. In-app messages between you and your driver, and drivers'' location history, are cleared after 90 days.

If you ask us to delete your account, your profile details — name, email, profile photo and saved addresses — are scrubbed within 30 days, separately from the trip records above.',
     'account', 'both'),

    ('Am I insured while I am riding?',
     'Yes. Every Spinr driver has to carry a ride-share endorsement on their own insurance before they can go online, and we check that document at sign-up and again every time they go online — an expired one stops them driving.

From the moment a driver is assigned to your trip until the trip ends, the ride is covered by commercial ride-share insurance rather than the driver''s personal auto policy. That covers the whole time you are in the car.

If you have been in an incident and need the specifics of a claim, contact support and we will put you in touch with the right details for your trip.',
     'safety', 'rider')
) AS v(question, answer, category, audience)
WHERE NOT EXISTS (
    SELECT 1 FROM faqs f
    WHERE f.question = v.question AND f.audience = v.audience
);
