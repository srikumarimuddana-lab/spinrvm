# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude (session), on behalf of @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (this PR) |
| Related issue or gap ID | Ranked blocker #13; PIA `docs/audit/2026-08-21-emergency-contact-pia-memo.md`; decision-writeups.md items #8 and (merge-queue) #5 |

This single PR closes out three decisions approved by @vikas on 2026-08-21:
1. **Encrypt `emergency_contacts.name`/`.phone`** (this log covers the code/migration for this one — the only behavior-changing item).
2. **Fare-estimate 3.5s SLA**: accepted as a permanent documented exception — doc-only, no code change (see `docs/audit/2026-08-19-decision-writeups.md` item #8's resolution note).
3. **Enable GitHub merge queue**: CI-side prep (`merge_group:` triggers) was already shipped by a prior session (commit `0e8fbee`); this PR only updates the decision-log doc to mark the decision itself made — the repo-settings toggle remains a manual repo-admin action (no GitHub MCP tool in this environment can flip it).

## 1. Issue / gap identified

`emergency_contacts.name`/`.phone` were stored as plaintext at rest. This is a third party's (the rider's emergency contact's) personal information — someone who never consented to being in Spinr's system — readable by anyone with DB/backup/service_role access.

## 2. Root cause

The table was never brought into the encrypted-PII pattern Spinr already uses for comparable driver PII (`drivers.license_number`/`vehicle_vin`, migration 32). No prior decision had been made on whether the sensitivity of this data justified the same treatment.

## 3. Fix / remediation

Migration 357 mirrors migration 32's proven pgsodium/Supabase-Vault pattern exactly, with its own dedicated key (`emergency_contacts_pii_key`) for blast-radius isolation, plus `SET search_path = public, pg_catalog` pinning on both new `SECURITY DEFINER` functions (a hardening migration 32 itself lacks — flagged by `spinr-migration-reviewer`, not retrofitted onto 32 since it's append-only).

`backend/utils/vault_pii.py` (new) generalizes `backend/routes/drivers/_shared.py`'s `_vault_encrypt`/`_vault_decrypt` pattern (same fail-closed encrypt / fail-open decrypt contract) to a second table without duplicating the driver-specific version.

