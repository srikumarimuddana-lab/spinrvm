# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude Code session (branch `claude/stripe-webhook-signature-cd3qxj`) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | TBD — commit on `claude/stripe-webhook-signature-cd3qxj` |
| Related issue or gap ID | Observed directly in production backend logs (repeated `Stripe webhook signature verification failed: No signatures found matching the expected signature for payload`, every delivery, multiple source IPs, back-to-back — no prior ACTION_ITEMS/tracked ID) |

## 1. Issue / gap identified

Every incoming Stripe webhook delivery to `/api/v1/webhooks/stripe` was failing signature
verification (`400 Bad Request`, `stripe.error.SignatureVerificationError: No signatures found
matching the expected signature for payload`). Per `docs/runbooks/stripe-webhook-failure.md`,
this means `payment_status` never flips `pending` → `paid` and riders never receive the
"Payment Confirmed" push — a P1 per that runbook.

## 2. Root cause

`stripe_webhook()` (`backend/routes/webhooks.py`) reads `stripe_webhook_secret` /
`stripe_connect_webhook_secret` / `stripe_secret_key` from the `app_settings` table
(`get_app_settings()`) and passes the value **verbatim** to `stripe.Webhook.construct_event()`
as the HMAC signing key. These values are entered by an admin pasting from the Stripe
Dashboard's "Reveal" field, a password manager, or a terminal — all of which routinely leave a
trailing (occasionally leading) newline/space in the clipboard. Neither the read path nor the
admin-settings write path (`backend/routes/admin/settings.py`) ever trimmed the value, so a
single stray whitespace character is persisted and used as-is. Because the computed HMAC then
diverges from Stripe's for every request, the failure is deterministic and 100% — which matches
the observed pattern (every delivery, every source IP, no successes) far better than a
transient/partial cause would.

**Not confirmed**: this session had no DB or admin-dashboard access, so the actual byte content
of the currently-stored `stripe_webhook_secret` was not inspected. A genuinely wrong or
rotated/mismatched secret would produce an identical error string and is not ruled out. See
§10.

## 3. Fix / remediation

- `backend/routes/webhooks.py`: `.strip()` the three Stripe secrets read from `app_settings`
  before using them to verify a delivery or build the Stripe client; a whitespace-only value is
  now treated the same as unset (existing 500 "not configured" path) instead of being used as an
  empty-string HMAC key.
- `backend/routes/admin/settings.py`: added a Pydantic `mode="before"` field validator on
  `stripe_secret_key` / `stripe_webhook_secret` / `stripe_connect_webhook_secret` that strips
  whitespace before the value is persisted, so a future admin save can't reintroduce the same
  corruption. It runs before the existing `sk_live_`/`sk_test_` environment-prefix check, so a
  merely whitespace-padded (but otherwise correct) key is no longer falsely rejected as "wrong
  environment" either.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to the three Stripe secret fields, at exactly two call sites.
  Grepped for every other reader: `stripe_webhook_secret` and `stripe_connect_webhook_secret`
  are read only in `backend/routes/webhooks.py` (this fix) and masked/revealed/written in
  `backend/routes/admin/settings.py` (this fix). `stripe_secret_key` is also read via
  `get_app_settings()` in `backend/routes/payments.py` and other services to build the Stripe API
  client — those call sites are **unchanged** (out of scope: this fix targets webhook signature
  verification specifically) and still receive the raw stored value. If the same whitespace
  defect exists in the stored `stripe_secret_key` and affects those other call sites, this fix
  does not address it — flagged as a residual gap, not silently expanded into a broader change.
- `get_app_settings()` / `settings_loader.py` itself is untouched — no change to any other
  settings field, caching, or default.
- No ride-state, wallet, or webhook-dispatch logic changed — only the secret value used to
  verify the signature and the value persisted on save.
- Cannot regress a currently-working webhook: Stripe's own signing secrets never contain
  whitespace, so `.strip()` is a no-op on any already-clean value.

## 5. User-experience effect

