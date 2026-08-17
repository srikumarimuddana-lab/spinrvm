# Spinr Saskatchewan Driver FAQ

Driver-facing FAQ content for the Spinr Driver app help section and the in-app AI assistant. Topic coverage mirrors the standard rideshare driver help-centre categories (as used by Uber and Lyft), but every answer is original Spinr content tailored to Saskatchewan (SGI, Saskatchewan Transportation Act, PIPEDA) — no third-party text is reused.

**33 entries**, audience `driver`. Seeded by `backend/migrations/212_seed_saskatchewan_driver_faqs.sql`. One of those 33 (the SOS/safety-features entry) was later deactivated and consolidated into a shared `audience='both'` FAQ — see the note in the Safety & coverage section below — leaving 32 active driver-only entries from this migration.

> Answers are guidance-only and deliberately avoid fabricated approval or payout timelines; personal-status questions ("what is my status", "was I paid") are also answered live from the driver's own record by the assistant's `get_driver_application_status`, `get_document_status`, and `get_driver_earnings_summary` tools.

## Getting started, eligibility & vehicle

**Q: How do I sign up to drive with Spinr?**

Download the Spinr Driver app, create your account, and complete the onboarding steps: your profile, vehicle details, and the required documents. Once everything is uploaded and readable, our team reviews your application and you'll be able to go online as soon as you're approved. You can check your status in the app at any time, and I can read it for you here.

_Category: `onboarding`_

**Q: What are the requirements to drive with Spinr in Saskatchewan?**

You need a valid Saskatchewan Class 5 driver's licence with at least 3 years of driving experience, a clean driving abstract (no major violations in the past 3 years and no Criminal Code driving offences), a current Criminal Record Check with Vulnerable Sector Check, a vehicle less than 10 years old that passes a safety inspection, and the required SGI rideshare insurance. You must also be authorized by SGI to carry passengers for hire.

_Category: `onboarding`_

**Q: What driver's licence do I need?**

A valid Saskatchewan Class 5 licence is the standard requirement. You must be authorized by SGI to carry passengers for hire. Drivers holding a Class 1-4 licence may still qualify but need separate approval — contact support if that's your situation.

_Category: `onboarding`_

**Q: How much driving experience do I need?**

At least 3 years of licensed driving experience. Your driving abstract must be clean, with no major violations in the past 3 years and no Criminal Code driving offences.

_Category: `onboarding`_

**Q: Do I need special SGI authorization or vehicle-for-hire registration?**

Yes. In Saskatchewan a vehicle used to carry passengers for compensation must be registered for passenger transport with SGI (Class PT) and carry the required rideshare insurance. Spinr also carries commercial liability coverage for trips on the platform. Make sure your SGI registration and rideshare insurance are current before you go online.

_Category: `onboarding`_

**Q: What are the vehicle requirements?**

Your vehicle must be less than 10 years old, have four doors and enough seatbelts for your passengers, be in good mechanical condition, and pass a safety inspection. It must be registered for passenger transport with SGI and covered by the required rideshare insurance.

_Category: `onboarding`_

**Q: Can I drive a wheelchair-accessible vehicle (WAV)?**

Yes. Spinr supports wheelchair-accessible vehicles and riders can request them where a WAV driver is online in the service area. If you operate a WAV, let support know so your vehicle is set up correctly.

_Category: `onboarding`_

**Q: How do I check the status of my application?**

Open the Spinr Driver app and go to your Account / Onboarding section, or just ask me here and I'll read your current application status. Review starts once all required documents are uploaded and readable.

_Category: `onboarding`_

**Q: How long does approval take?**

Review begins once every required document is uploaded and clear, and how long it takes depends on volume and whether anything needs to be re-submitted. Check your status in the app — if a document was rejected you'll see the reason so you can fix it quickly. Contact support if you've been waiting longer than expected.

_Category: `onboarding`_

**Q: Can support activate or approve my account faster?**

Approval is done by our review team and can't be skipped or rushed by support. The fastest path is to make sure every required document is uploaded, clear, and unexpired — including your Criminal Record Check and insurance. You'll be able to go online as soon as your account is approved. I can flag anything that's missing or expired.

_Category: `onboarding`_

