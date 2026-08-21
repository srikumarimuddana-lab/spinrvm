# Legacy Consent Legal-Sufficiency Fact Sheet

**Status:** informational only — no legal determination is made here. Oct 30 checklist item #1
(`docs/runbooks/legacy-migration-playbook.md`) requires either (a) a documented legal opinion that
the old app's consent covers current-system use, or (b) the re-consent prompt going live. This
document is fact-finding in support of (a) — it does not itself constitute the required legal
opinion, and it does not decide anything. Produced 2026-08-20 by `spinr-regulatory-compliance-checker`,
read-only, no code/doc changes made during the investigation itself.

**Scope note:** this is a factual inventory only. No judgment is made about whether old-app consent
is legally sufficient for current use — that determination is explicitly reserved for business/legal
counsel per the runbook item above and `ACTION_ITEMS.md` A42.

---

## 1. `pages.csv` — full inventory (6 rows)

All rows share `created_at = 1763720480752` ms → **2025-11-21T10:21:20.752 UTC**. All `updated_at`
fields are empty (never edited after creation). Source: the cached old-app MongoDB export's
`pages.csv`.

| # | title | type | Legal doc? | description length | Notes |
|---|---|---|---|---|---|
| 0 | Privacy Policy | driver | **Yes** | 14,373 chars | "SPINR MOBILITY INC. – DRIVER PRIVACY POLICY, Version 2.0, Effective Date: March 1, 2026, PIPEDA-Compliant" |
| 1 | Terms & Conditions | driver | **Yes** | 19,102 chars | "DRIVER TERMS OF SERVICE, SPINR Mobility Inc., Version 2.0, Effective Date: March 1, 2026" |
| 2 | About us | customer | No (marketing copy) | 2,737 chars | Marketing/brand page ("Driving Saskatchewan Forward"), not a consent document |
| 3 | Privacy Policy | customer | **Yes** | 17,280 chars | "SPINR MOBILITY INC. – RIDER PRIVACY POLICY, Version 2.0, Effective Date: March 1, 2026, PIPEDA-Compliant" |
| 4 | Terms & Conditions | customer | **Yes** | 19,233 chars | "RIDER TERMS OF SERVICE & USER AGREEMENT, Version 2.0, Effective Date: March 1, 2026" |
| 5 | About us | driver | No | 574 chars | **Verbatim Lorem Ipsum placeholder text** — not real content |

All four legal rows (0, 1, 3, 4) carry the identical version stamp: **"Version 2.0 | Effective Date:
March 1, 2026"**. Note the internal inconsistency: `created_at` (2025-11-21) predates the document's
own stated "Effective Date" (2026-03-01) by over three months, and today's date (2026-08-20) is after
both. Row 5's presence as unedited Lorem Ipsum in the same collection is a data-quality flag worth
naming: it means the "old app" export is not uniformly reliable production content — at least one row
in the same table is clearly placeholder/test data, which is relevant context when weighing how much
confidence to place in rows 0/1/3/4 as genuine, user-facing historical text versus possibly-unpublished
draft/seed content.

---

## 2. Current Spinr legal documents — status

- `docs/legal/privacy-policy.md` and `docs/legal/terms-of-service.md` are explicitly headed "Draft
  for Legal Review" and state "This is a draft, not legal advice... has not been reviewed by a lawyer
  licensed in Saskatchewan or Canada."
- Per `docs/legal/legal-text-publication-checklist.md`: both documents were **published live** to the
  `legal_documents` table on 2026-08-17 (version 1, rider+driver rows), explicitly without counsel
  review, at the product owner's direction, who was told and accepted this. The checklist calls out
  two known-false promises still live in `privacy-policy.md`: (1) the 30-day deletion promise (§5) —
  no automated enforcement job exists yet (DV-8); (2) `accessibility@spinr.ca` is not yet a live inbox.
