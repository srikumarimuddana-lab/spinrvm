# Change Impact & Risk Log — Meta conversion tracking

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude Code (branch `claude/spinr-conversion-tracking-u78x20`) |
| Surface(s) | backend, rider-app, driver-app (admin-dashboard: settings fields only) |
| Domain (Sentry tag) | payments, auth, drivers |
| PR / commit link | branch `claude/spinr-conversion-tracking-u78x20` |
| Related issue or gap ID | Meta receives only a web `PageView`; no signup/install/ride conversion exists |

## 1. Issue / gap identified

Meta receives a single web `PageView` from pixel `793865613741737` and nothing
else. There is no signup, install, or completed-ride conversion anywhere, so no
ad campaign can be optimised toward, or measured against, an acquired rider or
driver. The App Promotion campaign additionally fails outright with
`"application_id needs to be valid… set it in the Promoted Objects"` because no
Meta App exists to promote.

## 2. Root cause

No app-events SDK was ever installed in either mobile app, and no server-side
Conversions API integration existed. `shared/analytics/index.ts` has been a
no-op stub since Firebase Analytics was removed, so the ~hundreds of existing
`Analytics.*` call sites have been writing to nothing.

## 3. Fix / remediation

Server-side Conversions API integration for all six events, plus the Meta app
events SDK in both apps for install attribution and the client half of
`CompleteRegistration`.

Advanced Matching (hashed email / phone / external_id) is sent **server-side
only**. This was an explicit decision over the client-SDK alternative: on-device
matching needs IDFA and App Tracking Transparency, which would make both apps'
`NSPrivacyTracking: false` declaration untrue and change the App Store privacy
label on a live-tested app. Server-side gets the Event Match Quality lift with
no ATT prompt and no store-submission change.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface but additive.** No existing code path changes
behaviour. Details per shared thing touched:

| Shared thing | Other readers/writers | Regression risk |
|---|---|---|
| `users` table | ~everything | New nullable column only. No existing column changes meaning. `SELECT *` consumers get one extra key. |
| `users.first_ride_completed_at` | **nothing else** — new column, grepped `first_ride` across `*.py`/`*.ts`/`*.tsx`/`*.sql`: only promo-eligibility rules, which use their own logic and do not read this column | Isolated. |
| `AuthResponse` | `rider-app/app/otp.tsx`, `driver-app/app/otp.tsx`, `shared/store/authStore.ts`, `admin-dashboard` login | New **optional** field. Pydantic response model, additive — existing clients ignore unknown keys. |
| `VerifyOTPRequest` | both apps' `otp.tsx` | New **optional** field defaulting to `"rider"`. A build predating it sends nothing and gets the old behaviour. Does not affect auth, tokens, or the role assigned to the new row. |
| `process_payment` | rider app payment-confirm screen, payment retry sweep | One added call, inside the existing `if not result.already_paid:` block, spawned not awaited. |
| `auto_settle_guest_corporate` | corporate guest bookings, payment retry sweep | One added call on the success path. **This is the one place a test double had to change** — see §9. |
| `complete_ride` (driver) | driver app, dispatch, insurance periods | One added call after `set_driver_available`. Reads `total_rides` from the already-loaded driver row; adds no query. |
| `admin_verify_driver` | admin dashboard | One added call before the existing audit-log write. |
| `_CREDENTIAL_FIELDS` | admin settings GET | One added key, so the new token is masked like every other credential. |
| `shared/analytics/meta.ts` | new file, 4 importers (both `_layout.tsx`, both `otp.tsx`) | New module; no existing consumer. |

**Money:** no fare, wallet, Stripe, or corporate-billing arithmetic is touched.
`Purchase` **reads** the already-settled `charged_amount` and never computes or
writes a monetary value. The one numeric conversion (`_money`) is
Decimal-via-string and exists only to keep float artefacts off a reported value.

**Ride state machine:** untouched. No transition added, removed, or reordered.

**Background loops:** no new loop. The payment retry sweep reaches settlement
through `process_payment`/`auto_settle_guest_corporate`, so a swept ride emits
its `Purchase` through the same guarded path — and the `already_paid` guard
stops a re-driven ride from emitting a second one.

**Error-handling convention:** `utils/meta_capi.py` deliberately swallows its
own transport errors — a documented exception to the repo-wide no-swallow rule,
which exists to protect DB/auth/payment/dispatch correctness. This module
participates in none of those. Failures surface via `logger.error` plus
`spinr_meta_capi_events_total{outcome="failed"}`.

