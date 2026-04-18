# Wallet, Payments, Loyalty & Promotions Domain

Rider wallet, driver earnings + payouts, Stripe integration (PaymentIntents + Connect), loyalty tiers, quests, promotions, disputes, and ancillary features (favorites, addresses, notifications).

**Files covered:**
`routes/wallet.py`, `routes/payments.py`, `routes/loyalty.py`, `routes/quests.py`, `routes/promotions.py`, `routes/disputes.py`, `routes/favorites.py`, `routes/addresses.py`, `routes/notifications.py`, `routes/webhooks.py`, `routes/admin/wallet.py`, `routes/admin/promotions.py`, `routes/admin/subscriptions.py`, `routes/admin/messaging.py`, `utils/payment_retry.py`, `utils/email_receipt.py`, `utils/cloudinary.py`.

For corporate B2B wallet specifics — master wallet, off-session auto-topup, spend policies, low-balance nudges — see `docs/CORPORATE_B2B.md`. The B2B wallet shares the Stripe webhook dispatcher and idempotency tables documented here, but has its own RPC (`corporate_wallet_apply_delta`) and its own loops.

---

## 1. Domain overview

| Concept | Owner | Notes |
|---|---|---|
| Rider wallet | `wallets` table | Top-ups via Stripe, debits on ride pay, Decimal-safe ledger (`wallet_ledger`). |
| Driver wallet / earnings | `drivers.total_earnings*` + ride-level split | `driver_earnings = base + distance + time`; platform keeps `booking + airport + taxes`. |
| Driver payouts | Stripe Connect | `driver_bank_accounts`, `payouts`. |
| Stripe idempotency | `stripe_events` table | Claim event id before processing; mark processed on success. Same pattern used by corporate wallet. |
| Loyalty | `loyalty_points`, `loyalty_redemptions` | Tiered multipliers 1.0 / 1.25 / 1.5 / 2.0. |
| Quests | `quests`, `quest_progress` | 6 quest types; driver-only. |
| Promotions | `promotions`, `promotion_redemptions` | 10 validation rules layered on top of a coupon code. |
| Disputes | `disputes` | Ride dispute lifecycle with admin resolution. |
| Notifications | `notifications`, `notification_preferences`, FCM | Push + in-app inbox. |

---

## 2. Rider wallet

### Endpoints (`routes/wallet.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/wallet` | Current balance + pending holds. |
| POST | `/wallet/topup` | Create Stripe PaymentIntent for top-up. Client confirms; webhook credits wallet. |
| GET | `/wallet/transactions` | Ledger history (paginated). |
| POST | `/wallet/debit` (internal) | Called by `rides.process_payment` when `payment_method=wallet`. |

### Ledger invariants

- Every credit and debit is an `INSERT` into `wallet_ledger` (never update). The current balance is the aggregate.
- All amounts are `Decimal` until the response boundary (rounded to 2 dp via `ROUND_HALF_UP`).
- Debits check balance server-side, never trust a client-supplied "affordable" flag.
- Top-up credits happen **only** in the Stripe webhook handler — never in the client-side confirmation path. This guarantees idempotency via `stripe_events` and prevents a "payment succeeded client-side, failed server-side" class of duplicate credits.

---

## 3. Payments (Stripe)

### Endpoints (`routes/payments.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/payments/setup-intent` | SetupIntent for saving a card off-session. |
| POST | `/payments/payment-methods` | List saved cards for the user. |
| DELETE | `/payments/payment-methods/{id}` | Detach a card. |
| POST | `/payments/default-method` | Set default payment method. |
| POST | `/payments/ride-intent` | PaymentIntent for a specific ride (rider-confirmed path). |
| POST | `/payments/topup-intent` | PaymentIntent for wallet top-up. |

### PCI guard

`routes/payments.py` rejects any inbound body that contains raw card fields (`card`, `number`, `cvc`, `exp_month`, `exp_year`, `cardnumber`, `cvv`) **before** JSON parsing. Raw card data must go directly from the mobile Stripe SDK → Stripe's servers, never through our backend. Violation returns 400 with an explicit explanation.

