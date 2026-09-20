# Change Impact & Risk Log — user-facing message rewrite

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-18 |
| Author | Claude Code session (branch `claude/relaxed-planck-o6exdv`) |
| Surface(s) | backend, shared, rider-app (test only) |
| Domain (Sentry tag) | rides, payments, auth, corporate |
| PR / commit link | branch `claude/relaxed-planck-o6exdv` |
| Related issue or gap ID | `docs/audit/2026-09-18-user-facing-message-audit.md` (F1–F7) |

## 1. Issue / gap identified

A sweep of all 2,639 user-reachable strings found that ~126 backend 4xx messages — which the apps
print verbatim — were written for a developer. A driver mid-shift could read `Ride is in status
'driver_arrived'; cannot perform this action from that state (allowed: ['driver_accepted'])`; a rider
could read `payment_already_processing` or `forbidden` as the entire message.

No function name was displayed anywhere; the leaks were field names, ride-state names, internal
constants and bare machine tokens.

## 2. Root cause

Two structural causes, not carelessness at individual call sites:

1. **The 4xx path trusts route text completely.** `utils/error_handling.py::http_exception_handler`
   discards 5xx detail but deliberately keeps 4xx detail readable (PII-redacted only), because 4xx is
   "intended user-facing UX". Nothing ever enforced that the sentence *was* user-facing UX, so a
   `detail=` written as a developer assertion shipped straight to a toast.
2. **The `ERR_*` sentinels had no copy.** `isMachineErrorSentinel` correctly refuses to render them,
   but no map existed, so 17 precise reasons collapsed into the call site's generic fallback.

## 3. Fix / remediation

Wording only, at the raise site, plus one additive client-side map:

- Ride-state guards now speak from the driver's point of view; the generic guard renders through a
  `_RIDE_STATE_PHRASE` map so a raw state value can never be interpolated again.
- Bare tokens, field names, error-code prefixes and developer instructions rewritten as sentences.
- `shared/errors/sentinelMessages.ts` (new) maps the 17 `ERR_*` sentinels to real copy, consulted by
  `getApiErrorMessage` before its existing fallback path.
- The generic 5xx sentence is now "Something went wrong on our end. Please try again in a moment."

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend response text) + one shared client helper. No control-flow
change.**

- **Nothing reads these strings programmatically.** Grep across `backend/tests`, `admin-dashboard/src`,
  `rider-app`, `driver-app` and `shared` for every changed string found 7 assertions, all updated in
  the same commits. No app code branches on any of this text — the apps branch on
  `error.details.code` / `detailCode`, which is untouched.
- **Status codes, guard conditions, validation rules and transitions are unchanged.** The ride state
  machine itself is not touched: `_require_ride_in_state` still matches the same `allowed_states`, the
  atomic `update_one` guards still filter on the same statuses, and the same 409/400 is raised.
- **Insurance-period writes are unaffected** — `record_period_transition` calls are untouched and
  still sit after the same guards.
- **Money paths unaffected** — no `Decimal` arithmetic, no wallet delta, no Stripe call changed. The
  only payments change is response text plus the raw-card guard's message.
- **The 5xx sanitiser contract is intact** — route detail still discarded unless it matches the
  `ERR_*` pattern, full text still logged with the request id, `error.sanitised` still set. Only the
  replacement string changed.
- **`shared/api/client.ts` is imported by both apps**, so the sentinel map is genuinely cross-surface.
  It is additive: a mapped sentinel now renders copy, an unmapped one falls back exactly as before.
  The existing "sentinels never become toast copy" tests cover OTP sentinels, which are deliberately
  left unmapped and keep passing.
- **`backend/validators.py` is the widest change** — its UUID/datetime validators back many routes, so
  that wording surfaces across surfaces including admin. Text only; validation logic untouched.

## 5. User-experience effect

- **Who sees a difference:** riders, drivers, corporate admins and internal admins — anyone who hits a
  4xx or 5xx error.
- **Visible mid-session: yes.** A driver on an active ride who taps an action at the wrong moment gets
  the new wording immediately; a rider mid-booking who hits a 5xx sees the new generic sentence. No
  restart or reinstall needed — these are server-sent strings, except the sentinel map, which ships in
  the next app build.
