# Payment Dispute / Chargeback Evidence Runbook

**Covers:** Stripe chargebacks (`charge.dispute.created`) · card-network inquiries · rider "I didn't take this ride" / "I was overcharged" claims
**Owner:** Support lead (assembly) + Finance (submission in Stripe Dashboard)
**Clock:** Stripe sets an `evidence_due_by` on every dispute — typically **7–21 days** depending on the card network. Miss it and the dispute is **lost automatically**, regardless of how good the evidence was. Start assembly the day the webhook fires.

Cross-reference: `docs/runbooks/stripe-webhook-failure.md` · `docs/runbooks/stripe-reconciliation.md` · `docs/runbooks/trip-route-integrity.md` (deeper GPS forensics) · `.claude/context/domain-payments.md`

---

## 0. What already happens automatically

When the bank opens a dispute, Stripe sends `charge.dispute.created` and `backend/routes/webhooks.py` (≈ line 1107):

- inserts a row into **`stripe_disputes`** — `stripe_dispute_id`, `payment_intent_id`, `ride_id`, `amount_cents`, `reason`, `status`
- sets the ride's `payment_status` to **`disputed`**
- emits a `charge_dispute_created` notification and logs `CHARGEBACK: dispute opened reason=… amount_cents=… ride=… pi=…`

On `charge.dispute.closed` the row's `status` is updated and the ride moves to `paid` (won) or `dispute_lost`.

**What does NOT happen automatically: nothing is submitted to Stripe.** There is no auto-evidence job. A human assembles the pack and uploads it in the Stripe Dashboard. That is what the rest of this runbook is for.

---

## 1. Find the ride behind the dispute

```sql
-- from the Stripe dispute id (dp_...) shown in the Stripe Dashboard
select ride_id, payment_intent_id, amount_cents, reason, status, created_at
from   stripe_disputes
where  stripe_dispute_id = 'dp_XXXXXXXX';
```

If `ride_id` is null (webhook arrived before the PI was linked), fall back to the PaymentIntent:

```sql
select id, ride_code, rider_id, driver_id, status, payment_status, ride_completed_at
from   rides
where  payment_intent_id = 'pi_XXXXXXXX';
```

Then open **Admin Dashboard → Rides → (search the ride) → ride detail modal**. Everything in §2 is reachable from that modal or the endpoints listed beside it.

---

## 2. The evidence pack — Stripe field → Spinr artifact

Stripe's dispute form has named evidence slots. This is what fills each one for a ride-hailing charge.

| Stripe evidence field | What we submit | Where to get it |
|---|---|---|
| `receipt` | **Ride Invoice PDF** — invoice #, service date, pickup/dropoff, distance/duration, full fare breakdown with GST/PST as separate lines, tip, grand total, rider name/phone/email, driver code + vehicle, **and the embedded route map image** | Ride detail modal → **Download PDF** (`ride-invoice.tsx`). Raw data: `GET /api/admin/rides/{ride_id}/invoice` |
| `service_documentation` | **GPS route map PNG** (green `P` pickup marker, red `D` dropoff marker, blue actual driven path) | `GET /api/admin/rides/{ride_id}/route-map.png` — already embedded in the invoice PDF, but attach standalone at full size too |
| `service_documentation` (2nd file) | **Raw GPS breadcrumb trail** — per-point `lat, lng, speed, heading, accuracy, altitude, tracking_phase, timestamp` from `driver_location_history`, filtered to `navigating_to_pickup` + `trip_in_progress` | `GET /api/admin/rides/{ride_id}/location-trail`. Export as CSV. This is the single strongest artifact — it proves a physical vehicle moved from A to B at the disputed time |
| `service_date` | `rides.ride_completed_at` | Ride detail modal, or the invoice payload |
| `customer_name` / `customer_email_address` | `rider_name`, `rider_email` from the enriched ride record | `GET /api/admin/rides/{ride_id}/details` |
| `customer_purchase_ip` | IP + user-agent of the rider's session around booking time | `refresh_tokens` table — `ip`, `user_agent`, `issued_at` for that `user_id` (see §3d) |
| `access_activity_log` | **Account + trip activity**: account `created_at`, lifetime ride count, the trip timeline (requested → assigned → accepted → arrived → started → completed), the dispatch funnel (`ride_offers`: which drivers were offered, ETA, who accepted), and login history | Ride detail modal (timeline + offers panels); `ride_offers` and `refresh_tokens` for the raw rows |
| `customer_communication` | **In-app rider↔driver chat** for the ride (`ride_messages`), plus the support thread | `select text, sender, timestamp from ride_messages where ride_id = '…' order by timestamp;` · Zoho Desk ticket via `disputes.zoho_ticket_id` |
| `product_description` | Short standing description — "On-demand point-to-point passenger transportation (ride-hailing) in Saskatoon, SK. Fare is quoted before booking and charged on trip completion." | Cover letter (§5) |
| `refund_policy` / `cancellation_policy` | Published Terms & refund policy page + the in-app cancellation-fee disclosure screenshot | spinr.ca terms URL |
| `refund_refusal_explanation` / `cancellation_rebuttal` | Cover letter (§5) | — |
| `uncategorized_file` | Anything below that strengthens the case: the **rating and comment the rider left after the trip**, prior completed-ride history, prior dispute record | `rides.rider_rating`, `disputes` table |
| `duplicate_charge_id` / `duplicate_charge_explanation` | Only for `duplicate` reason — the other PaymentIntent id and why the two are distinct rides | `rides` filtered on `rider_id` + date |

