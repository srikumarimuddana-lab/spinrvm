# P4 — Backend Future / Backlog

The backend audit (`reports/audits/2026-04-23-backend-api-v1.txt`) did not produce any P4-bucketed findings. Every finding fell into P0–P3.

This file exists for sprint-cadence symmetry with rider/driver/admin remediation files. Add backlog items here as future audits surface them.

Source audit: `reports/audits/2026-04-23-backend-api-v1.txt`
Branch: `claude/audit-continuation-batch-2`

---

## Inventory

(none — empty)

---

## When to add an item here

Use P4 for backend changes that are:

- Forward-looking (depend on a feature that hasn't shipped yet)
- Optimization-only with no current SLA breach (e.g. caching wins below the 100 ms noise floor)
- Process / tooling improvements that don't directly remediate a finding
- DR or observability upgrades that exceed launch-readiness requirements

Items above the launch bar belong in P0–P3, not here.

## After this file

- All five backend remediation files (P0/P1/P2/P3/P4) are now in place.
- Next: re-run D22 (Third-party / vendor risk) and D23 (Mobile binary / build & release) for the driver app once the account-wide usage limit resets, then proceed to admin Phase A–E.
