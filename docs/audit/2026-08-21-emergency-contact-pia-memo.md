# Privacy Impact Assessment — Emergency Contact Storage & SOS Disclosure

| Field | Value |
|---|---|
| PIA Reference | SPINR-PIA-2026-01 |
| Version | 1.1 — Decided 2026-08-21 |
| Program/System | Rider emergency-contact storage (`emergency_contacts` table) and its use in the SOS/safety-check-in flow |
| Prepared by | Claude (session), on behalf of @vikas |
| Assessment date | 2026-08-21 |
| Decision date | 2026-08-21 (@vikas, standing in for Privacy/Legal) |
| Applicable legislation | PIPEDA (federal, private-sector) — Spinr is a commercial ride-share operator, not a government institution, so FOIP/HIPA do not apply here |
| Status | **DECIDED** — see Section 9. Both recommendations approved together: encrypt at rest (mirroring migration 32) AND build the third-party consent/opt-out handshake alongside it, not sequenced apart. Implementation tracked separately (not yet built as of this decision). |

This memo was historical evidence for Privacy/Legal's decision, per
`docs/audit/2026-08-19-decision-writeups.md`'s ranked blocker #13 and the corresponding decision-log
row. The decision has now been made (Section 9) — this document is retained as the record of what
was evaluated and why, ahead of implementation. Every code claim below was independently re-verified
against the current codebase on 2026-08-21, not carried forward from an earlier pass without
re-checking.

---

## Section 1: Executive Summary

Spinr riders can save up to 3 emergency contacts (name, phone, relationship). These are stored as
plain, unencrypted text in the `emergency_contacts` table and are read by the SOS safety flow, which
SMS-blasts the contact's name and a live-location link to up to 5 saved contacts when a rider
triggers an emergency alert.

**Privacy risk rating: Medium-High.** The data itself is modest in volume and sensitivity relative to,
say, health records — but it identifies a *third party* (the emergency contact) who never consented
to being in Spinr's system at all, and it is currently readable in plaintext by anyone with
database or backup-level access. Spinr already has a proven encryption pattern in production for a
comparable class of data (`drivers.license_number`/`vehicle_vin`), so closing this gap is not a
research problem — it is a scoping and consent decision.

**Recommendations:** 2 (1 High, 1 Medium) — see Section 8. Both require a Privacy/Legal decision
before implementation; neither has been implemented.

**Approval recommendation:** Decide both recommendations together (encryption approach AND the
third-party-consent gap), not encryption alone — encrypting storage without addressing consent
closes only half of the actual PIPEDA exposure on this data.

---

## Section 2: System/Program Description

**Purpose and business justification.** Emergency contacts exist to support Spinr's SOS safety
feature (`.claude/context/domain-safety.md`): if a rider triggers an emergency alert mid-ride, the
backend SMS-blasts their saved contacts with the rider's name and a live-tracking link, so someone
who knows the rider can act (call them, call 911, meet the vehicle). This is explicitly *not* a 911
replacement — Spinr's own product principles state SOS "notifies emergency contacts and our safety
team and *offers* one-tap 911; it never auto-dials."

**Data flow, current state:**

1. **Collection.** A rider adds a contact via `POST /users/emergency-contacts`
   (`backend/routes/users.py:785-819`) — name, phone, relationship, capped at `MAX_EMERGENCY_CONTACTS
   = 3` (enforced in the handler; see the note in Section 3 on a discrepancy against `domain-safety.md`'s
   older claim of a cap of 5).
2. **Storage.** Written to Supabase Postgres, table `emergency_contacts`
   (`backend/migrations/08_complete_schema.sql:328-336`, re-affirmed defensively by
   `120_ensure_emergency_contacts_and_gps_column.sql:22-34` for a fresh-DB ordering bug). Columns:
   `id`, `user_id`, `name`, `phone`, `relationship`, `created_at` — all plain `TEXT`, no
   column-level encryption of any kind.
3. **Access control.** Row Level Security is owner-scoped: `SELECT`/`INSERT`/`DELETE` policies
   restrict access to `auth.uid()::text = user_id` (migration 120, lines 37-72). There is no
   `UPDATE` policy — a rider must delete and re-add to change a contact. RLS is an
   *application-layer* control: it governs what a rider's own authenticated session can query
   through Supabase's PostgREST layer. It does **not** restrict direct database access, backup
   access, or `service_role`-key access (which the backend itself uses).
