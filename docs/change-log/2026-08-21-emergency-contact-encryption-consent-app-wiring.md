# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (subtask agents), approved by @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | commits `06596ca`, `d980bee`, `1b65bcd` on `claude/spinr-app-all-surfaces-de596c` |
| Related issue or gap ID | Ranked blocker #13 / decision-log item #13; PIA `docs/audit/2026-08-21-emergency-contact-pia-memo.md` risks R-001 and R-002 |

This entry covers the three app-code subtasks that complete the
encryption + consent-handshake feature approved 2026-08-21 (PIA memo
Section 9). It complements three earlier entries in this directory that
already cover the DB-side pieces individually:
- `2026-08-21-emergency-contact-encryption-consent.md` — migration 357 (encrypt/decrypt RPCs), a no-op alone
- `2026-08-21-sos-contact-suppression-migration.md` — migration 358 + `services/sos_contact_consent.py`, inert alone (no caller yet)
- `2026-08-21-sos-suppression-filtering-wired.md` — wiring the suppression check into the SOS send path

This entry is the missing piece: the two remaining app-code changes that
make migrations 357/358 and the suppression service actually take effect,
closing the loop the earlier three entries left open.

## 1. Issue / gap identified

Two gaps remained after the DB-side work above landed:
1. `backend/routes/users.py`'s emergency-contacts GET/POST/DELETE handlers
   still read/wrote `emergency_contacts.name`/`.phone` as plaintext —
   migration 357's encrypt/decrypt RPCs existed but nothing called them
   (PIA risk R-001, safeguards).
2. A newly-added emergency contact was never told they'd been added, and
   had no way to discover the STOP opt-out migration 358 and the SOS-send
   gating (already wired) depend on (PIA risk R-002, consent).

## 2. Root cause

Both are intentional sequencing, not oversights: this feature was
decomposed into ≤3-file subtasks per CLAUDE.md's task-decomposition rule
and built incrementally — DB schema first (357, 358), then the SOS-read
gating (already-fail-open-safe on its own), then these two app-write-path
changes last, since they're the pieces that actually start writing
encrypted data and sending a new SMS to a real third party.

## 3. Fix / remediation

- **`backend/routes/users.py`** (`add_emergency_contact`, `get_emergency_contacts`): calls `encrypt_emergency_contact_pii`/`decrypt_emergency_contact_pii` (migration 357) on every write/read. Write is fail-closed (503 rather than ever storing plaintext); read is fail-open (returns the raw stored value — either an encrypted token or a pre-migration plaintext row — rather than failing the whole contact list on a Vault hiccup). The POST response still returns the plaintext the rider just typed, not the encrypted token written to the DB.
- **`backend/utils/sos_contact_notice.py`** (new) + `add_emergency_contact`: on successful contact creation, backgrounds (via the existing `spawn()` helper, never blocking the HTTP response) a one-time SMS to the new contact: *"{first name} added you as their emergency contact on Spinr. If they trigger an SOS alert, you may receive a safety text. Reply STOP to opt out."* Skips sending if the phone is already suppressed. Never raises — any failure is logged and swallowed. On success, best-effort stamps `emergency_contacts.consent_notice_sent_at` (migration 358's column).
- **`backend/routes/webhooks.py`** (`_handle_sms_keyword`): the existing Twilio inbound STOP/START handler now also calls `sos_contact_consent.suppress`/`unsuppress` unconditionally (not gated on resolving a Spinr `user_id`, unlike the pre-existing marketing-consent call beside it) — a pure third-party emergency contact with no Spinr account can still opt out.

## 4. Risk & impact on existing functionality

- **Blast radius grep performed** (each subtask agent independently, cross-checked here): the only other reader of `emergency_contacts.name`/`.phone` is `backend/routes/rides/safety.py`'s SOS SMS flow (`trigger_emergency`/`trigger_emergency_rideless`), which reads the columns as opaque strings and passes them straight to `send_sms` — it never compares or decrypts them, so it is unaffected by the column now holding a vault-secret UUID for newly-written rows (this is the same reasoning migration 357's own Change Impact Log entry already gave; re-confirmed here since this is the commit that actually starts writing encrypted values).
- **Dual-read protects existing rows**: any `emergency_contacts` row written before this change stays plaintext and decrypts correctly forever (migration 357's `decrypt_emergency_contact_pii` falls back to returning its input verbatim when it isn't a valid UUID). Only contacts added or re-added after this deploy get encrypted.
- **`_handle_sms_keyword`'s existing marketing-consent behavior is untouched** — the new SOS-suppression calls are additive, wrapped in their own try/except so a failure there can't affect the pre-existing CASL STOP/START handling.
- **`add_emergency_contact`'s response contract is unchanged** — still `{"success": true, "contact": {...plaintext...}}`; the encryption and notice-SMS are invisible to the caller.
- No interaction with the ride state machine, wallet/money paths, or the 18 background loops.

## 5. User-experience effect

