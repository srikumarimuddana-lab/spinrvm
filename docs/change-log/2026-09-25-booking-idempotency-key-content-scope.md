# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (this branch, fix/5708-booking-idempotency-key) |
| Related issue or gap ID | #5708 |

## 1. Issue / gap identified

`POST /rides`'s client-generated `Idempotency-Key` was built only from `userId` + pickup/dropoff coordinates + a 2-minute time bucket — it omitted `vehicle_type_id`, `promo_code`, `stops`, schedule, and `corporate_account_id`, all of which affect price/dispatch/settlement.

## 2. Root cause

The key was added (commit `ac6451a09`, 2026-09-22) specifically to fix a client-retry-after-timeout double-Stripe-hold bug, and was scoped only to "the same request retried" — coordinates + a short time bucket. It didn't account for a rider making a *genuinely different* booking (different vehicle tier, promo, added stop, or switched to scheduled) on the identical route within the same 2-minute window — an ordinary rebook after a cancel, not a retry.

## 3. Fix / remediation

Extended the client-side key to also include `vehicle_type_id`, the applied promo code, `payment_method` + `payment_method_id`, `requires_wav`, the schedule (`is_scheduled`/`scheduled_time`), `corporate_account_id`, and a stable signature of `stops` — every field in the same request payload that affects price, dispatch, or settlement. No backend change: `backend/routes/rides/booking.py` treats the header as an opaque string (exact match only), so a richer client-generated key needs no server-side change.

`payment_method_id` and `requires_wav` were added after an adversarial pre-commit review (`spinr-edge-case-reviewer`) caught that the initial diff omitted them: without `payment_method_id`, a rider switching to a different saved card on rebook would silently keep charging the old card/hold; without `requires_wav`, a rider toggling WAV-required on rebook could still get matched to a non-WAV driver via the stale row. `preauthorized_payment_intent_id` is deliberately excluded — including it would break the SCA two-step retry, where the second call (after an on-device card confirmation) sets this field but must reuse the same key as the first. `rider_notes`, `quiet_mode`, and `planned_route_polyline` are also excluded — none affect price, dispatch, or settlement.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to rider-app**. Grepped every backend reader of the `Idempotency-Key` header and the `rides.idempotency_key` column (`routes/rides/booking.py:471-475`, `:1190-1191`, `:1671-1676`; `utils/idempotency.py`'s generic per-request dedup middleware, which SHA-256-hashes whatever string it's given). None of them interpret the key's content — it's always an opaque exact-string match or hash input. A longer, more specific key changes nothing about how the backend consumes it.
- The original bug this key exists to fix (client retry after network timeout minting a second Stripe hold) is unaffected: a genuine retry still sends byte-identical `rideData`, so every new field folded into the key produces the same value on retry as on the original attempt.
- No other caller of `createRide` or this key-generation code exists (single call site in `rider-app/store/rideStore.ts`).

## 5. User-experience effect

Rider-facing, but invisible in the normal case (same behavior for retries). Only changes behavior for the edge case the bug describes: a rider who cancels and rebooks a *different* trip (vehicle/promo/stop/schedule) on the same route within 2 minutes now correctly gets a **new** ride created instead of silently being handed back the first ride's stale row. Not visible mid-session to anyone already using the app — only affects the moment a new `POST /rides` is sent.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/store/rideStore.ts` | Extended `idempotencyKey` to include vehicle type, promo code, payment method, schedule, corporate account, and a stops signature | Prevent a legitimately different rebook from colliding with a stale ride row (#5708) |

## 7. Before / after

```js
// Before
const idempotencyBucket = Math.floor(Date.now() / 120_000);
const idempotencyKey =
  `ride-${userId}-${pickup.lat.toFixed(5)}-${pickup.lng.toFixed(5)}-` +
  `${dropoff.lat.toFixed(5)}-${dropoff.lng.toFixed(5)}-${idempotencyBucket}`;
```

```js
// After
const idempotencyBucket = Math.floor(Date.now() / 120_000);
const idempotencyStopsSig = stops
  .filter((s) => s.lat && s.lng)
  .map((s) => `${s.lat.toFixed(5)},${s.lng.toFixed(5)}`)
  .join('|');
const idempotencyPromoCode = get().appliedPromo?.code || 'none';
const idempotencySchedule = scheduledTime ? formatLocalNaiveIso(scheduledTime) : 'now';
const idempotencyKey =
  `ride-${userId}-${pickup.lat.toFixed(5)}-${pickup.lng.toFixed(5)}-` +
  `${dropoff.lat.toFixed(5)}-${dropoff.lng.toFixed(5)}-` +
  `${selectedVehicle.id}-${idempotencyPromoCode}-${paymentMethod}-${paymentMethodId ?? 'none'}-` +
  `${idempotencySchedule}-${corporateAccountId || 'none'}-${requiresWav}-` +
  `${idempotencyStopsSig}-${idempotencyBucket}`;
```

## 8. Rollback plan

`git revert` is sufficient — this is a pure client-side key-generation change with no persisted server-side state or migration. Reverting restores the narrower (bug-carrying) key; no data-level remediation needed since the backend's own row-level constraints (one active ride per rider, unique idempotency index) were never at risk either way.

## 9. Verification performed

- [x] Blast-radius grep performed: every backend reader/consumer of `Idempotency-Key`/`idempotency_key` across `backend/` (see §4).
- [ ] Automated tests run — no existing test file covers this key-generation logic; none added in this fix (would require mocking `useAuthStore`/`api.post` and asserting on the sent header — flagged as a gap below, not silently omitted).
- [ ] Manual repro / staging check — not performed, no staging environment reachable from this session.
- [x] Reviewed against relevant CLAUDE.md convention — this is a client-only string-construction change, no state-machine/money/RLS logic touched directly (money-adjacent only in that it prevents a *wrong* fare from being silently reused).
- [ ] Feature-flagged — not flagged; this is a straightforward bug-fix (narrowing an over-broad idempotency collision), not a new user-visible behavior, and the original key itself wasn't flagged either.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated: isolated to rider-app client code, zero backend interpretation of key content
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states exactly what changes and for whom)

## What was NOT verified

- No `tsc --noEmit` or full typecheck was run against this change — `rider-app/node_modules` isn't installed in this sandbox's git worktree and a full install wasn't performed. The new code reuses expressions already present and compiling elsewhere in the same file (`get().appliedPromo?.code`, `scheduledTime` passed to `formatLocalNaiveIso`, `stops.filter((s) => s.lat && s.lng)`), so type-correctness was reasoned about via that precedent, not confirmed by the compiler.
- No unit test exists or was added for the idempotency-key construction itself. Recommend a follow-up test asserting that two `createRide` calls with the same route/time-bucket but different `vehicle_type_id` (or promo/stop/schedule) produce different `Idempotency-Key` header values.
- Not tested against a live/staging backend — reasoned about via direct code reading of `booking.py`'s exact-match lookup, not an actual round-trip.