**PIPEDA:** raw email/phone never leave the process — SHA-256 only, with
`external_id` hashed too so a Spinr user id is not readable in Meta's systems.
No GPS, no addresses, no names, no card data. Logs carry event names and
`event_id` (a random UUID) only.

## 5. User-experience effect

**Rider / driver: no visible change.** No screen, copy, validation rule, or
notification changes. Nothing is visible mid-session to a rider mid-ride or a
driver online. Every added call is spawned off the request path and cannot slow
or fail signup, settlement, or ride completion.

**Internal admin: one new Settings card** with four Meta fields (two dataset
ids, a masked access token, a test-event code). No existing admin screen
behaviour changes.

Non-UX but worth stating: once credentials are live, riders' and drivers'
**hashed** contact details begin flowing to Meta for ad attribution. That is a
privacy-policy question, not a UX one, and it is flagged in §10 as requiring
sign-off before the token is pasted in.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/264_meta_conversions_tracking.sql` | New: `users.first_ride_completed_at`, `meta_capi_events` table | Once-per-user `FirstRide` gate; persisted `event_id` for backend-only events |
| `backend/utils/meta_capi.py` | New: hashing + Graph API transport | PII hashing, bounded retry, `test_event_code` |
| `backend/services/meta_conversions_service.py` | New: domain event senders | Row → event dictionary; both dedup gates |
| `backend/schemas.py` | Added 4 `AppSettings` keys, `VerifyOTPRequest.client_app`, `AuthResponse.meta_event_id` | Config + dedup id transport |
| `backend/routes/admin/settings.py` | Token added to `_CREDENTIAL_FIELDS`; 4 update fields | Mask the secret; let admin set config |
| `backend/routes/auth.py` | Fire `CompleteRegistration`, return `meta_event_id` | Signup conversion + dedup |
| `backend/routes/rides/payments.py` | Fire `Purchase`/`FirstRide` after settlement | Ride conversion at the money-moved moment |
| `backend/services/payment_service.py` | Fire `Purchase` on guest-corporate auto-settle | Otherwise this ride class emits nothing |
| `backend/routes/drivers/ride_complete.py` | Fire `DriverActivated` on first trip | Driver activation conversion |
| `backend/routes/admin/drivers.py` | Fire `DriverApproved` on approve | Driver approval conversion |
| `backend/tests/test_meta_conversions.py` | New: 54 tests | Hashing, gates, retry, isolation |
| `backend/tests/test_guest_auto_settle.py` | Test double now mirrors real `PaymentResult` | Stub was thinner than the real return type |
| `shared/analytics/meta.ts` | New: SDK wrapper | Guarded init + client `CompleteRegistration` |
| `rider-app/`, `driver-app/` `package.json` | `react-native-fbsdk-next` | App events SDK |
| `rider-app/`, `driver-app/` `app.config.ts` | Plugin block + `extra` keys | Native config, tracking pinned off |
| `rider-app/`, `driver-app/` `app/_layout.tsx` | `initMetaSdk(...)` at module scope | Install/activation event on first launch |
| `rider-app/`, `driver-app/` `app/otp.tsx` | `client_app` on request; fire `CompleteRegistration` | Dataset routing + deduped client event |
| `META_EVENTS.md` | New | Analyst-facing event contract |

## 7. Before / after

The only behaviour-changing edit to an existing flow is the settlement hook.
Everything else is additive.

```python
# Before — backend/routes/rides/payments.py
        _deps.spawn(send_ride_receipt(ride, current_user["id"], tip_rounded))
        email_sent = True
```

```python
# After
        _deps.spawn(send_ride_receipt(ride, current_user["id"], tip_rounded))
        email_sent = True

        _fire_purchase_conversion(ride, current_user, result.charged_amount)
