-- 367_seed_remaining_compliance_faqs.sql
--
-- The last five gaps from desktop_website/docs/faq-parity.md: minimum age,
-- children and car seats, data residency, complaint escalation, and app
-- accessibility. 366 covered retention and passenger insurance.
--
-- SOURCES — every claim below is taken from something that already exists,
-- not written fresh:
--   * age of majority to hold an account — docs/legal/terms-of-service.md s.49
--     and privacy-policy.md s.7 ("Children's Privacy")
--   * primary database in Canada, some providers in the US — privacy-policy.md
--     s.3. Corroborated: the Supabase project runs in ca-central-1.
--   * Office of the Privacy Commissioner of Canada as the escalation route —
--     privacy-policy.md s.5
--   * service animals may never be refused — terms-of-service.md s.109, which
--     cites Saskatchewan human rights law
--   * no car-seat option — vehicle_types holds Economy, Premium, Van and XL
--     only, so "we do not offer one" is a statement about the product
--
-- DELIBERATELY NOT CLAIMED:
--   * No unaccompanied-minor service. audit-framework/regulatory-matrix.md
--     lists "rider >=16 unaccompanied (policy)" as an intended check, but it is
--     not implemented and it sits awkwardly beside the Terms, which require the
--     age of majority to hold an account. The answer describes the account
--     rule, which is decided, and says nothing about minors travelling alone,
--     which is not.
--   * No child-restraint age or weight thresholds. Saskatchewan sets them;
--     quoting them from memory in a published answer is how you get them wrong.
--   * No WCAG conformance claim. WCAG 2.1 AA is the stated target in CLAUDE.md,
--     not an audited result, so the answer says what is supported and invites
--     reports rather than asserting a level.
--
-- CAVEAT: the two legal documents cited are still drafts pending review (see
-- the banner at the top of privacy-policy.md). These answers restate positions
-- from them; if review changes the wording, these change too.
--
-- The accessibility answer is about the APP — screen readers, text size. The
-- existing rider FAQ already covers wheelchair-accessible vehicles and service
-- animals, and 365 was spent merging duplicates, so this does not restate it.
--
-- Idempotent: insert-if-not-exists by (question, audience).
-- Rollback: DELETE FROM faqs WHERE question IN (the five below);

INSERT INTO faqs (id, question, answer, category, audience, is_active, created_at)
SELECT gen_random_uuid()::text, v.question, v.answer, v.category, v.audience, true, now()
FROM (
    VALUES
    ('Is there a minimum age to have a Spinr account?',
     'Yes. You must be at least the age of majority in Saskatchewan to create an account, and the account has to belong to you. Spinr is not directed at children and we do not knowingly collect personal information from them.

If a young person is travelling, the trip should be booked and taken with the adult whose account it is — they are responsible for the ride they book.',
     'account', 'both'),

    ('Can I bring a child, and do you provide car seats?',
     'Children are welcome when travelling with the adult whose account booked the ride, but Spinr does not offer a car-seat option — none of our vehicle categories comes with one, and drivers are not asked to carry them.

Saskatchewan law requires young children to be secured in an appropriate child restraint. Providing it and installing it correctly is the responsibility of the adult travelling with the child, so please plan for that before you book.',
     'safety', 'rider'),

    ('Where is my personal information stored?',
     'Our primary database is hosted in Canada.

Some of the services we rely on — payment processing and notifications, for example — operate outside Canada, principally in the United States. That means some of your information may be processed there and could be reachable by US authorities under US law. PIPEDA allows this provided the information gets comparable protection, which is the standard we hold those providers to.

Our full Privacy Policy sets out who we share information with and why.',
     'account', 'both'),

    ('How do I make a complaint if I am not happy with the outcome?',
     'Start with support — most things are settled fastest there, and we can see the trip in question. If it concerns your safety, say so and it goes to our safety team rather than the general queue.

If you have a privacy concern we have not resolved to your satisfaction, you can take it further: the Office of the Privacy Commissioner of Canada handles complaints about how Canadian companies manage personal information, and you can contact them directly and independently of us.',
     'account', 'both'),

    ('Is the Spinr app usable with a screen reader or larger text?',
     'We build the rider and driver apps to work with the accessibility tools your phone already provides — screen readers, larger text and higher-contrast display settings — and we treat a barrier in the app as a bug rather than a limitation you have to work around.

If something is unusable or hard to use for you, tell support what you were trying to do and which assistive tool you use. That gets it to the people who can fix it, and reports like these are how the gaps actually get found.

For wheelchair-accessible vehicles and service animals, see the accessibility question about booking a ride.',
     'accessibility', 'rider')
) AS v(question, answer, category, audience)
WHERE NOT EXISTS (
    SELECT 1 FROM faqs f
    WHERE f.question = v.question AND f.audience = v.audience
);
