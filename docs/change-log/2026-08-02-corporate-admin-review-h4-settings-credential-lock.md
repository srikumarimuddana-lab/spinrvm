# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin, payments, auth |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — High #4 |

## 1. Issue / gap identified

Reading (revealing) `stripe_secret_key`, `stripe_webhook_secret`,
`stripe_connect_webhook_secret`, `twilio_auth_token`,
`aws_ses_secret_access_key`, and `resend_api_key` already required
`super_admin`, but *writing* them only required the `settings` module — any
admin with that module grant could silently repoint live payment/SMS/email
credentials to attacker-controlled accounts, with no way to even read back
the current value and notice it changed.

## 2. Root cause

`_SUPER_ADMIN_ONLY_FIELDS` already existed and already covers this exact
risk shape for other destination-credential fields (LMS API key/URL, Meta
Conversions API token/dataset ids, SOS paging webhook/routing key) — each
with a comment explaining why write access needs the same privilege as
reveal. The six payment/messaging credential fields were simply never added
to that set when it was created, even though they're the same risk shape
(and arguably higher-stakes, since they gate real payment capture and OTP
delivery).

## 3. Fix / remediation

- Added all six fields to `_SUPER_ADMIN_ONLY_FIELDS`, with a comment
  explaining the reasoning in the same style as the existing entries.
- Added a `field_validator` on `stripe_secret_key` requiring the correct
  live/test prefix for the current environment (`sk_live_` in production,
  `sk_test_` otherwise, via `core.config.settings.ENV`) — rejects both an
  accidental copy-paste of the wrong key type and a downgrade attack. Empty
  values (field left unset) are allowed through unchanged. A masked preview
  round-tripped from `GET /settings` (`v[:8] + "*****"`) already starts with
  the real key's own prefix, so it passes this validator unchanged — the
  existing mask-roundtrip guard in `admin_update_settings` (unchanged by
  this fix) is what actually drops the preview from the persisted payload.
- Did not add format validators for the other five fields (Stripe webhook
  secrets, Twilio token, AWS SES secret, Resend key) — none of them have a
  well-known, stable prefix convention to validate against the way Stripe
  secret keys do, so a format check there would either be too loose to
  catch anything or too strict and reject legitimate values; scoped
  narrowly to the one field where format validation is actually meaningful.

## 4. Risk & impact on existing functionality

- **Blast radius: `_SUPER_ADMIN_ONLY_FIELDS`, one new field validator, one
  new import.** No change to `_CREDENTIAL_FIELDS` (masking behavior
  unchanged), no change to the reveal endpoint, no change to the mask-
  roundtrip guard.
- Grepped every test file touching `PUT /settings` /
  `SettingsUpdateRequest` (`test_admin_settings_lms_gate.py`,
  `test_ai_admin_settings.py`) for any of the six newly-gated fields —
  found none, so no existing test needed modification.
- Ran a broader `-k "settings"` sweep across the full backend test suite
  (112 tests matched) to catch anything outside the two files above that
  might construct a `SettingsUpdateRequest` with one of these fields or
  read `_SUPER_ADMIN_ONLY_FIELDS` — all passed unmodified.
- The new `stripe_secret_key` validator only rejects a NON-empty value with
  the wrong prefix — every test fixture elsewhere in the codebase that
  reads settings via `get_app_settings()` mocks (not through
  `SettingsUpdateRequest`) is unaffected, since those don't go through this
  Pydantic model at all.

## 5. User-experience effect

**Internal admin-facing only.** An admin whose role includes `settings` but
not the full `super_admin` role can no longer change any of these six
fields (they still see and can edit every other setting normally); they'll
get a 403 with a message naming the specific field, same as the existing
LMS/Meta/SOS-paging fields. A `super_admin` sees no change. Separately, any
admin attempting to save a Stripe secret key with the wrong live/test
prefix for the current environment now gets a clear validation error at
save time instead of the key being silently accepted and payment capture
breaking (or worse, quietly running against the wrong Stripe environment)
the next time it's used.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/settings.py` | Added 6 fields to `_SUPER_ADMIN_ONLY_FIELDS`; added `stripe_secret_key` format validator; added `core.config.settings` import | Close the write-access gap for payment/messaging credentials; catch a wrong-environment key at save time |
| `backend/tests/test_admin_settings_payment_credential_gate.py` (new) | Tests mirroring `test_admin_settings_lms_gate.py`'s structure for all 6 fields, plus dedicated `stripe_secret_key` format-validator tests | Cover the new gate and validator with the same rigor as the existing LMS/SOS-paging gate |

