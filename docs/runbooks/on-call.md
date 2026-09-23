# On-Call & Escalation Policy

**Purpose:** who gets paged, for what, how fast they're expected to respond,
and who they escalate to if they can't resolve it alone. This is the
policy layer; `docs/incident-response.md` is the *response playbook* once
someone is engaged — read this to know **when a human gets paged at all**,
that one for **what they do once paged**.

**Owner:** `devops` + engineering leadership · reviewed quarterly or after
any SEV-1/SEV-2 where paging or escalation itself was part of the problem.

---

## Two severity tracks — don't conflate them

This repo has two independent severity vocabularies for two different
things. Using the wrong one in an incident channel causes real confusion
about urgency — know which track you're on:

| Track | Vocabulary | Defined in | Applies to |
|---|---|---|---|
| **Engineering incidents** | SEV-1 / SEV-2 / SEV-3 / SEV-4 | `docs/incident-response.md` § Severity Ladder | Outages, breaches, safety incidents, degraded SLOs |
| **Support tickets** | P0 / P1 / P2 / P3 | `CLAUDE.md` KPI table (`Support ticket response (P1) < 2h`) | Rider/driver/corporate-admin support requests, not necessarily an active incident |

A P1 support ticket does not automatically page on-call — most are handled
within business hours by the support rotation. A SEV-1/SEV-2 engineering
incident always does. The overlap case (a support ticket that turns out to
be a live incident — e.g. a payment-processing report that's actually a
Stripe webhook outage) gets escalated from the support track into the
engineering track; see "Support → engineering escalation" below.

---

## Who gets paged, and for what

| Severity | Page? | Who | Target ack |
|---|:-:|---|---|
| SEV-1 (full outage, confirmed PII breach, safety incident) | **Yes, immediately** | Primary on-call engineer (PagerDuty) | ≤ 5 min |
| SEV-2 (partial outage, degraded critical-path SLO, suspected breach) | **Yes, immediately** | Primary on-call engineer (PagerDuty) | ≤ 15 min |
| SEV-3 (non-critical prod bug, vendor degradation with workaround) | No — normal-hours queue | On-call engineer, next business hours | ≤ 1 h |
| SEV-4 (minor/cosmetic) | No | Whoever picks it up from the backlog | Next sprint |

Full definitions and target resolution times: `docs/incident-response.md` §
Severity Ladder (this table only restates the paging column so it's visible
in one place alongside "who").

### Escalation ladder (SEV-1/SEV-2)

```
SEV-1/SEV-2 declared
  ↓
Primary on-call engineer (PagerDuty) — ack ≤ 5 min (SEV-1) / ≤ 15 min (SEV-2)
  ↓ (if unacknowledged after target, or unresolved after 15 min)
Secondary on-call engineer (PagerDuty escalation policy)
  ↓ (if unresolved after 15 min further, or privacy/safety impact confirmed)
Engineering manager
  ↓ (within 1 h if privacy or safety impact confirmed)
CTO + Legal + CEO
  ↓ (for regulator-trigger scenarios — see docs/incident-response.md § Regulatory Triggers)
OPC / CRA / CRTC as required
```

This mirrors `docs/incident-response.md`'s existing escalation flow
verbatim — restated here because this document is the one a newly-paged
engineer should be able to read standalone without also having the full
incident-response playbook open.

### SOS / safety incidents

Paged and escalated separately — `docs/runbooks/sos-incident.md` owns the
full flow (responder ack ≤ 2 min, 911 contact ≤ 5 min if unreachable). Not
folded into the general SEV ladder above; SOS has its own tighter timers.

### Support → engineering escalation