**Q: Am I an employee or an independent contractor?**

Drivers on Spinr are independent contractors, not employees. You choose when and whether to drive — there are no mandatory shifts, uniforms, or penalties for being offline. You keep 100% of your fares.

_Category: `onboarding`_

## Documents, Criminal Record Check & insurance

**Q: Does my vehicle need a safety inspection, and how often?**

Yes — your vehicle must pass a safety inspection to be eligible, and the inspection must stay current (renewed annually). If your inspection expires it will block you from going online until you upload an updated one. I can tell you whether your inspection on file is valid or expired.

_Category: `documents`_

**Q: What documents do I need to upload?**

Typically: your Saskatchewan Class 5 driver's licence, vehicle registration (passenger transport), proof of the required rideshare insurance, a recent vehicle safety inspection, and a current Criminal Record Check with Vulnerable Sector Check. A profile photo is also required. The app shows exactly which documents are needed and their status.

_Category: `documents`_

**Q: How do I upload or update a document?**

Open the Spinr Driver app, go to your documents section, and upload a clear photo of the original document (not a photocopy of a photocopy). Each document shows a status of pending review, approved, or rejected. Uploads can take a moment to sync. Ask me and I'll list your current documents and their status.

_Category: `documents`_

**Q: My document was rejected — what should I do?**

Open your documents in the app to see the reason it was rejected, then upload a clear, valid, unexpired copy. Common issues are blurry photos, cut-off edges, or an expired document. Once re-uploaded it goes back into review. I can show you which documents need action.

_Category: `documents`_

**Q: What is the Criminal Record Check requirement?**

A current Criminal Record Check that includes a Vulnerable Sector Check is required to drive, and it must be kept up to date (renewed on the schedule Spinr sets, typically annually). An expired check blocks you from going online until you upload a recent one.

_Category: `documents`_

**Q: Where do I get a Criminal Record Check in Saskatchewan?**

You obtain a Criminal Record Check with a Vulnerable Sector Check from your local police service or RCMP detachment — for example, the Regina Police Service or Saskatoon Police Service handle these for their residents. Upload the completed check in the app. If you're unsure where to go, contact support.

_Category: `documents`_

**Q: My Criminal Record Check has expired — what happens?**

An expired Criminal Record Check prevents you from going online. Obtain an updated check from your local police service or RCMP detachment and upload it in the app; once it's approved you can go back online. I can confirm whether the check on file is currently valid or expired.

_Category: `documents`_

**Q: What insurance do I need?**

Your vehicle needs the SGI rideshare / vehicle-for-hire coverage that applies while you're driving for a rideshare platform. Spinr additionally carries commercial liability coverage for trips booked on the platform. Keep your SGI coverage current — if it lapses you won't be able to go online.

_Category: `documents`_

## Safety & coverage

**Q: How does insurance coverage work while I'm driving?**

Coverage depends on what you're doing in the app. When the app is off you're on your personal auto insurance. When you're online and available, contingent coverage applies. Once you're matched and heading to a pickup, and while a passenger is in the car, commercial rideshare coverage applies. Driving with the required SGI rideshare insurance keeps you properly covered across all of these.

_Category: `safety`_

> `What safety features does the driver app have?` was deactivated by `backend/migrations/322_consolidate_sos_faq.sql` and replaced by a single `audience='both'` FAQ shared with riders ("What safety features does Spinr have?"), so the SOS/911 disclaimer has one canonical source instead of a driver-only and rider-only copy drifting independently. It is not a driver-only entry anymore, so it isn't listed here.

**Q: Do I have to accept service-animal or wheelchair requests?**

Service animals must be accommodated — drivers cannot refuse a rider with a service animal. Wheelchair-accessible vehicle requests are supported where a WAV driver is online. Refusing a service animal is a serious policy violation.

_Category: `safety`_

## Going online, trips & account

**Q: How do I go online and start getting ride requests?**

Once your account is approved, open the Spinr Driver app and tap Go Online. You'll start receiving nearby ride requests when riders are looking for a trip. Make sure your location is on and you have a stable connection.

_Category: `troubleshooting`_

**Q: Why can't I go online?**

