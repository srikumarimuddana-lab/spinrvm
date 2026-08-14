# Incident Response Plan

**Purpose:** When something goes wrong — outage, data breach, safety incident,
vendor compromise — this is the playbook. Speed and clarity of response
determine blast radius and regulatory posture.

**Owner:** `security` + `devops` (on-call rotation) · **D18 dimension**

---

## Severity Ladder

| Severity | Definition | Page? | Target ack | Target resolution | Comms |
|---|---|:-:|---|---|---|
| SEV-1 | Full outage OR confirmed breach of PII OR safety incident (SOS handled separately) | Yes | 5 min | 4 h | Status page + in-app + email |
| SEV-2 | Partial outage OR degraded SLO on critical path OR suspected breach | Yes | 15 min | 8 h | Status page |
| SEV-3 | Non-critical bug in prod OR vendor degradation with workaround | Normal hours | 1 h | 24 h | Internal only |
| SEV-4 | Minor issue OR cosmetic | No | 1 business day | Next sprint | Internal only |

SOS events are handled by `docs/runbooks/sos-incident.md` — not general IR.

---

## Roles

| Role | Responsibility |
|---|---|
| **Incident Commander** (IC) | Owns the response; coordinates others; calls shots |
| **Technical Lead** | Diagnoses + executes mitigation |
| **Comms Lead** | Updates status page, drafts user messaging, coordinates with legal/PR |
| **Scribe** | Keeps incident timeline in shared doc |
| **Legal/Compliance Liaison** | Assesses regulatory obligations (PIPEDA, SK laws) |

For small teams: IC + Tech Lead may be one person; pull in Comms + Legal when
severity warrants.

---

## Response Flow

```
Alert fires / user reports issue
    ↓
On-call acknowledges in PagerDuty (target ≤ 5 min for SEV-1)
    ↓
Open #incident-YYYY-MM-DD-slug Slack channel + video bridge
    ↓
IC declared; severity assigned
    ↓
Diagnose via runbooks:
  - docs/runbooks/supabase-down.md
  - docs/runbooks/redis-down.md
  - docs/runbooks/stripe-reconciliation.md (if financial)
  - docs/runbooks/sos-incident.md (if safety)
  - docs/runbooks/pitr-restore.md (if data recovery)
    ↓
Mitigate + verify
    ↓
Stand down; scribe files timeline
    ↓
Post-mortem ≤ 72h (blameless)
```

---

## Runbook Index

| Scenario | Runbook |
|---|---|
| Who gets paged, when, and rotation policy | `docs/runbooks/on-call.md` |
| Supabase unavailable | `docs/runbooks/supabase-down.md` |
| Redis unavailable | `docs/runbooks/redis-down.md` |
| Stripe reconciliation delta | `docs/runbooks/stripe-reconciliation.md` |
| Chargeback / payment dispute evidence | `docs/runbooks/payment-dispute-evidence.md` |
| Accidental data delete / corruption | `docs/runbooks/pitr-restore.md` |
| SOS event | `docs/runbooks/sos-incident.md` |
| pgsodium key compromise | `docs/runbooks/pii-key-rotation.md` |

**Not yet written (open items):**
- `docs/runbooks/tls-pin-rotation.md`
- `docs/runbooks/eas-credential-compromise.md`
- `docs/runbooks/supabase-region-failover.md`
- `docs/runbooks/stripe-connect-payout-rollback.md`

---

## Regulatory Triggers

Evaluate during every SEV-1 and SEV-2:

### PIPEDA / OPC — 72h notification rule
Applies when: personal information is involved AND there is a **real risk of
significant harm**.

Steps:
1. Determine whether PII was accessed, disclosed, modified, or lost
2. Assess harm (sensitivity × likelihood)
3. If real-risk threshold met → notify OPC + affected individuals within 72h of
   determining the threshold is met (note: 72h is from *determination*, not from
   the incident start)
4. Draft using template in `reports/compliance/breach-notification-template.md`
   (to be created on first real incident)

### Saskatchewan CPPA / FOIP
Applies when: SK provincial rules trigger for residents' data. Check with `legal`.

### CRA
Applies when: tax-related records or T4A data affected. File within 30 days
per CRA guidance.

### PCI-DSS
Applies when: card data is suspected compromised (Spinr is SAQ-A; full PAN is
not stored, so most payment incidents are out of scope for card-data reporting).

### Vendor breach
When a vendor notifies Spinr of a breach (see `docs/dpa-register.md` §
"Breach Notification Chain"):
1. Treat as incoming SEV-1 or SEV-2 depending on data class
2. Determine whether Spinr customer data was affected
3. If yes, follow PIPEDA flow above using vendor-provided breach details

---

## Communication Templates

**SEV-1 initial status-page update:**
```
⚠️ Service Disruption — [Area]
We are investigating reports of [issue]. [Active rides are not affected OR
Some features are currently unavailable.] We will provide updates every 15
minutes.
Last updated: HH:MM UTC
```

**SEV-1 resolved:**
```
✅ Resolved — [Area]
The [issue] has been resolved. [Describe impact in one sentence.] A detailed
post-mortem will be published within 72 hours.
Resolved at: HH:MM UTC · Duration: N minutes
```

**User email (> 1 h user impact):**
Draft lives in `reports/compliance/incident-email-template.md` (TBD on first real incident).
Must be reviewed by `legal` before send.

---

## Post-Mortem (Blameless)

Within 72 hours of resolution, publish `reports/postmortems/YYYY-MM-DD-slug.md`
using the shared template: `docs/templates/postmortem.md` (summary, timeline,
impact, root cause via 5-whys, what went well/wrong, action items with owner
+ due date, lessons for the framework). Each action item = a new
`OPEN-ITEMS-TRACKER.md` row per ground-rules.md rule 7 (incident → audit
feedback).

**Blameless:** focus on systems + process, not individual blame. Anyone should
feel safe describing what they did.

---

## Legal Hold / Evidence Preservation

If an incident may lead to regulatory action or litigation:

- [ ] Snapshot relevant database rows into `reports/legal-hold/YYYY-MM-DD-slug/`
- [ ] Preserve relevant logs (app, backend, audit_log, Cloudflare if applicable)
- [ ] Snapshot Redis state if it contained relevant OTPs or sessions
- [ ] Preserve Stripe event data via API export
- [ ] Disable any auto-delete cron jobs that would affect the preserved data
- [ ] Chain of custody: every access to the legal-hold folder logged

---

## Tabletop Exercises (see `docs/external-testing.md` § 6)

Scenarios for annual tabletop — exercise this plan without a real incident.
Each tabletop must hit at least one runbook and the post-mortem flow.

---

## Escalation Tree (example — adjust to real contacts)

```
SEV-1 declared
  ↓
On-call engineer (PagerDuty)
  ↓ (after 15 min if unresolved)
Engineering manager
  ↓ (within 1 h if privacy / safety impact)
CTO + Legal + CEO
  ↓ (for regulator-trigger scenarios)
OPC / CRA / CRTC as required
```

Contacts maintained in `docs/oncall-contacts.md` (internal only, not committed
here) — update quarterly.

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-04-24 | Initial IR plan | audit-framework |