Backend-only. No rider/driver/corporate-admin-facing change. Internal-admin effect: the Stripe
credential fields under Settings → Payment now silently trim whitespace on save instead of
persisting it verbatim — no visible change to the form itself, and not mid-session-visible to
anyone (admin settings are not read by an in-progress rider/driver session).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | Strip whitespace from `stripe_webhook_secret`, `stripe_connect_webhook_secret`, `stripe_secret_key` on read, before use; whitespace-only now treated as unset | Prevent a whitespace-corrupted stored secret from failing 100% of signature verifications |
| `backend/routes/admin/settings.py` | Added `mode="before"` validator stripping whitespace on the same three fields before persistence | Stop the corruption from being (re)introduced on save; runs before the existing environment-prefix check |
| `backend/tests/test_webhooks_main.py` | Added `TestStripeWebhookSecretWhitespaceNormalization` (platform secret, connect secret, whitespace-only-treated-as-unset) | Regression coverage for the read-side fix |
| `backend/tests/test_admin_settings_payment_credential_gate.py` | Added whitespace-stripping tests + a leading-whitespace/environment-check-ordering test | Regression coverage for the write-side fix |
| `docs/change-log/2026-09-03-stripe-webhook-signature-whitespace-strip.md` | This log | Mandatory for a payments-surface fix per `CLAUDE.md` |

## 7. Before / after

```python
# Before (backend/routes/webhooks.py)
settings = await get_app_settings()
webhook_secret = settings.get("stripe_webhook_secret", "")
connect_webhook_secret = settings.get("stripe_connect_webhook_secret", "")
stripe_secret = settings.get("stripe_secret_key", "")
```

```python
# After
settings = await get_app_settings()
webhook_secret = (settings.get("stripe_webhook_secret") or "").strip()
connect_webhook_secret = (settings.get("stripe_connect_webhook_secret") or "").strip()
stripe_secret = (settings.get("stripe_secret_key") or "").strip()
```

```python
# Before (backend/routes/admin/settings.py) — only this validator existed
@field_validator("stripe_secret_key")
@classmethod
def _stripe_secret_key_matches_environment(cls, v): ...
```

```python
# After — new before-mode validator added ahead of it
@field_validator("stripe_secret_key", "stripe_webhook_secret", "stripe_connect_webhook_secret", mode="before")
@classmethod
def _strip_stripe_credentials(cls, v):
    return v.strip() if isinstance(v, str) else v

@field_validator("stripe_secret_key")
@classmethod
def _stripe_secret_key_matches_environment(cls, v): ...
```

## 8. Rollback plan

Pure code change — no migration, no data mutation, no feature flag. `.strip()` is a no-op on
already-clean input, so there is no scenario where this needs a flag-off path. `git revert` of
this commit is a complete and sufficient rollback (unlike money/ride-state changes, nothing here
touches live data — no Stripe charge, wallet delta, or ride row is written by this fix).

If webhook deliveries are *still* failing after this deploys, the fix has ruled out whitespace
corruption as the cause — proceed to the runbook's Step 3 (re-verify/rotate the secret in the
Stripe Dashboard and re-save it in admin settings), which remains the correct next step for a
genuinely wrong/stale secret.

## 9. Verification performed

- [x] `ruff check` and `ruff format --check` on all 4 changed backend files — clean.
- [x] `python3 -m py_compile` on all 4 changed files — no syntax errors.
- [ ] Automated `pytest` run — **not performed**. This session's sandbox egress policy blocks
      `pypi.org` (confirmed via direct `curl`: `403 Host not in allowlist`), so backend Python
      dependencies (pytest, stripe, fastapi, pydantic, etc.) could not be installed and the new/
      updated tests were not executed. Traced by hand against Pydantic v2's documented
      before/after validator execution order and the existing test patterns already in both
      files, but that is reasoning, not a real run.
- [ ] Manual repro / staging check — not performed (no staging or admin-dashboard access from
      this session).
- [ ] Blast-radius grep — performed (see §4): confirmed the three fields' only other readers/
      writers before concluding scope.
- [x] Reviewed against `CLAUDE.md`'s "Do not silently swallow errors" convention — the
      whitespace-only case still surfaces as the existing loud 500 ("not configured"), not a
      silent fallback.
- Feature flag: not applicable — see §8.

## 10. What was NOT verified

- The actual stored production `stripe_webhook_secret` value was never inspected (no DB/admin
  access from this session). The whitespace-corruption theory best fits the observed
  100%-failure/all-source-IPs pattern, but a genuinely wrong or mismatched secret would look
  identical from the logs alone and is not ruled out.
- No automated test run in this session (sandbox network policy — see §9); the new tests have
  not actually executed, only been reasoned through.
- `stripe_secret_key`'s other read sites (`routes/payments.py`, and any other service that calls
  `get_app_settings()` to build a Stripe API client) were deliberately left unchanged — if the
  same whitespace defect is present there too, this fix does not cover it.
