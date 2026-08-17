# `financial_events` zero-row investigation (2026-08-17)

## Question

`ACTION_ITEMS.md` A36 (surfaced 2026-08-16 while investigating A34): `select
count(*) from financial_events` returns **0** in production despite the
table being read/written by 42 files across the backend — is it (a) wiped by
one of the ad-hoc scripts A34 found, or (b) a broken write path?

## Answer: neither. It's the correct, accurate state.

**No native Spinr ride has ever completed a real payment in production.**
`financial_events`'s only writer (`ledger_service.record_event`, called from
`payment_service.record_payment_event`/`record_refund_event`) has simply
never been invoked with real traffic — not wiped, not broken.

## Evidence chain

1. **`financial_events` has exactly one write path, and it's gated on a real
   ride.** `record_payment_event`/`record_refund_event` are called from
   exactly two places in the entire codebase, both inside `routes/
   webhooks.py`'s `if ride_id:` branches (`webhooks.py:751`, `:854`), where
   `ride_id = meta.get("ride_id")` comes from the incoming Stripe event's
   `metadata.ride_id`. No other call site exists.

2. **Every completed, card-paid ride in production is legacy-imported.**

   ```sql
   select
     count(*) filter (where legacy_import_metadata->>'source' = 'legacy_mongo_booking_import') as legacy,
     count(*) filter (where legacy_import_metadata->>'source' is distinct from 'legacy_mongo_booking_import') as native,
     count(*) as total
   from rides
   where status = 'completed' and payment_method in ('card','stripe');
   -- {"legacy": 186, "native": 0, "total": 186}
   ```

   Legacy imports are bulk `INSERT`s (`booking_import_service.py`) that never
   touch the live Stripe webhook path — and correctly never write
   `financial_events`; their money bookkeeping goes through the separate
   `legacy_import`/`stripe_sync` payout-type mechanism (see A31-A33).

3. **Only 2 native rides exist in all of production, and neither was ever
   charged.**

   ```sql
   select id, status, payment_method, payment_status, created_at
   from rides
   where legacy_import_metadata->>'source' is distinct from 'legacy_mongo_booking_import';
   -- a1552055-...: status=cancelled, payment_status=pending
   -- 9252502a-...: status=cancelled, payment_status=pending
   ```

   No native Spinr ride has ever reached `status='completed'` with a real
   Stripe payment in this production database. There has been nothing for
   `record_payment_event` to fire for.

4. **Real Stripe webhook traffic exists — 1,232 rows in `stripe_events` since
   2026-06-16 — but it isn't ours.** Every `payment_intent.succeeded` payload
   inspected carries metadata shaped like the OLD app, not the new one:

   ```
   metadata: {"type": "card", "booking": "6a80a75fca6c0d82beaba5cc", "user_id": "6a67f7a2ca6c0d82be14a07f"}
   ```

   24-hex-character MongoDB ObjectIds, not Spinr UUIDs, and the key is
   `booking` — not `ride_id`, which is what the new app's own webhook
   handler requires. Most recent observed: **2026-08-15**, real CAD amounts
   ($9.95, $6.00, $4.46, $20.27, …), real `booking` references.

5. **The webhook handler correctly no-ops this traffic.** Since these events
   carry no `ride_id`, the `if ride_id:` branch — the only call site of
   `record_payment_event` — never executes. They fall through harmlessly to
   the catch-all `mark_stripe_event_processed()` at the end of the handler
   (no retry storm). One minor, low-severity side effect: the `if user_id:`
   push-notification branch (`webhooks.py:877`) still attempts
   `send_push_notification(user_id=<mongo_objectid>, ...)` for these — wrapped
   in `try/except`, silently swallowed, wasted work only.

## Conclusion

`financial_events` is empty because the system has processed **zero real
completed native rides**, not because of a deletion or a broken write path.
The write path itself (`ledger_service.py`'s retry-with-Sentry-escalation
logic) is sound by inspection — it has simply never had real traffic to act
on. This is genuinely good news: the "42 files actively using
`financial_events`" reflects solid pre-launch engineering for a feature that
hasn't been exercised in production yet, not a broken pipeline.

## A more important finding, surfaced along the way

The webhook payload evidence in step 4 is direct confirmation — not
inference — of something the 2026-08-15 dual-run audit had already flagged
as a *risk* but could not confirm: **the old app is still live and
processing real customer payments on the same Stripe account as the new
app**, as recently as 2 days before this investigation
(`docs/audit/2026-08-15-dual-run-cutover/P0-critical-money-and-regulatory.md`
finding #2). Spun off as `ACTION_ITEMS.md` A40 — an operational question
(is this expected for the current migration phase?) rather than a code bug,
since the current handling is safe.

## Addendum (2026-08-17, same day): A40 question #2 closed

A40 raised three open questions. Question #2 — whether old-app Connect/
payout-side webhook events could interact unsafely with a new-app driver's
Stripe Connect account if the account is shared between both apps (per the
dual-run audit's P0 finding #2) — is now closed with live evidence:

- **`transfer.created`** (4 events observed) has **no handler at all** in
  `routes/webhooks.py` — it falls to the generic unhandled branch
  regardless of which account it references. Structurally inert.
- **`account.updated`** (15 events observed) *is* looked up by connected-
  account ID (`services/stripe_kyc_sync.py::apply_account_update`, matched
  against `drivers.stripe_account_id`) — this is the one event type that
  genuinely could cross-match a shared account. Checked live:

  ```sql
  select se.payload->'data'->'object'->>'id' as account_id, d.id as driver_id
  from stripe_events se
  left join drivers d on d.stripe_account_id = se.payload->'data'->'object'->>'id'
  where se.event_type = 'account.updated'
  order by se.received_at desc;
  -- all 15 rows: driver_id = null
  ```

  Also checked against `drivers.stripe_account_id_superseded` (the column
  tracking a detached/migrated account) — zero matches there either.

**No evidence the shared-account risk has actually manifested** at the
Connect/webhook layer, even though it remains structurally possible per the
original P0 finding (no code currently prevents it — this only confirms it
hasn't happened yet, as observed). Worth noting: even if it did match,
syncing `account.updated` regardless of triggering app would be *correct*
behavior, not a bug — Stripe Connect account state is a property of the
account itself, not of which app caused the change.

**Update, same day: question #1 confirmed by the product owner** — dual-run
(both apps live, old app still processing real Stripe charges on the shared
account) is intentional for the current migration phase, not an incident.
Question #3 (splitting the webhook endpoint) is downgraded to a
low-priority hygiene item — worth doing before the Oct 31 decommission so
old-app traffic naturally stops arriving here, but not urgent, since current
handling is already confirmed safe (no ride/payment side effects, no
Connect-account collision). A40 is closed with all three questions
resolved; no code change was needed anywhere in this investigation.

## What was NOT verified

- Event types other than `payment_intent.succeeded`, `transfer.created`, and
  `account.updated` were not individually traced — though the general
  pattern (ride/booking-scoped handlers gate on `metadata.ride_id`,
  Connect-scoped handlers gate on `stripe_account_id` match) covers the two
  structurally distinct risk shapes a webhook handler in this codebase can
  take.
- Whether old-app traffic in `stripe_events` predates 2026-06-16 — that's
  just the earliest row currently in the table, not necessarily when the old
  app's Stripe activity started.

## Full detail

`ACTION_ITEMS.md` A36 (closed) and A40 (question #2 closed, #1/#3 open)
carry the full write-up, including every query run and every file/line
referenced above.
