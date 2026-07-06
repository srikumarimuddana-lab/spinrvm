-- 210_seed_driver_faqs.sql
--
-- Seeds driver-audience FAQ rows for the highest-volume driver questions
-- (application status, review time, activation, document upload, Criminal
-- Record Check, registration/vehicle-category issues, payouts, trip/app
-- problems). Read by the AI assistant's search_faqs tool (audience in
-- {both, driver}); the personal-status parts are also answerable live via the
-- driver self-service tools (backend/ai/tools_driver.py).
--
-- Idempotent: each row is inserted only when no active driver FAQ with the same
-- question already exists, so re-running (or an admin having added one) is safe.
-- Content is intentionally guidance-only — no fabricated approval SLAs or payout
-- dates; timing-specific answers point the driver to their in-app status or
-- support. Forward-compatible, no schema change, no locks.
--
-- Rollback (manual): DELETE FROM faqs WHERE audience = 'driver'
--   AND category IN ('onboarding','documents','payments','troubleshooting')
--   AND question IN (<the questions below>);

INSERT INTO faqs (question, answer, category, audience, is_active, created_at)
SELECT v.question, v.answer, v.category, 'driver', true, now()
FROM (
    VALUES
    (
        'How do I check the status of my driver application?',
        'Open the driver app and go to your Account / Onboarding section to see your current status. You can also ask me here and I''ll read your current application status for you. Review starts once all your required documents are uploaded and readable. If it has been a while with no update, I can hand you to support.',
        'onboarding'
    ),
    (
        'How long does document review and approval take?',
        'Review begins once every required document is uploaded and clear. How long it takes depends on volume and whether anything needs to be re-submitted. Check your status in the app or ask me — if a document was rejected you''ll see the reason so you can fix it. If you''ve waited longer than expected, contact support.',
        'onboarding'
    ),
    (
        'Can you activate or approve my account?',
        'Approval is done by our review team — it is not automatic and support cannot skip the review. Make sure every required document is uploaded and none are expired (including your Criminal Record Check). You''ll be able to go online as soon as your account is approved. I can show you your current status and flag anything that''s missing or expired.',
        'onboarding'
    ),
    (
        'I uploaded my documents — were they received?',
        'Your uploaded documents show in the app with a status of pending review, approved, or rejected. Ask me and I''ll list your current documents and their status. If something is missing or was rejected, upload a clear, valid copy again. Documents can take a moment to sync after uploading.',
        'documents'
    ),
    (
        'What are the Criminal Record Check (CRC) requirements?',
        'A current Criminal Record Check (with Vulnerable Sector Check) is required and is renewed periodically. An expired CRC blocks you from going online and must be replaced with a recent one. Upload the most recent copy in the app; if yours has expired you''ll be asked for an updated version. I can tell you whether your CRC on file is valid or expired.',
        'documents'
    ),
    (
        'I can''t finish sign-up because the vehicle category won''t show',
        'If the vehicle category or type doesn''t appear during sign-up, make sure the driver app is updated to the latest version and you have a stable connection, then reopen the vehicle step. If it still won''t load, contact support and include your phone model so we can help you complete registration.',
        'troubleshooting'
    ),
    (
        'When and how do I get paid for my trips?',
        'Drivers keep 100% of the fare. Your completed trips and what you earned on each show in the app, and I can summarize your recent trips and earnings here. For bank-deposit timing or a payout you believe is missing, contact support and we''ll look into it.',
        'payments'
    ),
    (
        'I can''t start or complete a ride in the app',
        'If you can''t start a ride after arriving, confirm you''re at the pickup with a stable connection and refresh the screen; it also helps to check the rider is ready. If the trip still won''t start or complete, contact support right away so we can fix the ride and make sure you''re paid for it.',
        'troubleshooting'
    )
) AS v(question, answer, category)
WHERE NOT EXISTS (
    SELECT 1 FROM faqs f WHERE f.question = v.question AND f.audience = 'driver'
);