```

Still inside `if not result.already_paid:`, so an idempotent replay of an
already-settled ride emits no conversion. `_fire_purchase_conversion` spawns and
contains every error, so its return is unconditional and cannot raise.

## 8. Rollback plan

**No deploy required.** Clear `meta_capi_access_token` in admin → Settings.
Every sender checks it first and returns before claiming any gate or sending
anything, so tracking stops within the 60-second settings cache TTL.

Escalating options, in order:

1. **Clear the access token** (above) — instant, no deploy, no data change.
2. **Clear one dataset id** to disable a single app's funnel.
3. **Migration rollback** (only if the schema itself is a problem):
   ```sql
   ALTER TABLE users DROP COLUMN IF EXISTS first_ride_completed_at;
   DROP TABLE IF EXISTS public.meta_capi_events;
   ```
   Safe at any time — nothing else reads either object.

No live-data remediation is needed: this change moves no money, writes no ride
state, and creates no insurance-period rows. The only rows it writes are the
new `first_ride_completed_at` timestamp and append-only `meta_capi_events`.
Neither is read by any pre-existing code path, so a `git revert` genuinely is
sufficient here — unusually, but only because the change is additive.

## 9. Verification performed

- [x] **Automated tests run** — `pytest tests/test_meta_conversions.py`: **54
      passed**. Covers hashing/normalization, no-raw-PII-in-payload, the
      once-per-user `FirstRide` gate, backend-event idempotency, retry
      (5xx/429/network) vs fail-fast (4xx), and the tracking-disabled guards.
- [x] **Regression run on touched flows** — `pytest -k "auth or payment or
      ride_state or promo or settle"`: **633 passed, 1 failed → fixed → 6
      passed**. The failure was `test_guest_auto_settle.py`, whose
      `SimpleNamespace` stub lacked `already_paid`/`charged_amount`. The real
      `PaymentResult` dataclass has both with defaults, so this was an
      incomplete test double, not a production defect; the stub was widened to
      mirror the real type rather than papering over it with `getattr`.
- [x] **Blast-radius grep performed** — searched: `first_ride`, `Analytics`
      importers, `_make_auth_response` callers, `AuthResponse` consumers,
      `_CREDENTIAL_FIELDS`, `already_paid`, `total_rides`,
      `auto_settle_guest_corporate` callers, `spawn` usage. Results in §4.
- [x] **Reviewed against `CLAUDE.md`** — money (Decimal-only: no arithmetic
      added), migrations (append-only, RLS, rollback comment, next free number
      264), PIPEDA (hashed-only, no GPS/PII in logs), observability (metric
      naming `spinr_meta_capi_*`, `logger.error` on failures), error handling
      (swallow exception documented and justified).
- [x] **Effectively flagged** — `meta_capi_access_token` being empty is a hard
      off switch using the existing `app_settings`-in-DB pattern. Ships dark.
- [ ] **Manual repro in staging** — not performed. Requires Meta credentials
      that do not exist yet.
- [ ] **Production build** — **not run.** See §10.

## 10. What was NOT verified

Stated explicitly rather than implied by silence:

- **No production build was run for either app.** `rider-app/node_modules` and
  `driver-app/node_modules` are absent in this environment, so no
  `npm run build`, no `tsc --noEmit`, and no Jest run happened for the mobile
  changes. Modified `.ts`/`.tsx` files were syntax-checked with standalone
  `tsc` (no syntax errors), which is **not** equivalent to a real build.
- **The native build is unverified.** `react-native-fbsdk-next` adds native
  iOS/Android code to an Expo SDK 55 / RN 0.85 New-Architecture app with an
  already-delicate Gradle/Kotlin/Podfile setup (see `app.config.ts` comments
  and `docs/android-build-strategy.md`). **An EAS build for both apps is
  required before merge.** This is the highest-risk unverified item.
- **No event has ever reached Meta.** No Meta App, dataset, or token exists
  yet, so nothing was exercised end-to-end. Test Events verification, the
  de-duplication check, and the before/after Event Match Quality number
  (currently 6.1/10) all remain outstanding and need Events Manager access.
- **The app-event de-duplication parameter name is unverified.** The client
  sends `event_id` in the `logEvent` parameter bundle. If Events Manager shows
  the app and server copies as two conversions rather than one, this is the
  first thing to check.
- **No visual regression tooling exists** for either app; no UI changed, so
  this was reasoned about rather than screenshotted — a standing repo gap.
- **RLS on `meta_capi_events` was not exercised against a live Supabase.** The
  policy grants `service_role` only, with RLS enabled and no `authenticated`
  policy, so non-service-role access returns zero rows by default. Verified by
  reading the policy, not by connecting.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (clear one `app_settings` field)
- [x] Blast radius is stated, not assumed (§4 table)
- [x] No silent behaviour change to an already-shipped flow — §5 confirms no
      rider/driver-visible change
- [ ] **Privacy sign-off required before enabling.** Turning this on begins
      sending hashed rider and driver contact details to Meta for advertising
      attribution. Confirm the privacy policy discloses ad-attribution sharing
      and that the consent language version on file covers it (PIPEDA: material
      changes require re-consent). This is a business/legal decision, not a
      code one, and it gates pasting in the access token — not this merge.