Going online is blocked if your account isn't approved yet or if a required document has expired — most often the Criminal Record Check, driver's licence, insurance, or vehicle inspection. Ask me to check your status and documents and I'll tell you exactly what's blocking you.

_Category: `troubleshooting`_

**Q: I accepted a ride but can't start it in the app — what do I do?**

Confirm you're at the pickup location with a stable connection and refresh the screen; it also helps to check the rider is ready. If the trip still won't start or complete, contact support right away so we can fix the ride and make sure you're paid for it.

_Category: `troubleshooting`_

**Q: What happens if a rider cancels or doesn't show up?**

If a rider cancels late or doesn't show after you've arrived and waited, you may be eligible for a cancellation amount under Spinr's policy. Follow the in-app prompts to report a no-show. If something looks wrong with a cancellation, contact support.

_Category: `troubleshooting`_

**Q: How do I update my vehicle or profile details?**

You can update most profile details in the Spinr Driver app. Changing your vehicle, or a non-trivial detail tied to your verification, may need a quick review — contact support if a change you need isn't self-serve, and remember to keep your registration, insurance, and inspection current for the vehicle you drive.

_Category: `troubleshooting`_

**Q: How do I contact support?**

You can reach Spinr support from the Help section of the driver app, or ask me here and I'll hand you off for anything I can't resolve from your account. For emergencies, call 911 or use the in-app SOS.

_Category: `troubleshooting`_

## Earnings, pay & taxes

**Q: How much of the fare do I keep?**

You keep 100% of the fare. Spinr charges 0% commission on consumer rides — the amount the rider pays for the trip (base, distance, time, plus any surge and tip) is yours, with taxes handled separately.

_Category: `payments`_

**Q: When and how do I get paid?**

Your completed trips and what you earned on each appear in the Spinr Driver app, and I can summarize your recent trips and earnings here. For bank-deposit timing or a payout you believe is missing, check your payout settings in the app or contact support.

_Category: `payments`_

**Q: Where do I see my earnings?**

Open the Earnings section of the Spinr Driver app to see your completed trips and per-trip earnings. You can also ask me for a summary of your recent trips and earnings.

_Category: `payments`_

**Q: How does surge pricing affect what I earn?**

When demand is high, surge raises the fare and you keep 100% of it. Surge in Saskatchewan is capped at 2.5x and is always shown to the rider before they book — it's never added afterward. You don't set surge; it's based on real-time demand and supply.

_Category: `payments`_

**Q: How are taxes handled for drivers?**

Rider receipts show GST (5%) and, where it applies, PST (6%) as separate line items. As a driver you're an independent contractor, so you're responsible for your own tax obligations. Spinr provides a T4A-compatible earnings summary at year end. For how this applies to you, consult a tax professional.

_Category: `payments`_

---

## How this feeds the AI agent

The assistant does **not** hard-code answers — it retrieves them from the `faqs` table at query time via the `search_faqs` tool, so updating FAQs never requires a code change.

**Pipeline:** driver asks a question → the model calls `search_faqs` → the tool queries `faqs` where `is_active = true AND audience IN ('both','driver')` → matching Q&A is returned to the model, which answers in its own words (grounded, never invented).

**Two ways to load these FAQs:**

1. **Migration (this set):** run `212_seed_saskatchewan_driver_faqs.sql` — `python migrate.py --env production`. Idempotent (insert-if-not-exists by question+audience).
2. **Admin dashboard:** Admin → FAQs → New (`POST /admin/faqs` with `audience='driver'`). Best for ongoing edits by ops without a deploy.

**Matching quality (all optional, already built):**

- *Lexical + synonyms* (default): keyword overlap with a domain synonym map, so "earnings" matches a "payouts" FAQ.
- *Semantic* (`ai_faq_semantic_enabled`): embeds the query + FAQ questions and ranks by cosine similarity, catching rewordings with no shared keyword. Set `ai_embedding_provider` (`openai`/`gemini`) + key; vectors are embedded once and stored on the row.
- *Response cache* (`ai_faq_cache_enabled`): identical first-message questions replay without calling the LLM.

**Tips for good retrieval:** keep each `question` phrased the way a driver would ask it; put the key terms (CRC, payout, inspection, go online) in the question text; keep one topic per row; set `audience='driver'` (or `'both'` for shared topics like safety).
