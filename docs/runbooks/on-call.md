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

---

## Rotation

**Status: rotation roster not yet populated in this document.** PagerDuty
is the system of record for who is currently primary/secondary — this repo
does not duplicate the live schedule (it would drift and become
misleading). What belongs here instead, once decided:

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