4. **Use — the rider's own management screen.** `GET`/`DELETE /users/emergency-contacts`
   (`backend/routes/users.py:768-781`, `822-835`) let the rider view and remove their own saved
   contacts.
5. **Use — SOS disclosure to a third party.** `backend/routes/rides/safety.py:315`, the
   `trigger_emergency` handler: reads up to 5 contacts for the triggering rider and sends each an
   SMS via Twilio containing the rider's name and a live-location link. This is the point at which
   a third party's phone number is used for an unsolicited, safety-critical text message they never
   personally agreed to receive.
6. **Deletion.** `backend/routes/users.py:540` — bulk `delete_many("emergency_contacts", ...)` fires
   on account deletion, so contacts do not outlive the owning rider's account.

**No admin endpoint reads this table.** Confirmed via grep across `backend/routes/admin/` — internal
staff have no UI or API path into `emergency_contacts` at all. The only application-level readers
are the rider's own management screen and the SOS trigger.

**Third-party data subject.** The critical framing for this PIA: the *emergency contact* is a data
subject under PIPEDA whose personal information (name, phone number) Spinr collects and uses (to
send them an SMS) without ever interacting with them directly. Only the *rider* consents to
Spinr's terms; the contact does not. This is the structural reason this table's privacy profile
differs from, e.g., the rider's own address book — it is personal information about someone who has
never seen Spinr's privacy policy.

---

## Section 3: Personal Information Inventory

| Data Element | Category | Sensitivity | Source | Purpose | Legal Authority (PIPEDA) | Retention | Recipients |
|---|---|---|---|---|---|---|---|
| Contact `name` | Basic Identity (of a third party) | Medium — identifies a specific person | Entered by the rider, about someone else | Displayed to the contact via SOS SMS so they know who is contacting them | Implied consent from the *rider* only; no consent from the contact | Until rider deletes the contact or deletes their account | Twilio (SMS delivery), the contact themself (SMS recipient) |
| Contact `phone` | Basic Identity / Contact Info | Medium-High — a working phone number for a specific person, used to send them an unsolicited SOS SMS | Entered by the rider | SMS delivery target | Same as above | Same as above | Same as above |
| Contact `relationship` | Basic Identity (descriptive) | Low | Entered by the rider (free text) | Displayed to the rider on their own management screen only — not sent to the contact | Rider's own data about their own life | Same as above | None (rider-facing only) |
| `user_id` (the rider) | Basic Identity | Low in isolation, but links the above to a specific Spinr account | System-generated | Row ownership / RLS scoping | Rider's own account, already consented via ToS | Same as above | None beyond internal join |

