# P4 — Future Features: Plan for a Later Sprint

These 7 items are improvements and new features — not bugs or security issues. Plan them once the app is stable and in the hands of real users.

---

## P4-1 · Build an AI Support Bot (Gemini)

**What's missing:** Drivers have no in-app way to get answers to common questions. Currently there is no support chat, no FAQ, and no help section. A driver stuck on the side of the road needs instant answers.

**Suggested approach:**
1. Add a "Help" tab to the driver app
2. Connect it to a Gemini 1.5 Flash API endpoint on the backend
3. Pre-load the bot with a knowledge base (onboarding FAQ, payout questions, document requirements)
4. If the bot can't answer, show a "Contact Support" button with a phone number or email

The backend already has the Google Generative AI dependency installed — it just needs to be wired up.

**Effort:** 5–7 days

---

## P4-2 · Add an In-App FAQ Screen

**What's missing:** There is no FAQ or help screen in the driver app. The backend has a full FAQ admin system (`backend/routes/admin/faqs.py`) but nothing displays it to drivers.

**Suggested approach:**
1. Create `driver-app/app/driver/faq.tsx`
2. Show FAQ categories (Onboarding, Payments, Documents, Technical)
3. Add a search bar
4. Link specific error messages to relevant FAQ entries (e.g. "Payment failed" → payment FAQ)

**Effort:** 2–3 days

---

## P4-3 · Create 4 Missing App Screens

**What's missing:** These screens were planned but not built:
- **Payout History** — full list of all past payouts with dates and amounts
- **Tax Documents** — download T4A slips and earnings summaries for tax season
- **Report Safety Issue** — drivers can report unsafe situations beyond SOS
- **Legal / Terms** — Privacy policy, Terms of Service (required for App Store)

**Note:** The Legal screen is actually required for App Store submission. It should be bumped to P2 if the App Store review team flags it.

**Effort:** 3–5 days total

---

## P4-4 · Enable Firebase App Check (Device Security Verification)

**What's missing:** Firebase App Check verifies that API requests come from the real Spinr app, not from bots or modified copies. The library is already installed but the enforcement switch is turned off.

**To enable:**
1. Register the app in Firebase Console under App Check
2. Enable DeviceCheck (iOS) and Play Integrity (Android)
3. Set `app_check_enforcement=True` in the backend middleware

**Effort:** 1 day (testing across both platforms required)

---

## P4-5 · Add End-to-End Test Flows for Core Ride Scenarios

**What's missing:** The automated end-to-end tests (Playwright-style E2E — see `driver-app/e2e/`) only cover logging in and going online. The core business flow — accepting a ride, verifying the pickup code, completing the trip, checking earnings — has never been tested automatically.

> **DV-15 (2026-04-23):** Framework updated from Maestro → Playwright-style E2E (implemented in `driver-app/e2e/`). YAML flow files below should be translated to the current test runner format.

**To add:**
- accept_ride flow — receive offer, accept, navigate to pickup
- verify_otp flow — enter pickup code, start trip
- complete_trip flow — arrive at destination, complete, see earnings
- payout flow — request payout, confirm balance updates

**Effort:** 3–4 days

---

## P4-6 · Add GDPR Data Export for Drivers

**What's missing:** Canadian privacy law (PIPEDA) gives individuals the right to request a copy of all data held about them. There is no mechanism for a driver to download their data.

**Suggested approach:**
- Add a "Download my data" button in Settings
- Backend generates a JSON/CSV export of all driver data
- Send a download link via email (so it's not held in app memory)

**Effort:** 2–3 days

---

## P4-7 · Wire T4A / Earnings CSV Download to the UI

**What's missing:** The backend can generate a T4A earnings summary (Canadian tax slip) and an earnings CSV export, but these are not accessible from the app UI. The `payout.tsx` screen has no download button.

**File to fix:** `driver-app/app/driver/payout.tsx` — add a "Tax Documents" section with download buttons for T4A and CSV.

**Effort:** 1 day

---

## Checklist

- [ ] P4-1 AI support bot using Gemini 1.5 Flash
- [ ] P4-2 In-app FAQ screen with search and category filters
- [ ] P4-3 Build payout-history, tax-documents, report-safety, legal screens
- [ ] P4-4 Enable Firebase App Check enforcement in production
- [ ] P4-5 Add Playwright-style E2E flows for accept, OTP, complete, payout (framework: driver-app/e2e/)
- [ ] P4-6 Add GDPR/PIPEDA data export endpoint and UI
- [ ] P4-7 Wire T4A and earnings CSV download to payout screen
