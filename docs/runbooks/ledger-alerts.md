# Runbook — Ledger & settlement alerts (`spinr_alert`)

Six Sentry alert tags cover the rider-payment ledger and the card-settlement
path. **None of the rules below exists in Sentry yet** — the code emits the
tags, but an emitted tag with no rule behind it is a log line nobody reads.
Creating them is a Sentry-UI action; the specs are here so it is a
copy-and-configure job rather than a design job.

Rule format follows `docs/runbooks/saskatoon-launch.md` § G-2, which is the
established precedent in this repo (`refresh_token_reuse`).

Severity uses the **engineering** track (SEV-1…SEV-4) from
`docs/incident-response.md`, not the support P0…P3 track — see
`docs/runbooks/on-call.md` § "Two severity tracks".

> **Prerequisite.** All of these fire only once `SENTRY_DSN` is set
> (`saskatoon-launch.md` § G-1). Four of the six additionally require
> `ledger_double_entry_enabled` and/or `ledger_atomic_settle_enabled` to be on;
> with both flags off, only `ledger_write_failed` can fire.

---

## Summary

| `spinr_alert` | Severity | Page? | Money at risk? | Emitted from |
|---|---|:-:|:-:|---|
| `settlement_state_unverifiable` | SEV-2 | **Yes** | Charge captured, state unknown | `payment_service._finalize_card_settlement` |
| `ledger_write_failed` | SEV-2 | **Yes** | Tax record incomplete | `ledger_service.record_event` |
| `ledger_legs_unbalanced` | SEV-3 | No | No — defect, not loss | `ledger_service.write_legs` |
| `ledger_legs_lost` | SEV-3 | No | No — header intact | `ledger_service.write_legs` |
| `ledger_legs_degraded` | SEV-4 | No | No — split lost, totals right | `ledger_projection.project_pending_legs` |
| `ride_card_display_stale` | SEV-4 | No | No — cosmetic | `payment_service._write_display_fields` |

Everything below SEV-2 goes to a Slack channel, not PagerDuty. Resist the urge
to page on the leg alerts: the header ledger — the thing CRA cares about — is
already durable in every one of those cases, and a paging rule that mostly
fires for bookkeeping detail is a rule people learn to dismiss.

---

## SEV-2 — page

### `settlement_state_unverifiable`

- **Project:** spinr-backend (production)
- **Alert type:** Issue Alert
- **When:** `An event is captured`
- **If:** `event.tag spinr_alert equals settlement_state_unverifiable`
- **Then:** Notify PagerDuty service `spinr-oncall`
- **Frequency:** Every event — do not throttle

**What it means.** The atomic settle RPC returned an ambiguous transport error
*and* the follow-up ride re-read also failed, so the backend genuinely cannot
tell whether the charge committed. The rider has been shown "Payment was
captured but confirmation failed. Do not retry — our team has been notified."
That sentence is a promise; this alert is the only thing that keeps it.

**First response**
1. Pull `ride_id` and `payment_intent_id` from the Sentry context.
2. In Stripe, look up the PaymentIntent — did it capture, and for how much?
3. In Supabase: `select payment_status, payment_intent_id from rides where id = ...`
   and `select * from financial_events where ref = '<payment_intent_id>'`.
4. Reconcile the three states:
   - captured + `paid` + header present → the RPC committed; nothing to do
     beyond closing the alert.
   - captured + not `paid` → money moved, ride is stuck. Do **not** re-run
     settlement (it would double-charge). Fix the ride row forward and write
     the missing header.
   - not captured → no money moved; the ride can be settled normally.
5. Anything ambiguous after that → escalate per `on-call.md`; do not guess with
   a live charge.

### `ledger_write_failed`

- **If:** `event.tag spinr_alert equals ledger_write_failed`
- **Then:** Notify PagerDuty service `spinr-oncall`
- **Frequency:** Every event

**What it means.** A `financial_events` header could not be written after 3
retries — or the Supabase client was absent entirely. Money moved; the 7-year
CRA/SK tax record does not know about it. Not rider-visible, and deliberately
so: the charge succeeded, so failing the request would have been worse.

**First response**
1. Context carries `event_type`, `ride_id`, `user_id`, `delta_cents`, `ref`.
2. Check whether the DB was simply down — if `spinr_alert=ledger_write_failed`
   arrives in a burst alongside other DB errors, treat the outage as the
   incident and this as a symptom.