- So: there IS something "live" today to compare against — the same text in `docs/legal/*.md`, now
  serving from `legal_documents` v1 — separate from whatever the mobile apps actually render, which
  per `terms-of-service.md`'s own header is a **different, older single-blob path**
  (`GET /settings/legal`, a single free-text field in the admin dashboard), not yet the versioned
  `legal_documents` rows. This audit could not independently confirm from the repo what text currently
  sits in that single-blob `/settings/legal` field (it's DB-managed content, not in the repo) — a real
  gap: the mobile-app-served text and the `docs/legal/*.md` draft may not currently be the same string.

---

## 3. Are the old-app document and the current draft the same document?

**No — confirmed by direct comparison, not merely eyeballed.** They share the same entity name
("Spinr Mobility Inc." — separately confirmed correct/unchanged per `ACTION_ITEMS.md` A42), the same
jurisdiction, and broadly the same PIPEDA framing, but differ substantially in structure, length, and
specific factual claims:

- **Structure**: old-app rider ToS has 18 numbered sections plus a formal tiered dispute-resolution
  clause citing *Uber v. Heller* (SCC 2020); current draft's Part A has 10 shorter sections with far
  less legal detail.
- **Length/specificity**: old-app documents are markedly more granular — e.g. the old ToS states an
  exact fare formula ("Base Fare $3.50 + Per-Kilometre Rate $1.35/km + Per-Minute Rate $0.25/min +
  Booking Fee $1.50 + City Remittance $0.27/trip... Minimum fare: $4.75") and named subscription tiers
  with prices. Current `terms-of-service.md` §4 deliberately gives no dollar figures.
- **Factual mismatch against current live config** (checked against `backend/services/fare_service.py`):
  current `base_fare = $3.50` (matches old doc) but current `per_km_rate = $1.50` (old doc: $1.35/km —
  mismatch), current `minimum_fare = $8.00` (old doc: $4.75 — mismatch), current `booking_fee = $2.00`
  (old doc: $1.50 — mismatch).
- **Surge cap mismatch**: old-app rider ToS §4.2 states *"Spinr's surge is capped at a maximum of 1.3x
  the standard fare."* Current system's actual hard cap, per `backend/utils/surge_engine.py`
  (`SURGE_CAP = 2.5`) and CLAUDE.md, is **2.5×** — nearly double what the old-app document told users.
  Current `docs/legal/terms-of-service.md` §3 avoids stating a specific cap number at all.

Conclusion on this sub-question: pages.csv is **not** a byte-for-byte or even substantively-identical
copy of the current `docs/legal/*.md` drafts. It reads as a separate, more detailed, differently-priced
version of a conceptually similar document — consistent with either (a) a genuinely earlier version of
Spinr's own legal text describing an earlier pricing configuration, or (b) synthetic/seed content
generated for the old-app export that happens to closely mirror Spinr's business model. No direct
evidence in the repo distinguishes these two possibilities; that's an open question for whoever owns
the "old app" migration source, not something resolvable from file content alone.

---

## 4. Data-category comparison (old-app doc vs. current doc) — the PIPEDA "reasonable expectations" material

**New/materially different (present in current, absent or not disclosed in old-app text):**

- **Google Gemini** (generative-AI text processing) — current `privacy-policy.md` §3 lists it as a
  subprocessor. **Zero mentions** of Gemini, AI, or any generative-AI processor anywhere in the
  old-app pages.csv text. Internally flagged already in the current draft's own pre-publication notes
  as closing "DV-16 (Gemini undisclosed)" — i.e., this was itself an undisclosed gap in Spinr's *own*
  prior state, now closed only in the unreviewed draft.
- **LogRocket** (iOS driver-app session-diagnostics/replay tool) — same situation: named in current
  `privacy-policy.md` §3, zero mentions in old-app text.
- **SOS / emergency-contact data** — current `privacy-policy.md`'s "Safety information" paragraph
  explicitly describes collecting emergency-contact name/phone/relationship and SOS-incident records.
  The old-app privacy/ToS text has **no mention of SOS or emergency contacts anywhere** — despite the
  old app's own `customers.csv` schema containing `sos_contact` and `sos_country_code` columns. So the
  old app appears to have collected this data without describing it in its own privacy policy.
- **Azure vs. Supabase** — old-app rider privacy policy's subprocessor table names "Azure (Microsoft)"
  ("Canada Central") as the cloud-hosting subprocessor. Current architecture uses Supabase, hosted on
  Railway/Fly.io — Azure is not named anywhere in current docs. Either the old document was already
  describing a hosting setup Spinr doesn't actually use, or infrastructure changed without a
  corresponding consent update.

**Present in old-app text, absent from current draft (net-narrower, lower re-consent concern but worth
naming):**

- **Dashcam footage** — old-app driver privacy §2.2 and driver ToS §12 have a full dashcam policy.
  Current `docs/legal/*.md` has no dashcam section at all.
- **Continuous driver GPS while online** — old-app driver privacy explicitly separates rider location
  (active-trip-only) from driver location (continuous while online, updated every 3 seconds). Current
  `privacy-policy.md` Part A's single "Location information" paragraph says only "while you are
  matched to an active ride" — even though the dispatch/matching architecture implies ongoing location
  writes are needed for matching regardless of active-ride status. Reads as a potential *understatement*
  in the current draft relative to what the system actually does for drivers — worth its own question
  to counsel/eng, independent of the old-vs-new comparison.

**Consistent / no material difference identified:** core account fields, Stripe payment (last-4 only),
driver eligibility documents, T4A tax-reporting purpose, general PIPEDA access/correction/deletion
framing, and governing-law/jurisdiction clauses (both: Saskatchewan + federal Canadian law, non-exclusive
Saskatchewan-court jurisdiction) are consistent in substance across both documents.

---

## 5. Mention of migration/legacy/successor-system language

**Neither document contains any mention of "legacy," "migration," "old app," "successor platform,"
"new system," or "predecessor"** — confirmed by direct regex search of the raw text on both sides. The
old-app rider privacy policy's §5.5 "Business Transactions" clause is the closest analog ("If Spinr is
involved in a merger, acquisition, or asset sale, your personal information may be transferred as part
of that transaction. We will provide notice before your information is transferred and becomes subject
to a different privacy policy.") — but this describes a hypothetical *future* M&A event with a promised
*advance notice*, not what actually happened (an app-to-app data migration). No evidence exists in
either document that old-app users were ever told their data might move to a successor platform.

---

## 6. Retention periods: old-app text vs. CLAUDE.md's actual rules vs. current draft

| Record type | Old-app pages.csv text | CLAUDE.md / current rule | Current `docs/legal/privacy-policy.md` |
|---|---|---|---|
| Trip records/receipts | 7 years | 7 years | 7 years — **matches** |
| Driver/vehicle linkage | Folded into "trip records" | 7 years | 7 years — **matches CLAUDE.md** |
| GPS — full route/location history | **7 years**, "location history (trip routes)," driver location continuously collected while online | **3 years, pickup/dropoff points only — NOT the full route** | **90 days for full trail, then deleted; 3 years for pickup/dropoff only** — **contradicts** old-app text on both length and scope |
| Insurance-period transitions | No directly comparable line item | 7 years, append-only | 7 years — matches CLAUDE.md, no old-app comparison point |
| Background check / CRC results | "Duration of active account + 1 year" | Not specified in CLAUDE.md | **Unresolved** — per the publication checklist, no authoritative source exists yet; needs a Legal/Safety decision |
| Rider account data / ToS acceptance | "2 years after account closure" | Not a CLAUDE.md-specified figure | Deletion section says only "most personal information is removed within 30 days" — a different (shorter, more general) figure |
| CASL / marketing consent records | 3 years after consent/subscription ends (both docs) | Not in CLAUDE.md non-negotiables | Not addressed in `privacy-policy.md`; handled separately in `casl-marketing-consent-disclosure.md` (still Draft status) |

**Key finding**: the largest retention discrepancy is GPS/location data. The old-app document told
users their full trip-route location history would be kept for 7 years (and, for drivers, tracked
continuously whenever the app was online, not just during trips). CLAUDE.md's actual rule — and the
current, unpublished-to-counsel draft — is the opposite in both dimensions: shorter (90 days for the
full trail) and narrower (pickup/dropoff points only for the 3-year regulatory window). This is a
*reduction* relative to what the old-app text promised, generally lower PIPEDA re-consent risk than an
*expansion* would be, but it does mean the old-app text is not simply "the same policy, still accurate."

---

## 7. Consent-capture mechanism — old app

**Direct finding: no evidence of an actual consent-acceptance record was found anywhere in the old-app
export.** Column headers across all 51 CSVs in the export were checked for any column matching
`consent|accept|agree|tos_|policy|terms` — **zero matches in any file**, including `customers.csv` and
`drivers.csv`. `users.csv` — a separate file that might plausibly have held such a field — contains
only a header row with **zero data rows**, i.e., empty/unused in this export.

- `customers.csv` header (48 columns) — no consent/acceptance field. Has `is_email_verify`,
  `is_phone_verify`, `sos_contact`, `sos_country_code`, but nothing resembling `terms_accepted_at`,
  `consent_version`, or `tos_accepted`.
- `drivers.csv` header (68 columns) — same absence.

**This is a real, nameable gap, not something to assume away**: a stored legal document (`pages.csv`)
is not evidence that any specific old-app user actually saw it, scrolled through it, or affirmatively
accepted it — no field in the exported schema records an acceptance event, a version accepted, or a
timestamp of acceptance for either riders or drivers. Whether the old app captured consent through some
other mechanism not present in this Mongo export (e.g., a signup-flow checkbox whose click event was
logged elsewhere, or an app-store/website clickwrap outside the database) cannot be confirmed or ruled
out from the data available here.

---

## 8. Consent-capture mechanism — current Spinr app (for apples-to-apples comparison)

- **Backend**: `backend/routes/auth.py` stamps a `consent_version` and `consent_accepted_at` on the
  `users` row automatically at account creation (OTP-verified signup), not via any explicit user
  gesture captured in that request. The current value is a hardcoded backend constant:
  `CONSENT_VERSION = "consumer-tos-2026-01-draft"`, with an inline comment stating this is an interim
  placeholder ("...until a real consent screen lands; bump it whenever the shipped ToS/Privacy Policy
  text materially changes"). This column was only added in migration `334_users_consent_version.sql`,
  whose own header states: pre-existing rider/driver rows "legitimately have no recorded consent
  version — NULL is the honest value, not a fabricated backfill," and that before this migration,
  **zero** rider/driver signups wrote any consent_version at all (only corporate self-serve signup did,
  via a separate `terms_accepted_version`/`terms_accepted_at` pair, migration 224).
- **Rider-app UI** (`rider-app/app/login.tsx`): the phone-number entry screen shows passive text —
  "By continuing, you agree to our Terms of Service and Privacy Policy" — with the links styled but
  **no confirmed `onPress` handler in the reviewed excerpt** and **no checkbox or other affirmative
  click-to-accept gesture**. "Continued-use implies consent," not an explicit opt-in click.
- **`legacy-consent-notice.tsx`** (both apps): a purpose-built re-consent screen exists, reachable from
  `otp.tsx`'s post-login redirect via `GET /consent/status`, with a real `POST /consent/accept`
  endpoint and a "View Policy" link to `/legal`. Feature-flagged off
  (`app_settings.legacy_consent_notice_enabled`, default `False`). No user has seen this screen in
  production as of this audit.

**Apples-to-apples summary**: neither the old app nor the current app has a demonstrated explicit "I
have read and agree" click-to-accept event tied to a specific document version for ordinary rider/
driver signup. The old app has zero evidence of any consent mechanism in the exported schema. The
current app has a passive "by continuing you agree" notice plus a backend-stamped version constant
recorded automatically at account creation (not contingent on the user actually opening or reading the
linked documents), and a genuine explicit-accept re-consent flow that is built but not turned on.

---

## 9. Open questions for counsel (genuinely open, not leading)

1. Given the entity name is confirmed the same but the actual document text, pricing figures, surge
   cap, and several retention periods differ materially between the old-app `pages.csv` text and any
   version of Spinr's current draft — does the old-app document's consent (assuming it was ever
   actually presented to and accepted by users, which has no evidence trail — see §7) cover use of
   that data on the current, differently-configured platform? Or does the platform's current,
   unpublished-to-counsel `docs/legal/*.md` draft need to be the operative document for any
   re-consent analysis?
2. Since no consent-acceptance record exists anywhere in the old-app export for any individual rider
   or driver, what evidentiary standard is needed to establish that old-app users were shown and
   accepted any version of a privacy policy or ToS at all — is the mere existence of a `pages.csv` row
   sufficient, or is affirmative acceptance evidence required per PIPEDA's consent principles?
3. Gemini and LogRocket were, on the current draft's own internal accounting, previously "undisclosed"
   even relative to Spinr's own documentation gap — does processing old-app-migrated user data through
   these two subprocessors require fresh, affirmative consent given neither appears in any version of
   a document old-app users might have seen?
4. The current, unpublished draft's location-collection description does not explicitly carve out
   continuous driver-online GPS tracking the way the old-app driver policy did — does this draft, as
   currently worded, accurately and sufficiently disclose current driver-side location practices,
   independent of the old-vs-new comparison?
5. `docs/legal/terms-of-service.md` and `privacy-policy.md` were published live to `legal_documents`
   on 2026-08-17 without counsel review, at the product owner's explicit, informed decision. Does that
   publication event itself constitute a "material change" requiring re-consent under PIPEDA (separate
   from any old-app-migration question), given riders/drivers who signed up before 2026-08-17 have a
   `consent_version` stamped against an earlier state of the (still-unpublished-to-counsel) constant,
   not against this newly-live text?
6. The old-app text promised more protective location-retention terms in one direction (full-route
   history, 7 years) and the current system does something different (shorter, narrower) — is a
   *reduction* in stated retention/collection scope something that still requires notice to
   previously-consented users, or does PIPEDA's re-consent requirement only bite on *expansions*?
7. Is there any consent record for old-app users outside the Mongo export examined here — e.g., a
   website clickwrap log, an app-store terms-acceptance gate, or a separate audit table — that would
   change the answer in §7/§8 above? This audit could only examine the cached Mongo CSV export; it
   cannot rule out consent evidence existing elsewhere.

---

## Files referenced

- Old-app export: `pages.csv`, `customers.csv` (header), `drivers.csv` (header), `users.csv` (empty) —
  cached MongoDB export, session scratchpad.
- `docs/legal/privacy-policy.md`, `docs/legal/terms-of-service.md`,
  `docs/legal/legal-text-publication-checklist.md`
- `docs/runbooks/legacy-migration-playbook.md` (item #1)
- `ACTION_ITEMS.md` (A42)
- `backend/routes/auth.py`, `backend/migrations/334_users_consent_version.sql`,
  `backend/routes/legacy_consent.py`, `backend/schemas.py`
- `rider-app/app/login.tsx`, `rider-app/app/legacy-consent-notice.tsx`
- `backend/services/fare_service.py`, `backend/utils/surge_engine.py`