### Webhook (`routes/webhooks.py`)

```
POST /webhooks/stripe
  ├─ verify signature with STRIPE_WEBHOOK_SECRET (raise 400 on mismatch)
  ├─ parse event
  ├─ claim_stripe_event(event.id):
  │     INSERT stripe_events(event_id, type, received_at, status='processing')
  │     if conflict → event already seen → return 200 (idempotent no-op)
  ├─ dispatch by event.type:
  │     payment_intent.succeeded   → credit wallet (rider OR corporate master)
  │     payment_intent.payment_failed → schedule retry via utils/payment_retry
  │     charge.refunded            → debit wallet, mark ride refunded
  │     charge.dispute.created     → flag ride under dispute
  │     setup_intent.succeeded     → attach default payment method
  │     customer.subscription.*    → subscription state sync
  └─ mark_stripe_event_processed(event.id) on success
```

PaymentIntent metadata encodes intent purpose (`purpose=wallet_topup`, `purpose=ride_fare`, `purpose=corporate_topup`, `ride_id=...`, `user_id=...`) so the handler routes correctly. See `docs/CORPORATE_B2B.md` §6 for the corporate branch.

### Payment retry loop (`utils/payment_retry.py`)

Every 5 min:

```
for intent in PaymentIntents where status='requires_payment_method':
    if retries < max_attempts and next_retry_at <= now:
        attempt off_session confirm with default payment method
        increment retries
        on success → webhook fires naturally
        on failure → exponential backoff
```

---

## 4. Loyalty (`routes/loyalty.py`)

### Tiers

| Tier | Threshold (lifetime points) | Multiplier |
|---|---|---|
| Bronze | 0 | 1.0× |
| Silver | 500 | 1.25× |
| Gold | 2,000 | 1.5× |
| Platinum | 10,000 | 2.0× |

Multiplier is applied to earned points per ride — not to the fare.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/loyalty/summary` | Tier, points, next-tier delta, recent history. |
| POST | `/loyalty/redeem` | Redeem points for discount / perk. |
| GET | `/loyalty/redemptions` | History. |

Points awarded server-side when a ride completes and is fully paid — not on status change alone, to avoid phantom points on cancelled-after-complete edge cases.

---

## 5. Quests (`routes/quests.py`)

Driver-only progression system. Quest types:

- `ride_count` — complete N rides in window
- `earnings_target` — earn $X in window
- `online_hours` — N hours online
- `peak_rides` — N rides during peak hours
- `consecutive_days` — N days with ≥1 ride
- `rating_maintained` — keep ≥X rating over N rides

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/quests/active` | Active quests for the driver. |
| GET | `/quests/history` | Completed/expired quests. |
| POST | `/quests/{id}/claim` | Claim reward (wallet credit or badge). |

Progress is tracked in `quest_progress`, updated from ride completion hooks. Claim requires `progress >= target` and `not claimed`.

---

## 6. Promotions (`routes/promotions.py`)

Coupon-style codes. 10 layered validation rules checked in order at apply:

1. Code exists and is active (`is_active`, `active_from`, `active_until`).
2. Not expired for this user (`usage_limit_per_user`, redemption count).
3. Global usage limit not exceeded (`usage_limit`).
4. User is in target segment (new / existing / region / role).
5. Ride meets min-fare requirement.
6. Ride meets service-area requirement.
7. Ride meets vehicle-type requirement.
8. Scheduled vs on-demand constraint satisfied.
9. Discount calc respects max cap.
10. First-ride-only flag satisfied (if set).

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/promotions/available` | List promos the user is eligible for right now. |
| POST | `/promotions/apply` | Validate + attach to an in-flight ride create. |
| GET | `/promotions/history` | Past redemptions. |

### Admin (`routes/admin/promotions.py`)

Full CRUD, targeting editor, usage analytics, push-to-segment ("notify eligible users").

---

## 7. Disputes (`routes/disputes.py`)

Rider or driver opens a dispute on a completed ride (wrong fare, wrong route, etc.).

```
create  → status=open
admin reviews → resolve (refund | no-action | adjust) → status=resolved
escalation path via admin support module
```

Endpoints: `POST /disputes`, `GET /disputes/{id}`, `GET /disputes` (mine), `POST /disputes/{id}/message`. Admin-side CRUD + resolution lives under `routes/admin/*`.

---

## 8. Notifications (`routes/notifications.py`)

- FCM push tokens stored per device (rider / driver).
- In-app inbox: `notifications` table with `is_read`.
- Per-channel preferences (`notification_preferences`): ride updates, promos, safety, documents, etc.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/notifications` | Inbox with read/unread filter. |
| POST | `/notifications/{id}/read` | Mark read. |
| GET | `/notifications/preferences` | Current preferences. |
| PUT | `/notifications/preferences` | Update prefs. |
| POST | `/notifications/push-token` | Register/unregister FCM token per device. |

---

## 9. Favorites & addresses

### Favorites (`routes/favorites.py`)

Rider → favorite drivers (priority during dispatch within service area).

Driver → favorite riders (future: match preference).

### Saved addresses (`routes/addresses.py`)

Rider: home, work, custom. Used for one-tap pickup/dropoff in the app.

---

## 10. Email receipts (`utils/email_receipt.py`)

After ride completion + payment success, asynchronously render an HTML receipt (ride summary, fare breakdown, tax, driver name, vehicle, receipt id) and mail via configured SMTP / transactional email service. Failure logs a warning but does not block the ride completion path.

---

## 11. Cloudinary (`utils/cloudinary.py`)

User-uploaded images (profile photo, driver vehicle photo, dispute evidence). Signed upload URLs; server validates MIME + size. Driver documents still go to Supabase Storage (`STORAGE_BUCKET=driver-documents`) with stricter policies — documents handler under `documents.py`.

---

## 12. Admin surface

| File | Purpose |
|---|---|
| `routes/admin/wallet.py` | Credit/debit rider wallets (compliance adjustments), ledger search. |
| `routes/admin/promotions.py` | Promo CRUD + analytics. |
| `routes/admin/subscriptions.py` | Spinr Pass subscription plans, per-driver subscription state. |
| `routes/admin/messaging.py` | Broadcast push / SMS to segments (riders, drivers, admins). |

---

## 13. Schemas (`schemas.py` extracts)

| Model | Purpose |
|---|---|
| `WalletTopupRequest` | `{amount, currency, payment_method_id?}`. |
| `WalletTransaction` | Ledger entry (credit/debit, amount, reason, created_at). |
| `LoyaltyRedemptionRequest` | `{reward_id, metadata?}`. |
| `PromotionApplyRequest` | `{code, ride_id?}` (or apply during ride create). |
| `DisputeCreateRequest` | `{ride_id, reason, details, evidence_urls}`. |
| `NotificationPreferenceUpdate` | Per-channel booleans. |

---

## 14. Common tasks

| Task | Where |
|---|---|
| Add a new Stripe event handler | `routes/webhooks.py` dispatch block; wrap with `claim_stripe_event / mark_stripe_event_processed`. |
| Change loyalty tier thresholds | `routes/loyalty.py` tier table + migration to update existing users (backfill). |
| Add a quest type | Enum in schemas, progress update hook (ride-complete path), admin panel. |
| Tune payment retry window | `utils/payment_retry.py` (`max_attempts`, backoff). |
| Add a new promo validation rule | Append to the validator chain in `routes/promotions.py`; surface it in admin targeting UI. |
| Credit a user's wallet manually (compliance) | Admin endpoint under `routes/admin/wallet.py` — logs to `wallet_ledger` with `reason=admin_adjustment`. |
| Make wallet top-up auto-apply a promo | Extend `POST /wallet/topup` with optional `promo_code`; validate via promotions chain; apply as separate ledger credit post-webhook. |