3. Once healthy, backfill the missing rows: for each alerted `ref`, confirm
   Stripe has the charge and no `financial_events` row exists, then insert one.
   The table is append-only — `UPDATE` is blocked by trigger, and `DELETE` only
   under the migration-289 purge GUC.
4. The daily Stripe-vs-ledger reconciliation (`utils/reconciliation.py`) is the
   backstop that catches any you miss; it alerts on a >$0.01 discrepancy.

---

## SEV-3 — Slack `#spinr-payments-alerts`

### `ledger_legs_unbalanced`

- **If:** `event.tag spinr_alert equals ledger_legs_unbalanced`
- **Then:** Notify Slack `#spinr-payments-alerts`
- **Frequency:** Every event

**What it means.** A leg builder produced a set whose debits ≠ credits, so it
was **refused** rather than written — a half-written journal entry is worse
than none. Always a code defect, never a data condition. The header is intact
and the tax record is unaffected.

**Response.** Not urgent, but do not sit on it: it means
`build_charge_legs`/`build_refund_legs` hit inputs their arithmetic does not
model. Grab `event_id`/`ride_id` from context, reproduce against the ride row,
and fix the builder. The `promo_expense` fix
(`docs/change-log/2026-08-08-review-fixes-promo-legs-alerting-cadence.md`) is
the worked example of this class.

### `ledger_legs_lost`

- **If:** `event.tag spinr_alert equals ledger_legs_lost`
- **Then:** Notify Slack `#spinr-payments-alerts`
- **Frequency:** Every event

**What it means.** The legs were balanced and valid but the batch insert failed
3× (or the client was absent). Header present, legs missing.

**Response.** Self-healing by design — the event stays in the projection's
work queue and the next 15-minute tick retries it. Confirm that happens rather
than fixing by hand. If the queue is *not* draining, the daily
`_check_leg_completeness` will start alerting that the projection has made no
progress in 24 h; that is the escalation, and it points at a wedged loop.

---

## SEV-4 — Slack, no urgency

### `ledger_legs_degraded`

- **If:** `event.tag spinr_alert equals ledger_legs_degraded`
- **Then:** Notify Slack `#spinr-payments-alerts`
- **Frequency:** **Throttle — at most 1 notification per hour**

**What it means.** The projection could not decompose an event, so it booked
the whole amount to `platform_revenue` rather than skipping it (skipping would
wedge the oldest-first queue and starve every newer event). The entry is
balanced and correct at the money-in level; only the driver/tax/platform
*split* is lost. `context.reason` says which case: `ride_missing`,
`no_fee_split_metadata`, `amounts_inconsistent`, `refund_build_failed`.

**Expect a burst the first time `ledger_double_entry_enabled` is switched on** —
historical cancellation fees predate the fee-split metadata and will all
project degraded by design. That is why this one is throttled. A *steady* rate
afterwards is the signal worth chasing.

**Response.** Batch these up rather than handling individually. A cluster
sharing one `reason` is a decomposition gap worth a fix; scattered singletons
are the expected long tail.

### `ride_card_display_stale`

- **If:** `event.tag spinr_alert equals ride_card_display_stale`
- **Then:** Notify Slack `#spinr-payments-alerts`
- **Frequency:** Every event (it should be rare)

**What it means.** A rider used "Change Card", the new card charged
successfully, and the follow-up write recording *which* card was used failed 3
times. The ride is paid and the ledger is correct. The admin ride-detail view
will show the **rejected** card.

**Response.** Cosmetic and self-healing: `routes/admin/rides.py::_resolve_ride_card`
re-derives brand/last4 from the PaymentIntent and writes them back whenever
they are null. Worth investigating only if it clusters, which would point at
a `rides` write problem rather than a display one.

---

## Related documents

- `docs/runbooks/on-call.md` — paging policy, severity tracks, escalation ladder
- `docs/runbooks/saskatoon-launch.md` § G — Sentry setup, DSN, and the
  `refresh_token_reuse` rule this file's format follows
- `docs/runbooks/stripe-reconciliation.md` — the daily Stripe-vs-ledger job
- `docs/architecture/payments-rider-stripe.md` — where each alert is raised
- `docs/incident-response.md` — full severity ladder and regulatory triggers
