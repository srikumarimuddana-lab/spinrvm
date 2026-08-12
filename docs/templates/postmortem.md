# Blameless Postmortem Template

> Copy this template and fill it in. Save the completed postmortem to the
> path your incident's own runbook specifies (see "Where this gets saved"
> below) — this file defines the *shape*, each runbook still owns *where*
> its own incident class's postmortems live.
>
> **Blameless:** focus on systems and process, not individual blame. Anyone
> involved should feel safe describing exactly what they did and why —
> the goal is a system that fails safely next time, not a name attached to
> the failure.

## Summary

| Field | Value |
|---|---|
| Date | |
| Incident type | security / privacy breach / SOS / payment / dispatch / other |
| Author | |
| Incident channel / ticket | |
| Related runbook used | e.g. `docs/runbooks/security-incident.md` |
| Severity | SEV-1 / SEV-2 / SEV-3 |

One paragraph: what happened, in plain language, for someone who wasn't there.

## 1. Timeline (UTC timestamps)

| Time (UTC) | Event |
|---|---|
| | Incident began (or best estimate) |
| | Detected |
| | Triage started, IC assigned |
| | Contained |
| | Root cause identified |
| | Fully resolved |
| | Postmortem published |

## 2. Impact

- Users affected (count or %, and which segment — rider / driver / corporate admin / internal)
- Duration of impact (not duration of the incident response — when did users actually feel it)
- Financial cost (refunds issued, wallet corrections, Stripe fees, lost fares)
- Data exposure, if any (categories, individual count — cross-reference `docs/runbooks/data-breach.md` §1a if PII was involved)
- Regulatory/compliance exposure (PIPEDA breach threshold, SK Transportation Act, SGI)

## 3. Root cause — 5 Whys

State the *symptom* first, then ask "why" until you reach a systemic cause
(a process gap, a missing guard, a false assumption) rather than stopping
at "someone made a mistake."

1. Why did [symptom] happen? →
2. Why did that happen? →
3. Why did that happen? →
4. Why did that happen? →
5. Why did that happen? → *(systemic root cause — this is usually where the real action item lives)*

**Contributing factors** (anything that made this worse or slower to catch,
even if not the root cause): monitoring gap, missing test, unclear
ownership, stale documentation, etc.

## 4. What went well

What caught this, contained it, or kept the blast radius smaller than it
could have been? Credit the systems and people that worked as intended.

## 5. What went wrong

Where did detection, response, or communication lag? Be specific and
blameless — "the alert didn't fire because X" not "Y didn't notice."

## 6. Action items

Every action item needs an **owner** and a **due date** — an item with
neither is a wish, not a commitment. Link each to a tracked issue
(`ACTION_ITEMS.md`, a GitHub issue, or `OPEN-ITEMS-TRACKER.md` if your
runbook uses that).

| Action item | Owner | Due date | Tracking link |
|---|---|---|---|
| | | | |

## 7. Lessons for the framework

Does this incident reveal a gap in a runbook, a CI guard rail, or this
template itself? If so, file it (or fix it directly) rather than letting
the same gap catch the next incident too.

---

## Where this gets saved

Each incident class's own runbook specifies its output path and any
incident-specific timing requirement — use this template's structure, but
follow your runbook's path convention:

| Incident type | Runbook | Save completed postmortem to |
|---|---|---|
| PII / privacy breach | `docs/runbooks/data-breach.md` §7 | `docs/audit/postmortem-YYYY-MM-DD-<slug>.md` (within 5 business days) |
| Security incident (RLS bypass, credential exposure, etc.) | `docs/runbooks/security-incident.md` | same path/timing as above |
| General incident response | `docs/incident-response.md` | `reports/postmortems/YYYY-MM-DD-slug.md` (within 72 hours) |
| SOS / safety incident | `docs/runbooks/sos-incident.md` | `reports/incidents/YYYY-MM-DD-sos.md` (within 72 hours) |

These paths intentionally differ by incident class (established convention
in each runbook) — this template does not attempt to unify them.