- **This is a copy change to already-shipped screens**, which is why this log exists. It is strictly
  an improvement in legibility: no message became less specific, and several became more specific
  (the 17 sentinels went from "Something went wrong" to a real reason).
- **Reviewed against the customer-centric tone standard:** every new message is specific,
  non-technical and states what the user can do next. All fit the 140-char `clampToastMessage` budget.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/_shared.py` | Added `_RIDE_STATE_PHRASE`; generic guard no longer interpolates state or allowed-list | F2 — worst offender |
| `backend/routes/drivers/ride_flow.py` | 4 state-guard messages rewritten | F2 |
| `backend/routes/rides/lifecycle.py` | 2 state-guard messages rewritten | F2 |
| `backend/routes/payments.py` | `forbidden`, `payment_already_processing`, JSON/payload wording, raw-card guard | F1, F4 |
| `backend/routes/corporate_subscriptions.py` | `company_not_found`; billing-disabled message now addresses the customer | F1, F4 |
| `backend/routes/corporate_wallet.py` | auto-topup field names removed | F3 |
| `backend/routes/corporate_company.py` | `payment_method_id` removed | F3 |
| `backend/features.py` | `calc_mode`, `scheduled_time` | F3 |
| `backend/routes/promotions.py` | `discount_type` | F3 |
| `backend/routes/drivers/tax_exports.py` | `period_type`, `period_start` | F3 |
| `backend/routes/notifications.py` | `No user_id and admin has no id claim` | F3 |
| `backend/dependencies/__init__.py` | `super_admin` / module-permission wording | F3 |
| `backend/utils/password_policy.py` | dropped `password_*:` code prefixes | F3 |
| `backend/validators.py` | UUID / ISO 8601 jargon | F3 |
| `backend/utils/error_handling.py` | generic 5xx sentence (3 places) | F7 |
| `shared/errors/sentinelMessages.ts` | **new** — 17 sentinels → copy | F6 |
| `shared/api/client.ts` | `getApiErrorMessage` consults the map first | F6 |
| `rider-app/__tests__/getApiErrorMessage.test.ts` | 6 new regression tests | F6 |
| `backend/tests/*` (6 files) | 7 assertions updated to the new strings | keep suite green |
| `backend/routes/auth.py` | comment only (quoted the old 5xx phrase) | doc accuracy |
| `backend/routes/admin/staff.py` | `require_role` factory no longer returns `role_required:{role}` | admin pass |
| `backend/routes/admin/rides.py` | 3 × `role_required:finance` | admin pass |
| `backend/routes/admin/*.py` (25 files) | `requires super_admin` → `requires super admin access`; 2 SIN messages no longer name the handler | admin pass |
| `backend/routes/admin/driver_statements.py` | import re-sort + dict reformat (pre-existing, forced by the ruff gate) | gate |
| `backend/tests/*` (3 more files) | 5 assertions moved from `"super_admin"` to `"super admin"` | keep suite green |

## 7. Before / after

```python
# Before — backend/routes/drivers/_shared.py
raise HTTPException(
    status_code=409,
    detail=(
        f"Ride is in status '{current}'; cannot perform this action "
        f"from that state (allowed: {list(allowed_states)})."
    ),
)
```

```python
# After
phrase = _RIDE_STATE_PHRASE.get(current)
raise HTTPException(
    status_code=409,
    detail=(
        f"This ride is {phrase}, so that action isn't available right now. Refresh to see its latest status."
        if phrase
        else "That action isn't available for this ride right now. Refresh to see its latest status."
    ),
)
```

```ts
// Before — shared/api/client.ts: a sentinel fell through to the caller's fallback
if (message && message !== 'Request failed' && !isMachineErrorSentinel(message)) {
  return clampToastMessage(message);
}
```

```ts
// After: a sentinel we have copy for wins; unmapped ones fall back as before
const mapped = messageForSentinel(message);
if (mapped) return clampToastMessage(mapped);
if (message && message !== 'Request failed' && !isMachineErrorSentinel(message)) {
  return clampToastMessage(message);
}
```

## 8. Rollback plan

`git revert` **is** a sufficient rollback here, and this is one of the cases the policy carves out:
the change writes nothing to the database, moves no money, touches no ride state, and creates no
insurance-period rows. There is no live data in a changed shape to remediate — reverting the commits
restores the previous strings on the next backend deploy.

No feature flag was added. Per CLAUDE.md gate 3, a flag is for "new/changed UX, new notification copy,
new validation rules that could reject previously-valid input". No validation rule changed and nothing
new can be rejected; flagging a set of error strings would add an `app_settings` read to every error
path for no rollback benefit a revert doesn't already give.

The one piece that a backend revert does **not** cover is `shared/errors/sentinelMessages.ts`, which
ships inside the app bundle. If that copy is wrong, it needs an app release (or an OTA update) — but
its failure mode is benign: a wrong sentence where the user previously saw "Something went wrong".

## 9. Verification performed

- [ ] **Automated tests run — NO.** PyPI and npm are both blocked by this environment's network
      policy (`pip install pytest` → "No matching distribution found"; `npm install` → 403 from the
      registry). The backend suite and the Jest suites could not be executed here. **CI is the real
      gate for this change.**
- [x] `ruff check` **and** `ruff format --check` clean on every touched backend file (enforced by the
      repo's own pre-commit hook, which ran and passed on all 7 commits).
- [x] `python -m py_compile` clean on every touched backend file.
- [x] `tsc --noEmit --strict` clean on `shared/errors/sentinelMessages.ts`; `@shared/*` alias verified
      present in both `rider-app/tsconfig.json` and `rider-app/jest.config.js`.
- [x] **Blast-radius grep performed.** Searched `backend/tests`, `admin-dashboard/src`, `rider-app`,
      `driver-app`, `shared` for all 21 changed strings. Found 7 real assertions
      (`tests/routes/test_payments.py` ×2, `test_corporate_subscriptions_route.py`,
      `test_error_response_sanitisation.py` ×3, `test_ai_chat_route.py`,
      `test_admin_sgi_forms_coverage.py`) — all updated. `test_redact_error_detail.py` uses one of the
      old strings as a standalone fixture input and is unaffected.
- [x] Reviewed against CLAUDE.md conventions: ride state machine (unchanged), money (`Decimal`
      untouched), Stripe idempotency (untouched), PIPEDA (no new PII in any message).
- [x] `spinr-security-auditor` run against the branch diff (through the docs commit). Verdict: no
      blockers, no warnings. It independently confirmed behaviour preservation at every changed raise
      site, that the PCI raw-card guard still logs before rejecting and never echoes the offending
      keys, that `_should_sanitize_5xx_detail` and the `ERR_*` pass-through are logically identical,
      that the sentinel map is additive with no import cycle and cannot render a raw token, and that
      no assertion was weakened to a looser check. It also noted the 403-vs-404 split in
      `routes/payments.py` is a pre-existing enumeration oracle, unchanged by this diff.
- [x] The later admin commit (`require_role`, `role_required:*`) was verified by hand, not by the
      agent: same `admin.get("role") != role` condition, same 403, detail string only.
- [ ] Manual repro in staging — not performed.
- [ ] Feature-flagged — no, justified above.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — §5 filled in; this is a copy change and
      is described as one

## Final state

A re-run of the sweep leaves 36 non-sentinel flags, all accounted for:

- **31 admin-only** — field names that match the label on the admin's own form (`date_from`,
  `expiry_date`, `discount_value`, `period_type`), which read correctly in context, plus two
  extractor false positives (`admin001_detail` and `detail` are variables holding real sentences).
- **2 rider** — the deliberate `status_reason` passthrough (F5).
- **2 shared** — `routes/webhooks.py`, called by Stripe rather than a human.
- **1 rider** — a parser artifact in `rider-app/app/promotions.tsx:55`; the rendered string is
  "20% off — will apply on your next ride."

The 32 remaining `ERR_*` rows are the mapped sentinels, which the client never renders.

## Residual risk

1. **The suite has not been run.** Seven assertions were updated by hand against a grep sweep. If one
   was missed, CI catches it — but that is the first place it will be caught.
2. **New English copy is untranslated.** `fr`, `fr-CA`, `es`, `zh` locale files were not touched, and
   these backend strings are not routed through i18n at all.
3. **No visual check.** rider-app and driver-app have no visual-regression tooling, so longer messages
   (the state-guard ones are ~110–120 chars vs ~35 before) were reasoned about against the 140-char
   clamp and the 2-line toast box, not screenshotted. Worth one device pass on the driver arrive/start
   error path specifically.