Three call sites updated:
- `backend/routes/users.py`: `POST /emergency-contacts` encrypts name+phone before insert (fails closed, 503, on any Vault failure — never falls through to writing plaintext); `GET /emergency-contacts` decrypts each contact before returning.
- `backend/routes/rides/safety.py`: both SOS entry points (`trigger_emergency` and `trigger_emergency_rideless`) now decrypt contact name/phone via a shared `_decrypt_emergency_contacts()` helper before using them for SMS/response. Decrypt is fail-open (never raises) so a Vault hiccup can never silently drop an SOS alert.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to `emergency_contacts` and its 3 known application-level readers** (grepped full backend: `routes/users.py` GET/POST/DELETE, `routes/rides/safety.py`'s two SOS triggers, and the account-deletion bulk-delete at `routes/users.py:542`, which only deletes by `user_id` and never reads name/phone). No admin endpoint reads this table. No other table/background loop touches it.

**Caught during review, fixed before merge**: the first pass only wired decryption into `trigger_emergency` (the in-ride SOS path) and missed its sibling `trigger_emergency_rideless` (the rideless SOS path, `ACTION_ITEMS.md` B15(c)). A `spinr-security-auditor` pass caught this — left as-is, every post-migration SMS on that path would have sent a vault UUID to Twilio instead of a phone number, with `contacts_notified` silently staying 0 (no exception raised, since nothing in that path fails loudly). Fixed by extracting both call sites into one shared `_decrypt_emergency_contacts()` helper in `safety.py`, and added a regression test (`test_sos_rideless.py::test_emergency_contacts_decrypted_before_sms`) that would have caught the original gap.

**No backfill of existing plaintext rows** — matches migration 32's own precedent. `decrypt_emergency_contact_pii()` falls back to returning the stored value unchanged when it isn't a UUID, so pre-migration rows keep working read-side. There's no `UPDATE` endpoint for a contact (delete + re-add only), so an existing contact only becomes encrypted once the rider deletes and re-adds it. This is an accepted residual risk, stated explicitly in the migration's own header comment, not a silent gap.

## 5. User-experience effect

None. Purely a storage/serialization change — the rider's own emergency-contacts screen and the SOS flow behave identically from the rider's/driver's perspective. Backend-only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/357_encrypt_emergency_contacts.sql` | New: pgsodium/Vault key + encrypt/decrypt RPC functions + column-level `REVOKE SELECT` for `emergency_contacts.name`/`.phone` | Mirrors migration 32's proven pattern for a new table |
| `backend/utils/vault_pii.py` | New: generic `vault_encrypt`/`vault_decrypt`, RPC-name-parameterized | Reused by both `routes/users.py` and `routes/rides/safety.py` without duplicating driver-specific code |
| `backend/routes/users.py` | `POST /emergency-contacts` encrypts before insert; `GET /emergency-contacts` decrypts before returning | Close the plaintext-at-rest gap on write/read |
| `backend/routes/rides/_deps.py` | Export `vault_decrypt` for `safety.py` to import (dual-import pattern) | Wiring |
| `backend/routes/rides/safety.py` | New shared `_decrypt_emergency_contacts()` helper; both `trigger_emergency` and `trigger_emergency_rideless` now decrypt before SMS/response | Close the plaintext gap on the SOS read path for both SOS entry points |
| `backend/tests/test_routes_users_coverage.py` | New `TestVaultEmergencyContactEncryption` class; `_patches()` defaults extended with passthrough vault mocks | Regression coverage for encrypt-on-write/decrypt-on-read |
| `backend/tests/test_p2_sos.py` | New `test_emergency_contacts_decrypted_before_sms` | Regression coverage for the in-ride SOS decrypt path |
| `backend/tests/test_sos_rideless.py` | `_trigger()` now captures `sms_calls`; new `test_emergency_contacts_decrypted_before_sms` | Regression coverage for the rideless SOS decrypt path (the one the security review caught missing) |
| `docs/audit/2026-08-21-emergency-contact-pia-memo.md` | Sign-off section filled in, status updated to Decided | Durable record of the approval |
| `docs/audit/2026-08-19-decision-writeups.md` | Item #8 (fare-estimate SLA) resolution added | Decision closed, doc-only |
| `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` | 4 rows updated to reflect all 3 decisions closed 2026-08-21 | Keep the decision log accurate |

## 7. Before / after

```python
# Before (backend/routes/users.py, POST /emergency-contacts)
contact_doc = {
    "id": str(uuid.uuid4()),
    "user_id": current_user["id"],
    "name": contact.name.strip(),
    "phone": phone,
    "relationship": contact.relationship,
}
await db_supabase.insert_one("emergency_contacts", contact_doc)
return {"success": True, "contact": contact_doc}
```

```python
# After
contact_doc = {
    "id": str(uuid.uuid4()),
    "user_id": current_user["id"],
    "name": await vault_encrypt("encrypt_emergency_contact_pii", name, "emergency_contact.name"),
    "phone": await vault_encrypt("encrypt_emergency_contact_pii", phone, "emergency_contact.phone"),
    "relationship": contact.relationship,
}
await db_supabase.insert_one("emergency_contacts", contact_doc)
return {"success": True, "contact": {**contact_doc, "name": name, "phone": phone}}
```

## 8. Rollback plan

`DROP FUNCTION encrypt_emergency_contact_pii(text), decrypt_emergency_contact_pii(text);` — the columns already hold plain `TEXT` (vault UUIDs or pre-migration plaintext), no column type change to revert. On the app side, `git revert` the code changes; any post-migration rows written as vault UUIDs would become unreadable via `GET /emergency-contacts` until either the DB function rollback is also reverted or a forward-fix re-adds them — no data loss (ciphertext remains intact in `vault.secrets` either way), but a genuine rollback-window UX gap for any rider who added a contact between deploy and rollback. Given this is a safety-adjacent but non-critical-path table (contacts still resolve for SOS via the same decrypt fallback either way, since `vault_decrypt` degrades to the raw value rather than raising), this is an acceptable rollback profile for a backend-only, non-money change.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_routes_users_coverage.py backend/tests/test_p2_sos.py backend/tests/test_sos_rideless.py backend/tests/test_sos_paging.py` — 115 passed.
- [ ] Manual repro steps followed in staging — not performed; no staging/live Supabase access in this environment.
- [x] Blast-radius grep performed: `grep -rn "emergency_contacts" backend/` (all `.py` call sites enumerated in Section 4).
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA (plaintext PII), migration conventions (`backend/migrations/CLAUDE.md` — reviewed by `spinr-migration-reviewer`, verdict SAFE TO APPLY), "do not silently swallow errors" (encrypt fails loud/closed; decrypt fails open by design for the safety-critical SOS path, per `spinr-security-auditor`'s explicit confirmation this tradeoff is correct).
- [ ] Feature-flagged — not applicable; this is a storage-layer change with zero observable behavior difference to any user, so flagging would add complexity with no rollout-safety benefit.
- [x] `ruff check` clean on all touched files.
- [x] `python3 -m py_compile` clean on all touched files.
- [x] Manual review: `spinr-migration-reviewer` (verdict: SAFE TO APPLY, one non-blocking warning re: `search_path` pinning — fixed before this commit) and `spinr-security-auditor` (verdict: FIX BLOCKERS → one blocker found and fixed — the missing `trigger_emergency_rideless` decrypt — then re-verified with new tests).

## 10. What was NOT verified

- No real Supabase/Postgres access in this environment — the migration was reviewed for correctness (idempotent key creation, `SECURITY DEFINER`, `REVOKE`/`GRANT` shape) but never actually applied against a live or staging database. The pgsodium/Vault RPC round-trip is exercised only via mocks in unit tests.
- No production build of any frontend surface — this is a backend-only change with no `rider-app`/`driver-app`/`admin-dashboard` files touched.
- R-002 from the PIA (the emergency contact's own lack of consent to being in Spinr's system or receiving an SOS SMS) is explicitly **not** addressed by this change — tracked separately as safety-toolkit finding F13, per the PIA's own recommendation to decide it separately from encryption.
- `spinr-security-auditor`'s WARNING about `contacts_status`'s plaintext `name` still appearing in the SOS response body (pre-existing behavior, unchanged by this diff) was not addressed — flagged as a fold-in candidate for the R-002 follow-up, not a regression introduced here.
- No live-traffic/backfill migration was run; existing plaintext rows remain plaintext until a rider deletes and re-adds the contact (documented, accepted residual risk matching migration 32's own precedent).

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow (Section 5: zero UX effect)