**Confirmed via code**: no field beyond these five columns exists on this table (migration
08/120's `CREATE TABLE` statements are the complete schema). No email, no address, no photo.

---

## Section 4: Privacy Risk Analysis

### R-001 — Emergency contact PII readable by anyone with DB/backup access

- **Description:** RLS restricts *application-layer* (PostgREST) access to the owning rider, but the
  raw `name`/`phone` columns are plaintext at rest. Anyone with direct Postgres access, a database
  backup, or the `service_role` key can read every contact's name and phone number unencrypted.
- **Likelihood:** 2 (Unlikely) — requires privileged access (DB admin, backup custody, or a leaked
  service-role key), not a routine app-layer exposure.
- **Impact:** 3 (Moderate) — a name + phone number pairing is a real re-identification/harassment
  risk if exposed, but it is not a health record, SIN, or financial credential.
- **Risk rating:** Medium (2 × 3 = 6).
- **PIPEDA principle at risk:** Safeguards (Principle 7) — "personal information shall be protected
  by security safeguards appropriate to the sensitivity of the information."
- **Mitigation:** Column-level encryption at rest (see Section 8, Recommendation 1).
- **Residual risk after mitigation:** Low — matches the residual risk profile already accepted for
  `drivers.license_number`/`vehicle_vin` under the existing pgsodium/Vault pattern.
- **Owner if actioned:** Eng (backend), on Privacy/Legal's direction.

### R-002 — Emergency contact never consents to being in Spinr's system or receiving an SOS SMS

- **Description:** The rider adds a contact unilaterally. Spinr collects that person's name and
  phone number, and — only in an emergency — sends them an unsolicited SMS with the rider's name
  and live location. The contact has no opportunity to opt in, opt out, or even know in advance
  that a Spinr account lists them.
- **Likelihood:** 3 (Possible) — this is not a hypothetical: it happens every time SOS fires and a
  rider has saved contacts, which is the feature's entire intended operation.
- **Impact:** 2 (Minor) in the common case (a wanted, helpful alert to someone who'd want to know)
  but escalates sharply in edge cases: a contact added without the person's knowledge, a stale
  number now belonging to someone else, or a contact relationship that has since soured (e.g.
  post-breakup) receiving a location-sharing SMS tied to a name they may not want contacted about.
- **Risk rating:** Medium (3 × 2 = 6), with a plausible tail toward High in the edge cases above.
- **PIPEDA principle at risk:** Consent (Principle 3) — "the knowledge and consent of the individual
  are required for the collection, use, or disclosure of personal information," and this is
  information *about* a person who was never asked.
- **Mitigation:** Not scoped by this PIA in detail — tracked separately as a related, larger gap
  (safety-toolkit gap-analysis finding F13: "Emergency-contact OTP + STOP opt-out," effort M,
  `docs/proposals/2026-08-16-safety-toolkit-gap-analysis.md:340`). A consent/verification handshake
  (e.g. an opt-in text to the contact when first added, honoring STOP) would close this.
- **Residual risk after mitigation:** Low-Medium — full closure likely requires accepting some
  friction in the safety flow (a contact who hasn't confirmed opt-in might not be usable during a
  real emergency), which is itself a safety-vs-privacy tradeoff Privacy/Legal and Product need to
  weigh together, not an engineering call.
- **Owner if actioned:** Product + Eng (safety), on Privacy/Legal's direction; explicitly out of
  this PIA's scope to design in detail — flagged so it isn't decided in isolation from R-001.

### R-003 (context only, not a new finding) — No access logging on this table

- **Description:** There is no audit trail of who read `emergency_contacts` rows via privileged
  access (DB admin, backup restore, etc.). This isn't a gap unique to this table — it's the general
  absence of DB-query-level audit logging in this codebase — but it compounds R-001: even if a
  privileged-access read occurred, there would be no record of it.
- **Not independently risk-scored** — this is a cross-cutting infrastructure gap, not specific to
  emergency contacts, and is out of scope for this PIA to resolve.

---

## Section 5: Privacy Controls Assessment (PIPEDA Fair Information Principles)

| Principle | Current state |
|---|---|
| 1. Accountability | No single named owner for this table's privacy posture today; this PIA's existence is itself the first accountability artifact for it. |
| 2. Identifying Purposes | Purpose (SOS contact) is implicit in the feature name but not explicitly stated to the rider at collection time in a privacy-notice sense — the rider's own privacy notice covers their own data, not what happens with the third party's. |
| 3. Consent | **Gap** — see R-002. The rider consents on the contact's behalf, which PIPEDA does not treat as valid consent from the contact themselves. |
| 4. Limiting Collection | Compliant — only name/phone/relationship collected, capped at 3 contacts (code-verified 2026-08-21; note `domain-safety.md`'s historical text referenced a cap of 5 that does not match the current `MAX_EMERGENCY_CONTACTS = 3` in `routes/users.py` — worth a small doc-accuracy follow-up separate from this PIA). |
| 5. Limiting Use/Disclosure/Retention | Use is scoped to the SOS flow and the rider's own management screen; no secondary use (marketing, analytics) found via grep. Retention ends at account deletion via the bulk-delete call. Compliant. |
| 6. Accuracy | No `UPDATE` RLS policy — rider must delete/re-add to correct a contact. Minor friction, not a compliance gap. |
| 7. Safeguards | **Gap** — see R-001. Plaintext at rest, no encryption. |
| 8. Openness | Spinr's privacy policy (`docs/legal/privacy-policy.md`) was not re-read as part of this PIA to confirm whether it explicitly names emergency-contact collection-of-third-party-data as a documented practice — flagged as a follow-up check, not verified here. |
| 9. Individual Access | The *rider* can view/delete their saved contacts. The *emergency contact themselves* has no access mechanism to see what Spinr holds about them or request its deletion — a PIPEDA access-request gap for a data subject who isn't a Spinr account holder. |
| 10. Challenging Compliance | Standard Spinr support/complaint channels would presumably apply, but no contact-specific redress path exists (e.g. a phone number that receives an unwanted SOS SMS today has no obvious way to ask Spinr to stop). |

---

## Section 6: Data Sharing Assessment

The only external recipient is **Twilio** (SMS delivery for the SOS alert). Twilio is a
subprocessor; per `docs/legal/subprocessor-list.md` (not re-read in full for this PIA — flagged as
a follow-up check) Spinr's existing subprocessor agreement presumably already covers SMS content in
general. This PIA does not re-verify that agreement's specific coverage of third-party (non-account-holder)
personal information — that verification is recommended before any implementation, not assumed.

No other third party receives emergency-contact data. No admin export, no analytics pipeline, no AI
tool (`backend/ai/tools_*.py`) reads this table — confirmed via grep, not previously verified in the
2026-08-19 decision write-up.

---

## Section 7: Breach Response Plan

No table-specific breach plan exists beyond Spinr's general breach protocol
(`CLAUDE.md`'s Compliance section: P0 incident classification, 24h scope assessment, 72h Privacy
Commissioner notification if "real risk of significant harm"). Given R-001's plaintext-at-rest
finding, a DB-level breach exposing this table would very plausibly meet that "real risk of
significant harm" bar for the affected emergency contacts (third parties who never consented to
being in the system at all) — worth Privacy/Legal explicitly confirming this table is in scope for
the existing breach runbook (`docs/runbooks/data-breach.md`), not assuming it already is.

---

## Section 8: Recommendations

**[HIGH] — Decide the encryption approach for `name`/`phone`, using the proven migration-32 pattern as the default**

Spinr already runs a proven encryption pattern in production for a comparable PII class:
`drivers.license_number`/`vehicle_vin` via pgsodium + Supabase Vault
(`backend/migrations/32_encrypt_sensitive_fields.sql`) — `encrypt_driver_pii()`/`decrypt_driver_pii()`
RPCs, `service_role`-only execution, `REVOKE SELECT` on the raw columns from `anon`/`authenticated`.
There is **no** `pgcrypto` extension enabled anywhere in this repository despite the phrase
appearing in earlier framing of this decision — the actual reusable precedent is pgsodium/Vault, not
raw pgcrypto.

- **Action:** Privacy/Legal decides whether the data content justifies encryption (this PIA's
  position: yes, given R-001's risk rating and the direct precedent already accepted for
  comparably-sensitive driver PII).
- **If yes:** Eng mirrors migration 32's pattern — new migration adding ciphertext columns via
  pgsodium/Vault, a batched backfill of existing rows (per `backend/migrations/CLAUDE.md`'s
  no-long-blocking-`ALTER` rule), and updates to the ~3 call sites (`routes/users.py` GET/POST/DELETE,
  `routes/rides/safety.py:315`'s SOS read). SOS is safety-critical, so any added RPC latency/failure
  mode on the decrypt-before-SMS path needs the same "never silently drop an SOS" discipline as the
  rest of `domain-safety.md`.
- **Timeline:** Sized M (1-2 migrations + 3 call sites + tests) once decided — not a research
  problem, a scoping one.
- **Success criterion:** `name`/`phone` no longer readable via a raw `SELECT` on `emergency_contacts`
  with only DB/backup access; SOS flow's decrypt-before-SMS path has explicit failure-mode tests.

**[MEDIUM] — Decide whether to build a third-party consent/opt-out handshake before or alongside encryption**

Encrypting storage closes R-001 but leaves R-002 (the emergency contact never consented) fully open.
This is explicitly flagged as a *separate* decision, not a checkbox on the encryption work — it is
tracked in the safety-toolkit gap analysis (finding F13) as its own scoped effort.

- **Action:** Privacy/Legal and Product jointly decide whether PIPEDA compliance requires this before
  shipping any change to this table, or whether it can be sequenced as a fast-follow.
- **Timeline:** Not scoped by this PIA — genuinely a design question (SMS opt-in flow, STOP-keyword
  handling, what happens to an unconfirmed contact during an active SOS) that needs its own decision
  cycle.
- **Success criterion:** A documented decision, either way — "built before encryption ships" or
  "accepted as a fast-follow with a named owner and due date" are both acceptable outcomes of this
  recommendation; "undecided" is not.

---

## Section 9: Sign-off

```
Reviewed by:     @vikas (session decision-maker, standing in for Privacy/Legal)
                                                    Date: 2026-08-21
Approved by:     @vikas                            Date: 2026-08-21
Decision:        [x] Encrypt (mirror migration 32)   [ ] Accept plaintext with compensating controls
                  [x] Build consent/opt-out handshake now (alongside encryption, not sequenced apart)
Next action:     Implement both together: (1) pgsodium/Vault encryption for
                 emergency_contacts.name/phone mirroring migration 32's pattern,
                 (2) a third-party consent/opt-out (SMS opt-in + STOP-keyword)
                 handshake for the SOS disclosure flow. Scoped via /plan given
                 the combined size and the safety-critical surface touched
                 (backend/routes/rides/safety.py). Tracked as its own
                 implementation effort from this decision.
Implementation:  COMPLETE 2026-08-21. All 6 subtasks of the /plan
                 decomposition landed on claude/spinr-app-all-surfaces-de596c:
                 (1) migration 357 — encrypt_emergency_contact_pii()/
                 decrypt_emergency_contact_pii() RPCs (mirrors migration 138's
                 corrected end state, not 32's original draft — reviewed
                 twice by spinr-migration-reviewer); (2) routes/users.py wired
                 to those RPCs (fail-closed write, fail-open read); (3)
                 migration 358 + services/sos_contact_consent.py — SOS-contact
                 suppression storage, fail-open by design (reviewed by
                 spinr-migration-reviewer, PASS); (4) routes/rides/safety.py's
                 two SOS SMS paths gated on suppression, fail-open end-to-end
                 (reviewed by spinr-safety-sos-reviewer, SAFE TO MERGE, no
                 blockers); (5) routes/webhooks.py's inbound Twilio STOP/START
                 extended to update SOS-contact suppression unconditionally
                 (not gated on Spinr user_id resolution); (6) a one-time
                 opt-out notice SMS (utils/sos_contact_notice.py) sent
                 best-effort on contact add, closing R-002. Full detail and
                 verification record in docs/change-log/2026-08-21-emergency-
                 contact-encryption-consent.md, -sos-contact-suppression-
                 migration.md, -sos-suppression-filtering-wired.md, and
                 -emergency-contact-encryption-consent-app-wiring.md. Two
                 non-blocking follow-up notes surfaced during review (added
                 latency on the suppression-check step within its existing
                 fail-open bound; sos_contact_consent.normalize_phone() sits
                 outside is_suppressed()'s own try/except, currently covered
                 by an outer caller-side guard) — tracked for a future pass,
                 not blocking, per the reviewing agents' verdicts.
```

---

## Appendix: What was NOT verified in producing this memo

- No live Supabase/Postgres access in this sandbox — all RLS-policy and schema claims are read from
  migration SQL source, not exercised against a live database.
- Spinr's actual `privacy-policy.md` and `subprocessor-list.md` were not re-read in full to confirm
  whether third-party (emergency-contact) data collection is already explicitly documented — flagged
  above as a follow-up check, not assumed either way.
- `domain-safety.md`'s historical "cap of 5" reference was not investigated beyond noting the
  discrepancy against the current code's cap of 3 — not this PIA's scope to resolve, but worth a
  small doc-accuracy fix separately.
- No dollar/row-count figures for how many emergency contacts currently exist in production — this
  sandbox has no live-database access, so no scale estimate is offered.
- This memo is a recommendation input for the named decision-makers, not itself a decision. Nothing
  in it should be read as already-approved.