- **Rider**: no visible change to the emergency-contacts screen — same request/response shape, same latency characteristics (the encrypt call is on the request path but is a single fast RPC; the notice SMS and consent-column stamp are fully backgrounded).
- **The emergency contact (third party, not a Spinr user)**: NEW — they now receive a one-time SMS when added, with a STOP opt-out. This is the actual user-facing change this entire feature exists to deliver (PIA risk R-002 remediation). Not visible mid-session to anyone already using the app; it fires once, asynchronously, after the rider's add-contact action completes.
- **Internal**: none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/users.py` | Wired encrypt/decrypt RPCs into GET/POST; backgrounds the opt-out notice SMS on POST | Close PIA R-001 and R-002 |
| `backend/utils/sos_contact_notice.py` | New — best-effort opt-out notice SMS helper | PIA R-002 |
| `backend/routes/webhooks.py` | `_handle_sms_keyword` also updates SOS-contact suppression, unconditionally | Let a non-Spinr-user third party opt out via STOP |
| `backend/tests/test_routes_users_coverage.py`, `test_sos_contact_notice.py`, `test_webhooks_helpers_coverage.py` | New/extended test coverage for all of the above | Required per CLAUDE.md testing conventions |

## 7. Before / after

**Before** (`add_emergency_contact`, abridged):
```python
contact_doc = {"id": ..., "name": name, "phone": phone, ...}
await db_supabase.insert_one("emergency_contacts", contact_doc)
return {"success": True, "contact": contact_doc}
```

**After**:
```python
contact_doc = {"id": ..., "name": name, "phone": phone, ...}
encrypted_doc = dict(contact_doc)
encrypted_doc["name"] = await _encrypt_emergency_contact_pii(name)
encrypted_doc["phone"] = await _encrypt_emergency_contact_pii(phone)
await db_supabase.insert_one("emergency_contacts", encrypted_doc)
spawn(_notify_and_record_sos_contact_consent(contact_doc["id"], phone, current_user.get("first_name") or ""))
return {"success": True, "contact": contact_doc}  # plaintext, unchanged
```

## 8. Rollback plan

Each piece is independently revertible without a second deploy:
- **App-code**: `git revert` any of the three commits above. Reverting the `users.py` change reverts to plaintext writes going forward (existing encrypted rows from the window this was live keep decrypting correctly via the fail-open read path — no data loss). Reverting the notice-SMS change simply stops sending it; nothing to clean up (no state was created besides the optional `consent_notice_sent_at` stamp, which is harmless to leave behind).
- **Migrations 357/358**: rollback SQL already documented in each migration's own top comment (`DROP FUNCTION`/`DROP TABLE`/`DROP COLUMN`) — see those files directly.
- No Stripe charges, wallet deltas, or ride-state changes are involved anywhere in this feature — a plain code revert is a complete rollback for the app-code layer.

## 9. Verification performed

- [x] Automated tests run (unit, mocked Supabase/Vault/Twilio per this repo's testing tier conventions): 84 passed (`test_routes_users_coverage.py`, `test_sos_contact_notice.py`, `test_sos_contact_consent.py`) for the users.py + notice pieces; 47 and 145 passed respectively for the webhooks piece across two independent runs as it was integrated. `ruff check` clean on every changed/new file.
- [x] Blast-radius grep performed (see Section 4) — only other `emergency_contacts` PII reader (`routes/rides/safety.py`) confirmed unaffected.
- [x] Reviewed against CLAUDE.md conventions: dual-import pattern used throughout (all three files); fail-closed on PII write, fail-open on PII read and on SOS-suppression lookups (consistent with the existing driver-PII and SOS-gating precedents); PIPEDA log discipline followed (no phone numbers or raw exception text in any new log line — type/status codes only).
- [ ] Manual repro / staging check — **not performed**. No live Supabase/Twilio credentials were available in this session; all three pieces were verified against mocked Vault RPC responses and mocked `send_sms` calls only.
- [ ] Feature-flagged — **not flagged**. Reasoned as backend-only, additive, and reversible by plain code revert (see Section 8); the change is invisible to the rider and the only new externally-visible effect (the opt-out SMS) is exactly the deliverable the user approved. No `app_settings` flag was added — if a staged rollout is wanted before this reaches production traffic, that would need a follow-up.

## 10. What was NOT verified

- Not run against live Supabase Vault or a real Twilio account — every RPC/SMS call in every test is mocked, per this repo's unit-test tier. The actual `vault.create_secret()`/`decrypt_emergency_contact_pii()` round-trip and the actual Twilio delivery of the opt-out SMS have not been exercised end-to-end.
- No automated visual/regression tooling applies (backend-only, no UI surface).
- The `consent_notice_sent_at` stamp's use (e.g. never re-notifying the same contact) is not yet enforced anywhere — the column is written but nothing currently reads it to skip a duplicate notice on contact re-add. Not a correctness bug (a duplicate one-time notice is not harmful — it's the same message, and the contact can already reply STOP after the first one), but worth noting as a possible follow-up rather than silently implying it's already deduplicated.
- No load/latency testing was performed on the now-added encrypt/decrypt RPC round-trips on the emergency-contacts read/write path; Performance SLA impact was reasoned about (single fast RPC, same pattern already live for driver PII) rather than measured.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (Section 8)
- [x] Blast radius is stated, not assumed (Section 4)
- [x] No silent behavior change to an already-shipped flow — the only externally-visible new behavior (the opt-out SMS) is the feature's explicit deliverable, called out in Section 5

This completes all 4 subtasks of the encryption + consent-handshake
implementation approved in `docs/audit/2026-08-21-emergency-contact-pia-memo.md`
Section 9. See that memo's own Section 9 update (below) recording
completion.