## 7. Before / after

```python
# Before
_SUPER_ADMIN_ONLY_FIELDS = frozenset({
    "lms_api_base_url", "lms_api_key",
    "meta_rider_dataset_id", "meta_driver_dataset_id", "meta_capi_access_token",
    "sos_paging_webhook_url", "sos_paging_routing_key",
})
# stripe_secret_key, twilio_auth_token, etc. — writable by any "settings"-module admin
```

```python
# After
_SUPER_ADMIN_ONLY_FIELDS = frozenset({
    "lms_api_base_url", "lms_api_key",
    "meta_rider_dataset_id", "meta_driver_dataset_id", "meta_capi_access_token",
    "sos_paging_webhook_url", "sos_paging_routing_key",
    "stripe_secret_key", "stripe_webhook_secret", "stripe_connect_webhook_secret",
    "twilio_auth_token", "aws_ses_secret_access_key", "resend_api_key",
})

@field_validator("stripe_secret_key")
@classmethod
def _stripe_secret_key_matches_environment(cls, v):
    if not v:
        return v
    if _core_settings.ENV.lower() == "production":
        if not v.startswith("sk_live_"):
            raise ValueError("stripe_secret_key must start with sk_live_ in production")
    else:
        if not v.startswith("sk_test_"):
            raise ValueError("stripe_secret_key must start with sk_test_ outside production")
    return v
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores the prior (settings-module-writable) behavior. No feature flag —
this closes an authorization gap; there is no meaningful dark-ship version
of "require the same privilege to change a payment credential as to read
it." If the `stripe_secret_key` format validator ever proves too strict for
a legitimate operational need (e.g. a non-standard key format from a Stripe
Connect sub-account), it can be relaxed or removed independently of the
`_SUPER_ADMIN_ONLY_FIELDS` change, since the two are separate, independently
revertible pieces of this commit.

## 9. Verification performed

- [x] Automated tests: `test_admin_settings_payment_credential_gate.py` (11
      new tests), `test_admin_settings_lms_gate.py` (19 tests, unaffected),
      `test_ai_admin_settings.py` (13 tests, unaffected) — 46 passed. A
      broader `-k "settings"` sweep across the full suite (112 tests) also
      passed. All via the session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on both touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4): every test file constructing a
      `SettingsUpdateRequest`, every reference to `_SUPER_ADMIN_ONLY_FIELDS`.
- [x] Dry-run scenario: a `finance`-role admin (has the `earnings`,
      `corporate_accounts`, `pricing` modules per `ROLE_PRESETS`, but not
      `settings` specifically, though a custom role could grant `settings`
      without `super_admin`) attempts to change `stripe_secret_key` via
      `PUT /admin/settings`. Before this fix: succeeds, silently repointing
      live payment capture. After this fix: 403, "Only super admins can
      change stripe_secret_key." Separately: a super_admin in the test
      environment (`ENV=test`) attempts to save
      a live-prefixed key pasted in by mistake. Before this fix:
      accepted as-is. After this fix: rejected with a clear validation
      error before it's ever persisted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — one frozenset addition, one new
      validator, every dependent test file grepped and a broad sweep run
- [x] No silent behavior change to a working flow — a `super_admin`'s
      ability to manage these settings is completely unchanged; only a
      non-super-admin's write access is restricted, matching the existing
      gate already applied to structurally identical fields

## What was NOT verified

Not tested against a live/staging Supabase or a real Stripe secret key —
only Pydantic-level validation and mocked DB writes. Did not add format
validation for the other five newly-gated credential fields (see §3 for
why); if a similarly well-known format convention exists for any of them
(e.g. Twilio auth tokens have a fixed length/charset), that's a reasonable
low-effort follow-up but was not implemented here to keep this fix scoped
to what the original finding specifically named. Did not build a UI
indicator in the admin-dashboard settings page showing *which* fields
require super_admin to change (the backend now enforces it correctly
regardless, but a non-super-admin currently only discovers the restriction
by attempting the save and getting a 403) — a proactive UI hint is a
reasonable UX follow-up, not implemented here.
