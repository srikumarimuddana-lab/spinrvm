# Driver Document Verification — Root Cause + Automated-Onboarding Proposal

**Owner:** Engineering lead / Product — this is a scope/vendor/budget decision, not something to greenlight silently
**Trigger:** User-reported SGI D00032 form showing driver name but blank licence number/class, plus a request to design an automated, OCR-assisted onboarding pipeline
**Status:** Two concrete bugs found and fixed (see below). The larger automation proposal is **not started** — needs a go/no-go decision before any implementation begins.

---

## Part 1 — Root cause of the two reported bugs (both fixed)

### 1a. Address bug — FIXED (`backend/services/data_transfer/sgi_form_filler.py`)

**Root cause:** both real SGI templates (confirmed by reading actual AcroForm field names with `pypdf.PdfReader`, not assumed) already ship **separate** `Street address`/`City/town`/`Provincestate`/`Postalzip code` fields (D00032) and `StreetAddress`/`Citytown`/`Provincestate`/`Postalzipcode` fields (D00033). The code was setting only the street-address field to one combined string (`"#200, 1956 BROAD STREET, REGINA, SASKATCHEWAN, CANADA, S4P 1Y1"`) and leaving the template's own dedicated city/province/postal fields at their stale placeholder values (a different address entirely — `"2010 11th Ave, 7th Floor"`). Every generated form therefore showed **two different, disagreeing addresses** across its own fields. Neither template has a "Country" field at all, so `"Canada"` was being crammed into a field not meant to hold it.

**Fix:** address is now split into `_COMPANY_STREET` / `_COMPANY_CITY` / `_COMPANY_PROVINCE` / `_COMPANY_POSTAL` constants, each mapped to its correct dedicated field on both templates. Country is dropped (no field exists for it on either real form). Verified by generating both PDFs and reading every field back — each component lands exactly where it belongs, and the street-address field now contains only the street line. Two regression tests added (`test_fill_driver_details_form_address_split_across_dedicated_fields`, `test_fill_vehicle_details_form_address_split_across_dedicated_fields`) that explicitly assert city/province/postal/country strings do **not** leak into the street field, so this can't silently regress.

### 1b. Licence number/class blank — NOT a code bug; confirmed data gap

Investigated the full path: field-name mapping in `sgi_field_maps.py`, PDF field-slot generation in `sgi_form_filler.py` (verified `"Licence number_2"`, `"Licence number_3"`, etc. match the real template exactly, including the row-1-has-no-suffix inconsistency), and decryption in `routes/admin/sgi_forms.py` (`license_number` is Vault-encrypted at rest and is correctly decrypted via `_decrypt_driver_pii` before mapping). All of it is correct.

Checked the real production database directly (`soavhtdhefowwvforzwb`, table `drivers`):

```
total_drivers: 209
missing_license_number: 22
missing_license_class: 22
missing_name: 0
```

The 22 affected rows include drivers already marked `is_verified: true` / `status: active` — meaning they passed driver verification with these fields never populated. The form is rendering exactly what's in the database: nothing, because the database has nothing.

**Why the gap exists:** traced the intake path. `license_number`/`license_class` are `Optional[str] = None` fields on the self-serve profile-update endpoint (`routes/drivers/profile.py`) — nothing requires a driver to fill them in, at signup or ever. Separately, drivers *do* upload a `drivers_license` document image during onboarding (`driver_documents` table) — but **no code anywhere extracts the licence number or class from that uploaded image into the structured `license_number`/`license_class` columns.** An admin can see the license photo but has to manually retype the number/class into the driver's profile for it to reach the SGI form, and nothing prompts that to happen. That manual step is what's missing for these 22 drivers.

