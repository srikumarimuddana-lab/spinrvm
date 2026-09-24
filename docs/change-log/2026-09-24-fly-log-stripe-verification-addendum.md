# Addendum: Stripe/Supabase confirmation of the requires_capture pre-trip capture bug (Fly.io logs still unreachable)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude Code (session_01QuTTxSS6aZcLqU8ZNECf5i) |
| Surface(s) | backend (verification only — no code changed) |
| Domain (Sentry tag) | payments |
| Related entries | `docs/change-log/2026-09-21-payment-retry-requires-capture-pre-trip-guard.md`, `docs/change-log/2026-09-21-cancellation-refund-already-captured-hold.md`, `docs/change-log/2026-09-21-cancellation-captured-hold-refund-backfill.md` |

## What this addendum does

A follow-up session was asked to close the one open gap in the three 2026-09-21 change-log entries above: "exact trigger sequence... not confirmed via application logs" (Fly.io was unreachable at the time; Railway hadn't redeployed for that window). Fly.io API access was reconfigured for this session's container, so this entry re-attempts that verification and cross-checks Stripe's own record directly.

## 1. Fly.io logs — still not reachable

`https://api.fly.io/v1/apps` is rejected by this session's egress proxy with a hard `403` (`connect_rejected`, "gateway answered 403 to CONNECT — policy denial"). This is a container/environment network-policy gap, not a transient failure — no Fly.io log content was retrieved or is claimed below. If a future session needs actual Fly application logs for this window, the environment's egress allowlist needs `api.fly.io` (and likely `api.machines.dev`) added before that's possible.

## 2. What Stripe's own record confirms instead

Live Stripe account `Spinr Mobility Inc` (`acct_1SSk2XFXFgLO2LdO`) was queried directly for the PaymentIntents on all three rides referenced across the three related entries. Stripe's `charge.created` timestamp is the actual capture moment for a manual-capture PaymentIntent, and it's independent of anything in the app DB — this is closer to ground truth than the DB-only verification the original entries had.

| Ride | Created | Stripe capture (`charge.created`) | Cancelled / Completed | Refunded? |
|---|---|---|---|---|
| `e1b453ee-3c3e-44e6-a74f-80257c30a041` (`pi_3UGNkeFXFgLO2LdO04ITR7Ta`) | 2026-09-16 18:21:42 UTC | **18:40:12 UTC** (+18m30s) | Cancelled 18:47:38 UTC (+7m26s after capture) | **No** — `amount_refunded: 0`, 0 refund objects, as of 2026-09-24 |
| `2d766197-da3e-487b-921f-a841823fa867` / `SPR-XY55VL` (`pi_3UG17IFXFgLO2LdO13kygOEw`) | 2026-09-15 18:13:40 UTC | **18:30:04 UTC** (+16m24s) | Cancelled 18:30:54 UTC (+50s after capture) | **No** — `amount_refunded: 0`, 0 refund objects, as of 2026-09-24 |
| `0c24901f-7c9e-4de5-9792-19f954b713a5` (`pi_3UIAxoFXFgLO2LdO1D6VdhQg`) | 2026-09-21 17:07:12 UTC | **17:25:12 UTC** (+18m) | **Not cancelled** — `status='completed'`, `ride_completed_at` 17:48:04 UTC (capture landed 23 min *before* completion) | N/A — captured amount ($2.94) matched eventual `grand_total` exactly; no overcharge |

All three show the same mechanism: the Stripe hold was captured while the ride was still active — minutes to hours before the ride reached a terminal state — consistent with the `requires_capture` branch firing on a live, uncompleted ride rather than a genuinely stranded post-settlement hold. This directly confirms the root cause in `2026-09-21-payment-retry-requires-capture-pre-trip-guard.md` §2, previously supported only by code reading + DB state.

## 3. Correction to the original writeup

`0c24901f-7c9e-4de5-9792-19f954b713a5` was cited in the task handoff as "the one that showed the anomaly: rider cancelled a scheduled ride ~26 min after booking with a computed $0 cancellation fee... nothing refunded." **That description does not match this ride.** Supabase's `audit_logs` table has a `ride_created` entry for it but no `ride_cancelled` entry; `rides.cancelled_at` is null and `rides.status = 'completed'`. The ride went on to complete normally after the premature capture, and since the captured amount equalled the final `grand_total`, no money was actually lost on it.

The two rides that do match the "captured, cancelled, $0 fee, never refunded" loss pattern are the ones already named in `2026-09-21-cancellation-captured-hold-refund-backfill.md`: `e1b453ee-3c3e-44e6-a74f-80257c30a041` and `2d766197-da3e-487b-921f-a841823fa867` (`SPR-XY55VL`). `0c24901f` should not be cited as a third loss instance — it's useful only as a second, harm-free confirmation that the capture-before-completion mechanism is real and reproducible, independent of the two backlog rides.

## 4. Backlog status — still open

Per the backfill entry, the reconciliation script (`backend/scripts/reconcile_cancelled_captured_refunds.py`) was written but deliberately never run against these two rows — "left for a human to trigger deliberately." Stripe confirms, as of 2026-09-24 (3 days after the fix shipped), both PaymentIntents still show zero refunds:
- `pi_3UGNkeFXFgLO2LdO04ITR7Ta` — $2.10 CAD still uncollected-back-to-rider
- `pi_3UG17IFXFgLO2LdO13kygOEw` — $2.54 CAD still uncollected-back-to-rider

Total outstanding: **$4.64 CAD** across the two known real riders. This is an operational follow-up (run the script's dry run, review, then `--apply --ride-id` for these two rows), not a code change — the code path was already verified safe in the original entry's test suite.

## 5. What was NOT verified

- Fly.io application logs — still unreachable from this environment; no log content is claimed anywhere in this addendum.
- Whether any *other* rides beyond these three carry the same pattern — this addendum only re-checked the three ride IDs already named in the existing entries; the backfill entry itself says "the true size of the backlog is unknown... nobody has run the candidate query."
- No code was changed and no Stripe refund was issued by this session.
