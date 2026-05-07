# /incident — P0/P1 Incident Runbook

Structured guide to handle a Spinr production incident. Walks you from detection → mitigation → postmortem. Does **not** take destructive actions without explicit user confirmation.

## Usage

```
/incident                    # interactive — asks for severity, surface, symptom
/incident P0 dispatch "no drivers matching in Regina"
/incident P1 payments "Stripe webhooks backing up"
```

## Severity definitions

| Severity | Criteria | Response SLA |
|---|---|---|
| P0 | Revenue loss, safety impact, or full outage of any surface (rider/driver/admin/backend) | Immediate page, status post within 15 min |
| P1 | Degraded experience for > 10% of users, or any money/safety anomaly | Response within 2 h |
| P2 | Single-user impact with workaround, or non-critical tooling break | Response within 24 h |

## Phases

### 1. Scope — what, where, who

Ask / gather:
- Surface: backend / rider-app / driver-app / admin
- Domain: dispatch / payments / auth / safety / corporate / drivers / rides
- First noticed when? By whom? What did they see?
- Is the fare/money/safety flow affected? (If yes → escalate to P0 regardless of volume)

### 2. Capture evidence (before touching anything)

Run in order:
```
git log --oneline -20                 # recent deploys
git status                            # any in-flight local changes
```

Then, depending on surface, check:
- Railway/Render logs for backend errors in the last hour (tag: `domain=<domain>`)
- Sentry — filter by `env=production` and the affected `domain` tag
- Supabase logs — slow queries, RLS denials, connection spikes
- Stripe dashboard (if payments) — event log, webhook delivery failures
- Twilio status (if SMS/SOS affected)

Save the evidence into a running note — pasted into the incident Slack thread.

### 3. Stop the bleed (non-destructive first)

Choices in order of preference:
1. **Feature flag off** (if one exists for the affected path)
2. **Revert last deploy** — `git revert <sha>` on a branch, fast-track through CI
3. **Scale up replicas** — if it's load-related, not code-related
4. **Route around** — disable the affected service area / surge zone / corporate account

Before any destructive action (DB mutation, force-push, replica wipe, manual Stripe refund): **confirm with on-call lead**. Never auto-execute.

### 4. Communicate

Post updates at:
- T+0   — "Investigating: <one-line symptom>. Impact: <surface + estimate>."
- T+15  — "Identified: <root cause hypothesis>. Mitigation: <action>."
- T+X   — "Mitigated: <action taken>. Monitoring."
- T+60  — "Resolved" or escalation with new ETA.

Status page + Slack `#spinr-status`. Use plain language, no jargon.

### 5. Postmortem (within 5 business days for P0/P1)

Create `docs/incidents/YYYY-MM-DD-<slug>.md` from this template:

```
# Incident: <title>

- Date: YYYY-MM-DD
- Duration: <start UTC> → <end UTC>
- Severity: P0 / P1
- Surfaces: backend / rider-app / ...
- Domain: dispatch / payments / ...

## Impact
- Users affected: <count or %>
- Revenue impact: $<est>
- Safety impact: <none / <describe>>

## Timeline (UTC)
- HH:MM — <what happened>
- HH:MM — <detection>
- HH:MM — <mitigation>
- HH:MM — <resolved>

## Root cause
<one paragraph — no euphemisms>

## Contributing factors
- <list>

## What went well
- <list>

## What went poorly
- <list>

## Action items
| Item | Owner | Priority | Ticket |
|---|---|---|---|
|  |  |  |  |

## Lessons (for CLAUDE.md / context files)
<anything that should update durable context to prevent recurrence>
```

### 6. Durable fix

- Add a test that would have caught this (unit, integration, or E2E)
- Update `CLAUDE.md` or relevant `.claude/context/*.md` if the incident revealed a convention gap
- Add an alert/metric if detection was slow

## Do NOT

- Do not execute destructive SQL or `git push --force` without user confirmation
- Do not "try things" on production — branch + test + review
- Do not attribute blame in postmortems — blameless, focus on systems
- Do not close an incident until action items are filed and assigned