**Immediate remediation (small, not gated on the automation proposal below):**
1. One-time backfill campaign: an admin manually reads the licence number/class off each of the 22 drivers' already-uploaded `drivers_license` document images and enters them via the existing profile-edit path. No code change needed — this is data entry, and unblocks SGI form generation for these drivers today.
2. Going forward until the automation below is built (if it's approved): add `license_number`/`license_class` as a **required** step in the admin document-review "approve" action (not just an optional profile field) — i.e., an admin cannot mark a driver's `drivers_license` document approved without those two fields being populated first. Small, scoped change (route validation + a UI form field on the existing document-review screen), separate from this proposal. Flagging it here rather than silently doing it, since it changes an existing admin workflow (CLAUDE.md's "no silent behavior change to a live-tested flow" rule) and should go through its own PR with a Change Impact Log.

---

## Part 2 — Proposal: automated, OCR-assisted document intake

This is the larger system you asked me to design. Reasoning and recommendation below; **not implemented** — this is a multi-week, cross-surface feature with a real vendor/cost decision, and per CLAUDE.md's decomposition rules it needs its own `/plan` and phased rollout once you decide to proceed.

### 2a. What already exists (reuse, don't rebuild)

- `_REJECT_TEMPLATES` in `routes/admin/documents.py` already has exactly the tone you're asking for — polite, plain English, no jargon (`"We couldn't read your {doc} clearly. Please re-upload a sharper photo."`). This is the right pattern to extend, not replace.
- Push notification fires today on rejection. **Email and SMS do not** — only push. Given drivers may not always have the app open, and Twilio (SMS) and an email-sending path (used for Compliance report delivery, PR #2784) are both already integrated elsewhere in the codebase, extending the same rejection event to also send email + SMS is a small, well-scoped addition, not new infrastructure.
- No confirmation is sent on initial upload today — a driver uploads a document and gets no "we received it" signal at all until an admin acts (which could be hours or days later). Worth adding regardless of whether OCR is built.
- The dual-approval-gate infrastructure (`admin_export_approval_requests`, currently in progress for exports — ACTION_ITEMS.md B10) is a **second-admin-approves** pattern. The same shape — a pending state that requires a human decision before something becomes authoritative — is exactly what "OCR extracts data, human confirms it" needs. Recommend building the document-verification approval queue as a sibling table using the same state-machine shape (`pending → approved/denied`, single-use, no self-approval) rather than a third bespoke mechanism. This is the direct architectural link between what's already being built and what you're asking for here.

### 2b. Proposed pipeline (client → server → human)

**Client-side capture guidance (rider/driver apps, Expo/React Native — per CLAUDE.md this is already the cross-platform layer, so "every phone OS/make/version" is Expo's job, not something to hand-roll per-OS):**
- Use `expo-camera`'s document-capture mode (or a dedicated ID-scanning SDK — see vendor note below) instead of a bare photo picker. A guided capture UI (on-screen frame outline, "align your license inside the frame," live edge-detection) measurably reduces reject rates versus an unguided gallery upload — this is standard practice in ID-verification products (bank KYC apps, ride-share competitors) for exactly this reason.
- Client-side quality gate **before upload is even accepted**: blur-score check, minimum resolution, glare/glint detection on the flash. Reject and re-prompt locally — this avoids a slow, expensive round trip (upload → server rejects → driver re-uploads) for problems that are detectable in under a second on-device. `expo-camera` exposes enough to build a basic version of this; a dedicated document-scanning SDK (below) gives a much better one out of the box.
- Plain-English inline guidance text at capture time, e.g.: *"Place your driver's licence flat on a dark surface, in good light, with no glare from the flash. All four corners must be visible."* Explicit examples of what's rejected (photo of a photo, license in a wallet sleeve, cropped edges) reduce first-attempt failure rate — again, standard KYC-flow practice.

**Server-side OCR extraction:**
- **Recommendation: buy, don't build.** Extracting structured fields (licence number, class, name, expiry date) from a photographed government ID reliably, across lighting conditions, license formats, and print styles, is a solved-but-hard problem. Building and maintaining a custom OCR/parsing model for this is a poor use of a small team's time relative to buying it, and — more importantly — a **misread licence number on a regulator-facing SGI form is a compliance risk**, not just a UX inconvenience; that argues for a vendor with a purpose-built, continuously-improved ID-document model rather than a general-purpose OCR call plus custom regex.
- Candidates: AWS Textract (Analyze ID), Azure AI Document Intelligence (ID document model), Google Document AI (Identity module). Each returns structured key-value fields plus a per-field confidence score — the confidence score is the important part (see below). Final vendor pick should weigh: Canadian data-residency guarantees (CLAUDE.md requires Supabase-equivalent region matching for primary storage; the OCR vendor's processing region needs the same scrutiny — this is a PIPEDA question, not just a technical one), pricing at Spinr's volume, and how well each handles Saskatchewan's specific driver's-license layout (worth a small bake-off with a handful of real, consented sample documents before committing).
- **Never auto-trust the OCR output.** Every extracted field carries the vendor's confidence score. Low-confidence fields (threshold TBD during the vendor bake-off) get flagged for mandatory manual re-entry rather than silently accepted — this is the same principle as the JWT-trust-model rule elsewhere in this codebase ("never trust an unverified claim"): an OCR read is a claim about the document, not a verified fact, until a human confirms it or the confidence is high enough to treat it as such per an explicit, documented threshold.

**Human-in-the-loop second approval:**
- OCR-extracted fields populate the driver's profile as **pending**, not live — exactly mirroring the dual-approval-gate shape from B10. A different admin than whoever (if anyone) touched the record must confirm the extracted licence number/class before it's written to the authoritative `license_number`/`license_class` columns used on SGI forms. This closes the loop the current 22-driver gap exposes: no automated system should ever be the sole authority for data going onto a regulator submission.
- Reuse the exact `_REJECT_TEMPLATES` polite-plain-English pattern for every automated rejection reason (blurry, wrong document, expired, OCR-confidence-too-low, information-mismatch-with-profile) — extend it to also fire email + SMS, and add a parallel "received, under review" confirmation template sent immediately on upload, before any human or OCR review happens, so a driver isn't left wondering whether the upload worked.

### 2c. Precautions (PIPEDA / data-minimization, per CLAUDE.md)

- OCR processing means the raw document image is sent to a third party. That is a new PII disclosure that doesn't exist today (currently, document images stay entirely within Spinr's own Supabase Storage) — it needs its own PIA before implementation, following the same format as `docs/privacy/2026-07-28-pia-data-transfer-export.md`, and a vendor Data Processing Agreement with the region/retention guarantees CLAUDE.md already requires of every sub-processor.
- Don't retain OCR confidence scores or extraction metadata longer than the retention window already defined for the source document itself (CLAUDE.md's 7-year trip-log-adjacent retention rules don't automatically apply here — driver's-license OCR metadata should get its own explicit retention decision, likely much shorter, since it's a processing artifact, not the regulator-facing record itself).
- This is exactly the kind of new-third-party-SDK/data-flow change that CLAUDE.md's "What Spinr Is NOT" section flags for review — not a blocker, just confirming the guardrail applies here and should be walked through explicitly before implementation, not discovered after.

### 2d. Sizing and next step

This is materially larger than a single `/plan`-sized task — realistically: client capture UX (2-3 subtasks per app × up to 2 apps if riders never upload IDs this doesn't apply to rider-app), OCR vendor integration + confidence-threshold tuning (2-3 subtasks), the approval-queue backend (shares shape with B10 but is a distinct table/route set, 2-3 subtasks), notification-channel extension (1-2 subtasks, smallest and most independently shippable piece), and the PIA (non-engineering, but blocking). Recommend treating **notification-channel extension** (email/SMS parity on the existing rejection flow, plus an upload-received confirmation) as a fast, low-risk first slice that ships value immediately and doesn't wait on any vendor decision — then scoping the OCR/capture-guidance/approval-queue work as its own `/plan` once a vendor is chosen.

**Decision needed from you before any of Part 2 is implemented:** which slice (if any) to prioritize first, and whether to greenlight an OCR vendor bake-off (small cost, no commitment) as the next concrete step.

---

## Part 3 — Other findings from this pass (parallel best-practices check, as requested)

Scoped to what I directly observed while investigating the two bugs above — not a full separate audit:

- `routes/admin/documents.py`'s push-notification-on-rejection call wraps failures in `logger.warning(...)` and continues silently. CLAUDE.md's error-handling rule is stricter than this for DB/auth/payment paths; a failed rejection notification isn't in that exact category (the DB-level rejection is already committed, so nothing is silently lost), but it does mean a driver can be rejected with zero notification and no record that the notification failed loudly enough to catch. Worth reconsidering once email/SMS are added anyway (Part 2), since a multi-channel send makes "at least one channel worked" a more meaningful bar than today's single push-or-silence.
- No confirmation-of-receipt notification exists on any document upload today (not just licenses) — flagged above, applies to every document type in the admin review queue.
