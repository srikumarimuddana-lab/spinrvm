-- 325_faq_answers_drop_ai_voice.sql
--
-- Content fix, not a schema change: 17 driver-audience FAQ answers (from
-- migrations 210 and 212) were written in first-person "ask me and I'll..."
-- voice — copy that makes sense inside the AI support assistant's chat
-- bubble (there genuinely is a "me" who can look something up there), but
-- reads as a broken promise on the driver app's static Help Center screen,
-- which has no "me" and can't act on "ask me here."
--
-- Fix: rewrite each answer to drop the first-person AI-assistant framing,
-- replacing "ask me and I'll check/tell/show you X" with a direct pointer to
-- the in-app screen that shows X (Account / Onboarding, the documents
-- section, Earnings). Every other clause/fact in each answer is preserved
-- verbatim — this is a voice edit, not a content or policy change. The
-- rider-audience set (migration 230) needed no changes: grepped for the same
-- pattern and its one offending row ("What safety features does Spinr
-- have?") was already deactivated and consolidated by migration 322.
--
-- Matches by (question, audience, OLD answer text) rather than question
-- alone, so this UPDATE only touches rows that still hold the original
-- seeded wording — if an admin has since hand-edited one of these answers
-- through the admin dashboard, that edit is left alone rather than silently
-- overwritten. Re-running is a no-op once applied (the WHERE clause no
-- longer matches). Forward-compatible, no schema change, no locks.
--
-- Also clears embedding/embedding_model on every touched row, matching the
-- convention backend/routes/admin/faqs.py already follows on every
-- question/answer edit ("editing the question/answer invalidates any stored
-- semantic embedding — clear it so search re-embeds from the new text").
-- Embeddings are only lazily recomputed when embedding IS NULL (see
-- 209_faqs_add_embeddings.sql), not on every answer change, so skipping this
-- would leave these 17 rows semantically searchable only by their old,
-- now-incorrect wording once ai_faq_semantic_enabled is turned on.
--
-- Rollback: (manual) re-run this file's UPDATEs with the old/new answer
-- text swapped (embedding/embedding_model would clear again, which is
-- correct either direction) — the original wording for each row is quoted
-- in full above as the WHERE-clause match value, so no data was lost.

-- ---------- from migration 210 (general driver set) ----------

UPDATE faqs SET answer =
    'Open the driver app and go to your Account / Onboarding section to see your current status. Review starts once all your required documents are uploaded and readable. If it has been a while with no update, contact support.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'How do I check the status of my driver application?'
  AND answer = 'Open the driver app and go to your Account / Onboarding section to see your current status. You can also ask me here and I''ll read your current application status for you. Review starts once all your required documents are uploaded and readable. If it has been a while with no update, I can hand you to support.';

UPDATE faqs SET answer =
    'Review begins once every required document is uploaded and clear. How long it takes depends on volume and whether anything needs to be re-submitted. Check your status in the app — if a document was rejected you''ll see the reason so you can fix it. If you''ve waited longer than expected, contact support.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'How long does document review and approval take?'
  AND answer = 'Review begins once every required document is uploaded and clear. How long it takes depends on volume and whether anything needs to be re-submitted. Check your status in the app or ask me — if a document was rejected you''ll see the reason so you can fix it. If you''ve waited longer than expected, contact support.';

UPDATE faqs SET answer =
    'Approval is done by our review team — it is not automatic and support cannot skip the review. Make sure every required document is uploaded and none are expired (including your Criminal Record Check). You''ll be able to go online as soon as your account is approved. Check Account / Onboarding in the app to see your current status and whether anything is missing or expired.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'Can you activate or approve my account?'
  AND answer = 'Approval is done by our review team — it is not automatic and support cannot skip the review. Make sure every required document is uploaded and none are expired (including your Criminal Record Check). You''ll be able to go online as soon as your account is approved. I can show you your current status and flag anything that''s missing or expired.';

UPDATE faqs SET answer =
    'Your uploaded documents show in the app with a status of pending review, approved, or rejected. Check your documents section in the app to see each one''s status. If something is missing or was rejected, upload a clear, valid copy again. Documents can take a moment to sync after uploading.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'I uploaded my documents — were they received?'
  AND answer = 'Your uploaded documents show in the app with a status of pending review, approved, or rejected. Ask me and I''ll list your current documents and their status. If something is missing or was rejected, upload a clear, valid copy again. Documents can take a moment to sync after uploading.';

UPDATE faqs SET answer =
    'A current Criminal Record Check (with Vulnerable Sector Check) is required and is renewed periodically. An expired CRC blocks you from going online and must be replaced with a recent one. Upload the most recent copy in the app; if yours has expired you''ll be asked for an updated version. Check your documents section in the app to see whether your CRC on file is valid or expired.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'What are the Criminal Record Check (CRC) requirements?'
  AND answer = 'A current Criminal Record Check (with Vulnerable Sector Check) is required and is renewed periodically. An expired CRC blocks you from going online and must be replaced with a recent one. Upload the most recent copy in the app; if yours has expired you''ll be asked for an updated version. I can tell you whether your CRC on file is valid or expired.';

UPDATE faqs SET answer =
    'Drivers keep 100% of the fare. Open the Earnings section in the app to see your completed trips and what you earned on each. For bank-deposit timing or a payout you believe is missing, contact support and we''ll look into it.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'When and how do I get paid for my trips?'
  AND answer = 'Drivers keep 100% of the fare. Your completed trips and what you earned on each show in the app, and I can summarize your recent trips and earnings here. For bank-deposit timing or a payout you believe is missing, contact support and we''ll look into it.';

-- ---------- from migration 212 (Saskatchewan driver set) ----------

UPDATE faqs SET answer =
    'Download the Spinr Driver app, create your account, and complete the onboarding steps: your profile, vehicle details, and the required documents. Once everything is uploaded and readable, our team reviews your application and you''ll be able to go online as soon as you''re approved. You can check your status in the app at any time, under Account / Onboarding.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'How do I sign up to drive with Spinr?'
  AND answer = 'Download the Spinr Driver app, create your account, and complete the onboarding steps: your profile, vehicle details, and the required documents. Once everything is uploaded and readable, our team reviews your application and you''ll be able to go online as soon as you''re approved. You can check your status in the app at any time, and I can read it for you here.';

UPDATE faqs SET answer =
    'Yes — your vehicle must pass a safety inspection to be eligible, and the inspection must stay current (renewed annually). If your inspection expires it will block you from going online until you upload an updated one. Check your documents section in the app to see whether your inspection on file is valid or expired.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'Does my vehicle need a safety inspection, and how often?'
  AND answer = 'Yes — your vehicle must pass a safety inspection to be eligible, and the inspection must stay current (renewed annually). If your inspection expires it will block you from going online until you upload an updated one. I can tell you whether your inspection on file is valid or expired.';

UPDATE faqs SET answer =
    'Open the Spinr Driver app, go to your documents section, and upload a clear photo of the original document (not a photocopy of a photocopy). Each document shows a status of pending review, approved, or rejected. Uploads can take a moment to sync. Your documents section shows the status of each one.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'How do I upload or update a document?'
  AND answer = 'Open the Spinr Driver app, go to your documents section, and upload a clear photo of the original document (not a photocopy of a photocopy). Each document shows a status of pending review, approved, or rejected. Uploads can take a moment to sync. Ask me and I''ll list your current documents and their status.';

UPDATE faqs SET answer =
    'Open your documents in the app to see the reason it was rejected, then upload a clear, valid, unexpired copy. Common issues are blurry photos, cut-off edges, or an expired document. Once re-uploaded it goes back into review. Your documents section flags which ones still need action.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'My document was rejected — what should I do?'
  AND answer = 'Open your documents in the app to see the reason it was rejected, then upload a clear, valid, unexpired copy. Common issues are blurry photos, cut-off edges, or an expired document. Once re-uploaded it goes back into review. I can show you which documents need action.';

UPDATE faqs SET answer =
    'An expired Criminal Record Check prevents you from going online. Obtain an updated check from your local police service or RCMP detachment and upload it in the app; once it''s approved you can go back online. Check your documents section to see whether the check on file is currently valid or expired.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'My Criminal Record Check has expired — what happens?'
  AND answer = 'An expired Criminal Record Check prevents you from going online. Obtain an updated check from your local police service or RCMP detachment and upload it in the app; once it''s approved you can go back online. I can confirm whether the check on file is currently valid or expired.';

UPDATE faqs SET answer =
    'Open the Spinr Driver app and go to your Account / Onboarding section to see your current application status. Review starts once all required documents are uploaded and readable.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'How do I check the status of my application?'
  AND answer = 'Open the Spinr Driver app and go to your Account / Onboarding section, or just ask me here and I''ll read your current application status. Review starts once all required documents are uploaded and readable.';

UPDATE faqs SET answer =
    'Approval is done by our review team and can''t be skipped or rushed by support. The fastest path is to make sure every required document is uploaded, clear, and unexpired — including your Criminal Record Check and insurance. You''ll be able to go online as soon as your account is approved. Check Account / Onboarding in the app to see anything that''s missing or expired.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'Can support activate or approve my account faster?'
  AND answer = 'Approval is done by our review team and can''t be skipped or rushed by support. The fastest path is to make sure every required document is uploaded, clear, and unexpired — including your Criminal Record Check and insurance. You''ll be able to go online as soon as your account is approved. I can flag anything that''s missing or expired.';

UPDATE faqs SET answer =
    'Going online is blocked if your account isn''t approved yet or if a required document has expired — most often the Criminal Record Check, driver''s licence, insurance, or vehicle inspection. Check your documents section in the app — it flags exactly what''s blocking you.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'Why can''t I go online?'
  AND answer = 'Going online is blocked if your account isn''t approved yet or if a required document has expired — most often the Criminal Record Check, driver''s licence, insurance, or vehicle inspection. Ask me to check your status and documents and I''ll tell you exactly what''s blocking you.';

UPDATE faqs SET answer =
    'Your completed trips and what you earned on each appear in the Spinr Driver app''s Earnings section. For bank-deposit timing or a payout you believe is missing, check your payout settings in the app or contact support.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'When and how do I get paid?'
  AND answer = 'Your completed trips and what you earned on each appear in the Spinr Driver app, and I can summarize your recent trips and earnings here. For bank-deposit timing or a payout you believe is missing, check your payout settings in the app or contact support.';

UPDATE faqs SET answer =
    'Open the Earnings section of the Spinr Driver app to see your completed trips and per-trip earnings.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'Where do I see my earnings?'
  AND answer = 'Open the Earnings section of the Spinr Driver app to see your completed trips and per-trip earnings. You can also ask me for a summary of your recent trips and earnings.';

UPDATE faqs SET answer =
    'You can reach Spinr support from the Help section of the driver app, or ask our in-app assistant, which will hand you off for anything it can''t resolve. For emergencies, call 911 or use the in-app SOS.',
    embedding = NULL, embedding_model = NULL, updated_at = now()
WHERE audience = 'driver' AND question = 'How do I contact support?'
  AND answer = 'You can reach Spinr support from the Help section of the driver app, or ask me here and I''ll hand you off for anything I can''t resolve from your account. For emergencies, call 911 or use the in-app SOS.';