### Beyond the receipt — the five that actually win rides disputes

If you only have time for five attachments, use these:

1. **GPS breadcrumb CSV** — timestamped physical movement. Hard to argue with.
2. **Route map PNG** — the same data a bank reviewer can read in two seconds.
3. **Trip timeline with driver identity** — a named, background-checked, licensed driver was assigned and completed it.
4. **Rider account history** — established account, verified phone, N prior completed rides on the same card. Kills most `fraudulent` claims.
5. **Post-trip rating / in-app chat** — a rider who rated the driver or chatted during the trip took the trip.

---

## 3. Step-by-step assembly

### 3a. Invoice PDF
Admin Dashboard → Rides → ride detail modal → **Download PDF**. Filename `spinr-invoice-<ride8>.pdf`.
To also mail it to the rider (creates a paper trail showing the receipt was delivered):
`POST /api/admin/rides/{ride_id}/send-receipt` (optional `{"email": "..."}` override), or the **Send** button next to Download.

### 3b. Route map
```bash
curl -H "Authorization: Bearer $ADMIN_JWT" \
  "$API/api/admin/rides/$RIDE_ID/route-map.png" -o route-$RIDE_ID.png
```

### 3c. GPS trail → CSV
```bash
curl -H "Authorization: Bearer $ADMIN_JWT" \
  "$API/api/admin/rides/$RIDE_ID/location-trail" -o trail-$RIDE_ID.json
```
Convert to CSV before attaching — bank reviewers do not read JSON.

For a contested-distance or "driver took the long way" dispute, run the forensic analyzer instead of the raw dump — it produces a phase-by-phase distance ladder, GPS accuracy histogram, and dead-zone timeline:
```bash
python -m backend.scripts.analyze_ride_route --ride-id "$RIDE_ID" --incident-report
```
(read-only; console output is coordinate-sanitized — precise geometry is written only with an explicit `--route-output`)

### 3d. Rider account + session context
```sql
-- account age + verified contact
select id, created_at, status from users where id = '<rider_id>';

-- lifetime completed rides on this account
select count(*) from rides where rider_id = '<rider_id>' and status = 'completed';

-- session IP / device around the booking (customer_purchase_ip)
select issued_at, ip, user_agent, audience
from   refresh_tokens
where  user_id = '<rider_id>'
order  by issued_at desc
limit  20;

-- prior disputes by the same rider (pattern evidence)
select ride_id, reason, status, refund_amount, created_at
from   disputes where user_id = '<rider_id>' order by created_at desc;
```

### 3e. Communications
```sql
select sender, text, timestamp from ride_messages where ride_id = '<ride_id>' order by timestamp;
select zoho_ticket_id from disputes where ride_id = '<ride_id>';
```
Export the Zoho Desk thread to PDF if the rider contacted support before going to their bank — a rider who was offered a refund and declined it is strong material for `refund_refusal_explanation`.

---

## 4. Reason-code playbook

| Stripe `reason` | Lead with | Also attach |
|---|---|---|
| `fraudulent` / "card not recognized" | Account history (age, prior rides on the same card), session IP + device, verified phone | GPS trail, timeline, rating left after trip |
| `product_not_received` | GPS trail + route map + completion timestamp | Driver identity, in-app chat, arrival/start/complete timeline |
| `duplicate` | Both PaymentIntent ids side by side with distinct ride ids, times and routes | Both invoices |
| `subscription_canceled` | Subscription ledger row + cancellation timestamp | `subscription_payments` history |
| `general` / `unrecognized` | Full pack — invoice, GPS, timeline, account history | Cover letter walking the reviewer through it |
| `credit_not_processed` | Refund ledger showing whether a refund was issued and when | `financial_events` rows keyed on the PI |