A support agent who suspects a support ticket is actually symptomatic of a
live incident (not an isolated user issue) escalates by:
1. Paging the primary on-call engineer through the normal PagerDuty flow
   (same as any other SEV-1/SEV-2 trigger) — do not wait for a second
   ticket to "confirm the pattern" if the ticket itself describes outage-
   shaped symptoms (e.g. "no drivers showing up anywhere," "payment stuck
   for everyone").
2. Opening `#incident-YYYY-MM-DD-slug` per `docs/incident-response.md`.

## Alert and telemetry acceptance gates

Treat repository alert definitions as intended configuration until their live
Grafana rules, data source, scrape path, notification policy, and operator
receipt have all been checked. `metrics-agent/fly.toml` currently says the
Alloy app is not deployed; no live Grafana state is asserted by this runbook.
The `metrics-agent/grafana/alert-rules.yaml` header records that rules were
entered manually in the Grafana UI, but the live UI must be compared with the
file after any change.

### Existing thresholds

| Signal | Current rule/source | Operator meaning |
|---|---|---|
| Dispatch P95 | Grafana file: Fly `spinr_dispatch_offer_to_accept_duration_ms` P95 > 2,000 ms for 5 min (5 min rate window) | Check dispatch latency and driver availability; see `docs/runbooks/driver-not-receiving-rides.md`. |
| Payment settlement failure rate | Grafana file: Fly failure/total `increase()` ratio > 1% for 10 min, only with >=20 samples in the same 10 min; lower volume yields `NoData` and evaluation errors use `Alerting` | A low-volume `NoData` is not a measured 0% failure rate. Check scrape health before classifying it. Avoid triggering real charges for acceptance testing. |
| Insurance-period write failures | Grafana file: `increase(spinr_insurance_period_write_failed_total[5m]) > 0`, no pending delay | Investigate failed write and confirm reconciler convergence; see `docs/runbooks/deploy-migration-64-65.md`. |
| DB pool queue | `capacity_watchdog`: queue depth >50 for 3 consecutive 60 s ticks | Per-machine saturation; inspect pool stats and Supabase capacity. |
| DB circuit open | `capacity_watchdog`: any increase in `spinr_db_calls_rejected_total{reason="circuit_open"}` | Immediate DB failure signal. |
| DB deadline rejection | `capacity_watchdog`: non-circuit rejections >30/min for 3 ticks by default | Client budgets are expiring; inspect reasons and client clock/network before assuming a DB capacity issue. |
| Rate-limit pressure | `capacity_watchdog`: >120 `spinr_rate_limit_violation_total`/min for 3 ticks | Fleet traffic is receiving many 429s; distinguish a real burst from an overly strict limit. |
| Background-loop staleness | `loop_watchdog`: loop-specific heartbeat threshold; one successful webhook alert per loop per 3,600 s in-process cooldown | Inspect the named loop's startup/error logs and heartbeat. Staleness uses the live registry in `backend/core/lifespan.py`, not a copied loop count. |

The capacity watchdog's alert cooldown is 1,800 s; `db_pool_saturation` and
`db_circuit_open` are per-machine, while deadline-rejection and rate-limit
alerts use Redis fleet dedup (fail-open if Redis is unavailable). Their
thresholds and response steps are maintained in
[`capacity-scaling.md`](capacity-scaling.md). `loop_watchdog` uses
`ALERT_WEBHOOK_URL`; the capacity watchdog can use webhook and/or
`ALERT_EMAIL_TO`. Both are silent when no channel is configured. Tests mock
delivery; they do not prove a live operator received a notification.

### Stripe identity repairs

The only dedicated repair counter currently emitted is
`spinr_payments_stripe_identity_reprovisioned_total{surface="rider",reason=...}`.
It increments after a rider's stale Stripe customer is replaced; reasons are
`mode_mismatch` and `resource_missing`. Use a one-hour increase grouped by
`surface,reason` to see newly repaired rider identities, then check the mode
audit and the Stripe key/account before interpreting a cluster. There is no
numeric alert threshold today; first-touch repairs after a key cutover may be
expected, but a continuing or unexpected increase needs investigation.

Driver Connect retirement and corporate customer retire/re-provision currently
have actionable logs, not dedicated repair counters or Grafana rules. Driver
retirement logs that payouts must be re-onboarded. Corporate retirement logs
that the company cannot be charged until an admin re-adds a billing card. On
retirement logs, inspect `reason` (`mode_mismatch` or `resource_missing`) and
the entity ID; reprovision warning logs have entity IDs but do not always carry
a `reason` field. Do not infer that a missing counter means no repair happened.
The repair paths deliberately do not run on Stripe auth,
rate-limit, or transient connection failures. See the
[`Stripe cutover runbook`](stripe-legacy-migration.md#appendix--testlive-key-cutover-same-app)
for the mode audit and the `stripe_reprovision_stale_ids` kill switch; disabling
it is a global payment-identity change and may surface payment-profile errors.

### Before relying on a signal

1. Confirm the Grafana-managed rules match the checked-in file, the Prometheus
   data-source UID is real, and the notification receiver has valid recipients.
2. Confirm Alloy is deployed and remote-writing, backend scrapes are healthy
   for each running machine, and a real settlement series carries
   `provider="fly"`. A missing payment series can mean low volume or broken
   telemetry; inspect the target `up` series and Alloy health separately.
3. For the payment rule, preview the 19-sample and 20-sample cases without
   issuing payments. Verify the low-volume result is `NoData`, the breach
   result is >1%, and query errors are visible as alerting/error states.
4. Confirm webhook/email secrets are present by name (values stay secret),
   then have the responsible operator run an approved controlled notification
   check and acknowledge receipt. `fly secrets list` confirms names only; it
   is not delivery evidence. Record the check date, channel, recipient/rotation,
   and acknowledgement with the deployment or incident evidence.

Until those checks pass, report these alerts as configured in source or
unit-tested, not as measured production coverage. The source includes unit
tests for capacity threshold/delivery behavior and loop-alert retry/cooldown;
they use mocked metrics and channels. No live Grafana, remote-write, or
operator-receipt check is represented as complete here.

---

## Rotation

**Status: rotation roster not yet populated in this document.** PagerDuty
is the system of record for who is currently primary/secondary — this repo
does not duplicate the live schedule (it would drift and become
misleading). What belongs here instead, once decided:

Sizing reference (not a decision — for whoever sets the "minimum team size"
row below): Google's SRE literature states a sustainable 24/7 on-call
rotation needs a floor of roughly 6–8 engineers split across ≥2 timezones
per service, or a minimum of 3 people to cover a single-timezone rotation
without unsustainable burnout. Spinr's actual staffing is smaller than that
today — treat this as the target to grow toward, not a claim that it's
already met.

| Field | Value |
|---|---|
| Rotation cadence (weekly / bi-weekly) | *TBD* |
| Handoff day/time | *TBD* |
| Minimum team size for sustainable rotation | *TBD* |
| PagerDuty schedule link | *TBD* |
| Escalation policy name in PagerDuty | *TBD* |

Whoever owns standing this up: fill in this table and remove this notice,
rather than leaving the roster to live only in PagerDuty's UI where a new
engineer has no doc to find it from.

---

## Response-time expectations (reference)

Pulled from the two source-of-truth docs, not redefined here — if these
drift out of sync with `CLAUDE.md` or `docs/incident-response.md`, those
two win and this table should be corrected to match, not the other way
around.

| Metric | Target | Source |
|---|---|---|
| SEV-1 ack | ≤ 5 min | `docs/incident-response.md` |
| SEV-1 resolution | ≤ 4 h | `docs/incident-response.md` |
| SEV-2 ack | ≤ 15 min | `docs/incident-response.md` |
| SEV-2 resolution | ≤ 8 h | `docs/incident-response.md` |
| SOS responder ack | ≤ 2 min | `docs/runbooks/sos-incident.md` |
| SOS 911 contact (if unreachable) | ≤ 5 min | `docs/runbooks/sos-incident.md` |
| P1 support ticket response | < 2 h | `CLAUDE.md` KPI table |

---

## Related documents

- `docs/incident-response.md` — full response playbook once an incident is declared
- `docs/runbooks/ledger-alerts.md` — the six `spinr_alert` tags on the payment ledger
  and card-settlement path: what each means, which page and which do not, and the
  Sentry rule spec for each (**the rules are not created yet**)
- `docs/runbooks/sos-incident.md` — SOS-specific flow
- `docs/runbooks/data-breach.md` — PII breach-specific flow
- `docs/runbooks/security-incident.md` — security-specific flow
- `docs/templates/postmortem.md` — postmortem template for any of the above