For an **overcharge / wrong-fare** claim specifically: pull `fare_breakdown_snapshot` (the frozen quote the rider accepted) and put it next to the charged total. Under fare-lock the rider is charged the quoted road distance, not the GPS distance — say so explicitly, because the two numbers differing looks like an error to a reviewer who does not know the policy.

---

## 5. Cover letter

Always include one as `uncategorized_text`. A reviewer spends ~90 seconds on a dispute; the letter is what they actually read.

```
Spinr Mobility Inc. — ride-hailing, Saskatoon, Saskatchewan, Canada.

Charge: CAD $<amount> on <date>, PaymentIntent <pi_...>, Ride <ride_code>.

The cardholder booked and completed an on-demand ride through the Spinr rider
app on an account created <account_created>, which has completed <N> prior
rides on the same payment method.

Trip record:
  Requested        <ts>
  Driver assigned  <ts>  (driver <driver_code>, <vehicle>)
  Driver arrived   <ts>
  Trip started     <ts>  <pickup address>
  Trip completed   <ts>  <dropoff address>   <distance> km / <duration> min

GPS breadcrumbs recorded continuously during the trip are attached
(trail-<ride>.csv) along with a map of the driven route (route-<ride>.png).
The itemized receipt (spinr-invoice-<ride>.pdf) shows base fare, distance,
time, booking fee, GST and PST as separate line items, matching the charge.

The fare was quoted to and accepted by the rider in-app before booking. No
charge is applied that is not disclosed on the attached receipt.

<if applicable> The rider rated this trip <N>/5 on <date>.
<if applicable> The rider contacted support on <date>; the thread is attached.
```

---

## 6. PIPEDA — what must NOT go into the pack

Submitting evidence to Stripe/the card network is a disclosure of personal information. Keep it to what the dispute requires.

**Do not include:**
- The **driver's** personal phone number, home address, licence number, or plate. Use `driver_code` + vehicle make/model/colour — this is exactly why the invoice PDF already carries `driver_code` and not the driver's phone. The dispute is between us and the cardholder; the driver is not a party to it.
- Other riders' data — never attach an unfiltered export, a full location-history dump, or a multi-ride CSV.
- GPS points outside the ride window. The route-map endpoint already filters to `navigating_to_pickup` and `trip_in_progress` phases; apply the same filter to any CSV you build by hand.
- Payment card numbers in any form. Last-4 + brand only (`card_brand`, `card_last4` on the ride).

**Fine to include:** the disputing cardholder's own name, email, phone, IP and trip data. They are the subject of the dispute and it is the stated purpose of the disclosure.

Log the disclosure: note in the Zoho ticket what was submitted, to whom, and on what date.

---

## 7. Known gaps — say these out loud rather than rediscovering them

- **No one-click evidence pack.** Every dispute is assembled by hand from 4–6 endpoints and 3 SQL queries. If chargeback volume grows past a handful a month, build `GET /api/admin/rides/{ride_id}/dispute-pack` returning a zip.
- **No Stripe evidence submission from our side.** `backend/routes/webhooks.py` records disputes but never calls `stripe.Dispute.modify(...)`. Submission is manual in the Stripe Dashboard.
- **No dispute-deadline alerting.** `stripe_disputes` stores no `evidence_due_by` and nothing warns as it approaches. Watch the Stripe Dashboard, or add the column.
- **The admin Disputes page is the in-app dispute queue, not the chargeback queue.** `admin-dashboard/src/app/dashboard/disputes` reads the `disputes` table (rider-raised refund requests). Stripe chargebacks live in `stripe_disputes` and currently have **no admin UI at all** — they are visible only via SQL or the Stripe Dashboard.
- **GPS retention is 3 years** (pickup/dropoff trace, per the retention policy); trip records are 7. Any dispute lands far inside both windows, so this is not a practical constraint — but a re-opened dispute on a 3-year-old ride would have the receipt and timeline without the breadcrumbs.

---

## 8. After the dispute closes

`charge.dispute.closed` updates `stripe_disputes.status` and flips the ride to `paid` or `dispute_lost` automatically — no manual DB edit needed.

If **lost**: the funds and the dispute fee are already debited by Stripe. Reconcile against `financial_events` (see `docs/runbooks/stripe-reconciliation.md`) and record the outcome on the Zoho ticket. Do not issue a separate refund — the chargeback already moved the money.

If **won**: confirm the ride reads `payment_status = 'paid'` and the funds were returned in the Stripe balance.

Either way, if the dispute revealed a real product problem (a fare bug, a driver taking a wrong route, a duplicate charge), file it as a normal issue with a Change Impact entry — the chargeback is the symptom, not the fix.
